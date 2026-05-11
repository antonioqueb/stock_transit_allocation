/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ToBePurchased extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this._cancelPopupRoot = null;
        this._cancelPopupKeyHandler = null;

        this.state = useState({
            data: [],
            filteredData: [],
            loading: true,
            expanded: {},
            selectedLines: [],

            // Filtros
            searchQuery: "",
            showOnlyPending: true,
            groupBy: "product", // product | sale_order | vendor | customer | unit_type

            // Modal state
            showModal: false,
            allVendors: [],
            selectedVendor: null,
            selectedVendorName: "",
            vendorSearch: "",
            showVendorDropdown: false,
            openPOs: [],
            selectedPO: null,
            loadingPOs: false,
            creatingPO: false,
            cancelling: {},
        });

        onMounted(() => {
            this.loadData();
            this.loadAllVendors();
        });

        onWillUnmount(() => {
            this.destroyCancelPopup();
        });
    }

    async loadData() {
        this.state.loading = true;

        try {
            this.state.data = await this.orm.call(
                "purchase.manager.logic",
                "get_data",
                []
            );
            this.applyFilters();
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar datos:", error);
            this.state.data = [];
            this.state.filteredData = [];
            this.notification.add(
                "Error al cargar To Be Purchased: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.loading = false;
        }
    }

    async loadAllVendors() {
        try {
            this.state.allVendors = await this.orm.call(
                "purchase.manager.logic",
                "get_all_vendors",
                []
            );
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar proveedores:", error);
            this.state.allVendors = [];
        }
    }

    applyFilters() {
        let result = [...this.state.data];

        const query = (this.state.searchQuery || "").toLowerCase().trim();

        if (query) {
            result = result.filter((product) => {
                const lineText = (product.so_lines || []).map((line) => [
                    line.so_name || "",
                    line.customer || "",
                    line.description || "",
                    line.note || "",
                    line.po_name || "",
                ].join(" ")).join(" ");

                const transitText = (product.transit_free_lines || []).map((line) => [
                    line.lot_name || "",
                    line.voyage_name || "",
                    line.container_number || "",
                    line.purchase_name || "",
                    line.vendor || "",
                    line.x_bloque || "",
                    line.x_atado || "",
                    line.x_color || "",
                ].join(" ")).join(" ");

                const haystack = [
                    product.name || "",
                    product.vendor || "",
                    product.category || "",
                    product.group || "",
                    product.type || "",
                    product.product_type || "",
                    product.unit_label || "",
                    lineText,
                    transitText,
                ].join(" ").toLowerCase();

                return haystack.includes(query);
            });
        }

        if (this.state.showOnlyPending) {
            result = result.map((product) => {
                // El backend ya devuelve qty_pending como pendiente incremental
                // por comprar, neto de OC/allocation activa. No se debe ocultar
                // una línea solo porque tenga po_id: si la demanda subió, debe
                // seguir apareciendo con el faltante nuevo.
                const filteredLines = (product.so_lines || []).filter((line) => {
                    const pendingToBuy = Number(
                        line.qty_purchase_pending !== undefined
                            ? line.qty_purchase_pending
                            : (line.qty_pending || 0)
                    );
                    return pendingToBuy > 0.0001;
                });

                if (filteredLines.length === 0) {
                    return null;
                }

                const qtySo = filteredLines.reduce(
                    (sum, line) => sum + Number(
                        line.qty_purchase_pending !== undefined
                            ? line.qty_purchase_pending
                            : (line.qty_pending || 0)
                    ),
                    0
                );
                const qtySoM2 = filteredLines.reduce(
                    (sum, line) => sum + Number(
                        line.qty_purchase_pending_m2 !== undefined
                            ? line.qty_purchase_pending_m2
                            : (line.qty_pending_m2 || 0)
                    ),
                    0
                );
                const qtySoPieces = filteredLines.reduce(
                    (sum, line) => sum + Number(
                        line.qty_purchase_pending_pieces !== undefined
                            ? line.qty_purchase_pending_pieces
                            : (line.qty_pending_pieces || 0)
                    ),
                    0
                );

                const qtyToBuy = qtySo;
                const qtyToBuyM2 = product.unit_kind === "pieces" ? 0 : qtyToBuy;
                const qtyToBuyPieces = product.unit_kind === "pieces" ? qtyToBuy : 0;

                return {
                    ...product,
                    so_lines: filteredLines,
                    qty_so: qtySo,
                    qty_so_m2: qtySoM2,
                    qty_so_pieces: qtySoPieces,
                    qty_to_buy: qtyToBuy,
                    qty_to_buy_m2: qtyToBuyM2,
                    qty_to_buy_pieces: qtyToBuyPieces,
                };
            }).filter((product) => product !== null);
        }

        if (this.state.groupBy === "product") {
            this.state.filteredData = result;
        } else if (this.state.groupBy === "sale_order") {
            this.state.filteredData = this._groupBySaleOrder(result);
        } else if (this.state.groupBy === "vendor") {
            this.state.filteredData = this._groupByVendor(result);
        } else if (this.state.groupBy === "customer") {
            this.state.filteredData = this._groupByCustomer(result);
        } else if (this.state.groupBy === "unit_type") {
            this.state.filteredData = this._groupByUnitType(result);
        }
    }

    _groupBySaleOrder(data) {
        const soMap = {};

        for (const product of data) {
            for (const soLine of product.so_lines || []) {
                const soKey = soLine.so_id;

                if (!soMap[soKey]) {
                    soMap[soKey] = {
                        id: soLine.so_id,
                        so_name: soLine.so_name,
                        so_id: soLine.so_id,
                        date: soLine.date,
                        commitment_date: soLine.commitment_date,
                        customer: soLine.customer,
                        customer_id: soLine.customer_id,
                        location: soLine.location,
                        note: soLine.note,
                        products: [],
                        total_pending: 0,
                        total_pending_m2: 0,
                        total_pending_pieces: 0,
                        total_requested: 0,
                        total_assigned: 0,
                        max_days_unassigned: 0,
                    };
                }

                soMap[soKey].products.push({
                    ...soLine,
                    product_id: product.id,
                    product_name: product.name,
                    vendor: product.vendor,
                    vendors: product.vendors,
                    unit_kind: product.unit_kind,
                    unit_label: product.unit_label,
                    product_type: product.product_type,
                    qty_a: product.qty_a,
                    qty_i: product.qty_i,
                    qty_i_free: product.qty_i_free,
                    qty_p: product.qty_p,
                    qty_a_m2: product.qty_a_m2,
                    qty_i_m2: product.qty_i_m2,
                    qty_i_free_m2: product.qty_i_free_m2,
                    qty_p_m2: product.qty_p_m2,
                    qty_a_pieces: product.qty_a_pieces,
                    qty_i_pieces: product.qty_i_pieces,
                    qty_i_free_pieces: product.qty_i_free_pieces,
                    qty_p_pieces: product.qty_p_pieces,
                    transit_free_count: product.transit_free_count,
                    transit_free_lines: product.transit_free_lines,
                });

                soMap[soKey].total_pending += Number(soLine.qty_pending || 0);
                soMap[soKey].total_pending_m2 += Number(soLine.qty_pending_m2 || 0);
                soMap[soKey].total_pending_pieces += Number(soLine.qty_pending_pieces || 0);
                soMap[soKey].total_requested += Number(soLine.qty_requested || soLine.qty_orig || 0);
                soMap[soKey].total_assigned += Number(soLine.qty_assigned || 0);
                soMap[soKey].max_days_unassigned = Math.max(
                    soMap[soKey].max_days_unassigned,
                    Number(soLine.days_unassigned || 0)
                );
            }
        }

        return Object.values(soMap).sort((a, b) =>
            String(a.so_name || "").localeCompare(String(b.so_name || ""))
        );
    }

    _makeOperationalGroup({ id, key, name, subtitle }) {
        return {
            id,
            group_key: key,
            group_name: name,
            group_subtitle: subtitle,
            products: [],
            total_pending: 0,
            total_pending_m2: 0,
            total_pending_pieces: 0,
            total_to_buy: 0,
            total_to_buy_m2: 0,
            total_to_buy_pieces: 0,
            total_requested: 0,
            total_assigned: 0,
            max_days_unassigned: 0,
        };
    }

    _addProductLinesToOperationalGroup(group, product) {
        for (const soLine of product.so_lines || []) {
            group.products.push({
                ...soLine,
                product_id: product.id,
                product_name: product.name,
                vendor: product.vendor,
                vendors: product.vendors,
                unit_kind: product.unit_kind,
                unit_label: product.unit_label,
                product_type: product.product_type,
                qty_a: product.qty_a,
                qty_i: product.qty_i,
                qty_i_free: product.qty_i_free,
                qty_p: product.qty_p,
                qty_a_m2: product.qty_a_m2,
                qty_i_m2: product.qty_i_m2,
                qty_i_free_m2: product.qty_i_free_m2,
                qty_p_m2: product.qty_p_m2,
                qty_a_pieces: product.qty_a_pieces,
                qty_i_pieces: product.qty_i_pieces,
                qty_i_free_pieces: product.qty_i_free_pieces,
                qty_p_pieces: product.qty_p_pieces,
                transit_free_count: product.transit_free_count,
                transit_free_lines: product.transit_free_lines,
            });

            group.total_pending += Number(soLine.qty_pending || 0);
            group.total_pending_m2 += Number(soLine.qty_pending_m2 || 0);
            group.total_pending_pieces += Number(soLine.qty_pending_pieces || 0);
            group.total_requested += Number(soLine.qty_requested || soLine.qty_orig || 0);
            group.total_assigned += Number(soLine.qty_assigned || 0);
            group.max_days_unassigned = Math.max(
                group.max_days_unassigned,
                Number(soLine.days_unassigned || 0)
            );
        }

        group.total_to_buy += Number(product.qty_to_buy || 0);
        group.total_to_buy_m2 += Number(product.qty_to_buy_m2 || 0);
        group.total_to_buy_pieces += Number(product.qty_to_buy_pieces || 0);
    }

    _sortOperationalGroups(groups) {
        return groups.sort((a, b) => {
            if (b.max_days_unassigned !== a.max_days_unassigned) {
                return b.max_days_unassigned - a.max_days_unassigned;
            }
            return String(a.group_name || "").localeCompare(String(b.group_name || ""));
        });
    }

    _groupByVendor(data) {
        const map = {};

        for (const product of data) {
            const vendorName = product.vendor || "SIN PROVEEDOR";
            const vendorId = (product.vendors && product.vendors[0] && product.vendors[0].id) || 0;

            if (!map[vendorName]) {
                map[vendorName] = this._makeOperationalGroup({
                    id: vendorId,
                    key: `vendor_${vendorName}`,
                    name: vendorName,
                    subtitle: "Proveedor con líneas pendientes por compra",
                });
            }

            this._addProductLinesToOperationalGroup(map[vendorName], product);
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    _groupByCustomer(data) {
        const map = {};

        for (const product of data) {
            for (const soLine of product.so_lines || []) {
                const customerKey = soLine.customer_id || soLine.customer || "Sin cliente";

                if (!map[customerKey]) {
                    map[customerKey] = this._makeOperationalGroup({
                        id: customerKey,
                        key: `customer_${customerKey}`,
                        name: soLine.customer || "Sin cliente",
                        subtitle: "Cliente con material pendiente por comprar",
                    });
                }

                const pseudoProduct = { ...product, so_lines: [soLine] };
                this._addProductLinesToOperationalGroup(map[customerKey], pseudoProduct);
            }
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    _groupByUnitType(data) {
        const map = {};

        for (const product of data) {
            const unitKey = product.unit_kind || "m2";
            const groupName = unitKey === "pieces" ? "Piezas" : "Metros cuadrados";
            const subtitle = unitKey === "pieces" ? "Productos vendidos por pieza" : "Productos vendidos por m²";

            if (!map[unitKey]) {
                map[unitKey] = this._makeOperationalGroup({
                    id: unitKey,
                    key: `unit_${unitKey}`,
                    name: groupName,
                    subtitle,
                });
            }

            this._addProductLinesToOperationalGroup(map[unitKey], product);
        }

        return this._sortOperationalGroups(Object.values(map));
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this.applyFilters();
    }

    togglePendingFilter() {
        this.state.showOnlyPending = !this.state.showOnlyPending;
        this.applyFilters();
    }

    setGroupBy(mode) {
        this.state.groupBy = mode;
        this.state.expanded = {};
        this.state.selectedLines = [];
        this.applyFilters();
    }

    clearSearch() {
        this.state.searchQuery = "";
        this.applyFilters();
    }

    async refresh() {
        await this.loadData();
        this.notification.add("To Be Purchased actualizado", {
            type: "success",
            sticky: false,
        });
    }

    toggleExpand(itemId) {
        this.state.expanded[itemId] = !this.state.expanded[itemId];
    }

    toggleSelection(lineId, ev) {
        if (ev.target.checked) {
            if (!this.state.selectedLines.includes(lineId)) {
                this.state.selectedLines.push(lineId);
            }
        } else {
            this.state.selectedLines = this.state.selectedLines.filter((id) => id !== lineId);
        }
    }

    _escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value === null || value === undefined ? "" : String(value);
        return div.innerHTML;
    }

    destroyCancelPopup() {
        if (this._cancelPopupKeyHandler) {
            document.removeEventListener("keydown", this._cancelPopupKeyHandler);
            this._cancelPopupKeyHandler = null;
        }

        if (this._cancelPopupRoot) {
            this._cancelPopupRoot.remove();
            this._cancelPopupRoot = null;
        }
    }

    askCancelDecision(line) {
        this.destroyCancelPopup();

        return new Promise((resolve) => {
            const root = document.createElement("div");
            root.className = "o_tbp_cancel_root";
            document.body.appendChild(root);
            this._cancelPopupRoot = root;

            const pending = this.fmtQtyWithUnit(line.qty_pending || 0, line.unit_label || "");
            const requested = this.fmtQtyWithUnit(line.qty_requested || line.qty_orig || 0, line.unit_label || "");
            const assigned = this.fmtQtyWithUnit(line.qty_assigned || 0, line.unit_label || "");
            const poLabel = line.po_name || "Sin OC vinculada";

            root.innerHTML = `
                <div class="o_tbp_cancel_backdrop">
                    <div class="o_tbp_cancel_dialog">
                        <div class="o_tbp_cancel_header">
                            <div>
                                <h4>Cancelar pendiente de compra</h4>
                                <p>
                                    Esta acción saca la línea de To Be Purchased. No genera nota de crédito.
                                </p>
                            </div>
                            <button type="button" class="o_tbp_cancel_x" data-action="cancel">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>

                        <div class="o_tbp_cancel_summary">
                            <div><span>Pedido</span><strong>${this._escapeHtml(line.so_name || "-")}</strong></div>
                            <div><span>Cliente</span><strong>${this._escapeHtml(line.customer || "-")}</strong></div>
                            <div><span>Solicitado</span><strong>${this._escapeHtml(requested)}</strong></div>
                            <div><span>Asignado</span><strong>${this._escapeHtml(assigned)}</strong></div>
                            <div><span>Pendiente</span><strong>${this._escapeHtml(pending)}</strong></div>
                            <div><span>OC</span><strong>${this._escapeHtml(poLabel)}</strong></div>
                        </div>

                        <div class="o_tbp_cancel_options">
                            <label class="o_tbp_cancel_option active">
                                <input type="radio" name="tbp_cancel_action" value="settle" checked="checked"/>
                                <span class="o_tbp_cancel_option_icon"><i class="fa fa-check-circle"></i></span>
                                <span>
                                    <strong>Cancelar sin descuento</strong>
                                    <small>Se cierra el pendiente operativo. La cantidad y el cobro actual del pedido se mantienen intactos.</small>
                                </span>
                            </label>

                            <label class="o_tbp_cancel_option">
                                <input type="radio" name="tbp_cancel_action" value="discount"/>
                                <span class="o_tbp_cancel_option_icon"><i class="fa fa-tag"></i></span>
                                <span>
                                    <strong>Cancelar aplicando descuento</strong>
                                    <small>Se cierra el pendiente y se aplica descuento equivalente al faltante no comprado.</small>
                                </span>
                            </label>
                        </div>

                        <div class="o_tbp_cancel_note">
                            <label>Motivo</label>
                            <textarea id="tbp-cancel-reason" rows="3">Pendiente de compra cancelado desde To Be Purchased.</textarea>
                        </div>

                        <div class="o_tbp_cancel_footer">
                            <button type="button" class="btn btn-light" data-action="cancel">Volver</button>
                            <button type="button" class="btn btn-danger" data-action="accept">
                                <i class="fa fa-ban me-1"></i> Cancelar pendiente
                            </button>
                        </div>
                    </div>
                </div>
            `;

            const cleanup = () => this.destroyCancelPopup();
            const cancel = () => {
                cleanup();
                resolve(false);
            };

            root.querySelectorAll(".o_tbp_cancel_option").forEach((option) => {
                option.addEventListener("click", () => {
                    root.querySelectorAll(".o_tbp_cancel_option").forEach((el) => el.classList.remove("active"));
                    option.classList.add("active");

                    const input = option.querySelector("input[name='tbp_cancel_action']");
                    if (input) {
                        input.checked = true;
                    }
                });
            });

            root.querySelectorAll("[data-action='cancel']").forEach((btn) => {
                btn.addEventListener("click", (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    cancel();
                });
            });

            root.querySelector("[data-action='accept']").addEventListener("click", (ev) => {
                ev.preventDefault();
                ev.stopPropagation();

                const selected = root.querySelector("input[name='tbp_cancel_action']:checked");
                const reason = root.querySelector("#tbp-cancel-reason");

                const payload = {
                    action: selected ? selected.value : "settle",
                    reason: reason && reason.value
                        ? reason.value
                        : "Pendiente de compra cancelado desde To Be Purchased.",
                };

                cleanup();
                resolve(payload);
            });

            const keyHandler = (ev) => {
                if (ev.key === "Escape") {
                    ev.preventDefault();
                    ev.stopPropagation();
                    cancel();
                }
            };

            document.addEventListener("keydown", keyHandler);
            this._cancelPopupKeyHandler = keyHandler;
        });
    }

    async cancelPending(line, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!line || !line.id) {
            return;
        }

        const decision = await this.askCancelDecision(line);
        if (!decision) {
            return;
        }

        this.state.cancelling[line.id] = true;

        try {
            const result = await this.orm.call(
                "purchase.manager.logic",
                "cancel_pending",
                [
                    [line.id],
                    decision.reason || "Pendiente de compra cancelado desde To Be Purchased.",
                    decision.action || "settle",
                ]
            );

            if (result && result.error) {
                this.notification.add(result.error, { type: "danger" });
                return;
            }

            this.state.selectedLines = this.state.selectedLines.filter((id) => id !== line.id);

            const message = decision.action === "discount"
                ? "Pendiente cancelado y descuento aplicado."
                : "Pendiente cancelado sin modificar el pedido.";

            this.notification.add(message, {
                type: "success",
                sticky: false,
            });

            await this.loadData();
        } catch (error) {
            console.error("[ToBePurchased] Error cancelando pendiente:", error);
            this.notification.add(
                "Error al cancelar pendiente: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.cancelling[line.id] = false;
        }
    }

    _getSelectedLineObjects() {
        const selected = new Set(this.state.selectedLines || []);
        return this._allLines().filter((line) => selected.has(line.id));
    }

    async _loadOpenPurchaseOrdersForVendor(vendorId) {
        if (!vendorId) {
            this.state.openPOs = [];
            return [];
        }

        this.state.loadingPOs = true;

        try {
            const orders = await this.orm.call(
                "purchase.manager.logic",
                "get_open_purchase_orders",
                [vendorId]
            );
            this.state.openPOs = orders || [];
            return this.state.openPOs;
        } catch (error) {
            console.error("[ToBePurchased] Error al cargar OCs:", error);
            this.state.openPOs = [];
            return [];
        } finally {
            this.state.loadingPOs = false;
        }
    }

    async _prefillModalFromLinkedPOs() {
        const selectedLines = this._getSelectedLineObjects();
        const linkedLines = selectedLines.filter(
            (line) => line.po_id && line.po_partner_id && line.po_partner_name
        );

        if (!linkedLines.length) {
            return;
        }

        const vendorIds = [...new Set(linkedLines.map((line) => line.po_partner_id))];
        if (vendorIds.length !== 1) {
            return;
        }

        const vendorId = vendorIds[0];
        const vendorName = linkedLines[0].po_partner_name || "";

        this.state.selectedVendor = vendorId;
        this.state.selectedVendorName = vendorName;
        this.state.vendorSearch = vendorName;
        this.state.showVendorDropdown = false;

        await this._loadOpenPurchaseOrdersForVendor(vendorId);

        // Importante: si la línea ya tiene una OC vinculada, NO la seleccionamos
        // automáticamente. El flujo debe permitir dos decisiones claras:
        // 1) crear una OC nueva para el incremento, o
        // 2) elegir manualmente la OC vinculada/abierta y agregar ahí.
        // Esto evita que una OC vinculada bloquee la creación de una OC nueva.
        this.state.selectedPO = null;
    }

    async openPurchaseModal() {
        if (this.state.selectedLines.length === 0) {
            this.notification.add("Seleccione al menos una línea", { type: "warning" });
            return;
        }

        this.state.showModal = true;
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;
        this.state.openPOs = [];

        await this._prefillModalFromLinkedPOs();
    }

    closeModal() {
        if (this.state.creatingPO) {
            return;
        }

        this.state.showModal = false;
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;
        this.state.openPOs = [];
    }

    get filteredVendors() {
        if (!this.state.vendorSearch.trim()) {
            return this.state.allVendors;
        }

        const query = this.state.vendorSearch.toLowerCase().trim();
        return this.state.allVendors.filter((vendor) =>
            String(vendor.name || "").toLowerCase().includes(query)
        );
    }

    onVendorSearchInput(ev) {
        this.state.vendorSearch = ev.target.value;
        this.state.showVendorDropdown = true;

        if (!ev.target.value.trim()) {
            this.state.selectedVendor = null;
            this.state.selectedVendorName = "";
            this.state.openPOs = [];
            this.state.selectedPO = null;
        }
    }

    onVendorSearchFocus() {
        this.state.showVendorDropdown = true;
    }

    onVendorSearchBlur() {
        setTimeout(() => {
            this.state.showVendorDropdown = false;
        }, 200);
    }

    async selectVendor(vendor) {
        this.state.selectedVendor = vendor.id;
        this.state.selectedVendorName = vendor.name;
        this.state.vendorSearch = vendor.name;
        this.state.showVendorDropdown = false;
        this.state.selectedPO = null;

        await this._loadOpenPurchaseOrdersForVendor(vendor.id);
    }

    clearVendorSelection() {
        this.state.selectedVendor = null;
        this.state.selectedVendorName = "";
        this.state.vendorSearch = "";
        this.state.openPOs = [];
        this.state.selectedPO = null;
    }

    selectPO(poId) {
        this.state.selectedPO = poId || null;
    }

    async confirmPurchase() {
        if (!this.state.selectedVendor) {
            this.notification.add("Debe seleccionar un proveedor", { type: "warning" });
            return;
        }

        this.state.creatingPO = true;

        try {
            const resultAction = await this.orm.call(
                "purchase.manager.logic",
                "create_purchase_orders",
                [
                    this.state.selectedLines,
                    this.state.selectedVendor,
                    this.state.selectedPO,
                ]
            );

            if (resultAction.error) {
                this.notification.add(resultAction.error, { type: "danger" });
                return;
            }

            this.notification.add("Orden de Compra procesada correctamente", {
                type: "success",
            });

            this.state.selectedLines = [];
            this.closeModal();
            this.action.doAction(resultAction);
        } catch (error) {
            console.error("[ToBePurchased] Error generando OC:", error);
            this.notification.add("Error: " + (error.message || error), {
                type: "danger",
            });
        } finally {
            this.state.creatingPO = false;
        }
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

    fmtTransitFree(product) {
        if (!product) return "0.00";
        return this.fmtSplitQty(
            product.qty_i_free_m2 !== undefined ? product.qty_i_free_m2 : product.qty_i_m2,
            product.qty_i_free_pieces !== undefined ? product.qty_i_free_pieces : product.qty_i_pieces
        );
    }

    fmtTransitFreeLine(line) {
        if (!line) return "0.00";
        return this.fmtSplitQty(line.qty_m2, line.qty_pieces);
    }

    fmtDays(value) {
        const n = Number(value || 0);
        return n === 1 ? "1 día" : `${n} días`;
    }

    allocationPercent(assigned, requested) {
        const req = Number(requested || 0);
        if (req <= 0) return 0;
        return (Number(assigned || 0) / req) * 100;
    }

    linesAssignmentPercent(lines) {
        const requested = (lines || []).reduce(
            (sum, line) => sum + Number(line.qty_requested || line.qty_orig || 0),
            0
        );
        const assigned = (lines || []).reduce(
            (sum, line) => sum + Number(line.qty_assigned || 0),
            0
        );
        return this.allocationPercent(assigned, requested);
    }

    _allLines() {
        return this.state.data.flatMap((product) => product.so_lines || []);
    }

    _sumLines(fieldName) {
        return this._allLines().reduce(
            (sum, line) => sum + Number(line[fieldName] || 0),
            0
        );
    }

    _sumLinesFirst(primaryField, fallbackField) {
        return this._allLines().reduce(
            (sum, line) => sum + Number(line[primaryField] || line[fallbackField] || 0),
            0
        );
    }

    _sum(fieldName) {
        return this.state.data.reduce((sum, product) => sum + Number(product[fieldName] || 0), 0);
    }

    get totalLines() {
        return this._allLines().length;
    }

    get totalRequested() {
        return this._sumLinesFirst("qty_requested", "qty_orig");
    }

    get totalAssigned() {
        return this._sumLines("qty_assigned");
    }

    get totalAssignedPercentGlobal() {
        return this.allocationPercent(this.totalAssigned, this.totalRequested);
    }

    get totalDemandM2() {
        return this._sum("qty_so_m2");
    }

    get totalDemandPieces() {
        return this._sum("qty_so_pieces");
    }

    get totalToBuyM2() {
        return this._sum("qty_to_buy_m2");
    }

    get totalToBuyPieces() {
        return this._sum("qty_to_buy_pieces");
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
}

ToBePurchased.template = "stock_transit_allocation.ToBePurchased";

registry.category("actions").add(
    "action_to_be_purchased",
    ToBePurchased,
    { force: true }
);