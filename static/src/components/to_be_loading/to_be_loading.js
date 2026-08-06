/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * To Be Loading — bandeja de pedidos de compra pendientes por COMPLETAR
 * (PI, embarque, contenedores, packing list, worksheet, recepción).
 * Cada tarjeta abre su pedido para completarlo.
 */
export class ToBeLoading extends Component {
    static template = "stock_transit_allocation.ToBeLoading";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            rows: [],
            filtered: [],
            search: "",
            error: "",
        });
        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("to.be.loading.logic", "get_data", []);
            this.state.rows = data.rows || [];
            this.applyFilter();
        } catch (e) {
            console.error("[ToBeLoading]", e);
            this.state.error = "No se pudo cargar el tablero. Reintenta.";
        }
        this.state.loading = false;
    }

    applyFilter() {
        const q = (this.state.search || "").trim().toUpperCase();
        this.state.filtered = !q
            ? this.state.rows
            : this.state.rows.filter((r) =>
                  [r.name, r.partner, r.pi]
                      .join(" ")
                      .toUpperCase()
                      .includes(q)
              );
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
        this.applyFilter();
    }

    fmt(n) {
        return new Intl.NumberFormat("en-US", {
            maximumFractionDigits: 2,
        }).format(Number(n) || 0);
    }

    openOrder(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: row.po_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("lazy_components").add("ToBeLoading", ToBeLoading);
