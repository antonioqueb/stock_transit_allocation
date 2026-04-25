/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

export class TransitShipProgress extends Component {
    static template = "stock_transit_allocation.TransitShipProgress";

    static props = {
        ...standardFieldProps,
    };

    get progress() {
        const fieldName = this.props.name;
        const rawValue = this.props.record?.data?.[fieldName];

        const value = Number(rawValue);
        if (!Number.isFinite(value)) {
            return 0;
        }

        return Math.max(0, Math.min(100, Math.round(value)));
    }

    get progressLabel() {
        return `${this.progress}%`;
    }

    get progressTitle() {
        return `Progreso estimado: ${this.progress}%`;
    }

    get barStyle() {
        return `width: ${this.progress}%;`;
    }

    get shipStyle() {
        /*
         * Se limita entre 8% y 92% para que el barco no se salga visualmente
         * del contenedor cuando el progreso es 0% o 100%.
         */
        const left = Math.max(8, Math.min(92, this.progress));
        return `left: ${left}%;`;
    }

    get isComplete() {
        return this.progress >= 100;
    }
}

export const transitShipProgress = {
    component: TransitShipProgress,
    supportedTypes: ["integer", "float"],
};

registry.category("fields").add("transit_ship_progress", transitShipProgress);