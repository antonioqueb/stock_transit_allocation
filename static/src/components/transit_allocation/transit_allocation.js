/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class TransitAllocation extends Component {
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
            groupBy: "priority", // priority | product | vendor | category | unit_type
            assigning: false,
            modal: {
                open: false,
                product: null,
                line: null,
                transitLines: [],
                selectedTransitLineIds: [],
                reason: "Asignación operativa desde Transit Allocation.",
                overAction: "free",
                overReason: "Sobreasignación autorizada desde Transit Allocation.",
            },
        });

        onMounted(() => this.loadData());
    }

    // ---------------------------------------------------------------------
    // Data
    // ---------------------------------------------------------------------

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "transit.allocation.manager.logic",
                "get_data",
                []
            );
            this.applyFilters();
        } catch (error) {
            console.error("[TransitAllocation] Error cargando datos:", error);
            this.state.data = [];
            this.state.filteredData = [];
            this.notification.add(
                "Error al cargar Transit Allocation: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.loading = false;
        }
    }

    applyFilters() {
        let result = [...this.state.data];
        const query = (this.state.searchQuery || "").trim().toLowerCase();

        if (query) {
            result = result.filter((product) => {
                const soText = (product.so_lines || []).map((line) => [
                    line.so_name || "",
                    line.customer || "",
                    line.salesperson || "",
                    line.description || "",
                    line.po_name || "",
                    line.transit_voyage_name || "",
                ].join(" ")).join(" ");

                const transitText = (product.transit_lines || []).map((line) => [
                    line.lot_name || "",
                    line.voyage_name || "",
                    line.container_number || "",
                    line.purchase_name || "",
                    line.vendor || "",
                    line.x_bloque || "",
                    line.x_atado || "",
                ].join(" ")).join(" ");

                const haystack = [
                    product.name || "",
                    product.vendor || "",
                    product.category || "",
                    product.group || "",
                    product.type || "",
                    product.product_type || "",
                    product.unit_label || "",
                    soText,
                    transitText,
                ].join(" ").toLowerCase();

                return haystack.includes(query);
            });
        }

        result.sort((a, b) => this._sortProducts(a, b));
        this.state.filteredData = result;
    }

    _sortProducts(a, b) {
        if (this.state.groupBy === "vendor") {
            return String(a.vendor || "").localeCompare(String(b.vendor || "")) ||
                String(a.name || "").localeCompare(String(b.name || ""));
        }
        if (this.state.groupBy === "category") {
            return String(a.category || "").localeCompare(String(b.category || "")) ||
                String(a.name || "").localeCompare(String(b.name || ""));
        }
        if (this.state.groupBy === "unit_type") {
            return String(a.product_type || "").localeCompare(String(b.product_type || "")) ||
                String(a.name || "").localeCompare(String(b.name || ""));
        }
        if (this.state.groupBy === "product") {
            return String(a.name || "").localeCompare(String(b.name || ""));
        }

        const maxPayA = Math.max(...(a.so_lines || []).map((line) => line.payment_percent || 0), 0);
        const maxPayB = Math.max(...(b.so_lines || []).map((line) => line.payment_percent || 0), 0);
        if (maxPayB !== maxPayA) return maxPayB - maxPayA;

        const diff = Number(b.qty_to_allocate || 0) - Number(a.qty_to_allocate || 0);
        if (diff) return diff;

        return String(a.name || "").localeCompare(String(b.name || ""));
    }

    async refresh() {
        await this.loadData();
        this.notification.add("Transit Allocation actualizado", {
            type: "success",
            sticky: false,
        });
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
        this.applyFilters();
    }

    toggleExpand(productId) {
        this.state.expanded[productId] = !this.state.expanded[productId];
    }

    isExpanded(productId) {
        return !!this.state.expanded[productId];
    }

    // ---------------------------------------------------------------------
    // Modal
    // ---------------------------------------------------------------------

    openAllocationModal(product, line, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        this.state.modal = {
            open: true,
            product,
            line,
            transitLines: product.transit_lines || [],
            selectedTransitLineIds: [],
            reason: "Asignación operativa desde Transit Allocation.",
            overAction: "free",
            overReason: "Sobreasignación autorizada desde Transit Allocation.",
        };
    }

    closeModal() {
        this.state.modal = {
            open: false,
            product: null,
            line: null,
            transitLines: [],
            selectedTransitLineIds: [],
            reason: "Asignación operativa desde Transit Allocation.",
            overAction: "free",
            overReason: "Sobreasignación autorizada desde Transit Allocation.",
        };
    }

    isTransitSelected(transitLineId) {
        return (this.state.modal.selectedTransitLineIds || []).includes(transitLineId);
    }

    toggleTransitSelection(transitLineId, ev) {
        const ids = this.state.modal.selectedTransitLineIds || [];
        const checked = ev.target.checked;

        if (checked && !ids.includes(transitLineId)) {
            ids.push(transitLineId);
        }
        if (!checked) {
            this.state.modal.selectedTransitLineIds = ids.filter((id) => id !== transitLineId);
        }
    }

    selectSuggestedTransitLines() {
        const modal = this.state.modal;
        const target = Number(modal.line?.qty_pending || 0);
        const selected = [];
        let total = 0;

        for (const transitLine of modal.transitLines || []) {
            if (total >= target && target > 0) break;
            selected.push(transitLine.id);
            total += Number(transitLine.qty || 0);
        }

        modal.selectedTransitLineIds = selected;
    }

    clearTransitSelection() {
        this.state.modal.selectedTransitLineIds = [];
    }

    selectedTransitLines() {
        const ids = new Set(this.state.modal.selectedTransitLineIds || []);
        return (this.state.modal.transitLines || []).filter((line) => ids.has(line.id));
    }

    modalSelectedQty() {
        return this.selectedTransitLines().reduce((sum, line) => sum + Number(line.qty || 0), 0);
    }

    modalPendingQty() {
        return Number(this.state.modal.line?.qty_pending || 0);
    }

    modalRemainingQty() {
        return this.modalPendingQty() - this.modalSelectedQty();
    }

    modalIsOver() {
        return this.modalRemainingQty() < -0.0001;
    }

    modalOverQty() {
        return Math.max(Math.abs(this.modalRemainingQty()), 0);
    }

    async confirmAllocation() {
        const modal = this.state.modal;
        const selectedIds = modal.selectedTransitLineIds || [];

        if (!modal.line || !modal.line.id || selectedIds.length === 0) {
            this.notification.add("Seleccione al menos un lote en tránsito.", { type: "warning" });
            return;
        }

        this.state.assigning = true;

        try {
            const result = await this.orm.call(
                "transit.allocation.manager.logic",
                "assign_transit_lines",
                [
                    selectedIds,
                    modal.line.id,
                    modal.reason || "Asignación operativa desde Transit Allocation.",
                    this.modalIsOver() ? modal.overAction : false,
                    this.modalIsOver() ? modal.overReason : false,
                ]
            );

            if (result && result.need_over_assignment_decision) {
                this.notification.add(result.message || "Debe indicar acción sobre excedente.", {
                    type: "warning",
                    sticky: false,
                });
                return;
            }

            if (result && result.success === false) {
                this.notification.add(result.message || "No se pudo completar la asignación.", {
                    type: "danger",
                });
                return;
            }

            const selectedQty = result?.selected_qty !== undefined
                ? this.fmtNum(result.selected_qty)
                : this.fmtNum(this.modalSelectedQty());
            const pendingAfter = result?.pending_qty_after !== undefined
                ? this.fmtNum(result.pending_qty_after)
                : "0.00";
            const unit = result?.uom_name || modal.line.unit_label || "";

            let msg = `Inventario en tránsito asignado: ${selectedQty} ${unit}. Pendiente actual: ${pendingAfter} ${unit}.`;
            if (result?.discount_applied) {
                msg += ` Descuento aplicado: ${this.fmtPercent(result.discount_after)}.`;
            } else if (result?.qty_updated) {
                msg += " Cantidad solicitada actualizada por excedente cobrado.";
            }

            this.notification.add(msg, { type: "success", sticky: false });
            this.closeModal();
            await this.loadData();
        } catch (error) {
            console.error("[TransitAllocation] Error asignando tránsito:", error);
            this.notification.add(
                "Error al asignar tránsito: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.assigning = false;
        }
    }

    setModalReason(ev) {
        this.state.modal.reason = ev.target.value;
    }

    setOverAction(ev) {
        this.state.modal.overAction = ev.target.value;
    }

    setOverReason(ev) {
        this.state.modal.overReason = ev.target.value;
    }

    // ---------------------------------------------------------------------
    // Navigation
    // ---------------------------------------------------------------------

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

    openVoyage(voyageId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (!voyageId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            res_id: voyageId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openPurchaseOrder(poId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (!poId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: poId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ---------------------------------------------------------------------
    // Formatting
    // ---------------------------------------------------------------------

    fmtNum(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    fmtPercent(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        }) + "%";
    }

    fmtQtyWithUnit(value, unitLabel) {
        return `${this.fmtNum(value)} ${unitLabel || ""}`.trim();
    }

    fmtSplitQty(m2, pieces) {
        const parts = [];
        if (Number(m2 || 0) > 0) parts.push(`${this.fmtNum(m2)} m²`);
        if (Number(pieces || 0) > 0) parts.push(`${this.fmtNum(pieces)} pzas`);
        return parts.length ? parts.join(" · ") : "0.00";
    }

    paymentClass(value) {
        const n = Number(value || 0);
        if (n >= 80) return "o_tal_pay_high";
        if (n >= 30) return "o_tal_pay_mid";
        return "o_tal_pay_low";
    }

    _sum(fieldName) {
        return this.state.data.reduce((sum, product) => sum + Number(product[fieldName] || 0), 0);
    }

    get totalProducts() {
        return this.state.data.length;
    }

    get totalSaleLines() {
        return this.state.data.reduce((sum, product) => sum + (product.so_lines || []).length, 0);
    }

    get totalTransitLots() {
        return this.state.data.reduce((sum, product) => sum + (product.transit_lines || []).length, 0);
    }

    get totalDemandM2() {
        return this._sum("qty_so_m2");
    }

    get totalDemandPieces() {
        return this._sum("qty_so_pieces");
    }

    get totalTransitM2() {
        return this._sum("qty_transit_available_m2");
    }

    get totalTransitPieces() {
        return this._sum("qty_transit_available_pieces");
    }
}

TransitAllocation.template = "stock_transit_allocation.TransitAllocation";

registry.category("actions").add(
    "action_transit_allocation",
    TransitAllocation,
    { force: true }
);
