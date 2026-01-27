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
            expanded: {},
            selectedLines: [],
            // Filtros
            searchQuery: "",
            showOnlyPending: true,
            groupBy: "product", // 'product' | 'sale_order' | 'vendor'
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
        });

        onWillStart(async () => {
            await this.loadData();
            await this.loadAllVendors();
        });
    }

    async loadData() {
        try {
            this.state.data = await this.orm.call("purchase.manager.logic", "get_data", []);
            this.applyFilters();
        } catch (error) {
            console.error("Error al cargar datos:", error);
        }
    }

    async loadAllVendors() {
        try {
            this.state.allVendors = await this.orm.call("purchase.manager.logic", "get_all_vendors", []);
        } catch (error) {
            console.error("Error al cargar proveedores:", error);
        }
    }

    applyFilters() {
        let result = [...this.state.data];
        
        // Filtro por búsqueda de producto
        if (this.state.searchQuery.trim()) {
            const query = this.state.searchQuery.toLowerCase().trim();
            result = result.filter(p => p.name.toLowerCase().includes(query));
        }
        
        // Filtro solo pendientes (sin OC)
        if (this.state.showOnlyPending) {
            result = result.map(product => {
                const filteredLines = product.so_lines.filter(line => !line.po_id);
                if (filteredLines.length === 0) return null;
                return {
                    ...product,
                    so_lines: filteredLines,
                    qty_so: filteredLines.reduce((sum, l) => sum + l.qty_pending, 0),
                    qty_to_buy: Math.max(0, filteredLines.reduce((sum, l) => sum + l.qty_pending, 0) - (product.qty_a + product.qty_i + product.qty_p))
                };
            }).filter(p => p !== null);
        }
        
        // Aplicar agrupación según el modo seleccionado
        if (this.state.groupBy === "product") {
            this.state.filteredData = result;
        } else if (this.state.groupBy === "sale_order") {
            this.state.filteredData = this._groupBySaleOrder(result);
        } else if (this.state.groupBy === "vendor") {
            this.state.filteredData = this._groupByVendor(result);
        }
    }

    _groupBySaleOrder(data) {
        const soMap = {};
        
        for (const product of data) {
            for (const soLine of product.so_lines) {
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
                    };
                }
                soMap[soKey].products.push({
                    ...soLine,
                    product_id: product.id,
                    product_name: product.name,
                    vendor: product.vendor,
                    vendors: product.vendors,
                    qty_a: product.qty_a,
                    qty_i: product.qty_i,
                    qty_p: product.qty_p,
                });
                soMap[soKey].total_pending += soLine.qty_pending;
            }
        }
        
        return Object.values(soMap).sort((a, b) => a.so_name.localeCompare(b.so_name));
    }

    _groupByVendor(data) {
        const vendorMap = {};
        
        for (const product of data) {
            const vendorName = product.vendor || 'SIN PROVEEDOR';
            const vendorId = product.vendors?.[0]?.id || 0;
            
            if (!vendorMap[vendorName]) {
                vendorMap[vendorName] = {
                    id: vendorId,
                    vendor_name: vendorName,
                    vendor_id: vendorId,
                    products: [],
                    total_pending: 0,
                    total_to_buy: 0,
                };
            }
            
            // Agregar líneas de SO con info del producto
            for (const soLine of product.so_lines) {
                vendorMap[vendorName].products.push({
                    ...soLine,
                    product_id: product.id,
                    product_name: product.name,
                    qty_a: product.qty_a,
                    qty_i: product.qty_i,
                    qty_p: product.qty_p,
                });
                vendorMap[vendorName].total_pending += soLine.qty_pending;
            }
            vendorMap[vendorName].total_to_buy += product.qty_to_buy;
        }
        
        return Object.values(vendorMap).sort((a, b) => a.vendor_name.localeCompare(b.vendor_name));
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

    toggleExpand(itemId) {
        this.state.expanded[itemId] = !this.state.expanded[itemId];
    }

    toggleSelection(lineId, ev) {
        if (ev.target.checked) {
            this.state.selectedLines.push(lineId);
        } else {
            this.state.selectedLines = this.state.selectedLines.filter(id => id !== lineId);
        }
    }

    openPurchaseModal() {
        if (this.state.selectedLines.length === 0) {
            this.notification.add("Seleccione al menos una línea", { type: "warning" });
            return;
        }
        this.state.showModal = true;
        this.state.selectedVendor = null;
        this.state.selectedPO = null;
        this.state.openPOs = [];
    }

    closeModal() {
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
        return this.state.allVendors.filter(v => v.name.toLowerCase().includes(query));
    }

    onVendorSearchInput(ev) {
        this.state.vendorSearch = ev.target.value;
        this.state.showVendorDropdown = true;
        // Si borra el texto, limpiar selección
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
        // Delay para permitir click en dropdown
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
        
        // Cargar OCs abiertas del proveedor
        this.state.loadingPOs = true;
        try {
            this.state.openPOs = await this.orm.call("purchase.manager.logic", "get_open_purchase_orders", [vendor.id]);
        } catch (error) {
            console.error("Error al cargar OCs:", error);
            this.state.openPOs = [];
        }
        this.state.loadingPOs = false;
    }

    clearVendorSelection() {
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.openPOs = [];
        this.state.selectedPO = null;
    }

    async onVendorChange(ev) {
        const vendorId = parseInt(ev.target.value) || null;
        this.state.selectedVendor = vendorId;
        this.state.selectedPO = null;
        
        if (vendorId) {
            this.state.loadingPOs = true;
            try {
                this.state.openPOs = await this.orm.call("purchase.manager.logic", "get_open_purchase_orders", [vendorId]);
            } catch (error) {
                console.error("Error al cargar OCs:", error);
                this.state.openPOs = [];
            }
            this.state.loadingPOs = false;
        } else {
            this.state.openPOs = [];
        }
    }

    onPOChange(ev) {
        this.state.selectedPO = parseInt(ev.target.value) || null;
    }

    async confirmPurchase() {
        if (!this.state.selectedVendor) {
            this.notification.add("Debe seleccionar un proveedor", { type: "warning" });
            return;
        }

        try {
            const resultAction = await this.orm.call(
                "purchase.manager.logic", 
                "create_purchase_orders", 
                [this.state.selectedLines, this.state.selectedVendor, this.state.selectedPO]
            );
            
            if (resultAction.error) {
                this.notification.add(resultAction.error, { type: "danger" });
                return;
            }
            
            this.notification.add("Orden de Compra procesada correctamente", { type: "success" });
            this.state.selectedLines = [];
            this.closeModal();
            this.action.doAction(resultAction);
        } catch (error) {
            this.notification.add("Error: " + error.message, { type: "danger" });
        }
    }

    async openPurchaseOrder(poId, ev) {
        ev.stopPropagation();
        if (!poId) return;
        
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'purchase.order',
            res_id: poId,
            views: [[false, 'form']],
            target: 'current',
        });
    }

    async openSaleOrder(soId, ev) {
        ev.stopPropagation();
        if (!soId) return;
        
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'sale.order',
            res_id: soId,
            views: [[false, 'form']],
            target: 'current',
        });
    }
}

ToBePurchased.template = "stock_transit_allocation.ToBePurchased"; 
registry.category("actions").add("action_to_be_purchased", ToBePurchased);