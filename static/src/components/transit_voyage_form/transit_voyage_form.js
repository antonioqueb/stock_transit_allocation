/** @odoo-module **/
/**
 * TransitVoyageLinesWidget
 * Field widget que reemplaza la lista nativa de stock.transit.line
 * con una vista agrupada por producto + propagación inline + barra flotante de asignación.
 *
 * Uso en XML: <field name="line_ids" widget="transit_voyage_lines"/>
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

class TransitVoyageLinesWidget extends Component {
    static template = "stock_transit_allocation.TransitVoyageLines";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.orm          = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            groups:        [],
            collapsed:     {},
            loading:       true,
            allPartners:   [],
            allOrders:     {},
            editingCell:   null,   // { lineId, field }
            selectedLines: new Set(),
            // Popup de asignación masiva
            showAssignPopup: false,
            popupPartner:    null,
            popupOrder:      null,
        });

        onWillStart(async () => {
            const vid = this._voyageId();
            if (vid) await this.loadData(vid);
        });

        onWillUpdateProps(async (nextProps) => {
            const oldId = this._voyageIdFromProps(this.props);
            const newId = this._voyageIdFromProps(nextProps);
            if (newId && newId !== oldId) {
                await this.loadData(newId);
            }
        });
    }

    // ─── ID del viaje ─────────────────────────────────────────────────────────

    _voyageIdFromProps(props) {
        try {
            return props.record?.resId || props.record?.data?.id || false;
        } catch { return false; }
    }

    _voyageId() {
        return this._voyageIdFromProps(this.props);
    }

    // ─── Carga ────────────────────────────────────────────────────────────────

    async loadData(voyageId) {
        if (!voyageId) return;
        this.state.loading = true;
        try {
            const lines = await this.orm.searchRead(
                "stock.transit.line",
                [["voyage_id", "=", voyageId]],
                [
                    "id", "product_id", "lot_id", "container_number",
                    "product_uom_qty", "partner_id", "order_id",
                    "allocation_status", "x_grosor", "x_alto", "x_ancho",
                ],
                { order: "product_id asc, lot_id asc" }
            );

            // Cargar x_bloque / x_atado desde lot
            const lotIds = lines.filter(l => l.lot_id).map(l => l.lot_id[0]);
            let lotData  = {};
            if (lotIds.length) {
                const lots = await this.orm.searchRead(
                    "stock.lot",
                    [["id", "in", lotIds]],
                    ["id", "x_bloque", "x_atado"],
                );
                lots.forEach(l => { lotData[l.id] = l; });
            }
            lines.forEach(line => {
                const lot = line.lot_id && lotData[line.lot_id[0]];
                line.x_bloque = lot?.x_bloque || "";
                line.x_atado  = lot?.x_atado  || "";
            });

            this._buildGroups(lines);
            await this._loadPartnersAndOrders(lines);
        } catch (e) {
            console.error("[TransitVoyageLines]", e);
            this.notification.add("Error cargando líneas", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        const vid = this._voyageId();
        if (!vid) return;
        try { await this.props.record.load(); } catch {}
        await this.loadData(vid);
    }

    _buildGroups(lines) {
        const map = new Map();
        for (const line of lines) {
            const pid   = line.product_id ? line.product_id[0] : 0;
            const pname = line.product_id ? line.product_id[1] : "Sin producto";
            if (!map.has(pid)) {
                map.set(pid, {
                    product_id:   pid,
                    product_name: pname,
                    lines:        [],
                    total_m2:     0,
                    reserved_m2:  0,
                    lot_count:    0,
                    blocks:       new Map(),
                });
            }
            const g = map.get(pid);
            g.lines.push(line);
            g.total_m2 += line.product_uom_qty || 0;
            if (line.allocation_status === "reserved") g.reserved_m2 += line.product_uom_qty || 0;
            if (line.lot_id) g.lot_count++;

            const blockName = line.x_bloque || "Sin Bloque";
            if (!g.blocks.has(blockName)) {
                g.blocks.set(blockName, { name: blockName, total_m2: 0, count: 0 });
            }
            const b = g.blocks.get(blockName);
            b.total_m2 += line.product_uom_qty || 0;
            b.count++;
        }
        this.state.groups = [...map.values()].map(g => ({
            ...g,
            blocks: [...g.blocks.values()].sort((a, b) => b.count - a.count),
        }));
    }

    async _loadPartnersAndOrders(lines) {
        const productIds = [...new Set(lines.filter(l => l.product_id).map(l => l.product_id[0]))];
        if (!productIds.length) return;

        const saleLines = await this.orm.searchRead(
            "sale.order.line",
            [["product_id", "in", productIds], ["order_id.state", "in", ["sale", "done"]], ["display_type", "=", false]],
            ["order_id"],
            { limit: 500 }
        );
        const soIds = [...new Set(saleLines.map(sl => sl.order_id[0]))];
        if (!soIds.length) return;

        const orders = await this.orm.searchRead("sale.order", [["id", "in", soIds]], ["id", "name", "partner_id"]);

        const pmap = {};
        orders.forEach(o => {
            const pid = o.partner_id[0];
            if (!pmap[pid]) pmap[pid] = { id: pid, name: o.partner_id[1], orders: [] };
            pmap[pid].orders.push({ id: o.id, name: o.name });
        });

        this.state.allPartners = Object.values(pmap).sort((a, b) => a.name.localeCompare(b.name));
        this.state.allOrders   = {};
        Object.values(pmap).forEach(p => { this.state.allOrders[p.id] = p.orders; });
    }

    // ─── Grupos ───────────────────────────────────────────────────────────────

    toggleGroup(productId) {
        this.state.collapsed[productId] = !this.state.collapsed[productId];
    }

    isCollapsed(productId) { return !!this.state.collapsed[productId]; }

    // ─── Edición inline ───────────────────────────────────────────────────────

    startEdit(lineId, field) { this.state.editingCell = { lineId, field }; }

    isEditing(lineId, field) {
        const e = this.state.editingCell;
        return e && e.lineId === lineId && e.field === field;
    }

    async onPartnerChange(line, ev) {
        const partnerId = parseInt(ev.target.value) || false;

        line.partner_id = partnerId ? [partnerId, this._partnerName(partnerId)] : false;
        line.order_id   = false;
        line.allocation_status = "available";
        this._recalcGroup(line);

        try {
            await this.orm.write("stock.transit.line", [line.id], {
                partner_id: partnerId || false,
                order_id: false,
            });
        } catch (e) {
            this.notification.add("Error guardando cliente: " + e.message, { type: "danger" });
            return;
        }

        if (partnerId) {
            this.state.editingCell = { lineId: line.id, field: 'order_id' };
        } else {
            this.state.editingCell = null;
        }
    }

    async onOrderChange(line, ev) {
        const orderId = parseInt(ev.target.value) || false;
        this.state.editingCell = null;

        try {
            await this.orm.write("stock.transit.line", [line.id], { order_id: orderId || false });
        } catch (e) {
            this.notification.add("Error guardando orden: " + e.message, { type: "danger" });
            return;
        }

        line.order_id = orderId ? [orderId, this._orderName(line, orderId)] : false;
        const hasAssignment = line.partner_id && orderId;
        line.allocation_status = hasAssignment ? "reserved" : "available";
        this._recalcGroup(line);
    }

    _recalcGroup(line) {
        const g = this.state.groups.find(g => g.product_id === (line.product_id ? line.product_id[0] : 0));
        if (!g) return;
        g.reserved_m2 = g.lines.filter(l => l.allocation_status === "reserved").reduce((s, l) => s + (l.product_uom_qty || 0), 0);
    }

    _partnerName(id) {
        const p = this.state.allPartners.find(p => p.id === id);
        return p ? p.name : String(id);
    }

    _orderName(line, orderId) {
        const pid    = line.partner_id ? line.partner_id[0] : 0;
        const orders = this.state.allOrders[pid] || [];
        const o      = orders.find(o => o.id === orderId);
        return o ? o.name : String(orderId);
    }

    getOrdersForLine(line) {
        if (!line.partner_id) return [];
        return this.state.allOrders[line.partner_id[0]] || [];
    }

    // ─── Selección ────────────────────────────────────────────────────────────

    toggleLineSelect(lineId, ev) {
        if (ev.target.checked) this.state.selectedLines.add(lineId);
        else                   this.state.selectedLines.delete(lineId);
    }

    toggleGroupSelect(group, ev) {
        group.lines.forEach(l => {
            if (ev.target.checked) this.state.selectedLines.add(l.id);
            else                   this.state.selectedLines.delete(l.id);
        });
    }

    isGroupAllSelected(group) {
        return group.lines.length > 0 && group.lines.every(l => this.state.selectedLines.has(l.id));
    }

    isLineSelected(id) { return this.state.selectedLines.has(id); }
    get selectedCount() { return this.state.selectedLines.size; }

    // ─── M² total de los seleccionados ────────────────────────────────────────
    get selectedM2() {
        let total = 0;
        for (const g of this.state.groups) {
            for (const l of g.lines) {
                if (this.state.selectedLines.has(l.id)) {
                    total += l.product_uom_qty || 0;
                }
            }
        }
        return total;
    }

    // ─── Resumen de lotes seleccionados para el popup ─────────────────────────
    get selectedSummary() {
        const rows = [];
        for (const g of this.state.groups) {
            const gLines = g.lines.filter(l => this.state.selectedLines.has(l.id));
            if (!gLines.length) continue;
            rows.push({
                product: g.product_name,
                count: gLines.length,
                m2: gLines.reduce((s, l) => s + (l.product_uom_qty || 0), 0),
            });
        }
        return rows;
    }

    // ─── Popup de asignación masiva ───────────────────────────────────────────

    openAssignPopup() {
        if (!this.state.selectedLines.size) {
            this.notification.add("Seleccione al menos un lote", { type: "warning" });
            return;
        }
        this.state.popupPartner = null;
        this.state.popupOrder   = null;
        this.state.showAssignPopup = true;
    }

    closeAssignPopup() {
        this.state.showAssignPopup = false;
    }

    onPopupPartnerChange(ev) {
        this.state.popupPartner = parseInt(ev.target.value) || null;
        this.state.popupOrder   = null;
    }

    onPopupOrderChange(ev) {
        this.state.popupOrder = parseInt(ev.target.value) || null;
    }

    get popupOrders() {
        if (!this.state.popupPartner) return [];
        return this.state.allOrders[this.state.popupPartner] || [];
    }

    async confirmAssign() {
        if (!this.state.popupPartner) {
            this.notification.add("Seleccione un cliente", { type: "warning" });
            return;
        }
        const ids  = [...this.state.selectedLines];
        const vals = {
            partner_id: this.state.popupPartner,
            order_id:   this.state.popupOrder || false,
        };
        try {
            await this.orm.write("stock.transit.line", ids, vals);
            this.notification.add(`${ids.length} lotes asignados`, { type: "success" });
            this.state.selectedLines = new Set();
            this.state.showAssignPopup = false;
            await this.refresh();
        } catch (e) {
            this.notification.add("Error: " + e.message, { type: "danger" });
        }
    }

    clearSelection() {
        this.state.selectedLines = new Set();
        this.state.showAssignPopup = false;
    }

    // ─── Propagación ─────────────────────────────────────────────────────────

    async propagateDown(group, fromIndex, ev) {
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }
        const srcLine = group.lines[fromIndex];
        if (!srcLine.partner_id) return;
        const targets = group.lines.slice(fromIndex + 1);
        if (!targets.length) return;

        const ids  = targets.map(l => l.id);
        const vals = {
            partner_id: srcLine.partner_id[0],
            order_id:   srcLine.order_id ? srcLine.order_id[0] : false,
        };
        try {
            await this.orm.write("stock.transit.line", ids, vals);
            this.notification.add(`Propagado a ${ids.length} lotes`, { type: "success", sticky: false });
            await this.refresh();
        } catch (e) {
            this.notification.add("Error al propagar: " + e.message, { type: "danger" });
        }
    }

    // ─── Totales / Formato ────────────────────────────────────────────────────

    get grandTotal()    { return this.state.groups.reduce((s, g) => s + g.total_m2, 0); }
    get grandReserved() { return this.state.groups.reduce((s, g) => s + g.reserved_m2, 0); }
    get grandPercent()  {
        const t = this.grandTotal;
        return t > 0 ? ((this.grandReserved / t) * 100).toFixed(0) : "0";
    }

    fmtNum(v) {
        if (!v && v !== 0) return "—";
        return Number(v).toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    pct(g) {
        if (!g.total_m2) return 0;
        return Math.round((g.reserved_m2 / g.total_m2) * 100);
    }

    statusLabel(s) { return s === "reserved" ? "Reservado" : "Disponible"; }
    statusCls(s)   { return s === "reserved" ? "tvl-badge--reserved" : "tvl-badge--available"; }
}

// Registrar como field widget para one2many
registry.category("fields").add("transit_voyage_lines", {
    component: TransitVoyageLinesWidget,
    displayName: "Transit Voyage Lines (Agrupado)",
    supportedTypes: ["one2many"],
});
