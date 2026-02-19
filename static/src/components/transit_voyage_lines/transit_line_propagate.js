/** @odoo-module **/
/**
 * ARCHIVO: transit_line_propagate.js
 * Widget OWL que reemplaza la lista estándar de stock.transit.line dentro del formulario
 * del viaje, añadiendo botones de propagación rápida de cliente/orden.
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillUpdateProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

// =============================================================================
// CAMPO DE PROPAGACIÓN - Botones que aparecen al lado del campo partner_id
// =============================================================================
export class TransitPropagateButtons extends Component {
    static template = "stock_transit_allocation.TransitPropagateButtons";
    static props = {
        rowIndex: Number,
        totalRows: Number,
        hasValue: Boolean,
        onPropagateOne: Function,
        onPropagateAll: Function,
    };

    get isLast() {
        return this.props.rowIndex >= this.props.totalRows - 1;
    }

    get hasBelow() {
        return this.props.rowIndex < this.props.totalRows - 1;
    }

    onClickOne(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.props.onPropagateOne(this.props.rowIndex);
    }

    onClickAll(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.props.onPropagateAll(this.props.rowIndex);
    }
}

// =============================================================================
// WIDGET PRINCIPAL: Tabla de líneas con propagación integrada
// Se usa como widget="transit_voyage_lines" en el field line_ids
// =============================================================================
export class TransitVoyageLinesWidget extends Component {
    static template = "stock_transit_allocation.TransitVoyageLinesWidget";
    static props = {
        ...standardFieldProps,
    };
    static components = { TransitPropagateButtons };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");

        this.state = useState({
            hoveredRow: null,
        });

        onWillUpdateProps(() => {
            // Reaccionar si cambia el record
        });
    }

    // ─── Helpers ─────────────────────────────────────────────────────────────

    get lines() {
        const field = this.props.record.data[this.props.name];
        if (!field || !field.records) return [];
        return field.records;
    }

    get totalRows() {
        return this.lines.length;
    }

    _getPartner(rec) {
        const p = rec.data.partner_id;
        if (!p) return null;
        if (Array.isArray(p) && p[0]) return { id: p[0], name: p[1] || "" };
        if (typeof p === "object" && p.id) return { id: p.id, name: p.display_name || "" };
        return null;
    }

    _getOrder(rec) {
        const o = rec.data.order_id;
        if (!o) return null;
        if (Array.isArray(o) && o[0]) return { id: o[0], name: o[1] || "" };
        if (typeof o === "object" && o.id) return { id: o.id, name: o.display_name || "" };
        return null;
    }

    _getLot(rec) {
        const l = rec.data.lot_id;
        if (!l) return null;
        if (Array.isArray(l) && l[0]) return { id: l[0], name: l[1] || "" };
        if (typeof l === "object" && l.id) return { id: l.id, name: l.display_name || "" };
        return null;
    }

    _getProduct(rec) {
        const p = rec.data.product_id;
        if (!p) return null;
        if (Array.isArray(p) && p[0]) return { id: p[0], name: p[1] || "" };
        if (typeof p === "object" && p.id) return { id: p.id, name: p.display_name || "" };
        return null;
    }

    // ─── Propagación ─────────────────────────────────────────────────────────

    /**
     * Propaga partner_id + order_id de la fila `fromIndex` a la siguiente.
     */
    async propagateOne(fromIndex) {
        const records = this.lines;
        if (fromIndex >= records.length - 1) return;

        const src = records[fromIndex];
        const tgt = records[fromIndex + 1];

        const partner = this._getPartner(src);
        const order = this._getOrder(src);

        if (!partner) {
            this.notification.add("La fila no tiene cliente asignado", { type: "warning" });
            return;
        }

        try {
            const vals = {};
            vals.partner_id = src.data.partner_id;
            if (order) vals.order_id = src.data.order_id;
            await tgt.update(vals);
            this.notification.add(
                `✓ Propagado → fila ${fromIndex + 2}: ${partner.name}`,
                { type: "success" }
            );
        } catch (e) {
            this.notification.add("Error: " + e.message, { type: "danger" });
        }
    }

    /**
     * Propaga partner_id + order_id de la fila `fromIndex` a TODAS las siguientes.
     */
    async propagateAll(fromIndex) {
        const records = this.lines;
        if (fromIndex >= records.length - 1) return;

        const src = records[fromIndex];
        const partner = this._getPartner(src);
        const order = this._getOrder(src);

        if (!partner) {
            this.notification.add("La fila no tiene cliente asignado", { type: "warning" });
            return;
        }

        const count = records.length - fromIndex - 1;
        try {
            for (let i = fromIndex + 1; i < records.length; i++) {
                const vals = {};
                vals.partner_id = src.data.partner_id;
                if (order) vals.order_id = src.data.order_id;
                await records[i].update(vals);
            }
            this.notification.add(
                `✓ Propagado a ${count} fila(s): ${partner.name}`,
                { type: "success" }
            );
        } catch (e) {
            this.notification.add("Error: " + e.message, { type: "danger" });
        }
    }

    // ─── UI Helpers ───────────────────────────────────────────────────────────

    getAllocationStatus(rec) {
        return rec.data.allocation_status || "available";
    }

    getRowClass(rec, index) {
        const status = this.getAllocationStatus(rec);
        const lot = this._getLot(rec);
        let cls = "transit-line-row";
        if (status === "reserved") cls += " row-reserved";
        else if (!lot) cls += " row-muted";
        if (this.state.hoveredRow === index) cls += " row-hovered";
        return cls;
    }

    onRowMouseEnter(index) {
        this.state.hoveredRow = index;
    }

    onRowMouseLeave() {
        this.state.hoveredRow = null;
    }

    /**
     * Formatea el m2
     */
    fmt(val) {
        if (!val) return "0.00";
        return parseFloat(val).toFixed(2);
    }

    get totalM2() {
        return this.lines.reduce((sum, r) => sum + (r.data.product_uom_qty || 0), 0).toFixed(2);
    }

    get reservedM2() {
        return this.lines
            .filter((r) => r.data.allocation_status === "reserved")
            .reduce((sum, r) => sum + (r.data.product_uom_qty || 0), 0)
            .toFixed(2);
    }
}

// Registrar como vista de campo para one2many
registry.category("fields").add("transit_voyage_lines", {
    component: TransitVoyageLinesWidget,
    displayName: "Transit Voyage Lines with Propagation",
    supportedTypes: ["one2many"],
});