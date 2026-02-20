/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

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

const COLUMNS = [
    { key: "purchase_id",      label: "OC Sistema",         width: "110px" },
    { key: "date_order",       label: "Fecha OC",           width: "90px"  },
    { key: "voyage_status",    label: "Estado",             width: "120px" },
    { key: "salesperson_id",   label: "Vendedor",           width: "160px" },  // 100 → 160
    { key: "order_id",         label: "Sales Order",        width: "110px" },
    { key: "partner_id",       label: "Cliente / Proyecto", width: "200px" },
    { key: "proforma_ref",     label: "Proforma",           width: "110px" },
    { key: "vendor_id",        label: "Proveedor",          width: "200px" },  // 130 → 200
    { key: "product_id",       label: "Descripción",        width: "290px" },  // 160 → 290
    { key: "product_uom_qty",  label: "m² Embarcados",      width: "100px", align: "right", isNum: true },
    { key: "container_number", label: "Contenedor",         width: "110px" },
    { key: "bl_number",        label: "BL / Folio",         width: "120px" },
    { key: "etd",              label: "ETD",                width: "85px"  },
    { key: "eta",              label: "ETA",                width: "85px"  },
    { key: "arrival_date",     label: "Llegada Real",       width: "90px"  },
];

export class TransitSheetView extends Component {
    static template = "stock_transit_allocation.TransitSheetView";

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");

        this.COLUMNS    = COLUMNS;
        this.STATUS_MAP = STATUS_MAP;

        this.state = useState({
            records:         [],
            filtered:        [],
            loading:         true,
            searchText:      "",
            statusFilter:    "",
            sortKey:         "eta",
            sortDir:         "asc",
            groupBy:         "none",
            groups:          [],
            collapsedGroups: {},
            hiddenCols:      new Set(["arrival_date"]),
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
            data = data.filter(r =>
                this._str(r.purchase_id).toLowerCase().includes(q) ||
                this._str(r.order_id).toLowerCase().includes(q) ||
                this._str(r.partner_id).toLowerCase().includes(q) ||
                this._str(r.product_id).toLowerCase().includes(q) ||
                this._str(r.vendor_id).toLowerCase().includes(q) ||
                (r.bl_number || "").toLowerCase().includes(q) ||
                (r.container_number || "").toLowerCase().includes(q) ||
                (r.proforma_ref || "").toLowerCase().includes(q)
            );
        }

        if (this.state.statusFilter) {
            data = data.filter(r => r.voyage_status === this.state.statusFilter);
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
                label  = r.partner_id ? r.partner_id[1] : "Sin cliente";
            } else if (this.state.groupBy === "status") {
                grpKey = r.voyage_status || "none";
                label  = STATUS_MAP[grpKey] ? STATUS_MAP[grpKey].label : grpKey;
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

    _str(val) {
        if (!val) return "—";
        if (Array.isArray(val)) return val[1] || "—";
        return String(val);
    }

    /**
     * Formatea una fecha YYYY-MM-DD o datetime "YYYY-MM-DD HH:MM:SS" a DD/MM/YYYY.
     * No usa String() en el template — todo pasa por este método JS.
     */
    _fmtDate(val) {
        if (!val) return "—";
        // val puede ser string "2025-03-15" o "2025-03-15 00:00:00" o false
        const raw = (typeof val === "string") ? val : "";
        if (!raw) return "—";
        const datepart = raw.indexOf(" ") > -1 ? raw.split(" ")[0] : raw;
        const parts = datepart.split("-");
        if (parts.length !== 3) return raw;
        return parts[2] + "/" + parts[1] + "/" + parts[0];
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
        return Object.entries(STATUS_MAP).map(([k, v]) => ({ key: k, ...v }));
    }

    get isGrouped() {
        return this.state.groupBy !== "none";
    }

    // Wrappers simples — el template solo llama métodos sin lógica inline
    strOf(val)          { return this._str(val); }
    fmtDate(val)        { return this._fmtDate(val); }
    fmtDateOrder(row)   { return this._fmtDate(row.date_order); }   // evita split en template
    fmtNum(val)         { return this._fmtNum(val); }
    colHidden(key)      { return this.state.hiddenCols.has(key); }
    isFiltered(s)       { return this.state.statusFilter === s; }
    grpCollapsed(k)     { return !!this.state.collapsedGroups[k]; }
    hasLink(val)        { return !!this._id(val); }
    isContainer(val)    { return val && val !== "PENDIENTE"; }
}

TransitSheetView.template = "stock_transit_allocation.TransitSheetView";
registry.category("actions").add("action_transit_sheet_custom", TransitSheetView);