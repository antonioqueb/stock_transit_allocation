/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

export class TransitShipProgress extends Component {
    static template = "stock_transit_allocation.TransitShipProgress";
    static props = {
        ...standardFieldProps,
    };
}

export const transitShipProgress = {
    component: TransitShipProgress,
    supportedTypes: ["integer", "float"],
};

registry.category("fields").add("transit_ship_progress", transitShipProgress);
