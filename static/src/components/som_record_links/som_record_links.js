/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

/**
 * Many2many como folios CLICABLES (lista y formulario). En la lista el
 * clic abre el documento sin abrir el renglón; ctrl/cmd+clic abre en
 * otra pestaña por el href.
 */
export class SomRecordLinks extends Component {
    static template = "stock_transit_allocation.SomRecordLinks";
    static props = { ...standardFieldProps };

    setup() {
        this.action = useService("action");
    }

    get resModel() {
        return this.props.record.fields[this.props.name].relation;
    }

    get links() {
        const value = this.props.record.data[this.props.name];
        return (value?.records || []).map((rec) => ({
            id: rec.resId,
            name: rec.data.display_name || "",
            href: `/odoo/${this.resModel}/${rec.resId}`,
        }));
    }

    onClick(ev, link) {
        ev.stopPropagation();
        if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {
            return;
        }
        ev.preventDefault();
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: this.resModel,
            res_id: link.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

export const somRecordLinks = {
    component: SomRecordLinks,
    supportedTypes: ["many2many", "one2many"],
    relatedFields: [{ name: "display_name", type: "char" }],
};

registry.category("fields").add("som_record_links", somRecordLinks);
