/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";
import { somProgress } from "@stock_transit_allocation/utils/som_progress";

export class TransitAllocation extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this._transitPopupRoot = null;
        this._transitPopupKeyHandler = null;
        this._overAssignRoot = null;
        this._overAssignKeyHandler = null;

        this.state = useState({
            sourceData: [],
            data: [],
            filteredData: [],
            loading: true,
            expanded: {},
            searchQuery: "",
            groupBy: "product", // product | sale_order | salesperson | customer | unit_type | purchase
            assigning: {},
        });

        onMounted(() => {
            this.loadData();
        });

        onWillUnmount(() => {
            this.destroyTransitPopup();
            this.destroyOverAssignPopup();
        });
    }

    // =========================================================================
    // DATA
    // =========================================================================

    async loadData() {
        this.state.loading = true;

        try {
            const rawData = await this.orm.call(
                "transit.allocation.manager.logic",
                "get_data",
                []
            );

            const hydratedData = await this._hydrateTransitDates(rawData || []);

            this.state.sourceData = hydratedData || [];
            this.state.data = this._normalizeRows(hydratedData || []);
            this.applyFilters();
        } catch (error) {
            console.error("[TransitAllocation] Error cargando datos:", error);
            this.notification.add("Error al cargar Transit Allocation", {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }


    async _hydrateTransitDates(products) {
        const ids = [];

        for (const product of products || []) {
            for (const line of product.transit_lines || []) {
                if (line.id && !ids.includes(line.id)) {
                    ids.push(line.id);
                }
            }
        }

        if (!ids.length) return products || [];

        try {
            const fresh = await this.orm.read(
                "stock.transit.line",
                ids,
                ["id", "eta", "etd", "voyage_id", "purchase_id", "vendor_id"]
            );
            const byId = {};

            for (const row of fresh || []) {
                byId[row.id] = row;
            }

            return (products || []).map((product) => ({
                ...product,
                transit_lines: (product.transit_lines || []).map((line) => {
                    const freshLine = byId[line.id] || {};
                    return {
                        ...line,
                        eta: freshLine.eta || line.eta || "",
                        etd: freshLine.etd || line.etd || "",
                        voyage_id: this._m2oId(freshLine.voyage_id) || line.voyage_id || false,
                        voyage_name: this._m2oName(freshLine.voyage_id) || line.voyage_name || "",
                        purchase_id: this._m2oId(freshLine.purchase_id) || line.purchase_id || false,
                        purchase_name: this._m2oName(freshLine.purchase_id) || line.purchase_name || "",
                        vendor: this._m2oName(freshLine.vendor_id) || line.vendor || "",
                    };
                }),
            }));
        } catch (error) {
            console.warn("[TransitAllocation] No se pudieron hidratar ETA/ETD; usando payload base:", error);
            return products || [];
        }
    }

    _normalizeRows(products) {
        const rows = [];

        for (const product of products || []) {
            const transitLines = Array.isArray(product.transit_lines) ? product.transit_lines : [];
            const soLines = Array.isArray(product.so_lines) ? product.so_lines : [];
            const meta = this._getTransitMeta(product, transitLines);

            for (const line of soLines) {
                rows.push({
                    ...line,
                    product_id: product.id || line.product_id,
                    product_name: product.name || line.product_name || "Sin producto",
                    product_type: product.product_type || line.product_type || "Material",
                    category: product.category || "Sin categoría",
                    vendor: product.vendor || meta.vendor || "SIN PROVEEDOR",
                    unit_kind: line.unit_kind || product.unit_kind || "m2",
                    unit_label: line.unit_label || product.unit_label || "m²",
                    group: product.group || "N/A",
                    transit_lines: transitLines,
                    lot_count: product.lot_count || transitLines.length || 0,
                    voyage_count: product.voyage_count || meta.voyageCount || 0,
                    voyage_names: meta.voyageNames,
                    purchase_names: meta.purchaseNames,
                    transit_summary: meta.summary,
                    transit_eta: meta.eta,
                    transit_etd: meta.etd,
                    qty_transit_available: product.qty_transit_available || meta.qtyTransit || 0,
                    qty_transit_available_m2: product.qty_transit_available_m2 || meta.qtyTransitM2 || 0,
                    qty_transit_available_pieces: product.qty_transit_available_pieces || meta.qtyTransitPieces || 0,
                    qty_available: product.qty_transit_available || meta.qtyTransit || 0,
                    qty_available_m2: product.qty_transit_available_m2 || meta.qtyTransitM2 || 0,
                    qty_available_pieces: product.qty_transit_available_pieces || meta.qtyTransitPieces || 0,
                    qty_pending: line.qty_pending || line.qty_transit_pending || 0,
                    qty_pending_m2: line.qty_pending_m2 || line.qty_transit_pending_m2 || 0,
                    qty_pending_pieces: line.qty_pending_pieces || line.qty_transit_pending_pieces || 0,
                    _productPayload: product,
                });
            }
        }

        rows.sort((a, b) => {
            if ((b.payment_percent || 0) !== (a.payment_percent || 0)) {
                return (b.payment_percent || 0) - (a.payment_percent || 0);
            }
            return String(a.product_name || "").localeCompare(String(b.product_name || ""));
        });

        return rows;
    }

    _getTransitMeta(product, transitLines) {
        const voyageNames = [];
        const purchaseNames = [];
        const vendors = [];
        const etas = [];
        const etds = [];
        let qtyTransit = 0;
        let qtyTransitM2 = 0;
        let qtyTransitPieces = 0;

        for (const line of transitLines || []) {
            const qty = Number(line.qty || line.product_uom_qty || 0);
            qtyTransit += qty;

            const unitKind = line.unit_kind || product.unit_kind || "m2";
            if (unitKind === "pieces") {
                qtyTransitPieces += Number(line.qty_pieces || qty || 0);
            } else {
                qtyTransitM2 += Number(line.qty_m2 || qty || 0);
            }

            if (line.voyage_name && !voyageNames.includes(line.voyage_name)) {
                voyageNames.push(line.voyage_name);
            }
            if (line.purchase_name && !purchaseNames.includes(line.purchase_name)) {
                purchaseNames.push(line.purchase_name);
            }
            if (line.vendor && !vendors.includes(line.vendor)) {
                vendors.push(line.vendor);
            }
            if (line.eta) {
                etas.push(line.eta);
            }
            if (line.etd) {
                etds.push(line.etd);
            }
        }

        etas.sort();
        etds.sort();

        const summaryParts = [];
        if (voyageNames.length) {
            summaryParts.push(voyageNames.slice(0, 2).join(", ") + (voyageNames.length > 2 ? ` +${voyageNames.length - 2}` : ""));
        }
        if (purchaseNames.length) {
            summaryParts.push(purchaseNames.slice(0, 2).join(", ") + (purchaseNames.length > 2 ? ` +${purchaseNames.length - 2}` : ""));
        }
        if (etas.length) {
            summaryParts.push(`ETA ${etas[0]}`);
        }

        return {
            voyageNames,
            purchaseNames,
            vendor: vendors[0] || "",
            voyageCount: voyageNames.length,
            eta: etas[0] || "",
            etd: etds[0] || "",
            qtyTransit,
            qtyTransitM2,
            qtyTransitPieces,
            summary: summaryParts.join(" · ") || "Inventario en tránsito disponible",
        };
    }

    applyFilters() {
        let rows = [...this.state.data];
        const query = (this.state.searchQuery || "").trim().toLowerCase();

        // PARTICIÓN TALLER: la demanda de material de origen para procesos
        // de taller SOLO se ve bajo el filtro "Taller" (y ahí se ve solo
        // eso) — así el tablero comercial no se ensucia.
        const workshopMode = this.state.groupBy === "workshop";
        rows = rows.filter((line) => workshopMode ? !!line.is_workshop : !line.is_workshop);

        if (query) {
            rows = rows.filter((line) => {
                const haystack = [
                    line.so_name || "",
                    line.customer || "",
                    line.product_name || "",
                    line.salesperson || "",
                    line.description || "",
                    line.product_type || "",
                    line.unit_label || "",
                    line.vendor || "",
                    line.transit_summary || "",
                    (line.voyage_names || []).join(" "),
                    (line.purchase_names || []).join(" "),
                ].join(" ").toLowerCase();

                return haystack.includes(query);
            });
        }

        if (this.state.groupBy === "product") {
            this.state.filteredData = this._groupByProduct(rows);
        } else if (this.state.groupBy === "sale_order") {
            this.state.filteredData = this._groupBySaleOrder(rows);
        } else if (this.state.groupBy === "salesperson") {
            this.state.filteredData = this._groupBySalesperson(rows);
        } else if (this.state.groupBy === "customer") {
            this.state.filteredData = this._groupByCustomer(rows);
        } else if (this.state.groupBy === "unit_type") {
            this.state.filteredData = this._groupByUnitType(rows);
        } else if (this.state.groupBy === "purchase") {
            this.state.filteredData = this._groupByPurchase(rows);
        } else if (this.state.groupBy === "workshop") {
            this.state.filteredData = this._groupByWorkshop(rows);
        }
    }

    _groupByWorkshop(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.so_id || 0;
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `workshop_so_${key}`,
                    label: `${line.so_name || "Sin SO"} · TALLER`,
                    sublabel: line.customer || "Sin cliente",
                });
            }
            this._addLineToGroup(map[key], line);
        }
        return this._sortGroups(Object.values(map));
    }

    _makeGroup({ id, key, label, sublabel }) {
        return {
            id,
            key,
            label,
            sublabel,
            lines: [],
            qty_requested: 0,
            qty_ordered: 0,
            qty_assigned: 0,
            qty_pending: 0,
            qty_available: 0,
            qty_requested_m2: 0,
            qty_requested_pieces: 0,
            qty_ordered_m2: 0,
            qty_ordered_pieces: 0,
            qty_assigned_m2: 0,
            qty_assigned_pieces: 0,
            qty_pending_m2: 0,
            qty_pending_pieces: 0,
            qty_available_m2: 0,
            qty_available_pieces: 0,
            max_days_unassigned: 0,
            max_payment_percent: 0,
            voyage_count: 0,
            lot_count: 0,
            voyage_names: [],
            purchase_names: [],
        };
    }

    _addLineToGroup(group, line, { availableMode = "sum" } = {}) {
        group.lines.push(line);

        group.qty_requested += line.qty_requested || line.qty_ordered || 0;
        group.qty_ordered += line.qty_ordered || line.qty_requested || 0;
        group.qty_assigned += line.qty_assigned || 0;
        group.qty_pending += line.qty_pending || 0;

        if (availableMode === "max") {
            group.qty_available = Math.max(group.qty_available, line.qty_available || 0);
            group.qty_available_m2 = Math.max(group.qty_available_m2, line.qty_available_m2 || 0);
            group.qty_available_pieces = Math.max(group.qty_available_pieces, line.qty_available_pieces || 0);
        } else {
            group.qty_available += line.qty_available || 0;
            group.qty_available_m2 += line.qty_available_m2 || 0;
            group.qty_available_pieces += line.qty_available_pieces || 0;
        }

        group.qty_requested_m2 += line.qty_requested_m2 || line.qty_ordered_m2 || 0;
        group.qty_requested_pieces += line.qty_requested_pieces || line.qty_ordered_pieces || 0;
        group.qty_ordered_m2 += line.qty_ordered_m2 || line.qty_requested_m2 || 0;
        group.qty_ordered_pieces += line.qty_ordered_pieces || line.qty_requested_pieces || 0;
        group.qty_assigned_m2 += line.qty_assigned_m2 || 0;
        group.qty_assigned_pieces += line.qty_assigned_pieces || 0;
        group.qty_pending_m2 += line.qty_pending_m2 || 0;
        group.qty_pending_pieces += line.qty_pending_pieces || 0;

        group.max_days_unassigned = Math.max(group.max_days_unassigned, line.days_unassigned || 0);
        group.max_payment_percent = Math.max(group.max_payment_percent, line.payment_percent || 0);

        group.lot_count = Math.max(group.lot_count, line.lot_count || 0);
        group.voyage_count = Math.max(group.voyage_count, line.voyage_count || 0);

        for (const name of line.voyage_names || []) {
            if (name && !group.voyage_names.includes(name)) group.voyage_names.push(name);
        }
        for (const name of line.purchase_names || []) {
            if (name && !group.purchase_names.includes(name)) group.purchase_names.push(name);
        }
    }

    _sortGroups(groups) {
        return groups.sort((a, b) => {
            if (b.max_payment_percent !== a.max_payment_percent) {
                return b.max_payment_percent - a.max_payment_percent;
            }
            if (b.max_days_unassigned !== a.max_days_unassigned) {
                return b.max_days_unassigned - a.max_days_unassigned;
            }
            return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    _groupByProduct(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.product_id || 0;
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `product_${key}`,
                    label: line.product_name || "Sin producto",
                    sublabel: `${line.product_type || "Material"} · Inventario en tránsito`,
                });
            }
            this._addLineToGroup(map[key], line, { availableMode: "max" });
        }
        return this._sortGroups(Object.values(map));
    }

    _groupBySaleOrder(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.so_id || 0;
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `so_${key}`,
                    label: line.so_name || "Sin SO",
                    sublabel: line.customer || "Sin cliente",
                });
            }
            this._addLineToGroup(map[key], line);
        }
        return this._sortGroups(Object.values(map));
    }

    _groupBySalesperson(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.salesperson || "Sin vendedor";
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `salesperson_${key}`,
                    label: key,
                    sublabel: "Vendedor",
                });
            }
            this._addLineToGroup(map[key], line);
        }
        return this._sortGroups(Object.values(map));
    }

    _groupByPurchase(rows) {
        const map = {};
        for (const line of rows) {
            const names = (line.purchase_names || []).filter(Boolean);
            const keys = names.length ? names : ["Sin OC"];
            for (const key of keys) {
                if (!map[key]) {
                    map[key] = this._makeGroup({
                        id: key,
                        key: `purchase_${key}`,
                        label: key,
                        sublabel: "Orden de compra",
                    });
                }
                this._addLineToGroup(map[key], line);
            }
        }
        return this._sortGroups(Object.values(map));
    }

    _groupByCustomer(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.customer_id || line.customer || "Sin cliente";
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `customer_${key}`,
                    label: line.customer || "Sin cliente",
                    sublabel: "Cliente",
                });
            }
            this._addLineToGroup(map[key], line);
        }
        return this._sortGroups(Object.values(map));
    }

    _groupByUnitType(rows) {
        const map = {};
        for (const line of rows) {
            const key = line.unit_kind || "m2";
            const label = key === "pieces" ? "Piezas" : "Metros cuadrados";
            const sublabel = key === "pieces" ? "Productos vendidos por pieza" : "Productos vendidos por m²";
            if (!map[key]) {
                map[key] = this._makeGroup({
                    id: key,
                    key: `unit_${key}`,
                    label,
                    sublabel,
                });
            }
            this._addLineToGroup(map[key], line);
        }
        return this._sortGroups(Object.values(map));
    }

    // =========================================================================
    // HANDLERS UI
    // =========================================================================

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this.applyFilters();
    }

    clearSearch() {
        this.state.searchQuery = "";
        this.applyFilters();
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
        this.state.expanded = {};
        this.applyFilters();
    }

    toggleExpand(key) {
        this.state.expanded[key] = !this.state.expanded[key];
    }

    isExpanded(key) {
        return !!this.state.expanded[key];
    }

    async refresh() {
        await this.loadData();
        this.notification.add("Transit Allocation actualizado", {
            type: "success",
            sticky: false,
        });
    }

    closeLineDemand(line, ev) {
        if (ev) ev.stopPropagation();
        const pending = line.qty_pending || 0;
        this.dialog.add(ConfirmationDialog, {
            title: "Cerrar pico",
            body:
                `${line.product_name} (${line.so_name}): el Solicitado del pedido ` +
                `se ajustará a lo ASIGNADO (baja el pico pendiente de ` +
                `${pending.toFixed(2)} ${line.unit_label || ""}) y esta demanda ` +
                `desaparecerá de Transit Allocation. ¿Confirmas?`,
            confirmLabel: "Cerrar pico",
            cancelLabel: "Cancelar",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "transit.allocation.manager.logic",
                        "close_line_demand",
                        [line.id]
                    );
                    this.notification.add(
                        `Pico cerrado: solicitado ${result.qty_before.toFixed(2)} → ${result.qty_after.toFixed(2)}`,
                        { type: "success" }
                    );
                    await this.loadData();
                } catch (e) {
                    console.error("[TransitAllocation] close_line_demand", e);
                    this.notification.add(
                        (e.data && e.data.message) || "No se pudo cerrar el pico.",
                        { type: "danger" }
                    );
                }
            },
            cancel: () => {},
        });
    }

    async openSaleOrder(soId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (!soId) return;

        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: soId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openPurchaseOrder(poId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (!poId) return;

        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: poId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openVoyage(voyageId, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (!voyageId) return;

        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            res_id: voyageId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // =========================================================================
    // POPUP DE ASIGNACIÓN EN TRÁNSITO
    // =========================================================================

    async openTransitPopup(line, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!line || !line.id || !line.product_id) {
            this.notification.add("No se encontró la línea o producto para asignar.", {
                type: "warning",
            });
            return;
        }

        this.destroyTransitPopup();
        this.state.assigning[line.id] = true;

        try {
            const transitLines = await this._getFreshTransitLines(line);

            if (!transitLines.length) {
                this.notification.add("No hay lotes en tránsito disponibles para esta línea.", {
                    type: "warning",
                });
                return;
            }

            this._transitPopupRoot = document.createElement("div");
            this._transitPopupRoot.className = "stone-popup-root stone-transit-popup-root";
            document.body.appendChild(this._transitPopupRoot);

            this._renderTransitPopupDOM({
                line,
                transitLines,
                productId: line.product_id,
                productName: (line.product_name || "") + (
                    line.is_workshop
                        ? ` · TALLER ${line.workshop_process || ""} → ${line.product_final_name || ""}`
                        : ""
                ),
                soName: line.so_name || "",
                customer: line.customer || "",
                qtyRequested: line.qty_ordered || line.qty_requested || 0,
                qtyAssigned: line.qty_assigned || 0,
                qtyPending: line.qty_pending || 0,
                unitLabel: line.unit_label || "m²",
            });
        } catch (error) {
            console.error("[TransitAllocation] Error abriendo popup de asignación:", error);
            this.notification.add(
                "Error al abrir asignación en tránsito: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.assigning[line.id] = false;
        }
    }

    async _getFreshTransitLines(line) {
        const rawLines = Array.isArray(line.transit_lines) ? line.transit_lines : [];
        const ids = rawLines.map((item) => item.id).filter(Boolean);
        const rawById = {};

        for (const item of rawLines) {
            if (item.id) rawById[item.id] = item;
        }

        if (!ids.length) return [];

        try {
            const fresh = await this.orm.read(
                "stock.transit.line",
                ids,
                [
                    "id",
                    "product_id",
                    "lot_id",
                    "product_uom_qty",
                    "container_number",
                    "voyage_id",
                    "purchase_id",
                    "vendor_id",
                    "eta",
                    "etd",
                    "allocation_status",
                    "partner_id",
                    "order_id",
                    "x_bloque",
                    "x_atado",
                    "x_grosor",
                    "x_alto",
                    "x_ancho",
                    "x_tipo",
                ]
            );

            return fresh
                .filter((item) => item.allocation_status === "available" && !item.partner_id && !item.order_id)
                .map((item) => this._normalizeTransitLineRow(item, rawById[item.id] || {}, line));
        } catch (error) {
            console.warn("[TransitAllocation] No se pudo refrescar stock.transit.line; usando payload del hub:", error);
            return rawLines.map((item) => this._normalizeTransitLineRow(item, item, line));
        }
    }

    _normalizeTransitLineRow(item, fallback, saleLine) {
        const qty = Number(item.qty || item.product_uom_qty || fallback.qty || fallback.product_uom_qty || 0);
        const unitKind = saleLine.unit_kind || fallback.unit_kind || "m2";
        const tipo = String(item.x_tipo || fallback.x_tipo || "").toLowerCase();

        return {
            tipo,
            // Solo formatos/piezas admiten consumo parcial; las placas van enteras.
            fractionable: tipo === "formato" || tipo === "pieza",
            id: item.id || fallback.id,
            product_id: this._m2oId(item.product_id) || fallback.product_id || saleLine.product_id,
            product_name: this._m2oName(item.product_id) || fallback.product_name || saleLine.product_name || "",
            lot_id: this._m2oId(item.lot_id) || fallback.lot_id || false,
            lot_name: this._m2oName(item.lot_id) || fallback.lot_name || "",
            qty,
            qty_m2: unitKind === "pieces" ? 0 : qty,
            qty_pieces: unitKind === "pieces" ? qty : 0,
            unit_kind: unitKind,
            unit_label: saleLine.unit_label || fallback.unit_label || (unitKind === "pieces" ? "pzas" : "m²"),
            container_number: item.container_number || fallback.container_number || "",
            voyage_id: this._m2oId(item.voyage_id) || fallback.voyage_id || false,
            voyage_name: this._m2oName(item.voyage_id) || fallback.voyage_name || "",
            purchase_id: this._m2oId(item.purchase_id) || fallback.purchase_id || false,
            purchase_name: this._m2oName(item.purchase_id) || fallback.purchase_name || "",
            vendor: this._m2oName(item.vendor_id) || fallback.vendor || "",
            eta: item.eta || fallback.eta || "",
            etd: item.etd || fallback.etd || "",
            x_bloque: item.x_bloque || fallback.x_bloque || "-",
            x_atado: item.x_atado || fallback.x_atado || "-",
            x_grosor: item.x_grosor || fallback.x_grosor || "-",
            x_alto: item.x_alto || fallback.x_alto || 0,
            x_ancho: item.x_ancho || fallback.x_ancho || 0,
            x_color: fallback.x_color || "-",
            location: fallback.location || "Tránsito",
        };
    }

    _m2oId(value) {
        return Array.isArray(value) ? value[0] : value || false;
    }

    _m2oName(value) {
        return Array.isArray(value) ? value[1] : "";
    }

    destroyTransitPopup() {
        if (this._transitPopupKeyHandler) {
            document.removeEventListener("keydown", this._transitPopupKeyHandler);
            this._transitPopupKeyHandler = null;
        }
        if (this._transitPopupRoot) {
            this._transitPopupRoot.remove();
            this._transitPopupRoot = null;
        }
    }

    destroyOverAssignPopup() {
        if (this._overAssignKeyHandler) {
            document.removeEventListener("keydown", this._overAssignKeyHandler);
            this._overAssignKeyHandler = null;
        }
        if (this._overAssignRoot) {
            this._overAssignRoot.remove();
            this._overAssignRoot = null;
        }
    }

    _renderTransitPopupDOM(config) {
        const root = this._transitPopupRoot;
        const state = {
            allLines: config.transitLines || [],
            filteredLines: config.transitLines || [],
            selectedIds: new Set(),
            // Cantidad parcial elegida por línea (solo formatos/piezas).
            // { [transitLineId]: qty }. Las placas no se incluyen: van enteras.
            partialQty: {},
            saving: false,
            filters: {
                lot_name: "",
                bloque: "",
                atado: "",
                voyage: "",
                purchase: "",
                container: "",
            },
        };

        // Valores DISTINTOS para los dropdowns de filtro (contenedor,
        // bloque, atado): se eligen de lista, no se teclean.
        const distinctVals = (key) => Array.from(new Set(
            state.allLines.map((l) => String(l[key] || "").trim()).filter(Boolean)
        )).sort((a, b) => a.localeCompare(b, "es"));
        const optionsHtml = (vals) => vals.map((v) =>
            `<option value="${this._escapeHtml(v)}">${this._escapeHtml(v)}</option>`
        ).join("");
        const containerOpts = optionsHtml(distinctVals("container_number"));
        // Chips de contenedor con su total (m²/piezas): clic = filtrar
        const containerChips = (() => {
            const agg = {};
            for (const l of state.allLines) {
                const c = String(l.container_number || "").trim();
                if (!c) continue;
                if (!agg[c]) agg[c] = { qty: 0, unit: l.unit_label || "" };
                agg[c].qty += Number(l.qty || 0);
            }
            return Object.entries(agg).sort((a, b) => a[0].localeCompare(b[0], "es"));
        })();
        const chipsHtml = containerChips.map(([c, info]) =>
            `<button type="button" class="stone-cont-chip" data-chip-container="${this._escapeHtml(c)}">
                <i class="fa fa-cube me-1"></i>${this._escapeHtml(c)}
                <span class="stone-cont-chip-qty">${this._fmtPlain(info.qty)} ${this._escapeHtml(info.unit)}</span>
            </button>`).join("");
        const bloqueOpts = optionsHtml(distinctVals("x_bloque"));
        const atadoOpts = optionsHtml(distinctVals("x_atado"));

        // Cantidad efectiva de una línea: la parcial elegida si es fraccionable
        // y hay un valor capturado; de lo contrario, la cantidad completa.
        const getEffectiveQty = (line) => {
            if (line.fractionable && state.partialQty[line.id] !== undefined) {
                return Number(state.partialQty[line.id]) || 0;
            }
            return Number(line.qty || 0);
        };

        root.innerHTML = `
            <div class="stone-popup-overlay" id="stone-overlay">
                <div class="stone-popup-container stone-transit-popup-container">
                    <div class="stone-popup-header">
                        <div class="stone-popup-title">
                            <i class="fa fa-ship me-2"></i>
                            Asignar tránsito
                            <span class="stone-popup-subtitle">— ${this._escapeHtml(config.productName)}</span>
                        </div>

                        <div class="stone-popup-header-actions">
                            <span class="stone-badge-selected" title="${this._escapeHtml(config.soName)}">
                                <i class="fa fa-file-text-o me-1"></i>${this._escapeHtml(config.soName)}
                            </span>
                            <span class="stone-badge-selected" title="${this._escapeHtml(config.customer)}">
                                <i class="fa fa-user me-1"></i>${this._escapeHtml(config.customer)}
                            </span>
                            <span class="stone-badge-requested">
                                <i class="fa fa-bullseye me-1"></i>
                                Pendiente <span>${this._fmtPlain(config.qtyPending)}</span> ${this._escapeHtml(config.unitLabel)}
                            </span>
                            <span class="stone-badge-selected">
                                <i class="fa fa-check-circle me-1"></i>
                                <span id="sp-badge-count">0</span> selec.
                            </span>
                            <span class="stone-badge-qty-total">
                                <i class="fa fa-balance-scale me-1"></i>
                                <span id="sp-badge-qty">0.00</span>
                                <span id="sp-badge-unit">${this._escapeHtml(config.unitLabel)}</span>
                            </span>
                            <button class="stone-btn stone-btn-ghost" id="sp-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="stone-popup-allocation-summary" id="sp-allocation-summary">
                        <div class="stone-allocation-card stone-allocation-target">
                            <span class="stone-allocation-label">Solicitado</span>
                            <strong>${this._fmtPlain(config.qtyRequested)} ${this._escapeHtml(config.unitLabel)}</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-selected">
                            <span class="stone-allocation-label">Ya asignado</span>
                            <strong>${this._fmtPlain(config.qtyAssigned)} ${this._escapeHtml(config.unitLabel)}</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-remaining">
                            <span class="stone-allocation-label">Pendiente</span>
                            <strong>${this._fmtPlain(config.qtyPending)} ${this._escapeHtml(config.unitLabel)}</strong>
                        </div>
                        <div class="stone-allocation-progress-box">
                            <div class="stone-allocation-progress-head">
                                <span id="sp-allocation-progress-text">0.00 de ${this._fmtPlain(config.qtyPending)} ${this._escapeHtml(config.unitLabel)}</span>
                                <strong id="sp-allocation-progress-label">0%</strong>
                            </div>
                            <div class="stone-allocation-progress-track">
                                <div class="stone-allocation-progress-fill" id="sp-allocation-progress-fill"></div>
                            </div>
                        </div>
                    </div>

                    <div class="stone-popup-filters">
                        <div class="stone-filter-group">
                            <label>Lote</label>
                            <input type="text" class="stone-filter-input" id="sf-lot" placeholder="Buscar lote..."/>
                        </div>
                        <div class="stone-filter-group">
                            <label>Bloque</label>
                            <select class="stone-filter-input" id="sf-bloque">
                                <option value="">Todos</option>${bloqueOpts}
                            </select>
                        </div>
                        <div class="stone-filter-group">
                            <label>Atado</label>
                            <select class="stone-filter-input" id="sf-atado">
                                <option value="">Todos</option>${atadoOpts}
                            </select>
                        </div>
                        <div class="stone-filter-group">
                            <label>Embarque</label>
                            <input type="text" class="stone-filter-input" id="sf-voyage" placeholder="Embarque..."/>
                        </div>
                        <div class="stone-filter-group">
                            <label>Contenedor</label>
                            <select class="stone-filter-input" id="sf-container">
                                <option value="">Todos</option>${containerOpts}
                            </select>
                        </div>
                        <div class="stone-filter-group">
                            <label>OC</label>
                            <input type="text" class="stone-filter-input" id="sf-purchase" placeholder="OC..."/>
                        </div>

                        <div class="stone-filter-actions">
                            <button class="stone-btn stone-btn-select-all" id="sp-select-all" title="Seleccionar visibles">
                                <i class="fa fa-check-square-o me-1"></i> Todo
                            </button>
                            <button class="stone-btn stone-btn-clear-all" id="sp-clear-all" title="Borrar selección">
                                <i class="fa fa-square-o me-1"></i> Limpiar
                            </button>
                        </div>

                        <div class="stone-filter-spacer"></div>
                        <div class="stone-filter-stats">
                            <span id="sp-stat" class="stone-filter-stat-count ms-2">${state.filteredLines.length} lotes</span>
                        </div>
                    </div>

                    ${chipsHtml ? `<div class="stone-cont-chips" id="sp-cont-chips">${chipsHtml}</div>` : ""}
                    <div class="stone-popup-body" id="sp-body"></div>

                    <div class="stone-popup-footer">
                        <span class="stone-footer-info" id="sp-footer-info">—</span>
                        <div class="stone-footer-qty-summary" id="sp-footer-qty">
                            <span id="sp-footer-qty-text">0.00 ${this._escapeHtml(config.unitLabel)}</span>
                        </div>
                        <div class="stone-footer-actions">
                            <button class="stone-btn stone-btn-outline" id="sp-cancel">Cancelar</button>
                            <button class="stone-btn stone-btn-primary-dark" id="sp-open-order">
                                <i class="fa fa-external-link me-1"></i> Abrir pedido
                            </button>
                            <button class="stone-btn stone-btn-primary-dark" id="sp-confirm-bottom">
                                <i class="fa fa-check me-1"></i> Guardar
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const overlay = root.querySelector("#stone-overlay");
        const body = root.querySelector("#sp-body");
        const stat = root.querySelector("#sp-stat");
        const footerInfo = root.querySelector("#sp-footer-info");
        const badgeCount = root.querySelector("#sp-badge-count");
        const badgeQty = root.querySelector("#sp-badge-qty");
        const badgeUnit = root.querySelector("#sp-badge-unit");
        const footerQtyText = root.querySelector("#sp-footer-qty-text");
        const allocationSummary = root.querySelector("#sp-allocation-summary");
        const allocationProgressText = root.querySelector("#sp-allocation-progress-text");
        const allocationProgressLabel = root.querySelector("#sp-allocation-progress-label");
        const allocationProgressFill = root.querySelector("#sp-allocation-progress-fill");

        const computeSelectedTotals = () => {
            let total = 0;
            for (const line of state.allLines) {
                if (state.selectedIds.has(line.id)) {
                    total += getEffectiveQty(line);
                }
            }
            return total;
        };

        const updateSelectionDisplay = () => {
            const selectedQty = computeSelectedTotals();
            const pendingQty = Number(config.qtyPending || 0);
            const percent = pendingQty > 0 ? (selectedQty / pendingQty) * 100 : 0;
            const barPercent = Math.max(0, Math.min(percent, 100));

            badgeCount.textContent = state.selectedIds.size;
            badgeQty.textContent = this._fmtPlain(selectedQty);
            badgeUnit.textContent = config.unitLabel || "";
            footerQtyText.textContent = `${this._fmtPlain(selectedQty)} ${config.unitLabel || ""}`;

            allocationProgressText.textContent = `${this._fmtPlain(selectedQty)} de ${this._fmtPlain(pendingQty)} ${config.unitLabel || ""}`;
            allocationProgressLabel.textContent = `${this._fmtPct(percent)}%`;
            allocationProgressFill.style.width = `${barPercent}%`;

            allocationSummary.classList.toggle("is-under", pendingQty > 0 && percent < 99.995);
            allocationSummary.classList.toggle("is-ok", pendingQty > 0 && percent >= 99.995 && percent <= 100.005);
            allocationSummary.classList.toggle("is-over", pendingQty > 0 && percent > 100.005);
        };

        const applyPopupFilters = () => {
            const lot = state.filters.lot_name.toLowerCase();
            const bloque = state.filters.bloque.toLowerCase();
            const atado = state.filters.atado.toLowerCase();
            const voyage = state.filters.voyage.toLowerCase();
            const purchase = state.filters.purchase.toLowerCase();
            const container = (state.filters.container || "").toLowerCase();

            state.filteredLines = state.allLines.filter((line) => {
                if (lot && !String(line.lot_name || "").toLowerCase().includes(lot)) return false;
                if (bloque && String(line.x_bloque || "").trim().toLowerCase() !== bloque) return false;
                if (atado && String(line.x_atado || "").trim().toLowerCase() !== atado) return false;
                if (voyage && !String(line.voyage_name || "").toLowerCase().includes(voyage)) return false;
                if (purchase && !String(line.purchase_name || "").toLowerCase().includes(purchase)) return false;
                if (container && String(line.container_number || "").trim().toLowerCase() !== container) return false;
                return true;
            });

            renderTable();
        };

        const renderTable = () => {
            if (!state.filteredLines.length) {
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-inbox fa-3x text-muted"></i>
                        <div class="stone-empty-text mt-2">No hay lotes en tránsito con estos filtros</div>
                    </div>
                `;
                stat.textContent = "0 lotes";
                footerInfo.innerHTML = "Sin resultados visibles";
                updateSelectionDisplay();
                return;
            }

            let rows = "";

            for (const line of state.filteredLines) {
                const selected = state.selectedIds.has(line.id);

                // Celda "A asignar": formatos/piezas seleccionados muestran un
                // input para elegir la parcialidad (clamp al disponible); las
                // placas y los no seleccionados muestran el lote completo o "—".
                let assignCell;
                if (line.fractionable) {
                    if (selected) {
                        const fullQty = Number(line.qty || 0);
                        const chosen = state.partialQty[line.id] !== undefined
                            ? state.partialQty[line.id]
                            : fullQty;
                        const step = line.tipo === "pieza" ? "1" : "0.01";
                        assignCell = `
                            <div class="stone-transit-qty-wrap">
                                <input type="number"
                                       class="stone-transit-qty-input"
                                       data-qty-line-id="${line.id}"
                                       value="${chosen}"
                                       min="0"
                                       step="${step}"
                                       max="${fullQty}"/>
                                <span class="stone-transit-qty-unit">/ ${this._fmtPlain(fullQty)} ${this._escapeHtml(line.unit_label || "")}</span>
                            </div>
                        `;
                    } else {
                        assignCell = `<span class="text-muted">—</span>`;
                    }
                } else {
                    assignCell = `<span class="text-muted">Completo</span>`;
                }

                rows += `
                    <tr class="${selected ? "row-sel" : ""}" data-transit-line-id="${line.id}">
                        <td class="col-chk">
                            <div class="stone-chkbox ${selected ? "checked" : ""}">
                                ${selected ? '<i class="fa fa-check"></i>' : ""}
                            </div>
                        </td>
                        <td class="cell-lot">
                            <div class="stone-transit-lot-name">${this._escapeHtml(line.lot_name || "-")}</div>
                            <div class="stone-transit-lot-sub">${this._escapeHtml(line.container_number || "Sin contenedor")}</div>
                        </td>
                        <td>${this._escapeHtml(line.x_bloque || "-")}</td>
                        <td>${this._escapeHtml(line.x_atado || "-")}</td>
                        <td class="col-num">${this._fmtDim(line.x_alto)}</td>
                        <td class="col-num">${this._fmtDim(line.x_ancho)}</td>
                        <td class="col-num">${this._escapeHtml(line.x_grosor || "-")}</td>
                        <td class="col-num fw-semibold">${this._fmtPlain(line.qty)} ${this._escapeHtml(line.unit_label || "")}</td>
                        <td class="col-num">${assignCell}</td>
                        <td>
                            <button type="button" class="btn btn-link p-0 stone-transit-link" data-open-voyage="${line.voyage_id || ""}">
                                <i class="fa fa-external-link"></i>
                                ${this._escapeHtml(line.voyage_name || "-")}
                            </button>
                        </td>
                        <td>
                            <button type="button" class="btn btn-link p-0 stone-transit-link" data-open-po="${line.purchase_id || ""}">
                                <i class="fa fa-external-link"></i>
                                ${this._escapeHtml(line.purchase_name || "-")}
                            </button>
                        </td>
                        <td>${this._escapeHtml(line.vendor || "-")}</td>
                        <td class="text-center">${this._escapeHtml(line.etd || "N/A")}</td>
                        <td class="text-center">${this._escapeHtml(line.eta || "N/A")}</td>
                    </tr>
                `;
            }

            body.innerHTML = `
                <table class="stone-popup-table stone-transit-popup-table">
                    <thead>
                        <tr>
                            <th class="col-chk">✓</th>
                            <th>Lote / contenedor</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="col-num">Alto</th>
                            <th class="col-num">Largo</th>
                            <th class="col-num">Esp.</th>
                            <th class="col-num">Disponible</th>
                            <th class="col-num">A asignar</th>
                            <th>Embarque</th>
                            <th>OC</th>
                            <th>Proveedor</th>
                            <th class="text-center">ETD</th>
                            <th class="text-center">ETA</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            `;

            body.querySelectorAll("tr[data-transit-line-id]").forEach((tr) => {
                tr.addEventListener("click", (ev) => {
                    if (ev.target.closest("[data-open-voyage]")) return;
                    if (ev.target.closest("[data-open-po]")) return;

                    const id = parseInt(tr.dataset.transitLineId, 10);
                    if (!id) return;

                    if (state.selectedIds.has(id)) {
                        state.selectedIds.delete(id);
                    } else {
                        state.selectedIds.add(id);
                    }

                    renderTable();
                });
            });

            // Inputs de parcialidad (formatos/piezas): editar la cantidad NO debe
            // alternar la selección de la fila, por eso se detiene la propagación.
            body.querySelectorAll(".stone-transit-qty-input").forEach((input) => {
                const stop = (ev) => ev.stopPropagation();
                input.addEventListener("click", stop);
                input.addEventListener("mousedown", stop);

                const lineId = parseInt(input.dataset.qtyLineId, 10);
                const lineObj = state.allLines.find((l) => l.id === lineId);
                const fullQty = lineObj ? Number(lineObj.qty || 0) : Infinity;

                const apply = (ev, normalize) => {
                    ev.stopPropagation();
                    let val = parseFloat(input.value);
                    if (!isFinite(val) || val < 0) val = 0;
                    if (isFinite(fullQty) && val > fullQty) val = fullQty;
                    state.partialQty[lineId] = val;
                    if (normalize) input.value = val;
                    updateSelectionDisplay();
                };

                input.addEventListener("input", (ev) => apply(ev, false));
                input.addEventListener("change", (ev) => apply(ev, true));
            });

            body.querySelectorAll("[data-open-voyage]").forEach((btn) => {
                btn.addEventListener("click", (ev) => {
                    const id = parseInt(btn.dataset.openVoyage, 10);
                    if (id) this.openVoyage(id, ev);
                });
            });

            body.querySelectorAll("[data-open-po]").forEach((btn) => {
                btn.addEventListener("click", (ev) => {
                    const id = parseInt(btn.dataset.openPo, 10);
                    if (id) this.openPurchaseOrder(id, ev);
                });
            });

            stat.textContent = `${state.filteredLines.length} lotes`;
            footerInfo.innerHTML = `<strong>${state.filteredLines.length}</strong> de <strong>${state.allLines.length}</strong> lotes visibles`;
            updateSelectionDisplay();
        };

        const doSelectAll = () => {
            for (const line of state.filteredLines) {
                state.selectedIds.add(line.id);
            }
            renderTable();
        };

        const doClearAll = () => {
            state.selectedIds.clear();
            renderTable();
        };

        const doConfirm = async (overAction = false, overReason = false) => {
            if (state.saving) return false;

            const selectedIds = Array.from(state.selectedIds);
            if (!selectedIds.length) {
                this.notification.add("Seleccione al menos un lote en tránsito.", {
                    type: "warning",
                });
                return false;
            }

            const selectedQty = computeSelectedTotals();
            const projectedAssigned = Number(config.qtyAssigned || 0) + selectedQty;
            const requestedQty = Number(config.qtyRequested || 0);
            const overQty = requestedQty > 0 ? projectedAssigned - requestedQty : 0;

            // TALLER: sin decisión de excedente — el corte consume más
            // material base que lo vendido y eso es normal.
            if (!config.line.is_workshop && overQty > 0.0001 && !overAction) {
                const decision = await this.requestOverAssignmentDecision(overQty, config.unitLabel || "");
                if (!decision) return false;
                return doConfirm(decision.action, decision.reason);
            }

            // Parcialidades elegidas para formatos/piezas seleccionados. El
            // backend parte la línea: asigna esta cantidad y deja el saldo.
            const partialByLine = {};
            for (const line of state.allLines) {
                if (!state.selectedIds.has(line.id) || !line.fractionable) continue;
                partialByLine[line.id] = getEffectiveQty(line);
            }

            state.saving = true;

            const progress = somProgress({
                title: "Guardando asignación en tránsito",
                subtitle: `${config.soName || ""} — ${config.productName || ""}`,
                steps: [
                    "Validando selección",
                    "Asignando material del embarque",
                    "Actualizando el tablero",
                ],
            });

            try {
                progress.step(1);
                // Demanda de TALLER: método propio — reserva el material de
                // origen y crea las selecciones de taller en la venta, sin
                // STONE SYNC ni ratchet contra el producto vendido.
                const result = config.line.is_workshop
                    ? await this.orm.call(
                        "transit.allocation.manager.logic",
                        "assign_transit_lines_workshop",
                        [
                            selectedIds,
                            config.line.id,
                            "Asignación a taller desde Transit Allocation.",
                            partialByLine,
                        ]
                    )
                    : await this.orm.call(
                        "transit.allocation.manager.logic",
                        "assign_transit_lines",
                        [
                            selectedIds,
                            config.line.id,
                            "Asignación operativa desde Transit Allocation.",
                            overAction,
                            overReason,
                            partialByLine,
                        ]
                    );

                if (result && result.need_over_assignment_decision) {
                    progress.destroy();
                    // Liberar el candado ANTES de reintentar: el doConfirm
                    // recursivo checa state.saving y con true retornaba en
                    // silencio — el usuario daba "Continuar" y no pasaba nada.
                    state.saving = false;
                    const decision = await this.requestOverAssignmentDecision(
                        result.over_assigned_qty || 0,
                        config.unitLabel || ""
                    );
                    if (!decision) return false;
                    return doConfirm(decision.action, decision.reason);
                }

                if (result && result.success === false) {
                    progress.fail(result.message || "No se pudo aplicar la asignación.");
                    this.notification.add(result.message || "No se pudo aplicar la asignación.", {
                        type: "danger",
                    });
                    return false;
                }

                // Números de la LÍNEA elegida (la asignación es por línea):
                // reportar lo seleccionado contra el pendiente de la línea
                // mezclaba escalas y parecía que "no pasó nada".
                const selectedLabel = this._fmtPlain(result?.selected_qty || selectedQty);
                const assignedLabel = this._fmtPlain(result?.assigned_qty_after || 0);
                const pendingLabel = this._fmtPlain(result?.pending_qty_after || 0);
                const uom = result?.uom_name ? ` ${result.uom_name}` : ` ${config.unitLabel || ""}`;

                this.notification.add(
                    `Asignación en tránsito guardada. Seleccionado: ${selectedLabel}${uom}. ` +
                        `Asignado en la línea: ${assignedLabel}${uom}. Pendiente de la línea: ${pendingLabel}${uom}.`,
                    { type: "success", sticky: false }
                );

                this.destroyTransitPopup();
                progress.step(2);
                await this.loadData();
                progress.done("Asignación guardada");
                return true;
            } catch (error) {
                console.error("[TransitAllocation] Error guardando asignación:", error);
                progress.fail("Error guardando asignación: " + (error.message || error));
                this.notification.add(
                    "Error guardando asignación en tránsito: " + (error.message || error),
                    { type: "danger" }
                );
                return false;
            } finally {
                state.saving = false;
            }
        };

        const bindFilter = (id, key) => {
            const input = root.querySelector(`#${id}`);
            if (!input) return;
            input.addEventListener("input", (ev) => {
                state.filters[key] = ev.target.value || "";
                applyPopupFilters();
            });
        };

        bindFilter("sf-lot", "lot_name");
        bindFilter("sf-bloque", "bloque");
        bindFilter("sf-atado", "atado");
        bindFilter("sf-voyage", "voyage");
        const syncContainerChips = () => {
            const active = state.filters.container || "";
            root.querySelectorAll("[data-chip-container]").forEach((c2) => {
                c2.classList.toggle(
                    "chip-active",
                    !!active && c2.getAttribute("data-chip-container") === active);
            });
        };
        const contSelect = root.querySelector("#sf-container");
        if (contSelect) {
            contSelect.addEventListener("input", (ev) => {
                state.filters.container = ev.target.value || "";
                syncContainerChips();
                applyPopupFilters();
            });
        }
        root.querySelectorAll("[data-chip-container]").forEach((chip) => {
            chip.addEventListener("click", () => {
                const val = chip.getAttribute("data-chip-container") || "";
                const next = state.filters.container === val ? "" : val;
                state.filters.container = next;
                if (contSelect) contSelect.value = next;
                syncContainerChips();
                applyPopupFilters();
            });
        });
        bindFilter("sf-purchase", "purchase");

        root.querySelector("#sp-close").addEventListener("click", () => this.destroyTransitPopup());
        root.querySelector("#sp-cancel").addEventListener("click", () => this.destroyTransitPopup());
        root.querySelector("#sp-select-all").addEventListener("click", doSelectAll);
        root.querySelector("#sp-clear-all").addEventListener("click", doClearAll);
        root.querySelector("#sp-confirm-bottom").addEventListener("click", () => doConfirm(false, false));
        root.querySelector("#sp-open-order").addEventListener("click", (ev) => {
            this.destroyTransitPopup();
            this.openSaleOrder(config.line.so_id, ev);
        });

        overlay.addEventListener("click", (ev) => {
            if (ev.target === overlay) this.destroyTransitPopup();
        });

        const keyHandler = (ev) => {
            if (ev.key === "Escape") this.destroyTransitPopup();
        };
        document.addEventListener("keydown", keyHandler);
        this._transitPopupKeyHandler = keyHandler;

        renderTable();
    }

    requestOverAssignmentDecision(overQty, unitLabel) {
        this.destroyOverAssignPopup();

        return new Promise((resolve) => {
            const root = document.createElement("div");
            root.className = "stone-decision-root stone-overassign-root";
            root.style.position = "fixed";
            root.style.inset = "0";
            root.style.zIndex = "99999";
            document.body.appendChild(root);
            this._overAssignRoot = root;

            root.innerHTML = `
                <div class="stone-decision-overlay" style="z-index: 100000;">
                    <div class="stone-decision-dialog" style="z-index: 100001;">
                        <div class="stone-decision-header">
                            <div>
                                <h4>Asignación mayor a lo solicitado</h4>
                                <p>
                                    Hay un excedente de
                                    <strong>${this._fmtPlain(overQty)} ${this._escapeHtml(unitLabel)}</strong>.
                                    Selecciona cómo se administrará.
                                </p>
                            </div>
                            <button type="button" class="stone-decision-x" data-action="cancel">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>

                        <div class="stone-decision-options">
                            <label class="stone-decision-option active">
                                <input type="radio" name="over_action" value="free" checked="checked"/>
                                <span class="stone-decision-option-icon"><i class="fa fa-gift"></i></span>
                                <span>
                                    <strong>Entregar excedente sin cobrar</strong>
                                    <small>El cliente recibe el material extra, pero se aplica el descuento equivalente.</small>
                                </span>
                            </label>

                            <label class="stone-decision-option">
                                <input type="radio" name="over_action" value="bill"/>
                                <span class="stone-decision-option-icon"><i class="fa fa-money"></i></span>
                                <span>
                                    <strong>Cobrar excedente</strong>
                                    <small>La cantidad solicitada se ajustará para cobrar el excedente asignado.</small>
                                </span>
                            </label>
                        </div>

                        <div class="stone-decision-note">
                            <label>Nota administrativa</label>
                            <textarea id="sp-over-note" rows="3">Sobreasignación autorizada desde Transit Allocation.</textarea>
                        </div>

                        <div class="stone-decision-footer">
                            <button type="button" class="stone-btn stone-btn-outline" data-action="cancel">Volver</button>
                            <button type="button" class="stone-btn stone-btn-primary-dark" data-action="accept">Continuar</button>
                        </div>
                    </div>
                </div>
            `;

            const cleanup = () => this.destroyOverAssignPopup();
            const cancel = () => {
                cleanup();
                resolve(false);
            };

            root.querySelectorAll(".stone-decision-option").forEach((option) => {
                option.addEventListener("click", () => {
                    root.querySelectorAll(".stone-decision-option").forEach((el) => el.classList.remove("active"));
                    option.classList.add("active");
                    const input = option.querySelector("input[name='over_action']");
                    if (input) input.checked = true;
                });
            });

            root.querySelectorAll("[data-action='cancel']").forEach((btn) => btn.addEventListener("click", cancel));
            root.querySelector("[data-action='accept']").addEventListener("click", () => {
                const selected = root.querySelector("input[name='over_action']:checked");
                const note = root.querySelector("#sp-over-note");
                const payload = {
                    action: selected ? selected.value : "free",
                    reason: note && note.value ? note.value : "Sobreasignación autorizada desde Transit Allocation.",
                };
                cleanup();
                resolve(payload);
            });

            const keyHandler = (ev) => {
                if (ev.key === "Escape") cancel();
            };
            document.addEventListener("keydown", keyHandler);
            this._overAssignKeyHandler = keyHandler;
        });
    }

    // =========================================================================
    // FORMATO
    // =========================================================================

    _escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value === null || value === undefined ? "" : String(value);
        return div.innerHTML;
    }

    _fmtPlain(num) {
        if (num === null || num === undefined || isNaN(num)) return "0.00";
        return parseFloat(num || 0).toFixed(2);
    }

    _fmtDim(num) {
        if (!num) return "-";
        const v = parseFloat(num);
        if (isNaN(v)) return "-";
        return v % 1 === 0 ? v.toFixed(0) : v.toFixed(2);
    }

    _fmtPct(num) {
        if (num === null || num === undefined || isNaN(num)) return "0";
        const v = parseFloat(num);
        return v % 1 === 0 ? v.toFixed(0) : v.toFixed(1);
    }

    // Formato único del sistema: "13 ago 2026".
    fmtDate(value) {
        return somFormatDate(value, { empty: "N/A" });
    }

    fmtNum(value) {
        const n = Number(value || 0);
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    fmtPercent(value) {
        const n = Number(value || 0);
        return (
            n.toLocaleString("es-MX", {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0,
            }) + "%"
        );
    }

    fmtQtyWithUnit(value, unitLabel) {
        return `${this.fmtNum(value)} ${unitLabel || ""}`.trim();
    }

    fmtSplitQty(m2, pieces) {
        const parts = [];
        if (Number(m2 || 0) > 0) parts.push(`${this.fmtNum(m2)} m²`);
        if (Number(pieces || 0) > 0) parts.push(`${this.fmtNum(pieces)} pzas`);
        return parts.length ? parts.join(" · ") : "0.00";
    }

    fmtDays(value) {
        const n = Number(value || 0);
        if (n === 1) return "1 día";
        return `${n} días`;
    }

    splitPercent(done, requested) {
        const req = Number(requested || 0);
        if (req <= 0) return "—";
        return this.fmtPercent((Number(done || 0) / req) * 100);
    }

    paymentClass(value) {
        const n = Number(value || 0);
        if (n >= 80) return "o_tba_pay_high";
        if (n >= 30) return "o_tba_pay_mid";
        return "o_tba_pay_low";
    }

    assignmentStateLabel(value) {
        const labels = {
            no_demand: "Sin demanda",
            open: "Pendiente",
            partial: "Parcial",
            complete: "Completo",
            over_assigned: "Sobreasignado",
            to_purchase: "Mandado a pedir",
            closed_short: "Cerrado corto",
        };
        return labels[value] || value || "";
    }

    _sum(fieldName) {
        return this.state.data.reduce((sum, line) => sum + Number(line[fieldName] || 0), 0);
    }

    get totalLines() {
        return this.state.data.length;
    }

    get totalPendingM2() {
        return this._sum("qty_pending_m2");
    }

    get totalPendingPieces() {
        return this._sum("qty_pending_pieces");
    }

    get totalTransitAvailableM2() {
        const seen = new Set();
        let total = 0;
        for (const row of this.state.data) {
            const key = row.product_id || row.product_name;
            if (seen.has(key)) continue;
            seen.add(key);
            total += Number(row.qty_available_m2 || 0);
        }
        return total;
    }

    get totalTransitAvailablePieces() {
        const seen = new Set();
        let total = 0;
        for (const row of this.state.data) {
            const key = row.product_id || row.product_name;
            if (seen.has(key)) continue;
            seen.add(key);
            total += Number(row.qty_available_pieces || 0);
        }
        return total;
    }
}

TransitAllocation.template = "stock_transit_allocation.TransitAllocation";

registry.category("lazy_components").add("TransitAllocation", TransitAllocation);
