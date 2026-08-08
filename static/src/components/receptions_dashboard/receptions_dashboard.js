/** @odoo-module **/
// Tablero de Recepciones — pipeline operativo del almacén: En puerto,
// Listos para recibir y Recepcionados (folios validados, 7 días).
// Cards compactos: colapsados por default, click para desplegar.
// Refresco automático cada 60 s con skip si el payload no cambió.
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";

export class ReceptionsDashboard extends Component {
    static template = "stock_transit_allocation.ReceptionsDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ loading: true, data: null, detail: null, expanded: {} });
        this.timer = null;
        this.lastPayload = null;
        onMounted(async () => {
            await this.load();
            this.timer = setInterval(() => this.load(), 60000);
        });
        onWillUnmount(() => {
            if (this.timer) {
                clearInterval(this.timer);
            }
        });
    }

    async load() {
        let data;
        try {
            data = await this.orm.call(
                "stock.transit.voyage",
                "get_receptions_dashboard_data",
                []
            );
        } catch {
            this.state.loading = false;
            return;
        }
        const payload = JSON.stringify(data);
        if (payload === this.lastPayload) {
            return;
        }
        this.lastPayload = payload;
        this.state.data = data;
        this.state.loading = false;
    }

    // ── Cards colapsables ───────────────────────────────────────
    toggleCard(card) {
        this.state.expanded[card.id] = !this.state.expanded[card.id];
    }

    isExpanded(card) {
        return !!this.state.expanded[card.id];
    }

    // ── Navegación ──────────────────────────────────────────────
    // OJO: el personal de almacén NUNCA accede al viaje/embarque desde
    // aquí. Lo único que ve del material en camino es el popup de
    // materiales; su acceso operativo es la RECEPCIÓN.
    showMaterials(card) {
        this.state.detail = card;
    }

    closeMaterials() {
        this.state.detail = null;
    }

    openReception(id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // Reimpresión del Worksheet directo desde el tablero (también para
    // recepciones YA validadas: es documento de trabajo, se reimprime
    // a demanda).
    async printWorksheet(receptionId, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!receptionId) {
            return;
        }
        try {
            const act = await this.orm.call(
                "stock.picking", "action_print_worksheet_pdf", [[receptionId]]
            );
            if (act) {
                await this.action.doAction(act);
            }
        } catch (e) {
            console.error("[Recepciones] Error imprimiendo Worksheet:", e);
            const msg = (e && e.data && e.data.message) || e.message ||
                "No se pudo imprimir el Worksheet.";
            this.notification.add(msg, { type: "danger" });
        }
    }

    openReceptionsList() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Recepciones físicas",
            res_model: "stock.picking",
            domain: [["origin", "=like", "%(Recepción Física)"]],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    etaBadge(c) {
        if (c.days_to_eta === null || c.days_to_eta === undefined) {
            return { text: "Sin ETA", cls: "muted" };
        }
        if (c.days_to_eta > 1) {
            return { text: `en ${c.days_to_eta} días`, cls: "blue" };
        }
        if (c.days_to_eta >= 0) {
            return { text: c.days_to_eta === 0 ? "hoy" : "mañana", cls: "sky" };
        }
        return { text: `hace ${-c.days_to_eta} días`, cls: "red" };
    }
}

registry
    .category("actions")
    .add("stock_transit_allocation.receptions_dashboard", ReceptionsDashboard);
