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
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        try {
            this.state.data = await this.orm.call("purchase.manager.logic", "get_data", []);
        } catch (error) {
            console.error("Error al cargar datos:", error);
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

    async createPOs() {
        if (this.state.selectedLines.length === 0) return;
        
        try {
            const resultAction = await this.orm.call("purchase.manager.logic", "create_purchase_orders", [this.state.selectedLines]);
            this.notification.add("Órdenes de Compra generadas correctamente", { type: "success" });
            this.state.selectedLines = []; // Limpiar selección
            this.action.doAction(resultAction);
        } catch (error) {
            this.notification.add("Error al generar compras: " + error.message, { type: "danger" });
        }
    }
}

// CORRECCIÓN CRÍTICA: El nombre aquí debe coincidir con el XML
ToBePurchased.template = "stock_transit_allocation.ToBePurchased"; 
registry.category("actions").add("action_to_be_purchased", ToBePurchased);