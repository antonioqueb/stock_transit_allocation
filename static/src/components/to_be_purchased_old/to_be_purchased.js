/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ToBePurchased extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: [],
            filteredData: [],
            loading: true,
            expanded: {},
            selectedLines: [],

            // Filtros
            searchQuery: "",
            showOnlyPending: true,
            groupBy: "product", // product | sale_order | vendor | customer | unit_type

            // Modal state
            showModal: false,
            allVendors: [],
            selectedVendor: null,
            selectedVendorName: "",
            vendorSearch: "",
            showVendorDropdown: false,
            openPOs: [],
            selectedPO: null,
            loadingPOs: false,
            creatingPO: false,
        });

        onWillStart(async () => {
            await this.loadData();
            await this.loadAllVendors();
        });
    }

    async loadData() {
        this.state.loading = true;

        try {
            this.state.data = await this.orm.call(
                "purchase.manager.logic",
                "get_data",
                []
            );
            this.applyFilters();
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar datos:", error);
            this.state.data = [];
            this.state.filteredData = [];
            this.notification.add(
                "Error al cargar To Be Purchased: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.loading = false;
        }
    }

    async loadAllVendors() {
        try {
            this.state.allVendors = await this.orm.call(
                "purchase.manager.logic",
                "get_all_vendors",
                []
            );
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar proveedores:", error);
            this.state.allVendors = [];
        }
    }

    applyFilters() {
        let result = [...this.state.data];

        const query = (this.state.searchQuery || "").toLowerCase().trim();

        if (query) {
            result = result.filter((product) => {
                const lineText = (product.so_lines || []).map((line) => [
                    line.so_name || "",
                    line.customer || "",
                    line.description || "",
                    line.note || "",
                    line.po_name || "",
                ].join(" ")).join(" ");

                const haystack = [
                    product.name || "",
                    product.vendor || "",
                    product.category || "",
                    product.group || "",
                    product.type || "",
                    product.product_type || "",
                    product.unit_label || "",
                    lineText,
                ].join(" ").toLowerCase();

                return haystack.includes(query);
            });
        }

        if (this.state.showOnlyPending) {
            result = result.map((product) => {
                const filteredLines = (product.so_lines || []).filter((line) => !line.po_id);

                if (filteredLines.length === 0) {
                    return null;
                }

                const qtySo = filteredLines.reduce(
                    (sum, line) => sum + Number(line.qty_pending || 0),
                    0
                );
                const qtySoM2 = filteredLines.reduce(
                    (sum, line) => sum + Number(line.qty_pending_m2 || 0),
                    0
                );
                const qtySoPieces = filteredLines.reduce(
                    (sum, line) => sum + Number(line.qty_pending_pieces || 0),
                    0
                );

                // Debe respetar la misma regla del backend:
                // To Be Purchased NO descuenta stock interno ni tránsito.
                // Solo descuenta OC abierta.
                const qtyToBuy = Math.max(0, qtySo - Number(product.qty_p || 0));
                const qtyToBuyM2 = product.unit_kind === "pieces" ? 0 : qtyToBuy;
                const qtyToBuyPieces = product.unit_kind === "pieces" ? qtyToBuy : 0;

                return {
                    ...product,
                    so_lines: filteredLines,
                    qty_so: qtySo,
                    qty_so_m2: qtySoM2,
                    qty_so_pieces: qtySoPieces,
                    qty_to_buy: qtyToBuy,
                    qty_to_buy_m2: qtyToBuyM2,
                    qty_to_buy_pieces: qtyToBuyPieces,
                };
            }).filter((product) => product !== null);
        }

        if (this.state.groupBy === "product") {
            this.state.filteredData = result;
        } else if (this.state.groupBy === "sale_order") {
            this.state.filteredData = this._groupBySaleOrder(result);
        } else if (this.state.groupBy === "vendor") {
            this.state.filteredData = this._groupByVendor(result);
        } else if (this.state.groupBy === "customer") {
            this.state.filteredData = this._groupByCustomer(result);
        } else if (this.state.groupBy === "unit_type") {
            this.state.filteredData = this._groupByUnitType(result);
        }
    }

    _groupBySaleOrder(data) {
        const soMap = {};

        for (const product of data) {
            for (const soLine of product.so_lines || []) {
                const soKey = soLine.so_id;

                if (!soMap[soKey]) {
                    soMap[soKey] = {
                        id: soLine.so_id,
                        so_name: soLine.so_name,
                        so_id: soLine.so_id,
                        date: soLine.date,
                        commitment_date: soLine.commitment_date,
                        customer: soLine.customer,
                        customer_id: soLine.customer_id,
                        location: soLine.location,
                        note: soLine.note,
                        products: [],
                        total_pending: 0,
                        total_pending_m2: 0,
                        total_pending_pieces: 0,
                        max_days_unassigned: 0,
                    };
                }

                soMap[soKey].products.push({
                    ...soLine,
                    product_id: product.id,
                    product_name: product.name,
                    vendor: product.vendor,
                    vendors: product.vendors,
                    unit_kind: product.unit_kind,
                    unit_label: product.unit_label,
                    product_type: product.product_type,
                    qty_a: product.qty_a,
                    qty_i: product.qty_i,
                    qty_p: product.qty_p,
                    qty_a_m2: product.qty_a_m2,
                    qty_i_m2: product.qty_i_m2,
                    qty_p_m2: product.qty_p_m2,
                    qty_a_pieces: product.qty_a_pieces,
                    qty_i_pieces: product.qty_i_pieces,
                    qty_p_pieces: product.qty_p_pieces,
                });

                soMap[soKey].total_pending += Number(soLine.qty_pending || 0);
                soMap[soKey].total_pending_m2 += Number(soLine.qty_pending_m2 || 0);
                soMap[soKey].total_pending_pieces += Number(soLine.qty_pending_pieces || 0);
                soMap[soKey].max_days_unassigned = Math.max(
                    soMap[soKey].max_days_unassigned,
                    Number(soLine.days_unassigned || 0)
                );
            }
        }

        return Object.values(soMap).sort((a, b) =>
            String(a.so_name || "").localeCompare(String(b.so_name || ""))
        );
    }

    _makeOperationalGroup({ id, key, name, subtitle }) {
        return {
            id,
            group_key: key,
            group_name: name,
            group_subtitle: subtitle,
            products: [],
            total_pending: 0,
            total_pending_m2: 0,
            total_pending_pieces: 0,
            total_to_buy: 0,
            total_to_buy_m2: 0,
            total_to_buy_pieces: 0,
            max_days_unassigned: 0,
        };
    }

    _addProductLinesToOperationalGroup(group, product) {
        for (const soLine of product.so_lines || []) {
            group.products.push({
                ...soLine,
                product_id: product.id,
                product_name: product.name,
                vendor: product.vendor,
                vendors: product.vendors,
                unit_kind: product.unit_kind,
                unit_label: product.unit_label,
                product_type: product.product_type,
                qty_a: product.qty_a,
                qty_i: product.qty_i,
                qty_p: product.qty_p,
                qty_a_m2: product.qty_a_m2,
                qty_i_m2: product.qty_i_m2,
                qty_p_m2: product.qty_p_m2,
                qty_a_pieces: product.qty_a_pieces,
                qty_i_pieces: product.qty_i_pieces,
                qty_p_pieces: product.qty_p_pieces,
            });

            group.total_pending += Number(soLine.qty_pending || 0);
            group.total_pending_m2 += Number(soLine.qty_pending_m2 || 0);
            group.total_pending_pieces += Number(soLine.qty_pending_pieces || 0);
            group.max_days_unassigned = Math.max(
                group.max_days_unassigned,
                Number(soLine.days_unassigned || 0)
            );
        }

        group.total_to_buy += Number(product.qty_to_buy || 0);
        group.total_to_buy_m2 += Number(product.qty_to_buy_m2 || 0);
        group.total_to_buy_pieces += Number(product.qty_to_buy_pieces || 0);
    }

    _sortOperationalGroups(groups) {
        return groups.sort((a, b) => {
            if (b.max_days_unassigned !== a.max_days_unassigned) {
                return b.max_days_unassigned - a.max_days_unassigned;
            }
            return String(a.group_name || "").localeCompare(String(b.group_name || ""));
        });
    }

    _groupByVendor(data) {
        const map = {};

        for (const product of data) {
            const vendorName = product.vendor || "SIN PROVEEDOR";
            const vendorId = product.vendors?.[0]?.id || 0;

            if (!map[vendorName]) {
                map[vendorName] = this._makeOperationalGroup({
                    id: vendorId,
                    key: `vendor_${vendorName}`,
                    name: vendorName,
                    subtitle: "Proveedor con líneas pendientes por compra",
                });
            }

            this._addProductLinesToOperationalGroup(map[vendorName], product);
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    _groupByCustomer(data) {
        const map = {};

        for (const product of data) {
            for (const soLine of product.so_lines || []) {
                const customerKey = soLine.customer_id || soLine.customer || "Sin cliente";

                if (!map[customerKey]) {
                    map[customerKey] = this._makeOperationalGroup({
                        id: customerKey,
                        key: `customer_${customerKey}`,
                        name: soLine.customer || "Sin cliente",
                        subtitle: "Cliente con material pendiente por comprar",
                    });
                }

                const pseudoProduct = { ...product, so_lines: [soLine] };
                this._addProductLinesToOperationalGroup(map[customerKey], pseudoProduct);
            }
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    _groupByUnitType(data) {
        const map = {};

        for (const product of data) {
            const unitKey = product.unit_kind || "m2";
            const groupName = unitKey === "pieces" ? "Piezas" : "Metros cuadrados";
            const subtitle = unitKey === "pieces" ? "Productos vendidos por pieza" : "Productos vendidos por m²";

            if (!map[unitKey]) {
                map[unitKey] = this._makeOperationalGroup({
                    id: unitKey,
                    key: `unit_${unitKey}`,
                    name: groupName,
                    subtitle,
                });
            }

            this._addProductLinesToOperationalGroup(map[unitKey], product);
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this.applyFilters();
    }

    togglePendingFilter() {
        this.state.showOnlyPending = !this.state.showOnlyPending;
        this.applyFilters();
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
        this.state.expanded = {};
        this.state.selectedLines = [];
        this.applyFilters();
    }

    clearSearch() {
        this.state.searchQuery = "";
        this.applyFilters();
    }

    async refresh() {
        await this.loadData();
        this.notification.add("To Be Purchased actualizado", {
            type: "success",
            sticky: false,
        });
    }

    toggleExpand(itemId) {
        this.state.expanded[itemId] = !this.state.expanded[itemId];
    }

    toggleSelection(lineId, ev) {
        if (ev.target.checked) {
            if (!this.state.selectedLines.includes(lineId)) {
                this.state.selectedLines.push(lineId);
            }
        } else {
            this.state.selectedLines = this.state.selectedLines.filter((id) => id !== lineId);
        }
    }

    openPurchaseModal() {
        if (this.state.selectedLines.length === 0) {
            this.notification.add("Seleccione al menos una línea", { type: "warning" });
            return;
        }

        this.state.showModal = true;
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;
        this.state.openPOs = [];
    }

    closeModal() {
        if (this.state.creatingPO) {
            return;
        }

        this.state.showModal = false;
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;
        this.state.openPOs = [];
    }

    get filteredVendors() {
        if (!this.state.vendorSearch.trim()) {
            return this.state.allVendors;
        }

        const query = this.state.vendorSearch.toLowerCase().trim();
        return this.state.allVendors.filter((vendor) =>
            String(vendor.name || "").toLowerCase().includes(query)
        );
    }

    onVendorSearchInput(ev) {
        this.state.vendorSearch = ev.target.value;
        this.state.showVendorDropdown = true;

        if (!ev.target.value.trim()) {
            this.state.selectedVendor = null;
            this.state.selectedVendorName = "";
            this.state.openPOs = [];
            this.state.selectedPO = null;
        }
    }

    onVendorSearchFocus() {
        this.state.showVendorDropdown = true;
    }

    onVendorSearchBlur() {
        setTimeout(() => {
            this.state.showVendorDropdown = false;
        }, 200);
    }

    async selectVendor(vendor) {
        this.state.selectedVendor = vendor.id;
        this.state.selectedVendorName = vendor.name;
        this.state.vendorSearch = vendor.name;
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;

        this.state.loadingPOs = true;

        try {
            this.state.openPOs = await this.orm.call(
                "purchase.manager.logic",
                "get_open_purchase_orders",
                [vendor.id]
            );
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar OCs:", error);
            this.state.openPOs = [];
        } finally {
            this.state.loadingPOs = false;
        }
    }

    clearVendorSelection() {
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.openPOs = [];
        this.state.selectedPO = null;
    }

    selectPO(poId) {
        this.state.selectedPO = poId || null;
    }

    async confirmPurchase() {
        if (!this.state.selectedVendor) {
            this.notification.add("Debe seleccionar un proveedor", { type: "warning" });
            return;
        }

        this.state.creatingPO = true;

        try {
            const resultAction = await this.orm.call(
                "purchase.manager.logic",
                "create_purchase_orders",
                [
                    this.state.selectedLines,
                    this.state.selectedVendor,
                    this.state.selectedPO,
                ]
            );

            if (resultAction.error) {
                this.notification.add(resultAction.error, { type: "danger" });
                return;
            }

            this.notification.add("Orden de Compra procesada correctamente", {
                type: "success",
            });

            this.state.selectedLines = [];
            this.closeModal();
            this.action.doAction(resultAction);
        } catch (error) {
            console.error("[ToBePurchased] Error generando OC:", error);
            this.notification.add("Error: " + (error.message || error), {
                type: "danger",
            });
        } finally {
            this.state.creatingPO = false;
        }
    }

    async openPurchaseOrder(poId, ev) {
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

    fmtNum(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
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

    fmtDays(value) {
        const n = Number(value || 0);
        return n === 1 ? "1 día" : `${n} días`;
    }

    _sum(fieldName) {
        return this.state.data.reduce((sum, product) => sum + Number(product[fieldName] || 0), 0);
    }

    get totalDemandM2() {
        return this._sum("qty_so_m2");
    }

    get totalDemandPieces() {
        return this._sum("qty_so_pieces");
    }

    get totalToBuyM2() {
        return this._sum("qty_to_buy_m2");
    }

    get totalToBuyPieces() {
        return this._sum("qty_to_buy_pieces");
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
}

ToBePurchased.template = "stock_transit_allocation.ToBePurchased";

registry.category("actions").add(
    "action_to_be_purchased",
    ToBePurchased,
    { force: true }
);