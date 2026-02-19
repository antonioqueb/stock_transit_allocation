/** @odoo-module **/
/**
 * ARCHIVO: transit_line_propagate.js
 *
 * Widget "transit_propagate_btn" - Se inserta como columna en la lista EDITABLE
 * de stock.transit.line dentro del formulario del viaje.
 *
 * Estrategia:
 *  - Es un field widget estándar de Odoo (standardFieldProps).
 *  - Se aplica a un campo char dummy "x_propagate_placeholder" (no guardado)
 *    o simplemente se pone sobre el campo allocation_status con override.
 *  - Para no necesitar campo extra, lo aplicamos a un campo dummy definido en Python.
 *  - El widget accede a props.record.model.root para navegar el form padre
 *    y encontrar todos los records del one2many line_ids.
 *  - Con el ID del record actual, calcula el índice y propaga.
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class TransitPropagateBtnField extends Component {
    static template = "stock_transit_allocation.TransitPropagateBtnField";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ loading: false });
    }

    // ─── Helpers para navegar el árbol de records ─────────────────────────────

    /**
     * Devuelve todos los records del one2many line_ids del form padre.
     * Navega: props.record (la fila actual) → model.root (el voyage form) → data.line_ids.records
     */
    _getAllLineRecords() {
        try {
            const root = this.props.record.model.root;
            const lineIds = root.data.line_ids;
            if (!lineIds || !lineIds.records) return [];
            return lineIds.records;
        } catch (e) {
            console.error("[TRANSIT PROPAGATE] Error accediendo a line_ids:", e);
            return [];
        }
    }

    /**
     * Devuelve el índice del record actual dentro de la lista de líneas.
     */
    _getCurrentIndex() {
        const allRecords = this._getAllLineRecords();
        const currentId = this.props.record.id;
        return allRecords.findIndex(r => r.id === currentId);
    }

    /**
     * Lee partner_id y order_id del record actual de forma segura.
     */
    _getSourceValues() {
        const data = this.props.record.data;
        return {
            partner_id: data.partner_id || false,
            order_id: data.order_id || false,
        };
    }

    /**
     * Verifica si hay un partner asignado en el record actual.
     */
    get hasPartner() {
        const p = this.props.record.data.partner_id;
        if (!p) return false;
        // Many2one puede venir como [id, name] o como objeto con id
        if (Array.isArray(p)) return !!p[0];
        if (typeof p === "object") return !!(p.id);
        return false;
    }

    /**
     * ¿Hay filas debajo del record actual?
     */
    get hasRowsBelow() {
        const allRecords = this._getAllLineRecords();
        const idx = this._getCurrentIndex();
        return idx >= 0 && idx < allRecords.length - 1;
    }

    // ─── Lógica de propagación ────────────────────────────────────────────────

    /**
     * Propaga partner_id + order_id al record target.
     */
    async _applyToRecord(targetRecord, src) {
        const updates = {};

        if (src.partner_id) {
            updates.partner_id = src.partner_id;
        }
        // Solo propagar order_id si también hay partner
        if (src.partner_id && src.order_id) {
            // Verificar que la orden sea del mismo partner (puede haber cambiado)
            // Por seguridad, propagamos también y dejamos que el onchange maneje la validación
            updates.order_id = src.order_id;
        } else if (src.partner_id) {
            // Hay partner pero no order, limpiar order en destino para forzar selección
            updates.order_id = false;
        }

        await targetRecord.update(updates);
    }

    /**
     * Propaga al siguiente (↓1).
     */
    async onPropagateOne(ev) {
        ev.preventDefault();
        ev.stopPropagation();

        if (!this.hasPartner || !this.hasRowsBelow) return;

        this.state.loading = true;
        try {
            const allRecords = this._getAllLineRecords();
            const idx = this._getCurrentIndex();
            if (idx < 0 || idx >= allRecords.length - 1) return;

            const src = this._getSourceValues();
            const target = allRecords[idx + 1];

            await this._applyToRecord(target, src);

            const partnerName = this._getPartnerName();
            this.notification.add(
                `✓ Propagado → fila ${idx + 2}` + (partnerName ? `: ${partnerName}` : ""),
                { type: "success", sticky: false }
            );
        } catch (e) {
            console.error("[TRANSIT PROPAGATE ONE] Error:", e);
            this.notification.add("Error al propagar: " + e.message, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    /**
     * Propaga a todos los de abajo (↓↓).
     */
    async onPropagateAll(ev) {
        ev.preventDefault();
        ev.stopPropagation();

        if (!this.hasPartner || !this.hasRowsBelow) return;

        this.state.loading = true;
        try {
            const allRecords = this._getAllLineRecords();
            const idx = this._getCurrentIndex();
            if (idx < 0 || idx >= allRecords.length - 1) return;

            const src = this._getSourceValues();
            const count = allRecords.length - idx - 1;

            for (let i = idx + 1; i < allRecords.length; i++) {
                await this._applyToRecord(allRecords[i], src);
            }

            const partnerName = this._getPartnerName();
            this.notification.add(
                `✓ Propagado a ${count} fila(s)` + (partnerName ? `: ${partnerName}` : ""),
                { type: "success", sticky: false }
            );
        } catch (e) {
            console.error("[TRANSIT PROPAGATE ALL] Error:", e);
            this.notification.add("Error al propagar: " + e.message, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    _getPartnerName() {
        const p = this.props.record.data.partner_id;
        if (!p) return "";
        if (Array.isArray(p) && p[1]) return p[1];
        if (typeof p === "object" && p.display_name) return p.display_name;
        return "";
    }
}

// Registrar el widget como field type
registry.category("fields").add("transit_propagate_btn", {
    component: TransitPropagateBtnField,
    displayName: "Transit Propagate Button",
    // No tiene tipo de campo nativo asociado — funciona como widget sobre char/boolean
    supportedTypes: ["boolean", "char"],
    extractProps: ({ attrs }) => ({}),
});