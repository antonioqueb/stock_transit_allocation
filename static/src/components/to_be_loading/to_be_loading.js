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
            groupBy: "order", // 'order' (default) | 'vendor' | 'product'
            // COLAPSADO POR DEFAULT: solo se registran los grupos ABIERTOS;
            // un grupo sin entrada está cerrado (así el default no requiere
            // mutar estado durante el render).
            expanded: {},
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
        } catch (e) {
            console.error("[OpenPO]", e);
            this.state.error = "No se pudo cargar el tablero. Reintenta.";
        }
        this.state.loading = false;
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
    }

    /** Filas planas (línea de OC faltante) con su contexto de pedido. */
    get rows() {
        const out = [];
        for (const g of this.state.groups) {
            for (const p of g.products) {
                out.push({
                    ...p,
                    po_id: g.po_id,
                    po_name: g.po_name,
                    pi: g.pi,
                    partner: g.partner,
                    date: g.date,
                });
            }
        }
        return out;
    }

    get filteredGroups() {
        const q = (this.state.search || "").trim().toUpperCase();
        let rows = this.rows;
        if (q) {
            rows = rows.filter((r) =>
                [r.po_name, r.pi, r.partner, r.product]
                    .join(" ")
                    .toUpperCase()
                    .includes(q)
            );
        }

        const mode = this.state.groupBy;
        const groups = new Map();
        for (const r of rows) {
            let key, tag, title, sub, po_id = false;
            if (mode === "vendor") {
                key = "v_" + (r.partner || "SIN PROVEEDOR");
                tag = "PROV";
                title = r.partner || "Sin proveedor";
            } else if (mode === "product") {
                key = "p_" + r.product;
                tag = "PROD";
                title = r.product;
            } else {
                key = "po_" + r.po_id;
                tag = "PI";
                title = r.pi || "Sin PI";
                po_id = r.po_id;
            }
            if (!groups.has(key)) {
                groups.set(key, {
                    key, tag, title, po_id,
                    po_name: mode === "order" ? r.po_name : "",
                    sub: "",
                    partner: r.partner,
                    date: r.date,
                    solicitado: 0,
                    embarcado: 0,
                    diff: 0,
                    rows: [],
                });
            }
            const g = groups.get(key);
            g.solicitado += r.solicitado;
            g.embarcado += r.embarcado;
            g.diff += r.diff;
            g.rows.push(r);
        }

        const result = [...groups.values()];
        for (const g of result) {
            if (mode === "order") {
                g.sub = `${g.partner} · confirmado ${g.date}`;
            } else {
                const pos = new Set(g.rows.map((r) => r.po_id));
                g.sub = `${pos.size} pedido${pos.size === 1 ? "" : "s"} con faltante`;
            }
        }
        result.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
        return result;
    }

    get totalProducts() {
        return this.filteredGroups.reduce((s, g) => s + g.rows.length, 0);
    }

    get groupMetricLabel() {
        return { order: "Proformas", vendor: "Proveedores", product: "Productos" }[
            this.state.groupBy
        ];
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
    }

    clearSearch() {
        this.state.search = "";
    }

    toggleGroup(g) {
        this.state.expanded[g.key] = !this.state.expanded[g.key];
    }

    isCollapsed(g) {
        return !this.state.expanded[g.key];
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
        this.openOrderById(g.po_id);
    }

    openOrderById(poId) {
        if (!poId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: poId,
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
