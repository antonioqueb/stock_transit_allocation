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
            expanded: {},
            selectedLines: [],
            // Modal state
            showModal: false,
            allVendors: [],
            selectedVendor: null,
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

    toggleExpand(productId) {
        this.state.expanded[productId] = !this.state.expanded[productId];
    }

    toggleSelection(lineId, ev) {
        if (ev.target.checked) {
            this.state.selectedLines.push(lineId);
        } else {
            this.state.selectedLines = this.state.selectedLines.filter(id => id !== lineId);
        }
    }

    // Abrir modal de selección
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
        this.state.selectedPO = null;
        this.state.openPOs = [];
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

    // Navegar a la OC
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

    // Navegar a la SO
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