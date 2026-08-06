/** @odoo-module **/

// Cargadores perezosos de los hubs de Torre de Control.
//
// Los 6 hubs pesan ~470 KB (JS+XML+SCSS) y antes viajaban en
// web.assets_backend: TODO usuario los descargaba/parseaba en CADA arranque
// del webclient aunque nunca los abriera. Ahora viven en el bundle
// 'stock_transit_allocation.assets_hubs' y se cargan la primera vez que se
// abre su acción (patrón oficial LazyComponent + registro lazy_components).

import { Component, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { LazyComponent } from "@web/core/assets";

const BUNDLE = "stock_transit_allocation.assets_hubs";

function makeLazyAction(componentName) {
    class LazyHubAction extends Component {
        static components = { LazyComponent };
        static template = xml`
            <LazyComponent bundle="'${BUNDLE}'" Component="'${componentName}'" props="props"/>
        `;
        static props = { "*": true };
    }
    LazyHubAction.displayName = `Lazy${componentName}`;
    return LazyHubAction;
}

registry.category("actions").add("action_to_be_purchased", makeLazyAction("ToBePurchased"), { force: true });
registry.category("actions").add("action_transit_allocation", makeLazyAction("TransitAllocation"), { force: true });
registry.category("actions").add("action_to_be_allocated", makeLazyAction("ToBeAllocated"), { force: true });
registry.category("actions").add("action_to_be_loading", makeLazyAction("ToBeLoading"), { force: true });
registry.category("actions").add("action_transit_sheet_custom", makeLazyAction("TransitSheetView"), { force: true });
registry.category("actions").add("action_transit_kanban_custom", makeLazyAction("TransitKanbanView"), { force: true });
registry.category("actions").add("action_supplier_links_tracking", makeLazyAction("SupplierLinksTracking"), { force: true });
registry.category("actions").add("stock_transit_allocation.som_analytics", makeLazyAction("SomAnalytics"), { force: true });
