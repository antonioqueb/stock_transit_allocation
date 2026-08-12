/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

/**
 * Open PO — pedidos con material embarcado DIFERENTE a lo solicitado,
 * agrupados por PROFORMA (PI). Acciones: abrir el pedido o cerrar la
 * demanda por producto (aceptar la diferencia).
 */
export class ToBeLoading extends Component {
    static template = "stock_transit_allocation.ToBeLoading";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            groups: [],
            search: "",
            collapsed: {},
            closing: {},
            error: "",
        });
        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("to.be.loading.logic", "get_data", []);
            this.state.groups = data.groups || [];
            // COLAPSADO POR DEFAULT: cada grupo nuevo arranca cerrado; los
            // que el usuario ya abrió en la sesión conservan su estado.
            for (const g of this.state.groups) {
                if (!(g.po_id in this.state.collapsed)) {
                    this.state.collapsed[g.po_id] = true;
                }
            }
        } catch (e) {
            console.error("[OpenPO]", e);
            this.state.error = "No se pudo cargar el tablero. Reintenta.";
        }
        this.state.loading = false;
    }

    get filteredGroups() {
        const q = (this.state.search || "").trim().toUpperCase();
        if (!q) {
            return this.state.groups;
        }
        return this.state.groups.filter((g) =>
            [g.po_name, g.partner, g.pi]
                .concat(g.products.map((p) => p.product))
                .join(" ")
                .toUpperCase()
                .includes(q)
        );
    }

    get totalProducts() {
        return this.filteredGroups.reduce((s, g) => s + g.products.length, 0);
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
    }

    clearSearch() {
        this.state.search = "";
    }

    toggleGroup(g) {
        this.state.collapsed[g.po_id] = !this.state.collapsed[g.po_id];
    }

    isCollapsed(g) {
        return !!this.state.collapsed[g.po_id];
    }

    fmt(n) {
        return new Intl.NumberFormat("en-US", {
            maximumFractionDigits: 2,
        }).format(Number(n) || 0);
    }

    diffLabel(n) {
        return (n > 0 ? "+" : "") + this.fmt(n);
    }

    openOrder(g) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: g.po_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    closeDemand(g, p) {
        this.dialog.add(ConfirmationDialog, {
            title: "Cerrar demanda",
            body:
                `${p.product}: solicitado ${this.fmt(p.solicitado)}, embarcado ` +
                `${this.fmt(p.embarcado)} (dif. ${this.diffLabel(p.diff)}). ` +
                `La cantidad de la línea se ajustará a lo embarcado y dejará ` +
                `de aparecer aquí. ¿Confirmas?`,
            confirmLabel: "Cerrar demanda",
            cancelLabel: "Cancelar",
            confirm: async () => {
                this.state.closing[p.line_id] = true;
                try {
                    await this.orm.call("to.be.loading.logic", "close_demand", [p.line_id]);
                    this.notification.add(`Demanda cerrada: ${p.product}`, { type: "success" });
                    await this.loadData();
                } catch (e) {
                    console.error("[OpenPO] close_demand", e);
                    this.notification.add(
                        (e.data && e.data.message) || "No se pudo cerrar la demanda.",
                        { type: "danger" }
                    );
                } finally {
                    this.state.closing[p.line_id] = false;
                }
            },
            cancel: () => {},
        });
    }
}

registry.category("lazy_components").add("ToBeLoading", ToBeLoading);
