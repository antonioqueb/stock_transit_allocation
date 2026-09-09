/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";

/**
 * Submenú "Vendedores" de To Be Allocated: ranking de vendedores por
 * pedidos que aún tienen material sin asignar por completo, con filtro
 * por "Referencia del cliente" (pedidos SPS/legado que traen referencia).
 */
export class ToBeAllocatedSellers extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: [],
            loading: true,
            expanded: {},
            searchQuery: "",
            // "" = todos | "with_ref" = solo con referencia del cliente | "without_ref" = sin referencia
            refFilter: "",
        });

        onMounted(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("sale.allocation.manager.logic", "get_sellers_summary", []);
            this.state.data = Array.isArray(data) ? data : [];
        } catch (error) {
            console.error("[TBA Vendedores] error cargando", error);
            this.notification.add("No se pudo cargar el resumen por vendedor.", { type: "danger" });
            this.state.data = [];
        } finally {
            this.state.loading = false;
        }
    }

    refresh() {
        return this.loadData();
    }

    // ------------------------------------------------------------------
    // Filtros
    // ------------------------------------------------------------------
    onSearchInput(ev) {
        this.state.searchQuery = (ev.target.value || "").toString();
    }

    clearSearch() {
        this.state.searchQuery = "";
    }

    setRefFilter(value) {
        this.state.refFilter = this.state.refFilter === value ? "" : value;
    }

    _orderMatches(order) {
        if (this.state.refFilter === "with_ref" && !order.client_ref) return false;
        if (this.state.refFilter === "without_ref" && order.client_ref) return false;
        const q = (this.state.searchQuery || "").trim().toLowerCase();
        if (!q) return true;
        const hay = [order.so_name, order.client_ref, order.customer, ...(order.products || [])]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
        return hay.includes(q);
    }

    _sellerMatchesSearch(seller) {
        const q = (this.state.searchQuery || "").trim().toLowerCase();
        return q && (seller.seller_name || "").toLowerCase().includes(q);
    }

    /** Vendedores con sus pedidos ya filtrados, ordenados por pedidos pendientes. */
    get sellers() {
        const rows = [];
        for (const seller of this.state.data) {
            const bySeller = this._sellerMatchesSearch(seller);
            const orders = (seller.orders || []).filter((o) => {
                if (this.state.refFilter === "with_ref" && !o.client_ref) return false;
                if (this.state.refFilter === "without_ref" && o.client_ref) return false;
                return bySeller || this._orderMatches(o);
            });
            if (!orders.length) continue;
            const agg = {
                key: String(seller.seller_id || 0),
                seller_id: seller.seller_id,
                seller_name: seller.seller_name,
                orders,
                orders_count: orders.length,
                orders_with_ref: orders.filter((o) => !!o.client_ref).length,
                lines: 0,
                pending_m2: 0,
                pending_pieces: 0,
                to_be_allocated: 0,
                to_be_purchased: 0,
            };
            for (const o of orders) {
                agg.lines += o.lines || 0;
                agg.pending_m2 += o.pending_m2 || 0;
                agg.pending_pieces += o.pending_pieces || 0;
                agg.to_be_allocated += o.to_be_allocated || 0;
                agg.to_be_purchased += o.to_be_purchased || 0;
            }
            rows.push(agg);
        }
        rows.sort((a, b) => b.orders_count - a.orders_count || b.pending_m2 - a.pending_m2 || a.seller_name.localeCompare(b.seller_name));
        return rows;
    }

    get maxOrders() {
        return this.sellers.reduce((m, s) => Math.max(m, s.orders_count), 0) || 1;
    }

    get totalOrders() {
        return this.sellers.reduce((n, s) => n + s.orders_count, 0);
    }

    get totalOrdersWithRef() {
        return this.sellers.reduce((n, s) => n + s.orders_with_ref, 0);
    }

    get totalPendingM2() {
        return this.sellers.reduce((n, s) => n + s.pending_m2, 0);
    }

    get totalPendingPieces() {
        return this.sellers.reduce((n, s) => n + s.pending_pieces, 0);
    }

    /** Conteos globales (sin filtro) para los contadores de los botones. */
    get countWithRef() {
        let n = 0;
        for (const s of this.state.data) for (const o of s.orders || []) if (o.client_ref) n++;
        return n;
    }

    get countWithoutRef() {
        let n = 0;
        for (const s of this.state.data) for (const o of s.orders || []) if (!o.client_ref) n++;
        return n;
    }

    barWidth(seller) {
        return `${Math.max(4, Math.round((seller.orders_count / this.maxOrders) * 100))}%`;
    }

    // ------------------------------------------------------------------
    // Expansión / navegación
    // ------------------------------------------------------------------
    isExpanded(key) {
        return !!this.state.expanded[key];
    }

    toggleExpanded(key) {
        this.state.expanded[key] = !this.state.expanded[key];
    }

    openSaleOrder(soId, ev) {
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

    openToBeAllocated() {
        this.action.doAction("stock_transit_allocation.action_to_be_allocated_client");
    }

    // ------------------------------------------------------------------
    // Formato
    // ------------------------------------------------------------------
    fmtNum(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    fmtInt(value) {
        return Number(value || 0).toLocaleString("es-MX", { maximumFractionDigits: 0 });
    }

    fmtDate(value) {
        if (!value || value === "N/A") return "—";
        return somFormatDate(value);
    }

    fmtPending(order) {
        const parts = [];
        if (order.pending_m2 > 0.0001) parts.push(`${this.fmtNum(order.pending_m2)} m²`);
        if (order.pending_pieces > 0.0001) parts.push(`${this.fmtNum(order.pending_pieces)} pzas`);
        return parts.join(" · ") || "—";
    }
}

ToBeAllocatedSellers.template = "stock_transit_allocation.ToBeAllocatedSellers";

registry.category("lazy_components").add("ToBeAllocatedSellers", ToBeAllocatedSellers);
