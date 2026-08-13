/** @odoo-module **/

// EMBARQUES — mapa global de la flota (vista principal de Torre de Control).
//
// Un solo Leaflet (vendorizado, preferCanvas) con TODOS los viajes activos:
// ruta recorrida en azul sólido, ruta futura punteada, buque como marcador
// SVG coloreado por estatus, puertos de origen/destino, y un panel lateral
// con búsqueda que filtra tarjetas Y capas del mapa al mismo tiempo.

import { registry } from "@web/core/registry";
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";

// Paleta por estatus del viaje (branding azul + semáforo de entrega)
const STATUS_COLORS = {
    solicitud:         "#f59e0b",
    production:        "#f59e0b",
    booking:           "#8b5cf6",
    puerto_origen:     "#8b5cf6",
    on_sea:            "#2563eb",
    puerto_destino:    "#ec4899",
    arrived_port:      "#ec4899",
    reception_pending: "#0ea5e9",
    delivered:         "#22c55e",
};

const SVG_SHIP = (color) =>
    `<svg viewBox="0 0 24 24" width="17" height="17" fill="none">
        <path d="M3 15l1.5 4h15L21 15l-9-2.6L3 15z" fill="${color}"/>
        <path d="M7 12V7h10v5" stroke="${color}" stroke-width="1.6"/>
        <path d="M12 7V4" stroke="${color}" stroke-width="1.6"/>
    </svg>`;

export class TransitFleetMap extends Component {
    static template = "stock_transit_allocation.TransitFleetMap";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("fleetMap");

        this.state = useState({
            loading: true,
            voyages: [],
            searchText: "",
            statusFilter: "active",   // active | all | delivered
            selectedId: false,
            panelOpen: true,
        });

        this.layersById = {};

        onMounted(() => this.start());
        onWillUnmount(() => this.stop());
    }

    // ─── Ciclo de vida del mapa ──────────────────────────────────────────

    async start() {
        const L = window.L;
        if (!L || !this.mapRef.el) {
            this.notification.add("No se pudo inicializar el mapa (Leaflet).", { type: "danger" });
            return;
        }
        // UN SOLO MUNDO: sin réplicas horizontales de los continentes.
        // noWrap corta la repetición de tiles, maxBounds encierra el
        // paneo en una sola copia y minZoom impide alejarse tanto que
        // quepan dos mundos en pantalla.
        const WORLD = L.latLngBounds([[-85, -180], [85, 180]]);
        this.map = L.map(this.mapRef.el, {
            scrollWheelZoom: true,
            preferCanvas: true,
            zoomAnimation: true,
            wheelDebounceTime: 25,
            worldCopyJump: false,
            zoomControl: false,
            maxBounds: WORLD,
            maxBoundsViscosity: 1.0,
            minZoom: 2,
        }).setView([23.0, -60.0], 3);
        L.control.zoom({ position: "bottomright" }).addTo(this.map);
        L.tileLayer(
            "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            {
                attribution: "&copy; OpenStreetMap &copy; CARTO",
                maxZoom: 19,
                updateWhenZooming: false,
                noWrap: true,
                bounds: WORLD,
            }
        ).addTo(this.map);
        this.layerGroup = L.layerGroup().addTo(this.map);
        await this.load();
    }

    stop() {
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "stock.transit.voyage", "tv_get_fleet_map_data", []);
            this.state.voyages = data.voyages || [];
            this.redraw();
            this.fitAll();
        } catch (e) {
            console.error("[FleetMap] Error:", e);
            this.notification.add("Error al cargar los embarques", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        await this.load();
    }

    // ─── Filtro compartido lista+mapa ────────────────────────────────────

    get filteredVoyages() {
        const q = this.state.searchText.trim().toLowerCase();
        const f = this.state.statusFilter;
        return this.state.voyages.filter((v) => {
            if (f === "active" && v.status === "delivered") return false;
            if (f === "delivered" && v.status !== "delivered") return false;
            if (!q) return true;
            return [
                v.name, v.po_name, v.partner_ref, v.supplier,
                v.shipment_name, v.proforma_ref, v.bl_number,
                v.vessel_name, v.shipping_line,
                (v.containers || []).join(" "),
            ].join(" ").toLowerCase().includes(q);
        });
    }

    get counters() {
        const all = this.state.voyages;
        return {
            total: all.length,
            active: all.filter((v) => v.status !== "delivered").length,
            delivered: all.filter((v) => v.status === "delivered").length,
            tracked: all.filter((v) => this.hasRoute(v)).length,
        };
    }

    onSearch(ev) {
        this.state.searchText = ev.target.value;
        this.redraw();
    }

    clearSearch() {
        this.state.searchText = "";
        this.redraw();
        this.fitAll();
    }

    setFilter(f) {
        this.state.statusFilter = f;
        this.redraw();
        this.fitAll();
    }

    togglePanel() {
        this.state.panelOpen = !this.state.panelOpen;
        // Leaflet debe re-medir el contenedor cuando el panel entra/sale.
        setTimeout(() => this.map && this.map.invalidateSize(), 220);
    }

    // ─── Dibujo ──────────────────────────────────────────────────────────

    hasRoute(v) {
        const r = v.route || {};
        return Boolean(
            v.current_loc ||
            (r.past || []).length || (r.future || []).length ||
            (r.current_past || []).length || (r.current_future || []).length ||
            (v.origin && v.origin.loc) || (v.destination && v.destination.loc)
        );
    }

    statusColor(v) {
        return STATUS_COLORS[v.status] || "#64748b";
    }

    redraw() {
        const L = window.L;
        if (!L || !this.map) return;
        this.layerGroup.clearLayers();
        this.layersById = {};

        for (const v of this.filteredVoyages) {
            if (!this.hasRoute(v)) continue;
            const color = this.statusColor(v);
            const layers = [];
            const r = v.route || {};

            // Recorrido: sólido. Por recorrer: punteado (mismo color, tenue).
            const past = [...(r.past || []), ...(r.current_past ? [r.current_past] : [])];
            const future = [...(r.current_future ? [r.current_future] : []), ...(r.future || [])];
            for (const line of past) {
                if (line && line.length > 1) {
                    layers.push(L.polyline(line, {
                        color, weight: 3, opacity: 0.85,
                    }));
                }
            }
            for (const line of future) {
                if (line && line.length > 1) {
                    layers.push(L.polyline(line, {
                        color, weight: 2.5, opacity: 0.5, dashArray: "6 7",
                    }));
                }
            }

            // Puertos: origen (salida) y destino (llegada)
            if (v.origin && v.origin.loc) {
                layers.push(L.circleMarker(v.origin.loc, {
                    radius: 5, color, weight: 2, fillColor: "#ffffff",
                    fillOpacity: 1,
                }).bindTooltip(`Origen: ${v.origin.name || "?"}`));
            }
            if (v.destination && v.destination.loc) {
                layers.push(L.circleMarker(v.destination.loc, {
                    radius: 6, color, weight: 2.5, fillColor: color,
                    fillOpacity: 0.9,
                }).bindTooltip(`Destino: ${v.destination.name || "?"} · ETA ${v.eta_label || "?"}`));
            }

            // Buque: marcador principal con popup de ficha completa
            const anchor = v.current_loc
                || (v.destination && v.destination.loc)
                || (v.origin && v.origin.loc);
            if (anchor) {
                const icon = L.divIcon({
                    className: "tfm-ship-icon",
                    html: `<div class="tfm-ship" style="border-color:${color}">${SVG_SHIP(color)}</div>`,
                    iconSize: [30, 30],
                    iconAnchor: [15, 15],
                });
                const marker = L.marker(anchor, { icon, zIndexOffset: 500 });
                marker.bindPopup(this._popupHtml(v), {
                    maxWidth: 340, className: "tfm-popup",
                });
                marker.on("popupopen", (ev) => {
                    this.state.selectedId = v.id;
                    const btn = ev.popup.getElement().querySelector(".tfm-popup__open");
                    if (btn) {
                        btn.addEventListener("click", () => this.openVoyage(v.id));
                    }
                });
                layers.push(marker);
            }

            const group = L.featureGroup(layers).addTo(this.layerGroup);
            this.layersById[v.id] = group;
        }
    }

    _esc(s) {
        return String(s || "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    _popupHtml(v) {
        const e = this._esc.bind(this);
        const conts = (v.containers || []).map(e).join(", ") || "—";
        const pct = Math.max(0, Math.min(100, Math.round(v.transit_pct || 0)));
        const delay = v.delay_days > 0
            ? `<span class="tfm-popup__delay">+${v.delay_days}d de retraso</span>` : "";
        return `
            <div class="tfm-popup__card">
                <div class="tfm-popup__head" style="border-color:${this.statusColor(v)}">
                    <b>${e(v.name)}</b>
                    <span class="tfm-popup__status" style="background:${this.statusColor(v)}">${e(v.status_label)}</span>
                </div>
                <div class="tfm-popup__grid">
                    <span>Proveedor</span><b>${e(v.supplier) || "—"}</b>
                    <span>OC</span><b>${e(v.po_name) || "—"} ${e(v.partner_ref)}</b>
                    <span>Embarque</span><b>${e(v.shipment_name) || "—"}</b>
                    <span>Buque</span><b>${e(v.vessel_name) || "—"} · ${e(v.shipping_line)}</b>
                    <span>BL</span><b>${e(v.bl_number) || "—"}</b>
                    <span>Contenedores</span><b>${conts}</b>
                    <span>Ruta</span><b>${e((v.origin && v.origin.name) || "?")} → ${e((v.destination && v.destination.name) || "?")}</b>
                    <span>ETD</span><b>${e(v.etd_label) || "—"}</b>
                    <span>ETA</span><b>${e(v.eta_label) || "—"} ${delay}</b>
                    <span>Volumen</span><b>${(v.total_m2 || 0).toLocaleString("es-MX", { maximumFractionDigits: 1 })} m²</b>
                </div>
                <div class="tfm-popup__bar"><div style="width:${pct}%;background:${this.statusColor(v)}"></div></div>
                <div class="tfm-popup__pct">${pct}% del tránsito</div>
                <button class="tfm-popup__open">Abrir embarque</button>
            </div>`;
    }

    // ─── Interacción lista ↔ mapa ────────────────────────────────────────

    focusVoyage(v) {
        this.state.selectedId = v.id;
        const group = this.layersById[v.id];
        if (group && this.map) {
            const bounds = group.getBounds();
            if (bounds.isValid()) {
                this.map.flyToBounds(bounds, { padding: [70, 70], maxZoom: 7, duration: 0.7 });
            }
            group.eachLayer((l) => {
                if (l.getPopup && l.getPopup()) {
                    setTimeout(() => l.openPopup(), 750);
                }
            });
        } else {
            this.notification.add(
                "Este viaje aún no tiene tracking en el mapa (sin datos ShipsGo).",
                { type: "info" });
        }
    }

    fitAll() {
        const L = window.L;
        if (!L || !this.map) return;
        const groups = Object.values(this.layersById);
        if (!groups.length) return;
        let bounds = null;
        for (const g of groups) {
            const b = g.getBounds();
            if (!b.isValid()) continue;
            bounds = bounds ? bounds.extend(b) : L.latLngBounds(b.getSouthWest(), b.getNorthEast());
        }
        if (bounds && bounds.isValid()) {
            this.map.fitBounds(bounds, { padding: [60, 60], maxZoom: 6 });
        }
    }

    openVoyage(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ─── Helpers de plantilla ────────────────────────────────────────────

    fmtDate(value) {
        return somFormatDate(value, { empty: "—" });
    }

    fmtM2(v) {
        return (v || 0).toLocaleString("es-MX", { maximumFractionDigits: 1 });
    }

    pct(v) {
        return Math.max(0, Math.min(100, Math.round(v.transit_pct || 0)));
    }

    etaBadge(v) {
        if (v.status === "delivered") return { label: "Entregado", cls: "tfm-eta--done" };
        if (v.delay_days > 0) return { label: `+${v.delay_days}d retraso`, cls: "tfm-eta--late" };
        if (v.eta_label) return { label: `ETA ${v.eta_label}`, cls: "tfm-eta--ok" };
        return { label: "Sin ETA", cls: "tfm-eta--none" };
    }
}

TransitFleetMap.template = "stock_transit_allocation.TransitFleetMap";
registry.category("lazy_components").add("TransitFleetMap", TransitFleetMap);
