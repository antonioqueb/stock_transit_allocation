/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Columnas en orden fijo y definitivo.
 * arrived_port y reception_pending se mapean visualmente
 * dentro de las 7 etapas solicitadas.
 */
const STAGES = [
    {
        key:      "solicitud",
        label:    "Solicitud",
        sublabel: "Enviada al proveedor",
        icon:     "fa-file-text-o",
        color:    "#f59e0b",
        bg:       "#fffbeb",
        border:   "#fde68a",
    },
    {
        key:      "production",
        label:    "Producción",
        sublabel: "En fábrica",
        icon:     "fa-industry",
        color:    "#f97316",
        bg:       "#fff7ed",
        border:   "#fed7aa",
    },
    {
        key:      "booking",
        label:    "Booking",
        sublabel: "Reserva naviera",
        icon:     "fa-anchor",
        color:    "#8b5cf6",
        bg:       "#f5f3ff",
        border:   "#ddd6fe",
    },
    {
        key:      "puerto_origen",
        label:    "Puerto Origen",
        sublabel: "Carga en puerto",
        icon:     "fa-map-marker",
        color:    "#14b8a6",
        bg:       "#f0fdfa",
        border:   "#99f6e4",
    },
    {
        key:      "on_sea",
        label:    "Salida a Mar",
        sublabel: "En altamar",
        icon:     "fa-ship",
        color:    "#3b82f6",
        bg:       "#eff6ff",
        border:   "#bfdbfe",
    },
    {
        key:      "puerto_destino",
        label:    "Puerto Destino",
        sublabel: "Trámite aduanal",
        icon:     "fa-flag",
        color:    "#ec4899",
        bg:       "#fdf2f8",
        border:   "#fbcfe8",
        // arrived_port y reception_pending también caen aquí visualmente
        extraKeys: ["arrived_port", "reception_pending"],
    },
    {
        key:      "delivered",
        label:    "Entrega en Sitio",
        sublabel: "En almacén",
        icon:     "fa-check-circle",
        color:    "#22c55e",
        bg:       "#f0fdf4",
        border:   "#bbf7d0",
    },
];

// Mapa rápido key → stage para lookups
const STAGE_MAP = {};
for (const s of STAGES) {
    STAGE_MAP[s.key] = s;
    if (s.extraKeys) {
        for (const ek of s.extraKeys) STAGE_MAP[ek] = s;
    }
}

export class TransitKanbanView extends Component {
    static template = "stock_transit_allocation.TransitKanbanView";

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");

        this.STAGES = STAGES;

        this.state = useState({
            records:      [],
            loading:      true,
            searchText:   "",
            pendingOnly:  false,  // filtro "Pendiente de publicar"
            columns:      {},   // { stageKey: [records] }
            totals:          {},   // { stageKey: { count, m2 } }
            collapsed:       {},   // { stageKey: bool }
            draggingId:      false,
            dragOverStage:   false,
            updatingStageId: false,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    // ─── Data ─────────────────────────────────────────────────────────────────

    async loadData() {
        this.state.loading = true;
        try {
            const records = await this.orm.searchRead(
                "stock.transit.voyage",
                [["custom_status", "!=", "cancel"]],
                [
                    "name", "custom_status", "purchase_id", "vessel_name",
                    "shipping_line", "container_number", "bl_number",
                    "eta", "etd", "allocation_percent", "transit_progress",
                    "total_m2", "allocated_m2", "company_id",
                    "tc_publication_pending",
                ],
                { order: "eta asc, id desc", limit: 500 }
            );
            this.state.records = records;
            this._buildColumns(records);
        } catch (e) {
            console.error("[TransitKanban] Error:", e);
            this.notification.add("Error al cargar viajes", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        await this.loadData();
    }

    _buildColumns(records) {
        const q = this.state.searchText.trim().toLowerCase();

        const cols  = {};
        const tots  = {};

        for (const s of STAGES) {
            cols[s.key]  = [];
            tots[s.key]  = { count: 0, m2: 0 };
        }

        for (const r of records) {
            // Filtro "Pendiente de publicar": PL procesado + Puerto Origen o
            // superior + X días + inventario sin publicar. Meta: verlo vacío.
            if (this.state.pendingOnly && !r.tc_publication_pending) continue;

            // Filtro búsqueda
            if (q) {
                const haystack = [
                    r.name,
                    this._str(r.purchase_id),
                    r.vessel_name || "",
                    r.container_number || "",
                    r.bl_number || "",
                    r.shipping_line || "",
                ].join(" ").toLowerCase();
                if (!haystack.includes(q)) continue;
            }

            const stage = STAGE_MAP[r.custom_status];
            if (!stage) continue;

            cols[stage.key].push(r);
            tots[stage.key].count++;
            tots[stage.key].m2 += r.total_m2 || 0;
        }

        this.state.columns = cols;
        this.state.totals  = tots;
    }

    // ─── Handlers ─────────────────────────────────────────────────────────────

    get pendingCount() {
        return this.state.records.filter((r) => r.tc_publication_pending).length;
    }

    togglePendingOnly() {
        this.state.pendingOnly = !this.state.pendingOnly;
        this._buildColumns(this.state.records);
    }

    onSearch(ev) {
        this.state.searchText = ev.target.value;
        this._buildColumns(this.state.records);
    }

    clearSearch() {
        this.state.searchText = "";
        this._buildColumns(this.state.records);
    }

    toggleCollapse(key) {
        this.state.collapsed[key] = !this.state.collapsed[key];
    }

    openVoyage(id, ev) {
        if (ev) ev.stopPropagation();
        if (!id) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    createVoyage(ev) {
        if (ev) ev.stopPropagation();
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            views: [[false, "form"]],
            target: "current",
            context: { default_custom_status: "solicitud" },
        });
    }

    // ─── Drag & Drop ──────────────────────────────────────────────────────────

    onCardDragStart(card, ev) {
        if (!card || !card.id) return;
        ev.stopPropagation();
        this.state.draggingId = card.id;
        this.state.dragOverStage = false;

        if (ev.dataTransfer) {
            ev.dataTransfer.effectAllowed = "move";
            ev.dataTransfer.setData("text/plain", String(card.id));
        }
    }

    onCardDragEnd() {
        this.state.draggingId = false;
        this.state.dragOverStage = false;
    }

    onColumnDragEnter(stageKey, ev) {
        if (!this.state.draggingId) return;
        ev.preventDefault();
        this.state.dragOverStage = stageKey;
    }

    onColumnDragOver(stageKey, ev) {
        if (!this.state.draggingId) return;
        ev.preventDefault();
        if (ev.dataTransfer) {
            ev.dataTransfer.dropEffect = "move";
        }
        this.state.dragOverStage = stageKey;
    }

    onColumnDragLeave(stageKey, ev) {
        const related = ev.relatedTarget;
        if (related && ev.currentTarget && ev.currentTarget.contains(related)) {
            return;
        }
        if (this.state.dragOverStage === stageKey) {
            this.state.dragOverStage = false;
        }
    }

    async onColumnDrop(stageKey, ev) {
        ev.preventDefault();
        ev.stopPropagation();

        const rawId = ev.dataTransfer?.getData("text/plain") || this.state.draggingId;
        const recordId = parseInt(rawId, 10);
        this.state.dragOverStage = false;

        if (!recordId || !stageKey) {
            this.state.draggingId = false;
            return;
        }

        const record = this.state.records.find(r => r.id === recordId);
        if (!record) {
            this.state.draggingId = false;
            return;
        }

        if (record.custom_status === stageKey) {
            this.state.draggingId = false;
            return;
        }

        this.state.updatingStageId = recordId;
        try {
            await this.orm.write("stock.transit.voyage", [recordId], {
                custom_status: stageKey,
            });

            record.custom_status = stageKey;
            this._buildColumns(this.state.records);
            this.notification.add(
                `Viaje movido a ${this.stageLabel(stageKey)}`,
                { type: "success", sticky: false }
            );
        } catch (e) {
            console.error("[TransitKanban] Error actualizando estado:", e);
            this.notification.add("No se pudo cambiar el estado del viaje: " + (e.message || e), { type: "danger" });
            await this.loadData();
        } finally {
            this.state.draggingId = false;
            this.state.dragOverStage = false;
            this.state.updatingStageId = false;
        }
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    _str(val) {
        if (!val) return "";
        if (Array.isArray(val)) return val[1] || "";
        return String(val);
    }

    _fmtDate(val) {
        if (!val) return "—";
        const raw      = typeof val === "string" ? val : "";
        if (!raw) return "—";
        const datepart = raw.indexOf(" ") > -1 ? raw.split(" ")[0] : raw;
        const parts    = datepart.split("-");
        if (parts.length !== 3) return raw;
        return parts[2] + "/" + parts[1] + "/" + parts[0];
    }

    _fmtNum(val) {
        if (!val && val !== 0) return "0";
        return Number(val).toLocaleString("es-MX", {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
        });
    }

    _etaDays(eta) {
        if (!eta) return null;
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const d = new Date(eta);
        d.setHours(0, 0, 0, 0);
        return Math.round((d - today) / 86400000);
    }

    _etaClass(eta, status) {
        if (status === "delivered") return "tk-eta--done";
        const days = this._etaDays(eta);
        if (days === null) return "";
        if (days < 0)  return "tk-eta--overdue";
        if (days <= 5) return "tk-eta--urgent";
        return "tk-eta--ok";
    }

    _etaLabel(eta, status) {
        if (status === "delivered") return "Entregado";
        const days = this._etaDays(eta);
        if (days === null) return "—";
        if (days === 0)  return "Hoy";
        if (days === 1)  return "Mañana";
        if (days < 0)   return Math.abs(days) + "d vencido";
        return "en " + days + " días";
    }

    // Wrappers para template (sin lógica en OWL)
    strOf(val)              { return this._str(val); }
    fmtDate(val)            { return this._fmtDate(val); }
    fmtNum(val)             { return this._fmtNum(val); }
    etaClass(r)             { return this._etaClass(r.eta, r.custom_status); }
    etaLabel(r)             { return this._etaLabel(r.eta, r.custom_status); }
    colCards(key)           { return this.state.columns[key] || []; }
    colTotal(key)           { return this.state.totals[key] || { count: 0, m2: 0 }; }
    isCollapsed(key)        { return !!this.state.collapsed[key]; }
    progressWidth(pct)      { return (pct || 0) + "%"; }
    hasContainer(val)       { return val && val !== "PENDIENTE"; }
    isDragging(id)          { return this.state.draggingId === id; }
    isDropTarget(key)       { return this.state.dragOverStage === key; }
    isUpdating(id)          { return this.state.updatingStageId === id; }
    stageLabel(key)         {
        const stage = STAGES.find(s => s.key === key || (s.extraKeys || []).includes(key));
        return stage ? stage.label : key;
    }

    get totalVoyages() {
        return Object.values(this.state.totals).reduce((s, t) => s + t.count, 0);
    }
}

TransitKanbanView.template = "stock_transit_allocation.TransitKanbanView";
registry.category("actions").add("action_transit_kanban_custom", TransitKanbanView);
