/** @odoo-module **/
/**
 * TransitVoyageLines
 * Componente OWL que reemplaza la lista plana de stock.transit.line
 * con una vista agrupada por producto, mostrando cada lote como sub-fila.
 * 
 * Features:
 * - Agrupación automática por producto
 * - Sub-filas por lote con bloque, atado, m², estado
 * - Edición inline de partner_id y order_id
 * - Propagación rápida de cliente/orden hacia abajo
 * - Totales por producto y grand total
 * - Asignación masiva a todo un producto de una vez
 */
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class TransitVoyageLinesWidget extends Component {
    static template = "stock_transit_allocation.TransitVoyageLines";

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");
        this.dialog       = useService("dialog");

        this.state = useState({
            voyageId:          false,
            groups:            [],       // [{product, lines[], expanded, totals}]
            collapsed:         {},       // { productId: bool }
            loading:           true,
            saving:            false,
            allPartners:       [],
            allOrders:         {},       // { partnerId: [orders] }
            editingCell:       null,     // { lineId, field }
            selectedLines:     new Set(),
            bulkPartner:       null,
            bulkOrder:         null,
            showBulkMenu:      false,
        });

        onWillStart(async () => {
            const voyageId = this._getVoyageIdFromUrl();
            if (voyageId) {
                this.state.voyageId = voyageId;
                await this.loadData(voyageId);
            }
        });
    }

    // ─── Helpers para obtener el ID del viaje ────────────────────────────────

    _getVoyageIdFromUrl() {
        // Intentar obtener el ID desde la URL del navegador
        const match = window.location.hash.match(/id=(\d+)/);
        if (match) return parseInt(match[1]);
        // Alternativa: desde el contexto del componente
        const ctx = this.props.context || {};
        return ctx.active_id || ctx.voyage_id || false;
    }

    // ─── Carga de datos ──────────────────────────────────────────────────────

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
                    "allocation_status", "notes",
                    "x_grosor", "x_alto", "x_ancho",
                    // Los campos x_bloque y x_atado vienen de lot_id
                ],
                { order: "product_id asc, lot_id asc" }
            );

            // Cargar bloque y atado desde stock.lot para cada línea con lot_id
            const lotIds = lines.filter(l => l.lot_id).map(l => l.lot_id[0]);
            let lotData = {};
            if (lotIds.length > 0) {
                const lots = await this.orm.searchRead(
                    "stock.lot",
                    [["id", "in", lotIds]],
                    ["id", "x_bloque", "x_atado", "name"],
                );
                lots.forEach(lot => { lotData[lot.id] = lot; });
            }

            // Enriquecer las líneas con bloque/atado
            lines.forEach(line => {
                if (line.lot_id && lotData[line.lot_id[0]]) {
                    const lot = lotData[line.lot_id[0]];
                    line.x_bloque = lot.x_bloque || "—";
                    line.x_atado  = lot.x_atado  || "—";
                } else {
                    line.x_bloque = "—";
                    line.x_atado  = "—";
                }
            });

            this._buildGroups(lines);
            await this._loadPartnersAndOrders(lines);
        } catch (e) {
            console.error("[TransitVoyageLines] Error:", e);
            this.notification.add("Error al cargar líneas del viaje", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        if (this.state.voyageId) {
            await this.loadData(this.state.voyageId);
        }
    }

    _buildGroups(lines) {
        const map = new Map();
        for (const line of lines) {
            const pid = line.product_id ? line.product_id[0] : 0;
            const pname = line.product_id ? line.product_id[1] : "Sin producto";
            if (!map.has(pid)) {
                map.set(pid, {
                    product_id:   pid,
                    product_name: pname,
                    lines:        [],
                    total_m2:     0,
                    reserved_m2:  0,
                    lot_count:    0,
                });
            }
            const g = map.get(pid);
            g.lines.push(line);
            g.total_m2 += line.product_uom_qty || 0;
            if (line.allocation_status === "reserved") g.reserved_m2 += line.product_uom_qty || 0;
            if (line.lot_id) g.lot_count++;
        }
        this.state.groups = [...map.values()];
    }

    async _loadPartnersAndOrders(lines) {
        // Cargar socios elegibles desde las líneas existentes
        const partnerIds = new Set();
        const orderIds   = new Set();
        lines.forEach(l => {
            if (l.partner_id) partnerIds.add(l.partner_id[0]);
            if (l.order_id)   orderIds.add(l.order_id[0]);
        });

        // Cargar todos los socios que tienen SO con estos productos
        const productIds = [...new Set(lines.filter(l => l.product_id).map(l => l.product_id[0]))];
        if (productIds.length) {
            const saleLines = await this.orm.searchRead(
                "sale.order.line",
                [
                    ["product_id", "in", productIds],
                    ["order_id.state", "in", ["sale", "done"]],
                    ["display_type", "=", false],
                ],
                ["order_id"],
                { limit: 500 }
            );
            const soIds = [...new Set(saleLines.map(sl => sl.order_id[0]))];
            if (soIds.length) {
                const orders = await this.orm.searchRead(
                    "sale.order",
                    [["id", "in", soIds]],
                    ["id", "name", "partner_id"],
                );
                // Agrupar por partner
                const partnerMap = {};
                orders.forEach(o => {
                    const pid = o.partner_id[0];
                    if (!partnerMap[pid]) partnerMap[pid] = { id: pid, name: o.partner_id[1], orders: [] };
                    partnerMap[pid].orders.push({ id: o.id, name: o.name });
                });
                this.state.allPartners = Object.values(partnerMap);
                this.state.allOrders   = {};
                Object.values(partnerMap).forEach(p => {
                    this.state.allOrders[p.id] = p.orders;
                });
            }
        }
    }

    // ─── Grupos y expansión ──────────────────────────────────────────────────

    toggleGroup(productId) {
        this.state.collapsed[productId] = !this.state.collapsed[productId];
    }

    isCollapsed(productId) {
        return !!this.state.collapsed[productId];
    }

    // ─── Edición inline ──────────────────────────────────────────────────────

    startEdit(lineId, field) {
        this.state.editingCell = { lineId, field };
    }

    isEditing(lineId, field) {
        const e = this.state.editingCell;
        return e && e.lineId === lineId && e.field === field;
    }

    async onPartnerChange(line, partnerId) {
        const partnerIdNum = parseInt(partnerId) || false;
        line.partner_id = partnerIdNum ? [partnerIdNum, this._partnerName(partnerIdNum)] : false;
        line.order_id   = false;
        this.state.editingCell = null;

        // Guardar en backend
        await this._saveLine(line.id, {
            partner_id: partnerIdNum || false,
            order_id:   false,
        });
    }

    async onOrderChange(line, orderId) {
        const orderIdNum = parseInt(orderId) || false;
        line.order_id = orderIdNum ? [orderIdNum, this._orderName(line, orderIdNum)] : false;
        line.allocation_status = (line.partner_id && orderIdNum) ? "reserved" : "available";
        this.state.editingCell = null;

        await this._saveLine(line.id, {
            order_id: orderIdNum || false,
        });
    }

    _partnerName(id) {
        const p = this.state.allPartners.find(p => p.id === id);
        return p ? p.name : String(id);
    }

    _orderName(line, orderId) {
        const pid = line.partner_id ? line.partner_id[0] : 0;
        const orders = this.state.allOrders[pid] || [];
        const o = orders.find(o => o.id === orderId);
        return o ? o.name : String(orderId);
    }

    getOrdersForLine(line) {
        if (!line.partner_id) return [];
        const pid = line.partner_id[0];
        return this.state.allOrders[pid] || [];
    }

    async _saveLine(lineId, vals) {
        try {
            await this.orm.write("stock.transit.line", [lineId], vals);
            this.notification.add("Guardado", { type: "success", sticky: false });
        } catch (e) {
            this.notification.add("Error al guardar: " + e.message, { type: "danger" });
        }
    }

    // ─── Selección y asignación masiva ───────────────────────────────────────

    toggleLineSelect(lineId, checked) {
        if (checked) this.state.selectedLines.add(lineId);
        else         this.state.selectedLines.delete(lineId);
    }

    toggleGroupSelect(group, checked) {
        group.lines.forEach(l => {
            if (checked) this.state.selectedLines.add(l.id);
            else         this.state.selectedLines.delete(l.id);
        });
    }

    get selectedCount() { return this.state.selectedLines.size; }

    async assignBulk() {
        if (!this.state.selectedLines.size) return;
        if (!this.state.bulkPartner) {
            this.notification.add("Seleccione un cliente para asignación masiva", { type: "warning" });
            return;
        }
        const ids = [...this.state.selectedLines];
        const vals = {
            partner_id: this.state.bulkPartner,
            order_id:   this.state.bulkOrder || false,
        };
        try {
            await this.orm.write("stock.transit.line", ids, vals);
            this.notification.add(`${ids.length} líneas asignadas correctamente`, { type: "success" });
            this.state.selectedLines = new Set();
            this.state.bulkPartner   = null;
            this.state.bulkOrder     = null;
            this.state.showBulkMenu  = false;
            await this.refresh();
        } catch (e) {
            this.notification.add("Error en asignación masiva: " + e.message, { type: "danger" });
        }
    }

    onBulkPartnerChange(ev) {
        this.state.bulkPartner = parseInt(ev.target.value) || null;
        this.state.bulkOrder   = null;
    }

    onBulkOrderChange(ev) {
        this.state.bulkOrder = parseInt(ev.target.value) || null;
    }

    get bulkOrders() {
        if (!this.state.bulkPartner) return [];
        return this.state.allOrders[this.state.bulkPartner] || [];
    }

    // ─── Propagación (similar al widget existente) ────────────────────────────

    async propagateDown(group, fromIndex) {
        const lines   = group.lines;
        const srcLine = lines[fromIndex];
        if (!srcLine.partner_id) return;

        const targets = lines.slice(fromIndex + 1);
        if (!targets.length) return;

        const ids = targets.map(l => l.id);
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

    // ─── Helpers de formato ──────────────────────────────────────────────────

    get grandTotal() {
        return this.state.groups.reduce((s, g) => s + g.total_m2, 0);
    }

    get grandReserved() {
        return this.state.groups.reduce((s, g) => s + g.reserved_m2, 0);
    }

    get grandPercent() {
        const t = this.grandTotal;
        return t > 0 ? ((this.grandReserved / t) * 100).toFixed(0) : "0";
    }

    fmtNum(v) {
        if (!v && v !== 0) return "—";
        return Number(v).toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    pct(g) {
        if (!g.total_m2) return 0;
        return Math.round((g.reserved_m2 / g.total_m2) * 100);
    }

    strOf(val) {
        if (!val) return "—";
        if (Array.isArray(val)) return val[1] || "—";
        return String(val);
    }

    statusLabel(status) {
        return status === "reserved" ? "Reservado" : "Disponible";
    }

    statusCls(status) {
        return status === "reserved" ? "tvl-badge--reserved" : "tvl-badge--available";
    }

    isLineSelected(id) {
        return this.state.selectedLines.has(id);
    }
}

TransitVoyageLinesWidget.template = "stock_transit_allocation.TransitVoyageLines";
registry.category("actions").add("action_transit_voyage_lines", TransitVoyageLinesWidget);