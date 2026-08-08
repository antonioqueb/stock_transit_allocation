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
        label:    "Ordenado",
        sublabel: "Solicitud · clic en el engrane = en producción",
        icon:     "fa-file-text-o",
        color:    "#f59e0b",
        bg:       "#fffbeb",
        border:   "#fde68a",
        // production vive en esta misma columna (2 columnas fusionadas);
        // el toggle de manufactura de la tarjeta marca/desmarca producción.
        extraKeys: ["production"],
    },
    {
        key:      "booking",
        label:    "Booking",
        sublabel: "Reserva naviera · Carga en puerto",
        icon:     "fa-anchor",
        color:    "#8b5cf6",
        bg:       "#f5f3ff",
        border:   "#ddd6fe",
        // puerto_origen se fusionó aquí (toda su lógica cae en Booking)
        extraKeys: ["puerto_origen"],
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
        // arrived_port también cae aquí visualmente
        extraKeys: ["arrived_port"],
    },
    {
        key:      "delivered",
        label:    "Entrega en Sitio",
        sublabel: "Listo para recibir · Entregado al validar",
        icon:     "fa-check-circle",
        color:    "#22c55e",
        bg:       "#f0fdf4",
        border:   "#bbf7d0",
        // Soltar aquí pone el viaje EN RECEPCIÓN (listo para recibir);
        // 'Entregado' lo pone la validación de la recepción física.
        extraKeys: ["reception_pending"],
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
            const records = await this.orm.call(
                "stock.transit.voyage",
                "tk_get_kanban_records",
                []
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
                    this._str(r.tc_supplier_id),
                    r.partner_ref || "",
                    r.cargo_invoices || "",
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

    // ── Toggle de producción (columna Ordenado) ──────────────────────────────
    // Clic en el ícono de manufactura de la tarjeta: solicitud ⇄ production.
    // Guarda de inmediato y deja el ícono encendido cuando ya está en fábrica.
    isOrderStage(card) {
        return card.custom_status === "solicitud" || card.custom_status === "production";
    }

    isInProduction(card) {
        return card.custom_status === "production";
    }

    async toggleProduction(card, ev) {
        if (ev) ev.stopPropagation();
        if (!card || !this.isOrderStage(card)) return;

        const next = card.custom_status === "production" ? "solicitud" : "production";
        this.state.updatingStageId = card.id;
        try {
            await this.orm.write("stock.transit.voyage", [card.id], {
                custom_status: next,
            });
            card.custom_status = next;
            card.status_label = next === "production" ? "Producción" : "Solicitud Enviada";
            this._buildColumns(this.state.records);
            this.notification.add(
                next === "production"
                    ? "Marcado EN PRODUCCIÓN."
                    : "Regresado a Solicitud.",
                { type: "success", sticky: false }
            );
        } catch (e) {
            console.error("[TransitKanban] Error marcando producción:", e);
            this.notification.add("No se pudo actualizar: " + (e.message || e), { type: "danger" });
            await this.loadData();
        } finally {
            this.state.updatingStageId = false;
        }
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

        if (ev.dataTransfer) {
            ev.dataTransfer.effectAllowed = "move";
            ev.dataTransfer.setData("text/plain", String(card.id));
        }

        // DIFERIDO a propósito: mutar el estado reactivo DENTRO del
        // dragstart re-renderiza la tarjeta en ese mismo instante y el
        // navegador CANCELA el arrastre (la tarjeta "se suelta sola").
        // Con el estado mutado un tick después, el drag ya está en curso
        // y sobrevive al re-render.
        setTimeout(() => {
            this.state.draggingId = card.id;
            this.state.dragOverStage = false;
        }, 0);
    }

    onCardDragEnd() {
        this.state.draggingId = false;
        this.state.dragOverStage = false;
    }

    onColumnDragEnter(stageKey, ev) {
        // preventDefault siempre (ver onColumnDragOver).
        ev.preventDefault();
        if (this.state.draggingId) {
            this.state.dragOverStage = stageKey;
        }
    }

    onColumnDragOver(stageKey, ev) {
        // preventDefault SIEMPRE (no solo con draggingId ya seteado): el
        // dragstart difiere la mutación del estado un tick, y sin el
        // preventDefault temprano el navegador marca la columna como
        // destino inválido y el drop jamás dispara.
        ev.preventDefault();
        if (ev.dataTransfer) {
            ev.dataTransfer.dropEffect = "move";
        }
        if (this.state.draggingId && this.state.dragOverStage !== stageKey) {
            this.state.dragOverStage = stageKey;
        }
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

        // Si el estatus actual ya vive en esta columna (columnas fusionadas:
        // production→Solicitud/Producción, puerto_origen→Booking,
        // arrived_port→Puerto Destino, reception_pending→Entrega en Sitio),
        // no se reescribe nada — evita retrocesos accidentales de estatus.
        const currentStage = STAGE_MAP[record.custom_status];
        if (record.custom_status === stageKey || (currentStage && currentStage.key === stageKey)) {
            this.state.draggingId = false;
            return;
        }

        this.state.updatingStageId = recordId;
        try {
            await this.orm.write("stock.transit.voyage", [recordId], {
                custom_status: stageKey,
            });

            // El servidor puede re-enrutar el estado (p. ej. 'delivered' sin
            // recepción validada se convierte en 'reception_pending'): se lee
            // el estado REAL aplicado en lugar de asumir el solicitado.
            const fresh = await this.orm.read(
                "stock.transit.voyage", [recordId], ["custom_status"]
            );
            const applied = (fresh && fresh[0] && fresh[0].custom_status) || stageKey;

            record.custom_status = applied;
            this._buildColumns(this.state.records);

            if (stageKey === "delivered" && applied === "reception_pending") {
                this.notification.add(
                    "Viaje LISTO PARA RECIBIR: ya aparece en el tablero de Recepciones. " +
                    "Se marcará Entregado al VALIDAR la recepción física.",
                    { type: "info", sticky: false }
                );
            } else {
                this.notification.add(
                    `Viaje movido a ${this.stageLabel(applied)}`,
                    { type: "success", sticky: false }
                );
            }
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
registry.category("lazy_components").add("TransitKanbanView", TransitKanbanView);
