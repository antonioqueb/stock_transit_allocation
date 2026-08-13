/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";

const STATUS_MAP = {
    solicitud:         { label: "Solicitud",          cls: "ts-badge--solicitud" },
    production:        { label: "Producción",         cls: "ts-badge--production" },
    booking:           { label: "Booking",            cls: "ts-badge--booking" },
    puerto_origen:     { label: "Puerto Origen",      cls: "ts-badge--puerto" },
    on_sea:            { label: "En Altamar",         cls: "ts-badge--sea" },
    puerto_destino:    { label: "Pto. Destino",       cls: "ts-badge--puerto" },
    arrived_port:      { label: "Arribo Puerto",      cls: "ts-badge--arrived" },
    reception_pending: { label: "En Recepción",       cls: "ts-badge--reception" },
    delivered:         { label: "Entregado",          cls: "ts-badge--delivered" },
    cancel:            { label: "Cancelado",          cls: "ts-badge--cancel" },
};

const ETA_ALERT_MAP = {
    ok:      { label: "En Tiempo",         cls: "ts-eta-ok",      icon: "fa-circle" },
    warning: { label: "Próximo a Vencer",  cls: "ts-eta-warning", icon: "fa-exclamation-triangle" },
    danger:  { label: "Vencido",           cls: "ts-eta-danger",  icon: "fa-times-circle" },
    done:    { label: "Entregado",         cls: "ts-eta-done",    icon: "fa-check-circle" },
};

const COLUMNS = [
    { key: "purchase_id",         label: "OC Sistema",          minWidth: "120px" },
    { key: "date_order",          label: "Fecha OC",            minWidth: "95px"  },
    { key: "voyage_status",       label: "Estado",              minWidth: "135px" },
    { key: "eta_alert_level",     label: "Alerta",              minWidth: "135px" },
    { key: "salesperson_id",      label: "Vendedor",            minWidth: "170px" },
    { key: "order_id",            label: "Sales Order",         minWidth: "120px" },
    { key: "partner_id",          label: "Cliente / Proyecto",  minWidth: "280px" },
    { key: "proforma_ref",        label: "Proforma",            minWidth: "130px" },
    { key: "invoice_number",      label: "No. Invoice",         minWidth: "140px" },
    { key: "vendor_id",           label: "Proveedor",           minWidth: "220px" },
    { key: "product_id",          label: "Descripción",         minWidth: "340px" },
    { key: "product_categ_id",    label: "Categoría",           minWidth: "150px" },
    { key: "product_uom_qty",     label: "m² Embarcados",       minWidth: "115px", align: "right", isNum: true },
    { key: "container_number",    label: "Contenedor",          minWidth: "130px" },
    { key: "bl_number",           label: "BL / Folio",          minWidth: "150px" },
    { key: "etd",                 label: "ETD",                 minWidth: "95px"  },
    { key: "eta",                 label: "ETA",                 minWidth: "95px"  },
    { key: "eta_original",        label: "ETA Original",        minWidth: "110px" },
    { key: "delay_days",          label: "Días Retraso",        minWidth: "115px", align: "right", isNum: true },
    { key: "arrival_date",        label: "Llegada Real",        minWidth: "110px" },
    { key: "arrival_date_bodega", label: "En Bodega",           minWidth: "110px" },
];

export class TransitSheetView extends Component {
    static template = "stock_transit_allocation.TransitSheetView";

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");

        this.COLUMNS    = COLUMNS;
        this.STATUS_MAP = STATUS_MAP;
        this.ETA_ALERT_MAP = ETA_ALERT_MAP;

        this.state = useState({
            records:         [],
            filtered:        [],
            loading:         true,
            searchText:      "",
            statusFilter:    "",
            alertFilter:     "",
            sortKey:         "eta",
            sortDir:         "asc",
            groupBy:         "none",
            groups:          [],
            collapsedGroups: {},
            hiddenCols:      new Set(["arrival_date", "eta_original", "delay_days", "invoice_number"]),
            showColMenu:     false,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    // ─── Data ──────────────────────────────────────────────────────────────────

    async loadData() {
        this.state.loading = true;
        try {
            const fields = COLUMNS.map(c => c.key).concat(["voyage_id", "shipping_line"]);
            const records = await this.orm.searchRead(
                "stock.transit.sheet",
                [],
                fields,
                { order: "eta asc, voyage_id desc", limit: 2000 }
            );
            this.state.records = records;
            this.applyFiltersAndSort();
        } catch (e) {
            console.error("[TransitSheet] Error cargando datos:", e);
            this.notification.add("Error al cargar la sábana", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        await this.loadData();
        this.notification.add("Datos actualizados", { type: "success", sticky: false });
    }

    // ─── Filtros y Sort ────────────────────────────────────────────────────────

    applyFiltersAndSort() {
        let data = [...this.state.records];

        const q = this.state.searchText.trim().toLowerCase();
        if (q) {
            data = data.filter(r => {
                const stockToken = (!this._id(r.order_id) || !this._id(r.partner_id)) ? "Stock" : "";
                const haystack = [
                    this._str(r.purchase_id),
                    this._stockStr(r.order_id),
                    this._stockStr(r.partner_id),
                    this._str(r.salesperson_id),
                    this._str(r.product_id),
                    this._str(r.vendor_id),
                    this._str(r.product_categ_id),
                    r.bl_number || "",
                    r.container_number || "",
                    r.proforma_ref || "",
                    r.invoice_number || "",
                    stockToken,
                ].join(" ").toLowerCase();
                return haystack.includes(q);
            });
        }

        if (this.state.statusFilter) {
            // El chip unificado Destino/Arribo usa llave compuesta.
            const statusKeys = this.state.statusFilter.split(",");
            data = data.filter(r => statusKeys.includes(r.voyage_status));
        }

        if (this.state.alertFilter) {
            data = data.filter(r => r.eta_alert_level === this.state.alertFilter);
        }

        const key = this.state.sortKey;
        const dir = this.state.sortDir === "asc" ? 1 : -1;
        data.sort((a, b) => {
            const va = this._sortVal(a, key);
            const vb = this._sortVal(b, key);
            if (va < vb) return -1 * dir;
            if (va > vb) return  1 * dir;
            return 0;
        });

        this.state.filtered = data;
        this._buildGroups(data);
    }

    _sortVal(r, key) {
        const v = r[key];
        if ((key === "order_id" || key === "partner_id") && !this._id(v)) return "Stock";
        if (!v) return "";
        if (Array.isArray(v)) return v[1] || "";
        return v;
    }

    _buildGroups(data) {
        if (this.state.groupBy === "none") {
            this.state.groups = [];
            return;
        }
        const map = new Map();
        for (const r of data) {
            let grpKey, label;
            if (this.state.groupBy === "voyage") {
                grpKey = r.voyage_id ? r.voyage_id[0] : 0;
                label  = r.voyage_id ? r.voyage_id[1] : "Sin viaje";
            } else if (this.state.groupBy === "partner") {
                grpKey = r.partner_id ? r.partner_id[0] : 0;
                label  = r.partner_id ? r.partner_id[1] : "Stock";
            } else if (this.state.groupBy === "status") {
                grpKey = r.voyage_status || "none";
                label  = STATUS_MAP[grpKey] ? STATUS_MAP[grpKey].label : grpKey;
            } else if (this.state.groupBy === "category") {
                grpKey = r.product_categ_id ? r.product_categ_id[0] : 0;
                label  = r.product_categ_id ? r.product_categ_id[1] : "Sin categoría";
            }
            if (!map.has(grpKey)) map.set(grpKey, { key: grpKey, label, rows: [], total_m2: 0 });
            const g = map.get(grpKey);
            g.rows.push(r);
            g.total_m2 += r.product_uom_qty || 0;
        }
        this.state.groups = [...map.values()];
    }

    // ─── Handlers UI ──────────────────────────────────────────────────────────

    onSearch(ev) {
        this.state.searchText = ev.target.value;
        this.applyFiltersAndSort();
    }

    clearSearch() {
        this.state.searchText = "";
        this.applyFiltersAndSort();
    }

    onStatusFilter(status) {
        this.state.statusFilter = this.state.statusFilter === status ? "" : status;
        this.applyFiltersAndSort();
    }

    onAlertFilter(level) {
        this.state.alertFilter = this.state.alertFilter === level ? "" : level;
        this.applyFiltersAndSort();
    }

    onSort(key) {
        if (this.state.sortKey === key) {
            this.state.sortDir = this.state.sortDir === "asc" ? "desc" : "asc";
        } else {
            this.state.sortKey = key;
            this.state.sortDir = "asc";
        }
        this.applyFiltersAndSort();
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
        this.state.collapsedGroups = {};
        this._buildGroups(this.state.filtered);
    }

    toggleGroup(key) {
        this.state.collapsedGroups[key] = !this.state.collapsedGroups[key];
    }

    toggleCol(key) {
        const h = this.state.hiddenCols;
        if (h.has(key)) h.delete(key); else h.add(key);
    }

    toggleColMenu() {
        this.state.showColMenu = !this.state.showColMenu;
    }

    closeColMenu() {
        this.state.showColMenu = false;
    }

    // ─── Navegación ───────────────────────────────────────────────────────────

    openSaleOrder(row, ev) {
        if (ev) ev.stopPropagation();
        const id = this._id(row.order_id);
        if (!id) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openPurchase(row, ev) {
        if (ev) ev.stopPropagation();
        const id = this._id(row.purchase_id);
        if (!id) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ─── Helpers internos ────────────────────────────────────────────────────

    _id(val) {
        if (!val) return false;
        if (Array.isArray(val)) return val[0] || false;
        if (typeof val === "object") return val.id || false;
        return false;
    }

    _str(val, fallback = "—") {
        if (!val) return fallback;
        if (Array.isArray(val)) return val[1] || fallback;
        return String(val);
    }

    _stockStr(val) {
        return this._id(val) ? this._str(val) : "Stock";
    }

    _fmtDate(val) {
        return somFormatDate(val);
    }

    _fmtNum(val) {
        if (!val && val !== 0) return "—";
        return Number(val).toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    statusInfo(code) {
        return STATUS_MAP[code] || { label: code || "—", cls: "" };
    }

    alertInfo(level) {
        return ETA_ALERT_MAP[level] || { label: "—", cls: "", icon: "fa-circle" };
    }

    sortIcon(key) {
        if (this.state.sortKey !== key) return "fa-sort";
        return this.state.sortDir === "asc" ? "fa-sort-asc" : "fa-sort-desc";
    }

    // ─── Getters / wrappers para el template ─────────────────────────────────

    get visibleCols() {
        return COLUMNS.filter(c => !this.state.hiddenCols.has(c.key));
    }

    get totalM2() {
        return this.state.filtered.reduce((s, r) => s + (r.product_uom_qty || 0), 0);
    }

    get allStatuses() {
        // CHIP UNIFICADO: 'Pto. Destino' y 'Arribo Puerto' son la misma
        // fase operativa para el cronograma — un solo filtro con la suma.
        // La llave compuesta (separada por coma) filtra ambos estados.
        const out = [];
        for (const [k, v] of Object.entries(STATUS_MAP)) {
            if (k === "puerto_destino") {
                out.push({
                    key: "puerto_destino,arrived_port",
                    label: "Destino / Arribo",
                    cls: "ts-badge--puerto",
                });
                continue;
            }
            if (k === "arrived_port") {
                continue;
            }
            out.push({ key: k, ...v });
        }
        return out;
    }

    statusChipCount(chipKey) {
        const keys = chipKey.split(",");
        return this.state.records.filter(
            (r) => keys.includes(r.voyage_status)
        ).length;
    }

    get alertCounts() {
        const counts = { ok: 0, warning: 0, danger: 0, done: 0 };
        for (const r of this.state.records) {
            if (r.eta_alert_level && counts[r.eta_alert_level] !== undefined) {
                counts[r.eta_alert_level]++;
            }
        }
        return counts;
    }

    get isGrouped() {
        return this.state.groupBy !== "none";
    }

    strOf(val)          { return this._str(val); }
    stockStrOf(val)     { return this._stockStr(val); }
    fmtDate(val)        { return this._fmtDate(val); }
    fmtDateOrder(row)   { return this._fmtDate(row.date_order); }
    fmtNum(val)         { return this._fmtNum(val); }
    fmtDelayDays(val)   {
        if (!val && val !== 0) return "—";
        const n = Number(val);
        if (n === 0) return "0 días";
        return (n > 0 ? "+" : "") + n + " días";
    }
    colHidden(key)      { return this.state.hiddenCols.has(key); }
    isFiltered(s)       { return this.state.statusFilter === s; }
    isAlertFiltered(l)  { return this.state.alertFilter === l; }
    grpCollapsed(k)     { return !!this.state.collapsedGroups[k]; }
    hasLink(val)        { return !!this._id(val); }
    isStock(val)        { return !this._id(val); }
    isContainer(val)    { return val && val !== "PENDIENTE"; }
}

TransitSheetView.template = "stock_transit_allocation.TransitSheetView";
registry.category("lazy_components").add("TransitSheetView", TransitSheetView);
