/** @odoo-module **/
// Barra de estatus del flujo de recepción (0-7): espejo visual de la
// botonera numerada. Cada paso se enciende con el dato real del picking;
// el paso actual pulsa para guiar al operador.
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const STEPS = [
    { n: "0", label: "Comenzada",
      done: (d) => ["assigned", "done"].includes(d.state) || Boolean(d.packing_list_imported) },
    { n: "1", label: "PL abierto", done: (d) => Boolean(d.spreadsheet_id) },
    { n: "2", label: "PL procesado", done: (d) => Boolean(d.packing_list_imported) },
    { n: "3", label: "WS impreso", done: (d) => Boolean(d.worksheet_printed) },
    { n: "4", label: "WS en edición", done: (d) => Boolean(d.ws_spreadsheet_id) },
    { n: "5", label: "WS procesado", done: (d) => Boolean(d.worksheet_imported) },
    { n: "6", label: "Validada", done: (d) => d.state === "done" },
    { n: "7", label: "Etiquetas", done: (d) => (d.tc_label_print_count || 0) > 0 },
];

export class ReceptionFlowBar extends Component {
    static template = "stock_transit_allocation.ReceptionFlowBar";
    static props = { ...standardWidgetProps };

    get steps() {
        const d = this.props.record.data || {};
        const flags = STEPS.map((s) => s.done(d));
        const current = flags.findIndex((f) => !f);
        return STEPS.map((s, i) => ({
            n: s.n,
            label: s.label,
            state: flags[i] ? "done" : (i === current ? "current" : "pending"),
        }));
    }

    get complete() {
        return this.steps.every((s) => s.state === "done");
    }
}

// Odoo 19: el registry view_widgets espera { component }, no la clase
// pelona (el wrapper Widget hace widget.component en su template).
registry.category("view_widgets").add("reception_flow_bar", {
    component: ReceptionFlowBar,
});
