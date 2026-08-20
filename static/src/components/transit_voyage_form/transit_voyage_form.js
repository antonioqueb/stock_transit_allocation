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
import { Component, useState, onWillStart, onWillUpdateProps, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";
import { SaleOrderDialog } from "@inventory_visual_enhanced/components/dialogs/sale_order/sale_order_dialog";
import { HoldInfoDialog } from "@inventory_visual_enhanced/components/dialogs/hold_info/hold_info_dialog";

/**
 * Visor de fotos del lote: la imagen llega en base64 por RPC (mismo
 * mecanismo que el inventario visual), nunca vía /web/image.
 */
class LotPhotoDialog extends Component {
    static template = xml`
        <Dialog size="'lg'" title="props.title" footer="false">
            <div class="text-center" style="max-height: 70vh; overflow: auto;">
                <t t-foreach="props.photos" t-as="ph" t-key="ph.id">
                    <figure style="margin-bottom: 14px;">
                        <img t-attf-src="data:image/png;base64,{{ ph.image }}"
                             style="max-width: 100%; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,.15);"/>
                        <figcaption style="font-size: 11px; color: #6c757d; margin-top: 4px;">
                            <t t-esc="ph.name"/>
                            <t t-if="ph.notas"> — <t t-esc="ph.notas"/></t>
                        </figcaption>
                    </figure>
                </t>
            </div>
        </Dialog>`;
    static components = { Dialog };
    static props = { title: String, photos: Array, close: Function };
}

class TransitVoyageLinesWidget extends Component {
    static template = "stock_transit_allocation.TransitVoyageLines";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        console.warn("[TVOYAGE_JS_RUNTIME_13_7_4] loaded", { url: window.location.href });
        this.orm          = useService("orm");
        this.notification = useService("notification");
        this.dialog       = useService("dialog");
        this.action       = useService("action");

        // Helper usado dentro de expresiones de template (t-set con arrow):
        // bind explícito para no depender del `this` del scope compilado.
        this.lineMatchesFilter = this.lineMatchesFilter.bind(this);

        this.state = useState({
            groups:        [],
            // Torre de Control
            productFlow:   {},     // {product_id: {purchased, shipped, received, ...}}
            coverage:      {},     // {orderId: {productId: {requested, covered, pending}}}
            activeFilter:  "all",  // all | unassigned | client_no_order | over
            searchText:    "",
            drawer:        null,   // {line, group, order, qty} asignación pendiente
            collapsed:     {},
            loading:       true,
            allPartners:   [],
            allOrders:     {},
            ordersByProduct: {},   // { product_id: [{id, name, partner_id, partner_name}] }
            editingCell:   null,   // { lineId, field }
            selectedLines: new Set(),
            // Parcialidad elegida por línea (solo formatos/piezas). { [lineId]: qty }
            partialQty:    {},
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
            // Autocura: normaliza lotes duplicados (gemelas infladas) antes
            // de pintar. Solo actúa cuando hay duplicados; si no, es no-op.
            try {
                await this.orm.call("stock.transit.line", "tc_normalize_voyage_twins", [voyageId]);
            } catch (e) {
                console.warn("[TransitVoyageLines] normalize twins:", e);
            }

            // Flujo comprado/embarcado/recibido POR PRODUCTO (una sola llamada).
            try {
                this.state.productFlow = await this.orm.call(
                    "stock.transit.voyage", "tc_get_product_flow_map", [voyageId]) || {};
            } catch (e) {
                console.warn("[TransitVoyageLines] product flow:", e);
                this.state.productFlow = {};
            }

            const lines = await this.orm.searchRead(
                "stock.transit.line",
                [["voyage_id", "=", voyageId]],
                [
                    "id", "product_id", "lot_id", "container_number",
                    "product_uom_qty", "partner_id", "order_id",
                    "allocation_status", "x_grosor", "x_alto", "x_ancho", "x_tipo",
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
                    ["id", "x_bloque", "x_atado", "x_numero_placa"],
                );
                lots.forEach(l => { lotData[l.id] = l; });
            }
            // Estado actual + foto por lote (mismo criterio que inventario visual)
            let traceMap = {};
            if (lotIds.length) {
                try {
                    traceMap = await this.orm.call("stock.lot", "som_trace_state_map", [lotIds]);
                } catch (e) {
                    console.warn("[TransitVoyageLines] trace map:", e);
                }
            }

            // Reservas para TALLER (módulo puente): pintar a quién y qué
            // proceso está comprometida la placa aunque no tenga pedido
            // comercial directo.
            let workshopMap = {};
            try {
                workshopMap = await this.orm.call(
                    "stock.transit.line", "tc_workshop_info_map",
                    [lines.map(l => l.id)]) || {};
            } catch (e) {
                console.warn("[TransitVoyageLines] workshop map:", e);
            }

            lines.forEach(line => {
                const lot = line.lot_id && lotData[line.lot_id[0]];
                line.x_bloque = lot?.x_bloque || "";
                line.x_atado  = lot?.x_atado  || "";
                line.x_numero_placa = lot?.x_numero_placa || "";
                line.trace    = (line.lot_id && traceMap[line.lot_id[0]]) || null;
                line.workshop = workshopMap[line.id] || null;
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

    // ─── Drawer de asignación (preventivo) ───────────────────────────────────

    openAssignDrawer(line, group, order) {
        const qty = this.isFractionable(line)
            ? this.partialQtyValue(line)
            : (line.product_uom_qty || 0);
        this.state.drawer = {
            lineId: line.id,
            line,
            group,
            order,
            qty,
        };
    }

    closeAssignDrawer() {
        this.state.drawer = null;
    }

    onDrawerQtyChange(ev) {
        const d = this.state.drawer;
        if (!d) return;
        const max = d.line.product_uom_qty || 0;
        let val = parseFloat(ev.target.value);
        if (!isFinite(val) || val < 0) val = 0;
        if (val > max) { val = max; ev.target.value = val; }
        this.state.drawer = { ...d, qty: val };
    }

    /** Proyección en tiempo real: cubierto + asignación vs solicitado. */
    get drawerProjection() {
        const d = this.state.drawer;
        if (!d) return null;
        const cov = (this.state.coverage[d.order.id] || {})[d.line.product_id[0]] || null;
        if (!cov || !cov.requested) {
            return { noProduct: !cov, cov: null };
        }
        const projected = cov.covered + (d.qty || 0);
        const remaining = cov.requested - projected;
        let verdict, level;
        if (cov.pending <= 0.005) {
            verdict = "Este pedido ya está completamente cubierto.";
            level = "warn";
        } else if (remaining > 0.005) {
            verdict = `Esta asignación cubre parcialmente el pedido. Quedarán ${this.fmtNum(remaining)} pendientes.`;
            level = "ok";
        } else if (remaining >= -0.005) {
            verdict = "Esta asignación completará el pedido.";
            level = "full";
        } else {
            verdict = `Esta asignación excede el requerimiento del pedido por ${this.fmtNum(-remaining)}.`;
            level = "over";
        }
        return {
            cov,
            projected,
            remaining: Math.max(remaining, 0),
            excess: Math.max(-remaining, 0),
            pct: Math.min(100, (projected / cov.requested) * 100),
            verdict,
            level,
        };
    }

    async confirmAssignDrawer() {
        const d = this.state.drawer;
        if (!d) return;

        // Parcialidad del drawer → mismo almacén que el input de la tabla.
        if (this.isFractionable(d.line)) {
            this.state.partialQty[d.lineId] = d.qty;
        }

        const ok = await this._assignWithOverCheck([d.lineId], false, d.order.id);
        this.closeAssignDrawer();
        if (!ok) return;

        this.notification.add(
            `Asignado a ${d.order.name}`, { type: "success", sticky: false });
        // Refresca líneas + cobertura + flujo (conserva grupos abiertos y filtros).
        await this.refresh();
        await this._reloadVoyageBanner();
    }

    // ─── Torre de Control: incidencias, filtros y cobertura ─────────────────

    /** Incidencias accionables (cada una aplica un filtro sobre la tabla). */
    get issues() {
        const out = [];
        let clientNoOrder = 0;
        let unassigned = 0;
        for (const g of this.state.groups) {
            for (const l of g.lines) {
                if (l.partner_id && !l.order_id) clientNoOrder++;
                if (!l.partner_id && !l.order_id) unassigned++;
            }
        }
        if (clientNoOrder) {
            out.push({
                key: "client_no_order", level: "warn",
                text: `${clientNoOrder} material(es) tienen cliente pero sin pedido asignado`,
                action: "Ver materiales",
            });
        }
        // Retiradas por pedido (2026-08-12): las incidencias "N material(es)
        // sin asignar", "Exceso embarcado vs OC" y "Pendiente por embarcar de
        // la OC" ya no se muestran. El acceso "Ver materiales" sigue vivo en
        // el chip "Sin asignar · N" de la toolbar (mismo filtro).
        void unassigned;
        let noLink = 0;
        for (const f of Object.values(this.state.productFlow || {})) {
            if (!f.has_po_link) noLink++;
        }
        if (noLink) {
            out.push({ key: "none", level: "info", text: `${noLink} producto(s) sin vínculo con OC` });
        }
        return out;
    }

    filterCount(key) {
        let n = 0;
        for (const g of this.state.groups) {
            for (const l of g.lines) {
                if (key === "unassigned" && !l.order_id) n++;
                if (key === "client_no_order" && l.partner_id && !l.order_id) n++;
            }
        }
        return n;
    }

    setFilter(key) {
        this.state.activeFilter = this.state.activeFilter === key ? "all" : key;
    }

    lineMatchesFilter(l) {
        const q = (this.state.searchText || "").trim().toLowerCase();
        if (q) {
            const hay = [
                l.lot_id ? l.lot_id[1] : "",
                l.x_numero_placa || "",
                l.container_number || "",
                l.x_bloque || "",
                l.partner_id ? l.partner_id[1] : "",
                l.order_id ? l.order_id[1] : "",
            ].join(" ").toLowerCase();
            if (!hay.includes(q)) return false;
        }
        switch (this.state.activeFilter) {
            case "unassigned":       return !l.order_id;
            case "client_no_order":  return !!l.partner_id && !l.order_id;
            default:                 return true;
        }
    }

    onSearchInput(ev) {
        this.state.searchText = ev.target.value;
    }

    /** Cobertura del pedido de la línea (desde el mapa batch). */
    lineCoverage(l) {
        if (!l.order_id || !l.product_id) return null;
        const c = (this.state.coverage[l.order_id[0]] || {})[l.product_id[0]];
        if (!c || !c.requested) return null;
        const pct = Math.min(100, (c.covered / c.requested) * 100);
        return { ...c, pct };
    }

    covLabel(c) {
        if (!c) return "";
        if (c.pending <= 0.005 && c.covered <= c.requested + 0.005) return "Completo";
        if (c.covered > c.requested + 0.005) return `Excede por ${this.fmtNum(c.covered - c.requested)}`;
        return `Faltan ${this.fmtNum(c.pending)}`;
    }

    covClass(c) {
        if (!c) return "";
        if (c.covered > c.requested + 0.005) return "tvl-cov--over";
        if (c.pending <= 0.005) return "tvl-cov--full";
        return "tvl-cov--partial";
    }

    /** Flujo compra↔embarque del grupo (cabecera de producto). */
    groupFlow(group) {
        return (this.state.productFlow || {})[group.product_id] || null;
    }

    expandAll()   { this.state.collapsed = {}; }
    collapseAll() {
        const c = {};
        this.state.groups.forEach(g => { c[g.product_id] = true; });
        this.state.collapsed = c;
    }

    /** ¿El grupo tiene dimensiones físicas? (si no, se ocultan esas columnas) */
    groupHasDims(group) {
        return group.lines.some(l => l.x_grosor || l.x_alto || l.x_ancho || l.x_atado);
    }

    // ─── Trazabilidad (columnas tipo inventario visual) ─────────────────────

    traceChipCls(state) {
        return {
            libre: "tvl-trace--free",
            hold: "tvl-trace--hold",
            vendido: "tvl-trace--sold",
            entregado: "tvl-trace--delivered",
            en_transito: "tvl-trace--transit",
        }[state] || "tvl-trace--other";
    }

    // Popup de órdenes de venta: EL MISMO del inventario visual (réplica 1:1).
    async onLotSaleOrderClick(line) {
        const ids = line.trace?.sale_order_ids || [];
        if (!ids.length) {
            this.notification.add("No hay órdenes de venta asociadas", { type: "info" });
            return;
        }
        try {
            const soInfo = await this.orm.call(
                "stock.quant",
                "get_sale_order_info",
                [],
                {
                    sale_order_ids: ids,
                    quant_id: line.trace?.quant_id || false,
                }
            );
            if (soInfo.error) {
                this.notification.add(soInfo.error, { type: "warning" });
                return;
            }
            this.dialog.add(SaleOrderDialog, {
                soInfo,
                title: `Órdenes de Venta (${soInfo.count})`,
                size: "lg",
            });
        } catch (e) {
            console.error("[TransitVoyageLines] sale order popup:", e);
            this.notification.add("Error al cargar información de órdenes de venta", { type: "danger" });
        }
    }

    // Popup de apartado: EL MISMO del inventario visual (réplica 1:1).
    onLotHoldClick(line, group) {
        const holdInfo = line.trace?.hold_info;
        if (!holdInfo) {
            this.notification.add("Lote con apartado activo", { type: "info" });
            return;
        }
        const detailData = {
            lot_name: line.lot_id ? line.lot_id[1] : "",
            product_name: group ? group.product_name : "",
        };
        this.dialog.add(HoldInfoDialog, {
            holdInfo,
            detailData,
            title: `Apartado Activo - ${detailData.lot_name}`,
            size: "lg",
        });
    }

    openLotHistory(line) {
        if (!line.lot_id) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Historial — ${line.lot_id[1]}`,
            res_model: "stock.move.line",
            views: [[false, "list"], [false, "form"]],
            domain: [["lot_id", "=", line.lot_id[0]]],
            target: "current",
            context: { create: false, edit: false },
        });
    }

    async openLotPhoto(line) {
        if (!line.lot_id || !line.trace?.photo_count) {
            return; // sin foto: no hace nada
        }
        try {
            const data = await this.orm.call("stock.lot", "som_get_lot_photos", [line.lot_id[0]]);
            const photos = (data.photos || []).filter((p) => p.image);
            if (!photos.length) {
                this.notification.add("El lote no tiene fotografías con contenido.", { type: "warning" });
                return;
            }
            this.dialog.add(LotPhotoDialog, {
                title: `Fotografías — ${data.lot_name}`,
                photos,
            });
        } catch (e) {
            console.error("[TransitVoyageLines] fotos:", e);
            this.notification.add("No se pudieron cargar las fotografías.", { type: "danger" });
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
            [
                "order_id", "product_id",
                // Cobertura del pedido (campos calculados ya existentes):
                "product_uom_qty", "tc_qty_assigned_lots", "tc_qty_pending_allocation",
            ],
            { limit: 500 }
        );

        // Mapa de cobertura por (pedido, producto) — sin RPC por fila.
        const cov = {};
        saleLines.forEach(sl => {
            const oid = sl.order_id[0];
            const pid = sl.product_id[0];
            if (!cov[oid]) cov[oid] = {};
            const entry = cov[oid][pid] || { requested: 0, covered: 0, pending: 0 };
            entry.requested += sl.product_uom_qty || 0;
            entry.covered   += sl.tc_qty_assigned_lots || 0;
            entry.pending   += sl.tc_qty_pending_allocation || 0;
            cov[oid][pid] = entry;
        });
        this.state.coverage = cov;
        const soIds = [...new Set(saleLines.map(sl => sl.order_id[0]))];
        if (!soIds.length) return;

        const orders = await this.orm.searchRead("sale.order", [["id", "in", soIds]], ["id", "name", "partner_id"]);
        const orderById = {};
        orders.forEach(o => { orderById[o.id] = o; });

        const pmap = {};
        orders.forEach(o => {
            const pid = o.partner_id[0];
            if (!pmap[pid]) pmap[pid] = { id: pid, name: o.partner_id[1], orders: [] };
            pmap[pid].orders.push({ id: o.id, name: o.name });
        });

        this.state.allPartners = Object.values(pmap).sort((a, b) => a.name.localeCompare(b.name));
        this.state.allOrders   = {};
        Object.values(pmap).forEach(p => { this.state.allOrders[p.id] = p.orders; });

        // Órdenes elegibles POR PRODUCTO (no por cliente de la línea): permite
        // asignar una línea disponible eligiendo la orden directamente y derivando
        // el cliente desde la orden. Cada entrada lleva el cliente para mostrarlo.
        const obp = {};
        saleLines.forEach(sl => {
            const prod = sl.product_id[0];
            const ord = orderById[sl.order_id[0]];
            if (!ord) return;
            if (!obp[prod]) obp[prod] = [];
            if (!obp[prod].some(x => x.id === ord.id)) {
                const c = (cov[ord.id] || {})[prod] || {};
                obp[prod].push({
                    id: ord.id,
                    name: ord.name,
                    partner_id: ord.partner_id[0],
                    partner_name: ord.partner_id[1],
                    // Preventivo: el vendedor ve solicitado/pendiente ANTES de asignar.
                    requested: c.requested || 0,
                    pending: c.pending || 0,
                });
            }
        });
        Object.values(obp).forEach(list => list.sort((a, b) => a.name.localeCompare(b.name)));
        this.state.ordersByProduct = obp;
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
        console.warn("[TVOYAGE_onPartnerChange_13_7_4]", { lineId: line.id, partnerId });

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

        // POPUP INVASIVO: solo si se está asignando OTRA VEZ el mismo cliente
        // mientras ya hay otra(s) línea(s) con ese cliente SIN pedido. No molesta
        // en cada asignación; solo cuando se repite un cliente sin haber
        // completado el anterior con su orden.
        if (partnerId) {
        }

        // El aviso de "cliente sin pedido" vive SOLO en la banda amarilla del
        // formulario (pending_order_count): sin popups invasivos.
        // Recarga el registro del viaje para que el banner "Falta asignar
        // pedido" (pending_order_count) se actualice EN VIVO, sin refrescar.
        await this._reloadVoyageBanner();
    }

    /**
     * Recarga el registro del viaje (sin recargar las líneas del widget) para
     * que los campos calculados del formulario —en particular el aviso
     * "cliente sin pedido"— se refresquen en vivo tras asignar/limpiar.
     */
    async _reloadVoyageBanner() {
        try {
            await this.props.record.load();
        } catch (e) {
            // No es crítico para la operación; el banner se verá al refrescar.
        }
    }

    async onOrderChange(line, ev) {
        const orderId = parseInt(ev.target.value) || false;
        this.state.editingCell = null;
        console.warn("[TVOYAGE_onOrderChange_13_7_4]", { lineId: line.id, rawValue: ev.target.value, orderId });

        // Limpiar orden (— Sin orden —) es una acción EXPLÍCITA con ruta propia,
        // no una asignación. Así no entra al bloqueo de "sin order_id".
        if (!orderId) {
            await this._clearLineOrder(line);
            return;
        }

        // PREVENTIVO: elegir un pedido abre el drawer con la cobertura y el
        // resultado proyectado. Nada se asigna hasta Confirmar.
        const ord = this.getOrdersForProduct(line).find(o => o.id === orderId);
        if (ord) {
            const group = this.state.groups.find(
                g => g.product_id === (line.product_id ? line.product_id[0] : 0));
            this.openAssignDrawer(line, group, ord);
            return;
        }

        // Pedido fuera del catálogo del producto (no debería ocurrir): flujo directo.
        const ok = await this._assignWithOverCheck([line.id], false, orderId);
        if (!ok) return;

        line.order_id = [orderId, String(orderId)];
        if (ord) {
            line.partner_id = [ord.partner_id, ord.partner_name];
        }
        line.allocation_status = "reserved";
        this._recalcGroup(line);
        await this._reloadVoyageBanner();
    }

    async _clearLineOrder(line) {
        try {
            await this.orm.call("stock.transit.line", "tc_voyage_assign", [[line.id], false, false]);
        } catch (e) {
            this.notification.add("Error al limpiar la orden: " + (e.message || e), { type: "danger" });
            return;
        }
        line.order_id = false;
        line.allocation_status = "available";
        this._recalcGroup(line);
        await this._reloadVoyageBanner();
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

    // Órdenes elegibles para el PRODUCTO de la línea (independiente del cliente
    // de la línea). Permite asignar líneas disponibles sin cliente previo.
    getOrdersForProduct(line) {
        const pid = line.product_id ? line.product_id[0] : 0;
        return this.state.ordersByProduct[pid] || [];
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

    // ─── Parcialidad (FORMATOS / PIEZAS) ──────────────────────────────────────

    // Solo formatos/piezas admiten consumo parcial; las placas van enteras.
    isFractionable(line) {
        const t = String(line.x_tipo || "").toLowerCase();
        return t === "formato" || t === "pieza";
    }

    // Cantidad mostrada en el input de parcialidad: la elegida o el lote completo.
    partialQtyValue(line) {
        const v = this.state.partialQty[line.id];
        return v !== undefined ? v : (line.product_uom_qty || 0);
    }

    onPartialQtyChange(line, ev) {
        const max = line.product_uom_qty || 0;
        let val = parseFloat(ev.target.value);
        if (!isFinite(val) || val < 0) val = 0;
        if (val > max) { val = max; ev.target.value = val; }
        this.state.partialQty[line.id] = val;
    }

    // Mapa {transit_line_id: qty} de parcialidades para las líneas dadas. El
    // backend solo parte formatos/piezas; el resto se ignora.
    _buildPartialMap(transitLineIds) {
        const map = {};
        for (const id of transitLineIds) {
            if (this.state.partialQty[id] !== undefined) {
                map[id] = this.state.partialQty[id];
            }
        }
        return map;
    }

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
        if (!this.state.popupOrder) {
            this.notification.add("Seleccione la orden de venta a asignar", { type: "warning" });
            return;
        }
        const ids  = [...this.state.selectedLines];
        // partner=false: el backend deriva el cliente de la orden seleccionada.
        const ok = await this._assignWithOverCheck(ids, false, this.state.popupOrder);
        if (!ok) return;

        this.notification.add(`${ids.length} lotes asignados`, { type: "success" });
        this.state.selectedLines = new Set();
        this.state.showAssignPopup = false;
        await this.refresh();
    }

    clearSelection() {
        this.state.selectedLines = new Set();
        this.state.showAssignPopup = false;
    }

    // ─── Propagación ─────────────────────────────────────────────────────────

    async propagateDown(group, fromIndex, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }

        const srcLine = group.lines[fromIndex];
        const orderId = srcLine.order_id ? srcLine.order_id[0] : false;
        console.warn("[TVOYAGE_propagateDown_13_7_4]", { fromIndex, srcLineId: srcLine.id, orderId });

        if (!orderId) {
            this.notification.add("Primero seleccione una orden para propagar.", { type: "warning" });
            return;
        }

        const targets = group.lines.slice(fromIndex + 1);
        if (!targets.length) return;

        const ids = targets.map(l => l.id);

        // Vista previa: qué se va a copiar y a cuántas líneas (nunca silencioso).
        const totalQty = targets.reduce((sum, l) => sum + (l.product_uom_qty || 0), 0);
        const ordName = srcLine.order_id ? srcLine.order_id[1] : "";
        const proceed = window.confirm(
            `Copiar asignación al resto del producto:\n\n` +
            `Pedido destino: ${ordName}\n` +
            `Líneas afectadas: ${ids.length}\n` +
            `Cantidad total: ${this.fmtNum(totalQty)}\n\n` +
            `Se llenará hasta lo solicitado del pedido y se detendrá antes de generar excedente.`
        );
        if (!proceed) return;

        // Propagación INTELIGENTE: el backend llena hasta lo solicitado y se
        // detiene ANTES del excedente (parcialidad exacta en formatos/piezas).
        // Sin popup de decisión ni asignación uno-por-uno.
        try {
            const result = await this.orm.call(
                "stock.transit.line",
                "tc_voyage_propagate_smart",
                [ids, orderId],
            );

            if (result && result.success === false) {
                this.notification.add(result.message || "No se pudo propagar.", { type: "danger" });
                return;
            }

            const assigned = result?.assigned_count || 0;
            const skipped  = result?.skipped_count || 0;

            if (!assigned) {
                this.notification.add(
                    result?.message || "No se propagó ningún lote (excedería lo solicitado).",
                    { type: "warning", sticky: true },
                );
                return;
            }

            this.notification.add(
                result?.message || `Propagado a ${assigned} lotes`,
                { type: skipped ? "warning" : "success", sticky: !!skipped },
            );
            await this.refresh();
        } catch (e) {
            this.notification.add("Error al propagar: " + (e.message || e), { type: "danger" });
        }
    }

    // ─── Asignación con control de sobre-asignación ───────────────────────────

    /**
     * Asigna líneas de tránsito a (cliente, orden) vía servidor. Si el servidor
     * detecta excedente sobre lo solicitado, muestra el popup de decisión y
     * reintenta con la decisión. Devuelve true si la asignación se aplicó.
     */
    async _assignWithOverCheck(transitLineIds, partnerId, orderId, overAction = false, overReason = false) {
        console.warn("[TVOYAGE_ASSIGN_CALL_13_7_4]", {
            transitLineIds, partnerId, orderId, overAction, overReason,
            url: window.location.href,
        });

        // Asignar SIEMPRE requiere orden. Sin orden no se llama aquí (limpiar
        // orden tiene su propia ruta). Esto evita "rama sin-orden" silenciosa.
        if (!orderId) {
            console.error("[TVOYAGE_ASSIGN_BLOCKED_NO_ORDER_13_7_4]", { transitLineIds, partnerId, orderId });
            this.notification.add(
                "Asignación bloqueada: el frontend no envió order_id. Selecciona una orden.",
                { type: "danger" },
            );
            return false;
        }

        try {
            // partner SIEMPRE false: el backend deriva el cliente de la orden.
            // Endurecido a nivel orm.call para blindar contra callers viejos.
            const result = await this.orm.call(
                "stock.transit.line",
                "tc_voyage_assign",
                [transitLineIds, false, orderId, overAction, overReason, this._buildPartialMap(transitLineIds)],
            );

            if (result && result.need_over_assignment_decision) {
                const decision = await this.requestOverAssignmentDecision(result.over_assigned_qty || 0, "");
                if (!decision) return false;
                return this._assignWithOverCheck(transitLineIds, false, orderId, decision.action, decision.reason);
            }

            if (result && result.success === false) {
                this.notification.add(result.message || "No se pudo aplicar la asignación.", { type: "danger" });
                return false;
            }

            return true;
        } catch (e) {
            this.notification.add("Error al asignar: " + (e.message || e), { type: "danger" });
            return false;
        }
    }

    _escapeHtml(str) {
        return String(str ?? "").replace(/[&<>"']/g, (c) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
        ));
    }

    destroyOverAssignPopup() {
        if (this._overAssignRoot && this._overAssignRoot.parentNode) {
            this._overAssignRoot.parentNode.removeChild(this._overAssignRoot);
        }
        this._overAssignRoot = null;
    }

    /**
     * Popup de decisión cuando se asigna más de lo solicitado.
     * Resuelve a {action, reason} o a false si se cancela.
     */
    requestOverAssignmentDecision(overQty, unitLabel) {
        this.destroyOverAssignPopup();

        return new Promise((resolve) => {
            const root = document.createElement("div");
            root.className = "stone-decision-root stone-overassign-root";
            root.style.position = "fixed";
            root.style.inset = "0";
            root.style.zIndex = "99999";
            document.body.appendChild(root);
            this._overAssignRoot = root;

            root.innerHTML = `
                <div class="stone-decision-overlay" style="z-index: 100000;">
                    <div class="stone-decision-dialog" style="z-index: 100001;">
                        <div class="stone-decision-header">
                            <div>
                                <h4>Asignación mayor a lo solicitado</h4>
                                <p>
                                    Hay un excedente de
                                    <strong>${this.fmtNum(overQty)} ${this._escapeHtml(unitLabel)}</strong>.
                                    Selecciona cómo se administrará.
                                </p>
                            </div>
                            <button type="button" class="stone-decision-x" data-action="cancel">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>

                        <div class="stone-decision-options">
                            <label class="stone-decision-option active">
                                <input type="radio" name="over_action" value="free" checked="checked"/>
                                <span class="stone-decision-option-icon"><i class="fa fa-gift"></i></span>
                                <span>
                                    <strong>Entregar excedente sin cobrar</strong>
                                    <small>El cliente recibe el material extra, pero se aplica el descuento equivalente.</small>
                                </span>
                            </label>

                            <label class="stone-decision-option">
                                <input type="radio" name="over_action" value="bill"/>
                                <span class="stone-decision-option-icon"><i class="fa fa-money"></i></span>
                                <span>
                                    <strong>Cobrar excedente</strong>
                                    <small>La cantidad solicitada se ajustará para cobrar el excedente asignado.</small>
                                </span>
                            </label>
                        </div>

                        <div class="stone-decision-note">
                            <label>Nota administrativa</label>
                            <textarea id="tvl-over-note" rows="3">Sobreasignación autorizada desde el Viaje.</textarea>
                        </div>

                        <div class="stone-decision-footer">
                            <button type="button" class="stone-btn stone-btn-outline" data-action="cancel">Volver</button>
                            <button type="button" class="stone-btn stone-btn-primary-dark" data-action="accept">Continuar</button>
                        </div>
                    </div>
                </div>
            `;

            const cleanup = () => this.destroyOverAssignPopup();
            const cancel = () => { cleanup(); resolve(false); };

            root.querySelectorAll(".stone-decision-option").forEach((option) => {
                option.addEventListener("click", () => {
                    root.querySelectorAll(".stone-decision-option").forEach((el) => el.classList.remove("active"));
                    option.classList.add("active");
                    const input = option.querySelector("input[name='over_action']");
                    if (input) input.checked = true;
                });
            });

            root.querySelectorAll("[data-action='cancel']").forEach((btn) => btn.addEventListener("click", cancel));
            root.querySelector("[data-action='accept']").addEventListener("click", () => {
                const selected = root.querySelector("input[name='over_action']:checked");
                const note = root.querySelector("#tvl-over-note");
                const payload = {
                    action: selected ? selected.value : "free",
                    reason: note && note.value ? note.value : "Sobreasignación autorizada desde el Viaje.",
                };
                cleanup();
                resolve(payload);
            });
        });
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
