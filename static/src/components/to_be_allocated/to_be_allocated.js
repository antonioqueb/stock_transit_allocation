/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ToBeAllocated extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: [],
            filteredData: [],
            loading: true,
            expanded: {},
            searchQuery: "",
            groupBy: "product", // product | sale_order | salesperson
            sending: {},
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;

        try {
            this.state.data = await this.orm.call(
                "sale.allocation.manager.logic",
                "get_data",
                []
            );
            this.applyFilters();
        } catch (error) {
            console.error("[ToBeAllocated] Error cargando datos:", error);
            this.notification.add("Error al cargar To Be Allocated", {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    applyFilters() {
        let rows = [...this.state.data];

        const query = (this.state.searchQuery || "").trim().toLowerCase();

        if (query) {
            rows = rows.filter((line) => {
                const haystack = [
                    line.so_name || "",
                    line.customer || "",
                    line.product_name || "",
                    line.salesperson || "",
                    line.description || "",
                ].join(" ").toLowerCase();

                return haystack.includes(query);
            });
        }

        if (this.state.groupBy === "product") {
            this.state.filteredData = this._groupByProduct(rows);
        } else if (this.state.groupBy === "sale_order") {
            this.state.filteredData = this._groupBySaleOrder(rows);
        } else if (this.state.groupBy === "salesperson") {
            this.state.filteredData = this._groupBySalesperson(rows);
        }
    }

    _groupByProduct(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.product_id || 0;

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `product_${key}`,
                    label: line.product_name || "Sin producto",
                    sublabel: "Producto",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available = Math.max(
                map[key].qty_available,
                line.qty_available || 0
            );
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) => {
            if (b.max_payment_percent !== a.max_payment_percent) {
                return b.max_payment_percent - a.max_payment_percent;
            }
            return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    _groupBySaleOrder(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.so_id || 0;

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `so_${key}`,
                    label: line.so_name || "Sin SO",
                    sublabel: line.customer || "Sin cliente",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available += line.qty_available || 0;
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) => {
            if (b.max_payment_percent !== a.max_payment_percent) {
                return b.max_payment_percent - a.max_payment_percent;
            }
            return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    _groupBySalesperson(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.salesperson || "Sin vendedor";

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `salesperson_${key}`,
                    label: key,
                    sublabel: "Vendedor",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available += line.qty_available || 0;
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) =>
            String(a.label || "").localeCompare(String(b.label || ""))
        );
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this.applyFilters();
    }

    clearSearch() {
        this.state.searchQuery = "";
        this.applyFilters();
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
        this.state.expanded = {};
        this.applyFilters();
    }

    toggleExpand(key) {
        this.state.expanded[key] = !this.state.expanded[key];
    }

    isExpanded(key) {
        return !!this.state.expanded[key];
    }

    async refresh() {
        await this.loadData();
        this.notification.add("To Be Allocated actualizado", {
            type: "success",
            sticky: false,
        });
    }

    async openSaleOrder(soId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!soId) return;

        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: soId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async sendToPurchase(line, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!line || !line.id) return;

        const confirmed = window.confirm(
            `¿Mandar a pedir el pendiente de ${line.product_name} en ${line.so_name}?\n\n` +
            "Esto moverá la línea a To Be Purchased aunque exista stock disponible."
        );

        if (!confirmed) return;

        this.state.sending[line.id] = true;

        try {
            const result = await this.orm.call(
                "sale.allocation.manager.logic",
                "send_to_purchase",
                [[line.id], "Stock rechazado desde To Be Allocated"]
            );

            if (result && result.error) {
                this.notification.add(result.error, { type: "danger" });
                return;
            }

            this.notification.add("Línea enviada a To Be Purchased", {
                type: "success",
                sticky: false,
            });

            await this.loadData();
        } catch (error) {
            console.error("[ToBeAllocated] Error enviando a compra:", error);
            this.notification.add(
                "Error al mandar pedido: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.sending[line.id] = false;
        }
    }

    fmtNum(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    fmtPercent(value) {
        const n = Number(value || 0);
        return (
            n.toLocaleString("es-MX", {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0,
            }) + "%"
        );
    }

    paymentClass(value) {
        const n = Number(value || 0);

        if (n >= 80) return "o_tba_pay_high";
        if (n >= 30) return "o_tba_pay_mid";

        return "o_tba_pay_low";
    }

    get totalLines() {
        return this.state.data.length;
    }

    get totalPending() {
        return this.state.data.reduce(
            (sum, line) => sum + (line.qty_pending || 0),
            0
        );
    }
}

ToBeAllocated.template = "stock_transit_allocation.ToBeAllocated";

registry.category("actions").add(
    "action_to_be_allocated",
    ToBeAllocated,
    { force: true }
);