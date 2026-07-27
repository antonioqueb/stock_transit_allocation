/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const STATE_META = {
    no_started: { label: "No atendida", cls: "o_sl_gray" },
    in_progress: { label: "En captura", cls: "o_sl_blue" },
    done: { label: "Terminada", cls: "o_sl_green" },
};

const FILTERS = [
    { key: "all", label: "Todas" },
    { key: "no_started", label: "No atendidas" },
    { key: "in_progress", label: "En captura" },
    { key: "done", label: "Terminadas" },
];

export class SupplierLinksTracking extends Component {
    static template = "stock_transit_allocation.SupplierLinksTracking";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.filters = FILTERS;
        this.state = useState({
            loading: true,
            rows: [],
            counts: {},
            filter: "all",
            search: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "stock.picking.supplier.access",
                "get_links_tracking",
                []
            );
            this.state.rows = data.rows || [];
            this.state.counts = data.counts || {};
        } catch (e) {
            console.error("[SupplierLinks] error:", e);
            this.notification.add("Error al cargar el seguimiento de ligas.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    get filtered() {
        const f = this.state.filter;
        const q = (this.state.search || "").toLowerCase().trim();
        return this.state.rows.filter((r) => {
            let okF = true;
            if (f !== "all") {
                okF = r.state === f;
            }
            const hay = `${r.partner} ${r.po_name} ${r.proforma_number} ${r.token}`.toLowerCase();
            const okQ = !q || hay.includes(q);
            return okF && okQ;
        });
    }

    meta(state) {
        return STATE_META[state] || STATE_META.no_started;
    }

    progressClass(p) {
        if (p >= 100) return "o_sl_pf_green";
        if (p >= 50) return "o_sl_pf_blue";
        return "o_sl_pf_amber";
    }

    setFilter(key) {
        this.state.filter = key;
    }

    refresh() {
        this.load();
    }

    openRecord(model, id) {
        if (!id) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async copyLink(url) {
        try {
            await navigator.clipboard.writeText(url);
            this.notification.add("Liga copiada al portapapeles.", { type: "success" });
        } catch (e) {
            this.notification.add(url, { type: "info", title: "Liga del proveedor" });
        }
    }
}

registry.category("lazy_components").add("SupplierLinksTracking", SupplierLinksTracking);
