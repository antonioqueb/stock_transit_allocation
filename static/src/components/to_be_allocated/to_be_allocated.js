/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ToBeAllocated extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this._allocationPopupRoot = null;
        this._allocationPopupObserver = null;
        this._allocationPopupKeyHandler = null;
        this._lightboxRoot = null;
        this._lightboxKeyHandler = null;

        this.state = useState({
            data: [],
            filteredData: [],
            loading: true,
            expanded: {},
            searchQuery: "",
            groupBy: "product", // product | sale_order | salesperson
            sending: {},
            assigning: {},
        });

        onWillStart(async () => {
            await this.loadData();
        });

        onWillUnmount(() => {
            this.destroyAllocationPopup();
            this._destroyLightbox();
        });
    }

    // =========================================================================
    // DATA
    // =========================================================================

    async loadData() {
        this.state.loading = true;

        try {
            this.state.data = await this.orm.call(
                "sale.allocation.manager.logic",
                "get_data",
                []
            );
            this.applyFilters();
        } catch (error) {
            console.error("[ToBeAllocated] Error cargando datos:", error);
            this.notification.add("Error al cargar To Be Allocated", {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    applyFilters() {
        let rows = [...this.state.data];

        const query = (this.state.searchQuery || "").trim().toLowerCase();

        if (query) {
            rows = rows.filter((line) => {
                const haystack = [
                    line.so_name || "",
                    line.customer || "",
                    line.product_name || "",
                    line.salesperson || "",
                    line.description || "",
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
        }
    }

    _groupByProduct(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.product_id || 0;

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `product_${key}`,
                    label: line.product_name || "Sin producto",
                    sublabel: "Producto",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available = Math.max(
                map[key].qty_available,
                line.qty_available || 0
            );
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) => {
            if (b.max_payment_percent !== a.max_payment_percent) {
                return b.max_payment_percent - a.max_payment_percent;
            }
            return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    _groupBySaleOrder(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.so_id || 0;

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `so_${key}`,
                    label: line.so_name || "Sin SO",
                    sublabel: line.customer || "Sin cliente",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available += line.qty_available || 0;
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) => {
            if (b.max_payment_percent !== a.max_payment_percent) {
                return b.max_payment_percent - a.max_payment_percent;
            }
            return String(a.label || "").localeCompare(String(b.label || ""));
        });
    }

    _groupBySalesperson(rows) {
        const map = {};

        for (const line of rows) {
            const key = line.salesperson || "Sin vendedor";

            if (!map[key]) {
                map[key] = {
                    id: key,
                    key: `salesperson_${key}`,
                    label: key,
                    sublabel: "Vendedor",
                    lines: [],
                    qty_pending: 0,
                    qty_available: 0,
                    max_payment_percent: 0,
                };
            }

            map[key].lines.push(line);
            map[key].qty_pending += line.qty_pending || 0;
            map[key].qty_available += line.qty_available || 0;
            map[key].max_payment_percent = Math.max(
                map[key].max_payment_percent,
                line.payment_percent || 0
            );
        }

        return Object.values(map).sort((a, b) =>
            String(a.label || "").localeCompare(String(b.label || ""))
        );
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
        this.notification.add("To Be Allocated actualizado", {
            type: "success",
            sticky: false,
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

    // =========================================================================
    // POPUP DE ASIGNACIÓN DIRECTA DESDE TO BE ALLOCATED
    // =========================================================================

    _fmt(num) {
        if (num === null || num === undefined || isNaN(num)) return "0.00";
        return Number(num || 0).toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
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

    _isPieceUnit(unit) {
        const txt = String(unit || "").toLowerCase();
        return txt.includes("pza") || txt.includes("pieza") || txt.includes("unidad") || txt.includes("unit");
    }

    _getAllocationBaseFromTotals(totals, targetUnit) {
        if (this._isPieceUnit(targetUnit)) {
            return totals.totalPiezas || totals.totalM2 || 0;
        }
        return totals.totalM2 || totals.totalPiezas || 0;
    }

    _normalizeLotIds(rawLots) {
        if (!rawLots) return [];
        if (Array.isArray(rawLots)) {
            return rawLots.filter((x) => typeof x === "number");
        }
        if (rawLots.currentIds) return rawLots.currentIds;
        if (rawLots.resIds) return rawLots.resIds;
        if (rawLots.records) {
            return rawLots.records.map((r) => r.resId || r.data?.id).filter(Boolean);
        }
        return [];
    }

    _normalizeBreakdown(raw) {
        if (!raw) return {};
        if (typeof raw === "string") {
            try {
                return JSON.parse(raw) || {};
            } catch {
                return {};
            }
        }
        if (typeof raw === "object") {
            return { ...raw };
        }
        return {};
    }

    _escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value === null || value === undefined ? "" : String(value);
        return div.innerHTML;
    }

    _renderPhotoCell(photoBase64, photoCount, lotId, lotName) {
        const safeLotName = this._escapeHtml(lotName || "");

        if (photoBase64) {
            const badge = photoCount > 1
                ? `<span class="stone-photo-count">${photoCount}</span>`
                : "";

            return `
                <div class="stone-photo-cell"
                     data-lot-id="${lotId}"
                     data-lot-name="${safeLotName}"
                     data-has-photo="1">
                    <img src="data:image/jpeg;base64,${photoBase64}"
                         class="stone-photo-thumb"
                         alt="Foto"/>
                    ${badge}
                </div>
            `;
        }

        if (photoCount > 0) {
            return `
                <div class="stone-photo-cell"
                     data-lot-id="${lotId}"
                     data-lot-name="${safeLotName}"
                     data-has-photo="1">
                    <div class="stone-photo-placeholder-has">
                        <i class="fa fa-camera"></i>
                        <span>${photoCount}</span>
                    </div>
                </div>
            `;
        }

        return `
            <div class="stone-photo-cell stone-photo-empty">
                <i class="fa fa-picture-o text-muted"></i>
            </div>
        `;
    }

    _bindPhotoClicks(container) {
        container.querySelectorAll(".stone-photo-cell[data-has-photo]").forEach((cell) => {
            cell.addEventListener("click", (ev) => {
                ev.stopPropagation();

                const lotId = parseInt(cell.dataset.lotId, 10);
                const lotName = cell.dataset.lotName || "";

                const img = cell.querySelector(".stone-photo-thumb");
                let mainPhoto = false;

                if (img && img.src && img.src.startsWith("data:")) {
                    mainPhoto = img.src.replace(/^data:image\/\w+;base64,/, "");
                }

                this.openLightbox(lotId, lotName, mainPhoto);
            });
        });
    }

    async openLightbox(lotId, lotName, mainPhoto) {
        this._destroyLightbox();

        this._lightboxRoot = document.createElement("div");
        this._lightboxRoot.className = "stone-lightbox-root";
        document.body.appendChild(this._lightboxRoot);

        const initialSrc = mainPhoto ? `data:image/jpeg;base64,${mainPhoto}` : null;

        this._lightboxRoot.innerHTML = `
            <div class="stone-lightbox-overlay" id="slb-overlay">
                <div class="stone-lightbox-container">
                    <div class="stone-lightbox-header">
                        <span class="stone-lightbox-title">
                            <i class="fa fa-camera me-2"></i>
                            Fotos del lote <strong>${this._escapeHtml(lotName || lotId)}</strong>
                            <span class="stone-lightbox-counter" id="slb-counter"></span>
                        </span>
                        <button class="stone-lightbox-close" id="slb-close">
                            <i class="fa fa-times"></i>
                        </button>
                    </div>
                    <div class="stone-lightbox-body" id="slb-body">
                        ${
                            initialSrc
                                ? `<img src="${initialSrc}" class="stone-lightbox-img" id="slb-main-img"/>`
                                : `<div class="stone-lightbox-loading">
                                       <i class="fa fa-circle-o-notch fa-spin fa-2x"></i>
                                       <div class="mt-2">Cargando fotos...</div>
                                   </div>`
                        }
                    </div>
                    <div class="stone-lightbox-nav" id="slb-nav" style="display:none;">
                        <button class="stone-lightbox-nav-btn" id="slb-prev">
                            <i class="fa fa-chevron-left"></i>
                        </button>
                        <div class="stone-lightbox-thumbs" id="slb-thumbs"></div>
                        <button class="stone-lightbox-nav-btn" id="slb-next">
                            <i class="fa fa-chevron-right"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;

        const overlay = this._lightboxRoot.querySelector("#slb-overlay");
        const bodyEl = this._lightboxRoot.querySelector("#slb-body");
        const navEl = this._lightboxRoot.querySelector("#slb-nav");
        const thumbsEl = this._lightboxRoot.querySelector("#slb-thumbs");
        const counterEl = this._lightboxRoot.querySelector("#slb-counter");
        const prevBtn = this._lightboxRoot.querySelector("#slb-prev");
        const nextBtn = this._lightboxRoot.querySelector("#slb-next");

        const closeLb = () => this._destroyLightbox();

        this._lightboxRoot.querySelector("#slb-close").addEventListener("click", closeLb);
        overlay.addEventListener("click", (ev) => {
            if (ev.target === overlay) closeLb();
        });

        const keyHandler = (ev) => {
            if (ev.key === "Escape") closeLb();
            if (ev.key === "ArrowLeft" && prevBtn) prevBtn.click();
            if (ev.key === "ArrowRight" && nextBtn) nextBtn.click();
        };

        document.addEventListener("keydown", keyHandler);
        this._lightboxKeyHandler = keyHandler;

        try {
            const photos = await this.orm.searchRead(
                "stock.lot.image",
                [["lot_id", "=", lotId]],
                ["id", "name", "image", "notas", "fecha_captura"],
                { order: "sequence, id", limit: 50 }
            );

            if (!photos || photos.length === 0) {
                if (initialSrc) {
                    counterEl.textContent = "(1 foto)";
                } else {
                    bodyEl.innerHTML = `
                        <div class="stone-lightbox-loading">
                            <i class="fa fa-picture-o fa-2x text-muted"></i>
                            <div class="mt-2 text-muted">Este lote no tiene fotografías</div>
                        </div>
                    `;
                }
                return;
            }

            let currentIdx = 0;

            const showPhoto = (idx) => {
                currentIdx = idx;
                const photo = photos[idx];
                const src = `data:image/jpeg;base64,${photo.image}`;

                bodyEl.innerHTML = `
                    <img src="${src}" class="stone-lightbox-img" id="slb-main-img"/>
                    <div class="stone-lightbox-info" id="slb-info">
                        <strong>${this._escapeHtml(photo.name || "")}</strong>
                        ${
                            photo.notas
                                ? `<span class="ms-3 text-muted">${this._escapeHtml(photo.notas)}</span>`
                                : ""
                        }
                        ${
                            photo.fecha_captura
                                ? `<span class="ms-3 text-muted small">
                                       <i class="fa fa-clock-o me-1"></i>${photo.fecha_captura}
                                   </span>`
                                : ""
                        }
                    </div>
                `;

                counterEl.textContent = `(${idx + 1} / ${photos.length})`;

                thumbsEl.querySelectorAll(".stone-lightbox-thumb").forEach((th, i) => {
                    th.classList.toggle("active", i === idx);
                });
            };

            if (photos.length > 1) {
                navEl.style.display = "flex";

                let thumbsHtml = "";
                for (let i = 0; i < photos.length; i++) {
                    const src = `data:image/jpeg;base64,${photos[i].image}`;
                    thumbsHtml += `
                        <img src="${src}"
                             class="stone-lightbox-thumb ${i === 0 ? "active" : ""}"
                             data-idx="${i}"/>
                    `;
                }

                thumbsEl.innerHTML = thumbsHtml;

                thumbsEl.querySelectorAll(".stone-lightbox-thumb").forEach((th) => {
                    th.addEventListener("click", () => showPhoto(parseInt(th.dataset.idx, 10)));
                });

                prevBtn.addEventListener("click", () => {
                    if (currentIdx > 0) showPhoto(currentIdx - 1);
                });

                nextBtn.addEventListener("click", () => {
                    if (currentIdx < photos.length - 1) showPhoto(currentIdx + 1);
                });
            }

            showPhoto(0);
        } catch (error) {
            console.error("[ToBeAllocated] Error cargando fotos:", error);
            bodyEl.innerHTML = `
                <div class="stone-lightbox-loading">
                    <i class="fa fa-exclamation-triangle fa-2x text-danger"></i>
                    <div class="mt-2 text-danger">
                        Error cargando fotos: ${this._escapeHtml(error.message || error)}
                    </div>
                </div>
            `;
        }
    }

    _destroyLightbox() {
        if (this._lightboxKeyHandler) {
            document.removeEventListener("keydown", this._lightboxKeyHandler);
            this._lightboxKeyHandler = null;
        }

        if (this._lightboxRoot) {
            this._lightboxRoot.remove();
            this._lightboxRoot = null;
        }
    }

    destroyAllocationPopup() {
        if (this._allocationPopupObserver) {
            this._allocationPopupObserver.disconnect();
            this._allocationPopupObserver = null;
        }

        if (this._allocationPopupKeyHandler) {
            document.removeEventListener("keydown", this._allocationPopupKeyHandler);
            this._allocationPopupKeyHandler = null;
        }

        if (this._allocationPopupRoot) {
            this._allocationPopupRoot.remove();
            this._allocationPopupRoot = null;
        }
    }

    async openAllocationPopup(line, ev) {
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

        this.destroyAllocationPopup();

        this.state.assigning[line.id] = true;

        try {
            const [saleLine] = await this.orm.read(
                "sale.order.line",
                [line.id],
                [
                    "id",
                    "product_id",
                    "product_uom_qty",
                    "lot_ids",
                    "x_lot_breakdown_json",
                ]
            );

            if (!saleLine) {
                this.notification.add("No se encontró la línea de venta.", {
                    type: "warning",
                });
                return;
            }

            const currentLotIds = this._normalizeLotIds(saleLine.lot_ids);
            const currentBreakdown = this._normalizeBreakdown(saleLine.x_lot_breakdown_json);

            this._allocationPopupRoot = document.createElement("div");
            this._allocationPopupRoot.className = "stone-popup-root";
            document.body.appendChild(this._allocationPopupRoot);

            this._renderAllocationPopupDOM({
                line,
                saleLine,
                productId: line.product_id,
                productName: line.product_name || "",
                soName: line.so_name || "",
                customer: line.customer || "",
                qtyOrdered: line.qty_ordered || saleLine.product_uom_qty || 0,
                qtyAssigned: line.qty_assigned || 0,
                qtyPending: line.qty_pending || 0,
                currentLotIds,
                currentBreakdown,
            });
        } catch (error) {
            console.error("[ToBeAllocated] Error abriendo popup de asignación:", error);
            this.notification.add(
                "Error al abrir asignación: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.assigning[line.id] = false;
        }
    }

    _renderAllocationPopupDOM(config) {
        const root = this._allocationPopupRoot;
        const PAGE_SIZE = 35;

        const orderedQty = Number(
            config.qtyOrdered !== undefined && config.qtyOrdered !== null
                ? config.qtyOrdered
                : (
                    config.saleLine?.product_uom_qty !== undefined && config.saleLine?.product_uom_qty !== null
                        ? config.saleLine.product_uom_qty
                        : (config.qtyPending || 0)
                )
        );

        const assignedQty = Number(config.qtyAssigned || 0);

        const pendingQty = Number(
            config.qtyPending !== undefined && config.qtyPending !== null
                ? config.qtyPending
                : Math.max(orderedQty - assignedQty, 0)
        );

        const state = {
            quants: [],
            totalCount: 0,
            hasMore: false,
            isLoading: false,
            isLoadingMore: false,
            page: 0,
            pendingIds: new Set(config.currentLotIds || []),
            pendingBreakdown: { ...(config.currentBreakdown || {}) },

            // Objetivo total actual de la línea.
            // Si la línea fue cambiada manualmente a 50 m² y ya tiene 24.92 m² asignados,
            // el popup debe medir el avance contra 50 m², no contra el pendiente.
            requestedQty: orderedQty,

            // Referencias informativas del estado actual.
            assignedQty,
            pendingQty,

            requestedUnit: "m²",
            qtyCache: {},
            filters: {
                lot_name: "",
                bloque: "",
                atado: "",
                alto_min: "",
                ancho_min: "",
                tipo: "",
            },
        };

        let searchTimeout = null;

        root.innerHTML = `
            <div class="stone-popup-overlay" id="stone-overlay">
                <div class="stone-popup-container">

                    <div class="stone-popup-header">
                        <div class="stone-popup-title">
                            <i class="fa fa-th me-2"></i>
                            Asignar lotes
                            <span class="stone-popup-subtitle">
                                — ${this._escapeHtml(config.productName)}
                            </span>
                        </div>

                        <div class="stone-popup-header-actions">
                            <span class="stone-badge-selected" title="${this._escapeHtml(config.soName)}">
                                <i class="fa fa-file-text-o me-1"></i>
                                ${this._escapeHtml(config.soName)}
                            </span>

                            <span class="stone-badge-selected" title="${this._escapeHtml(config.customer)}">
                                <i class="fa fa-user me-1"></i>
                                ${this._escapeHtml(config.customer)}
                            </span>

                            <span class="stone-badge-requested">
                                <i class="fa fa-bullseye me-1"></i>
                                Mandado <span id="sp-badge-target">${this._fmtPlain(state.requestedQty)}</span>
                                <span id="sp-badge-target-unit">${this._escapeHtml(state.requestedUnit)}</span>
                            </span>

                            <span class="stone-badge-selected">
                                <i class="fa fa-check-circle me-1"></i>
                                <span id="sp-badge-count">${state.pendingIds.size}</span> selec.
                            </span>

                            <span class="stone-badge-qty-total">
                                <i class="fa fa-balance-scale me-1"></i>
                                <span id="sp-badge-qty">0.00</span>
                                <span id="sp-badge-unit">m²</span>
                            </span>

                            <button class="stone-btn stone-btn-accent" id="sp-confirm-top">
                                <i class="fa fa-check me-1"></i> Confirmar
                            </button>

                            <button class="stone-btn stone-btn-ghost" id="sp-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="stone-popup-allocation-summary" id="sp-allocation-summary">
                        <div class="stone-allocation-card stone-allocation-target">
                            <span class="stone-allocation-label">Mandado</span>
                            <strong id="sp-allocation-target">${this._fmtPlain(state.requestedQty)} ${this._escapeHtml(state.requestedUnit)}</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-selected">
                            <span class="stone-allocation-label">Asignado</span>
                            <strong id="sp-allocation-selected">0.00 ${this._escapeHtml(state.requestedUnit)}</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-remaining">
                            <span class="stone-allocation-label">Pendiente</span>
                            <strong id="sp-allocation-remaining">${this._fmtPlain(state.pendingQty)} ${this._escapeHtml(state.requestedUnit)}</strong>
                        </div>
                        <div class="stone-allocation-progress-box">
                            <div class="stone-allocation-progress-head">
                                <span id="sp-allocation-progress-text">0.00 de ${this._fmtPlain(state.requestedQty)} ${this._escapeHtml(state.requestedUnit)}</span>
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
                            <input type="text" class="stone-filter-input" id="sf-bloque" placeholder="Bloque..."/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Atado</label>
                            <input type="text" class="stone-filter-input" id="sf-atado" placeholder="Atado..."/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Alto mín.</label>
                            <input type="number" class="stone-filter-input stone-filter-sm" id="sf-alto" placeholder="0" step="0.01"/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Ancho mín.</label>
                            <input type="number" class="stone-filter-input stone-filter-sm" id="sf-ancho" placeholder="0" step="0.01"/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Tipo</label>
                            <select class="stone-filter-input" id="sf-tipo">
                                <option value="">Todos</option>
                                <option value="placa">Placa</option>
                                <option value="formato">Formato</option>
                                <option value="pieza">Pieza</option>
                            </select>
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
                            <span class="stone-filter-stat-count">
                                Pedido: <strong class="ms-1">${this._fmt(config.qtyOrdered)}</strong>
                            </span>
                            <span class="stone-filter-stat-count ms-2">
                                Pendiente: <strong class="ms-1">${this._fmt(config.qtyPending)}</strong>
                            </span>
                            <span id="sp-stat" class="stone-filter-stat-loading ms-2">
                                <i class="fa fa-circle-o-notch fa-spin me-1"></i> Buscando...
                            </span>
                        </div>
                    </div>

                    <div class="stone-popup-body" id="sp-body">
                        <div class="stone-empty-state">
                            <i class="fa fa-circle-o-notch fa-spin fa-2x text-muted"></i>
                            <div class="stone-empty-text mt-2">Cargando inventario...</div>
                        </div>
                    </div>

                    <div class="stone-popup-footer">
                        <span class="stone-footer-info" id="sp-footer-info">—</span>

                        <div class="stone-footer-qty-summary" id="sp-footer-qty">
                            <span id="sp-footer-qty-text">0.00 m²</span>
                        </div>

                        <div class="stone-footer-actions">
                            <button class="stone-btn stone-btn-outline" id="sp-cancel">
                                Cancelar
                            </button>

                            <button class="stone-btn stone-btn-primary-dark" id="sp-open-order">
                                <i class="fa fa-external-link me-1"></i> Abrir pedido
                            </button>

                            <button class="stone-btn stone-btn-primary-dark" id="sp-confirm-bottom">
                                <i class="fa fa-check me-1"></i> Guardar asignación
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
        const allocationTarget = root.querySelector("#sp-allocation-target");
        const allocationSelected = root.querySelector("#sp-allocation-selected");
        const allocationRemaining = root.querySelector("#sp-allocation-remaining");
        const allocationProgressText = root.querySelector("#sp-allocation-progress-text");
        const allocationProgressLabel = root.querySelector("#sp-allocation-progress-label");
        const allocationProgressFill = root.querySelector("#sp-allocation-progress-fill");

        const cacheQuantForTotals = (q) => {
            const lotId = q && q.lot_id ? q.lot_id[0] : 0;
            if (!lotId) return;

            const key = String(lotId);
            const current = state.qtyCache[key] || {
                qty: 0,
                tipo: (q.x_tipo || "placa").toLowerCase(),
            };

            state.qtyCache[key] = {
                qty: q.quantity || current.qty || 0,
                tipo: (q.x_tipo || current.tipo || "placa").toLowerCase(),
            };
        };

        const cacheQuantListForTotals = (items) => {
            for (const q of items || []) {
                cacheQuantForTotals(q);
            }
        };

        const ensureQtyCacheForPending = async () => {
            const missingIds = Array.from(state.pendingIds).filter(
                (lotId) => !state.qtyCache[String(lotId)]
            );

            if (!missingIds.length) return;

            try {
                const [lotsData, quants] = await Promise.all([
                    this.orm.searchRead(
                        "stock.lot",
                        [["id", "in", missingIds]],
                        ["id", "x_tipo"],
                        { limit: missingIds.length }
                    ),
                    this.orm.searchRead(
                        "stock.quant",
                        [
                            ["lot_id", "in", missingIds],
                            ["location_id.usage", "=", "internal"],
                            ["quantity", ">", 0],
                        ],
                        ["lot_id", "quantity"],
                        { limit: missingIds.length * 5 }
                    ),
                ]);

                const tipoMap = {};
                for (const lot of lotsData || []) {
                    tipoMap[lot.id] = (lot.x_tipo || "placa").toLowerCase();
                }

                for (const lotId of missingIds) {
                    state.qtyCache[String(lotId)] = {
                        qty: 0,
                        tipo: tipoMap[lotId] || "placa",
                    };
                }

                for (const q of quants || []) {
                    const lotId = q.lot_id ? q.lot_id[0] : 0;
                    if (!lotId) continue;

                    const key = String(lotId);
                    if (!state.qtyCache[key]) {
                        state.qtyCache[key] = {
                            qty: 0,
                            tipo: tipoMap[lotId] || "placa",
                        };
                    }

                    state.qtyCache[key].qty += q.quantity || 0;
                }
            } catch (error) {
                console.warn("[ToBeAllocated] No se pudo precargar cantidad de lotes seleccionados:", error);
            }
        };

        const computeSelectedTotals = () => {
            let totalM2 = 0;
            let totalPiezas = 0;
            let hasPiezas = false;
            let hasM2 = false;

            for (const lotId of state.pendingIds) {
                const lotIdStr = String(lotId);
                const cached = state.qtyCache[lotIdStr];
                const q = state.quants.find((qq) => qq.lot_id && qq.lot_id[0] === lotId);
                const tipo = (cached?.tipo || q?.x_tipo || "placa").toLowerCase();

                let qty = 0;

                if (
                    (tipo === "formato" || tipo === "pieza")
                    && state.pendingBreakdown[lotIdStr] !== undefined
                ) {
                    qty = parseFloat(state.pendingBreakdown[lotIdStr]) || 0;
                } else if (cached) {
                    qty = cached.qty || 0;
                } else if (q) {
                    qty = q.quantity || 0;
                } else if (
                    config.currentBreakdown
                    && config.currentBreakdown[lotIdStr] !== undefined
                ) {
                    qty = parseFloat(config.currentBreakdown[lotIdStr]) || 0;
                }

                if (tipo === "pieza") {
                    totalPiezas += qty;
                    hasPiezas = true;
                } else {
                    totalM2 += qty;
                    hasM2 = true;
                }
            }

            return { totalM2, totalPiezas, hasM2, hasPiezas };
        };

        const updateQtyDisplay = () => {
            const totals = computeSelectedTotals();
            const { totalM2, totalPiezas, hasM2, hasPiezas } = totals;

            if (hasM2 && hasPiezas) {
                badgeQty.textContent = this._fmtPlain(totalM2);
                badgeUnit.textContent = `m² + ${this._fmtPlain(totalPiezas)} pzas`;
            } else if (hasPiezas && !hasM2) {
                badgeQty.textContent = this._fmtPlain(totalPiezas);
                badgeUnit.textContent = "pzas";
            } else {
                badgeQty.textContent = this._fmtPlain(totalM2);
                badgeUnit.textContent = "m²";
            }

            const parts = [];
            if (hasM2) parts.push(`${this._fmtPlain(totalM2)} m²`);
            if (hasPiezas) parts.push(`${this._fmtPlain(totalPiezas)} pzas`);
            footerQtyText.textContent = parts.length > 0 ? parts.join(" + ") : "0.00 m²";

            const selectedForTarget = this._getAllocationBaseFromTotals(totals, state.requestedUnit);
            const requestedQty = state.requestedQty || 0;
            const requestedUnit = state.requestedUnit || "m²";
            const rawPercent = requestedQty > 0 ? (selectedForTarget / requestedQty) * 100 : 0;
            const barPercent = Math.max(0, Math.min(rawPercent, 100));
            const diff = requestedQty - selectedForTarget;

            if (allocationTarget) {
                allocationTarget.textContent = `${this._fmtPlain(requestedQty)} ${requestedUnit}`;
            }

            if (allocationSelected) {
                allocationSelected.textContent = `${this._fmtPlain(selectedForTarget)} ${requestedUnit}`;
            }

            if (allocationRemaining) {
                allocationRemaining.textContent =
                    `${diff >= 0 ? this._fmtPlain(diff) : "+" + this._fmtPlain(Math.abs(diff))} ${requestedUnit}`;
            }

            if (allocationProgressText) {
                allocationProgressText.textContent =
                    `${this._fmtPlain(selectedForTarget)} de ${this._fmtPlain(requestedQty)} ${requestedUnit}`;
            }

            if (allocationProgressLabel) {
                allocationProgressLabel.textContent = `${this._fmtPct(rawPercent)}%`;
            }

            if (allocationProgressFill) {
                allocationProgressFill.style.width = `${barPercent}%`;
            }

            if (allocationSummary) {
                allocationSummary.classList.toggle("is-empty-target", requestedQty <= 0);
                allocationSummary.classList.toggle("is-under", requestedQty > 0 && rawPercent < 99.995);
                allocationSummary.classList.toggle("is-ok", requestedQty > 0 && rawPercent >= 99.995 && rawPercent <= 100.005);
                allocationSummary.classList.toggle("is-over", requestedQty > 0 && rawPercent > 100.005);
            }
        };

        const updateBadge = () => {
            badgeCount.textContent = state.pendingIds.size;
            updateQtyDisplay();
        };

        const updateStats = () => {
            stat.className = "stone-filter-stat-count ms-2";
            stat.innerHTML = `${state.totalCount} lotes`;
            footerInfo.innerHTML = `<strong>${state.quants.length}</strong> de <strong>${state.totalCount}</strong> visibles`;
        };

        const renderTable = () => {
            if (state.quants.length === 0 && !state.isLoading) {
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-inbox fa-3x text-muted"></i>
                        <div class="stone-empty-text mt-2">No hay lotes con estos filtros</div>
                    </div>
                `;
                updateStats();
                updateBadge();
                return;
            }

            let rows = "";

            for (const q of state.quants) {
                const lotId = q.lot_id ? q.lot_id[0] : 0;
                const lotName = q.lot_id ? q.lot_id[1] : "-";
                const loc = q.location_id ? String(q.location_id[1] || "").split("/").pop() : "-";
                const sel = state.pendingIds.has(lotId);
                const reserved = q.reserved_quantity > 0;
                const tipo = (q.x_tipo || "placa").toLowerCase();
                const isPartial = tipo === "formato" || tipo === "pieza";
                const lotIdStr = String(lotId);
                const qtyLabel = tipo === "pieza" ? "pzas" : "m²";
                const inputStep = tipo === "pieza" ? "1" : "0.01";

                let statusBadge;
                if (sel) {
                    statusBadge = `<span class="stone-tag stone-tag-ok">Selec.</span>`;
                } else if (reserved) {
                    statusBadge = `<span class="stone-tag stone-tag-warn">Reserv.</span>`;
                } else {
                    statusBadge = `<span class="stone-tag stone-tag-free">Libre</span>`;
                }

                const tipoLabel = tipo.charAt(0).toUpperCase() + tipo.slice(1);

                let qtyCell;
                if (isPartial && sel) {
                    const currentVal = state.pendingBreakdown[lotIdStr] !== undefined
                        ? state.pendingBreakdown[lotIdStr]
                        : q.quantity;

                    qtyCell = `
                        <input type="number"
                               class="stone-popup-qty-input"
                               data-lot-id="${lotId}"
                               data-max="${q.quantity}"
                               step="${inputStep}"
                               min="0"
                               max="${q.quantity}"
                               value="${currentVal}"/>
                    `;
                } else if (isPartial && !sel) {
                    qtyCell = `<span class="text-muted">—</span>`;
                } else {
                    qtyCell = `<span>${this._fmtPlain(q.quantity)} ${qtyLabel}</span>`;
                }

                const photoCell = this._renderPhotoCell(
                    q.x_fotografia_principal || false,
                    q.x_cantidad_fotos || 0,
                    lotId,
                    lotName
                );

                rows += `
                    <tr class="${sel ? "row-sel" : ""}"
                        data-lot-id="${lotId}"
                        data-reserved="${reserved ? "1" : "0"}"
                        data-tipo="${tipo}">
                        <td class="col-chk">
                            <div class="stone-chkbox ${sel ? "checked" : ""}">
                                ${sel ? '<i class="fa fa-check"></i>' : ""}
                            </div>
                        </td>
                        <td class="col-photo">${photoCell}</td>
                        <td class="cell-lot">${this._escapeHtml(lotName)}</td>
                        <td>${this._escapeHtml(q.x_bloque || "-")}</td>
                        <td>${this._escapeHtml(q.x_atado || "-")}</td>
                        <td class="col-num">${this._fmtDim(q.x_alto)}</td>
                        <td class="col-num">${this._fmtDim(q.x_ancho)}</td>
                        <td class="col-num">${this._fmtDim(q.x_grosor)}</td>
                        <td class="col-num fw-semibold">${this._fmtPlain(q.quantity)}</td>
                        <td><span class="stone-tag stone-tag-tipo-${tipo}">${tipoLabel}</span></td>
                        <td class="col-num col-popup-qty">${qtyCell}</td>
                        <td>${this._escapeHtml(q.x_color || "-")}</td>
                        <td class="cell-loc">${this._escapeHtml(loc)}</td>
                        <td>${statusBadge}</td>
                    </tr>
                `;
            }

            const sentinel = `
                <div id="sp-sentinel" class="stone-scroll-sentinel">
                    ${
                        state.isLoadingMore
                            ? '<div class="stone-loading-more"><i class="fa fa-circle-o-notch fa-spin me-1"></i> Cargando más...</div>'
                            : ""
                    }
                    ${
                        state.hasMore && !state.isLoadingMore
                            ? '<div class="stone-scroll-hint"><i class="fa fa-chevron-down me-1"></i> Más resultados</div>'
                            : ""
                    }
                </div>
            `;

            body.innerHTML = `
                <table class="stone-popup-table">
                    <thead>
                        <tr>
                            <th class="col-chk">✓</th>
                            <th class="col-photo">Foto</th>
                            <th>Lote</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="col-num">Alto</th>
                            <th class="col-num">Ancho</th>
                            <th class="col-num">Esp.</th>
                            <th class="col-num">Disp.</th>
                            <th>Tipo</th>
                            <th class="col-num">A tomar</th>
                            <th>Color</th>
                            <th>Ubic.</th>
                            <th>Estado</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
                ${sentinel}
            `;

            updateStats();
            updateBadge();

            body.querySelectorAll("tr[data-lot-id]").forEach((tr) => {
                tr.addEventListener("click", (ev) => {
                    if (ev.target.closest(".stone-popup-qty-input")) return;
                    if (ev.target.closest(".stone-photo-cell[data-has-photo]")) return;

                    const lotId = parseInt(tr.dataset.lotId, 10);
                    if (!lotId) return;

                    const tipo = tr.dataset.tipo || "placa";
                    const isPartial = tipo === "formato" || tipo === "pieza";

                    if (state.pendingIds.has(lotId)) {
                        state.pendingIds.delete(lotId);
                        delete state.pendingBreakdown[String(lotId)];
                    } else {
                        state.pendingIds.add(lotId);

                        if (isPartial) {
                            const q = state.quants.find((qq) => qq.lot_id && qq.lot_id[0] === lotId);
                            if (q) {
                                state.pendingBreakdown[String(lotId)] = q.quantity || 0;
                            }
                        }
                    }

                    updateBadge();
                    renderTable();
                });
            });

            body.querySelectorAll(".stone-popup-qty-input").forEach((input) => {
                input.addEventListener("click", (ev) => ev.stopPropagation());

                input.addEventListener("input", () => {
                    const lotId = parseInt(input.dataset.lotId, 10);
                    const max = parseFloat(input.dataset.max) || 0;
                    let val = parseFloat(input.value) || 0;

                    if (val < 0) val = 0;
                    if (val > max) {
                        val = max;
                        input.value = val;
                    }

                    state.pendingBreakdown[String(lotId)] = val;
                    updateQtyDisplay();
                });
            });

            this._bindPhotoClicks(body);

            if (this._allocationPopupObserver) {
                this._allocationPopupObserver.disconnect();
                this._allocationPopupObserver = null;
            }

            const sentinelEl = body.querySelector("#sp-sentinel");
            if (sentinelEl && state.hasMore) {
                this._allocationPopupObserver = new IntersectionObserver(
                    (entries) => {
                        if (
                            entries[0].isIntersecting
                            && state.hasMore
                            && !state.isLoadingMore
                        ) {
                            loadPage(state.page + 1, false);
                        }
                    },
                    { root: body, rootMargin: "100px", threshold: 0.1 }
                );
                this._allocationPopupObserver.observe(sentinelEl);
            }
        };

        const loadPage = async (page, reset) => {
            if (reset) {
                state.isLoading = true;
                state.quants = [];
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-circle-o-notch fa-spin fa-2x text-muted"></i>
                        <div class="stone-empty-text mt-2">Buscando...</div>
                    </div>
                `;
                stat.className = "stone-filter-stat-loading ms-2";
                stat.innerHTML = `<i class="fa fa-circle-o-notch fa-spin me-1"></i> Buscando...`;
            } else {
                state.isLoadingMore = true;
            }

            try {
                let result;

                try {
                    result = await this.orm.call(
                        "stock.quant",
                        "search_stone_inventory_for_so_paginated",
                        [],
                        {
                            product_id: config.productId,
                            filters: state.filters,
                            current_lot_ids: Array.from(state.pendingIds),
                            page,
                            page_size: PAGE_SIZE,
                        }
                    );
                } catch (_error) {
                    const all = await this.orm.call(
                        "stock.quant",
                        "search_stone_inventory_for_so",
                        [],
                        {
                            product_id: config.productId,
                            filters: state.filters,
                            current_lot_ids: Array.from(state.pendingIds),
                        }
                    );

                    result = {
                        items: (all || []).slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
                        total: (all || []).length,
                    };
                }

                const items = result.items || [];
                cacheQuantListForTotals(items);

                if (reset || page === 0) {
                    state.quants = items;
                } else {
                    const seen = new Set(state.quants.map((q) => q.id));
                    const newItems = items.filter((q) => !seen.has(q.id));
                    state.quants = [...state.quants, ...newItems];
                }

                state.totalCount = result.total || 0;
                state.page = page;
                state.hasMore = state.quants.length < state.totalCount;

                await ensureQtyCacheForPending();
            } catch (error) {
                console.error("[ToBeAllocated] Error cargando lotes:", error);
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-exclamation-triangle fa-2x text-danger"></i>
                        <div class="stone-empty-text mt-2 text-danger">
                            Error: ${this._escapeHtml(error.message || error)}
                        </div>
                    </div>
                `;
                return;
            } finally {
                state.isLoading = false;
                state.isLoadingMore = false;
            }

            renderTable();
        };

        const doSelectAll = () => {
            for (const q of state.quants) {
                cacheQuantForTotals(q);
                const lotId = q.lot_id ? q.lot_id[0] : 0;
                if (!lotId) continue;

                state.pendingIds.add(lotId);

                const tipo = (q.x_tipo || "placa").toLowerCase();
                if (
                    (tipo === "formato" || tipo === "pieza")
                    && state.pendingBreakdown[String(lotId)] === undefined
                ) {
                    state.pendingBreakdown[String(lotId)] = q.quantity || 0;
                }
            }

            updateBadge();
            renderTable();
        };

        const doClearAll = () => {
            state.pendingIds.clear();
            state.pendingBreakdown = {};
            updateBadge();
            renderTable();
        };

        const doConfirm = async () => {
            const newIds = Array.from(state.pendingIds);

            const cleanBreakdown = {};
            for (const [key, value] of Object.entries(state.pendingBreakdown)) {
                if (state.pendingIds.has(parseInt(key, 10))) {
                    cleanBreakdown[key] = value;
                }
            }

            try {
                const result = await this.orm.call(
                    "sale.order.line",
                    "action_tc_apply_allocation_from_hub",
                    [[config.line.id], newIds, cleanBreakdown]
                );

                const finalQty = result && result.final_qty !== undefined
                    ? this._fmtPlain(result.final_qty)
                    : "0.00";
                const uomName = result && result.uom_name ? ` ${result.uom_name}` : "";

                this.notification.add(
                    `Asignación guardada. Cantidad final: ${finalQty}${uomName}`,
                    {
                        type: "success",
                        sticky: false,
                    }
                );

                this.destroyAllocationPopup();
                await this.loadData();
            } catch (error) {
                console.error("[ToBeAllocated] Error guardando asignación:", error);
                this.notification.add(
                    "Error guardando asignación: " + (error.message || error),
                    { type: "danger" }
                );
            }
        };

        const doClose = () => this.destroyAllocationPopup();

        root.querySelector("#sp-close").addEventListener("click", doClose);
        root.querySelector("#sp-cancel").addEventListener("click", doClose);
        root.querySelector("#sp-confirm-top").addEventListener("click", doConfirm);
        root.querySelector("#sp-confirm-bottom").addEventListener("click", doConfirm);
        root.querySelector("#sp-select-all").addEventListener("click", doSelectAll);
        root.querySelector("#sp-clear-all").addEventListener("click", doClearAll);

        root.querySelector("#sp-open-order").addEventListener("click", (ev) => {
            ev.stopPropagation();
            this.destroyAllocationPopup();
            this.openSaleOrder(config.line.so_id, ev);
        });

        overlay.addEventListener("click", (ev) => {
            if (ev.target === overlay) doClose();
        });

        const keyHandler = (ev) => {
            if (ev.key === "Escape") doClose();
        };

        document.addEventListener("keydown", keyHandler);
        this._allocationPopupKeyHandler = keyHandler;

        const bindFilter = (id, key) => {
            const input = root.querySelector(`#${id}`);
            if (!input) return;

            const handler = (ev) => {
                state.filters[key] = ev.target.value;
                if (searchTimeout) clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => loadPage(0, true), 350);
            };

            input.addEventListener("input", handler);
            input.addEventListener("change", handler);
        };

        bindFilter("sf-lot", "lot_name");
        bindFilter("sf-bloque", "bloque");
        bindFilter("sf-atado", "atado");
        bindFilter("sf-alto", "alto_min");
        bindFilter("sf-ancho", "ancho_min");
        bindFilter("sf-tipo", "tipo");

        loadPage(0, true);
    }

    // =========================================================================
    // MANDAR A COMPRA
    // =========================================================================

    async sendToPurchase(line, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!line || !line.id) return;

        const confirmed = window.confirm(
            `¿Mandar a pedir el pendiente de ${line.product_name} en ${line.so_name}?\n\n` +
            "Esto moverá la línea a To Be Purchased aunque exista stock disponible."
        );

        if (!confirmed) return;

        this.state.sending[line.id] = true;

        try {
            const result = await this.orm.call(
                "sale.allocation.manager.logic",
                "send_to_purchase",
                [[line.id], "Stock rechazado desde To Be Allocated"]
            );

            if (result && result.error) {
                this.notification.add(result.error, { type: "danger" });
                return;
            }

            this.notification.add("Línea enviada a To Be Purchased", {
                type: "success",
                sticky: false,
            });

            await this.loadData();
        } catch (error) {
            console.error("[ToBeAllocated] Error enviando a compra:", error);
            this.notification.add(
                "Error al mandar pedido: " + (error.message || error),
                { type: "danger" }
            );
        } finally {
            this.state.sending[line.id] = false;
        }
    }

    // =========================================================================
    // FORMATO
    // =========================================================================

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

    paymentClass(value) {
        const n = Number(value || 0);

        if (n >= 80) return "o_tba_pay_high";
        if (n >= 30) return "o_tba_pay_mid";

        return "o_tba_pay_low";
    }

    get totalLines() {
        return this.state.data.length;
    }

    get totalPending() {
        return this.state.data.reduce(
            (sum, line) => sum + (line.qty_pending || 0),
            0
        );
    }
}

ToBeAllocated.template = "stock_transit_allocation.ToBeAllocated";

registry.category("actions").add(
    "action_to_be_allocated",
    ToBeAllocated,
    { force: true }
);