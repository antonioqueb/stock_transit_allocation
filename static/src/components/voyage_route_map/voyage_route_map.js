/** @odoo-module **/

// SEGUIMIENTO DEL VIAJE — mapa Leaflet nativo en el formulario (27 sep 2026).
//
// Antes el servidor armaba el mapa con Folium y lo guardaba como HTML
// (iframe con Leaflet por CDN y fondo CARTO, que ahora pinta "API key
// required"). Ahora el widget dibuja directo desde shipsgo_payload (los
// datos de la última sincronización con ShipsGo) con el Leaflet
// vendorizado del módulo y fondo OpenStreetMap: sin llaves, sin CDN y
// siempre al día con el payload.

import { registry } from "@web/core/registry";
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";

// Leaflet vive en el bundle perezoso de los hubs (mismo del mapa de flota):
// se carga una sola vez por sesión y solo si no está ya en la página.
const LEAFLET_BUNDLE = "stock_transit_allocation.assets_hubs";
const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';
const BLUE = "#2563eb";

const SVG_SHIP =
    `<svg viewBox="0 0 24 24" width="17" height="17" fill="none">
        <path d="M3 15l1.5 4h15L21 15l-9-2.6L3 15z" fill="${BLUE}"/>
        <path d="M7 12V7h10v5" stroke="${BLUE}" stroke-width="1.6"/>
        <path d="M12 7V4" stroke="${BLUE}" stroke-width="1.6"/>
    </svg>`;

function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
}

function validLoc(loc) {
    return Array.isArray(loc) && loc.length === 2 &&
        Number.isFinite(Number(loc[0])) && Number.isFinite(Number(loc[1]));
}

export class VoyageRouteMap extends Component {
    static template = "stock_transit_allocation.VoyageRouteMap";
    static props = { ...standardFieldProps };

    setup() {
        this.mapRef = useRef("map");
        this.map = null;
        this.layer = null;
        this.drawnPayload = null;
        this.fitted = false;

        onWillStart(async () => {
            if (!window.L) {
                await loadBundle(LEAFLET_BUNDLE);
            }
        });
        onMounted(() => this.render());
        onPatched(() => this.render());
        onWillUnmount(() => {
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
            }
            if (this.map) {
                this.map.remove();
                this.map = null;
            }
        });
    }

    get payloadText() {
        return this.props.record.data[this.props.name] || "";
    }

    get data() {
        try {
            return this.payloadText ? JSON.parse(this.payloadText) : null;
        } catch {
            return null;
        }
    }

    get hasData() {
        const d = this.data;
        if (!d) {
            return false;
        }
        const r = d.route || {};
        return Boolean(
            validLoc(d.current_loc) ||
            (d.origin && validLoc(d.origin.loc)) ||
            (d.destination && validLoc(d.destination.loc)) ||
            (r.past || []).length || (r.future || []).length ||
            (r.current_past || []).length || (r.current_future || []).length
        );
    }

    // ─── Dibujo ──────────────────────────────────────────────────────────

    render() {
        const L = window.L;
        if (!L || !this.mapRef.el || !this.hasData) {
            return;
        }
        if (!this.map) {
            this.map = L.map(this.mapRef.el, {
                scrollWheelZoom: false,
                zoomControl: true,
                worldCopyJump: false,
                minZoom: 2,
            }).setView([20, -40], 2);
            L.tileLayer(OSM_TILES, { attribution: OSM_ATTR, maxZoom: 19 }).addTo(this.map);
            this.layer = L.featureGroup().addTo(this.map);
            // El mapa vive dentro de un <details> colapsado: nace con tamaño
            // 0. Al expandirse (o cambiar de ancho) se recalcula y, la
            // primera vez que tiene tamaño, se encuadra la ruta.
            this.resizeObserver = new ResizeObserver(() => {
                if (!this.map || !this.mapRef.el.offsetWidth) {
                    return;
                }
                this.map.invalidateSize();
                if (!this.fitted) {
                    this.fit();
                }
            });
            this.resizeObserver.observe(this.mapRef.el);
        }
        if (this.drawnPayload === this.payloadText) {
            return;
        }
        this.drawnPayload = this.payloadText;
        this.fitted = false;
        this.draw(this.data);
        if (this.mapRef.el.offsetWidth) {
            this.fit();
        }
    }

    fit() {
        const bounds = this.layer.getBounds();
        if (!bounds.isValid()) {
            return;
        }
        this.fitted = true;
        const ne = bounds.getNorthEast();
        const sw = bounds.getSouthWest();
        if (ne.lat === sw.lat && ne.lng === sw.lng) {
            this.map.setView(ne, 5);
        } else {
            this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 8 });
        }
    }

    // Ruta continua aunque cruce el antimeridiano (Asia → México): cada
    // punto se acerca al anterior ±360° (misma lógica del mapa de flota).
    unwrapRoute(r) {
        const order = [];
        for (const l of r.past || []) {
            if (l && l.length) order.push(["past", l]);
        }
        if (r.current_past && r.current_past.length) order.push(["current_past", r.current_past]);
        if (r.current_future && r.current_future.length) order.push(["current_future", r.current_future]);
        for (const l of r.future || []) {
            if (l && l.length) order.push(["future", l]);
        }
        let prev = null;
        let minLng = Infinity;
        const adjusted = [];
        for (const [kind, line] of order) {
            const newLine = [];
            for (const pt of line) {
                let lng = pt[1];
                if (prev !== null) {
                    while (lng - prev > 180) lng -= 360;
                    while (lng - prev < -180) lng += 360;
                }
                prev = lng;
                minLng = Math.min(minLng, lng);
                newLine.push([pt[0], lng]);
            }
            adjusted.push([kind, newLine]);
        }
        const shift = minLng < -180 ? 360 : 0;
        const out = { past: [], current_past: null, current_future: null, future: [] };
        for (const [kind, line] of adjusted) {
            const l2 = shift ? line.map((p) => [p[0], p[1] + shift]) : line;
            if (kind === "past") out.past.push(l2);
            else if (kind === "future") out.future.push(l2);
            else out[kind] = l2;
        }
        return out;
    }

    snapLng(loc, refLng) {
        if (!validLoc(loc) || refLng === null || refLng === undefined) {
            return validLoc(loc) ? loc : null;
        }
        let lng = loc[1];
        while (lng - refLng > 180) lng -= 360;
        while (lng - refLng < -180) lng += 360;
        return [loc[0], lng];
    }

    draw(d) {
        const L = window.L;
        this.layer.clearLayers();
        const u = this.unwrapRoute(d.route || {});

        // Recorrido: gris (tramos anteriores) y azul sólido (tramo actual).
        // Por recorrer: azul punteado (actual) y gris punteado (siguientes).
        for (const line of u.past) {
            if (line.length > 1) L.polyline(line, { color: "#6b7280", weight: 3, opacity: 0.7 }).addTo(this.layer);
        }
        if (u.current_past && u.current_past.length > 1) {
            L.polyline(u.current_past, { color: BLUE, weight: 4, opacity: 0.85 }).addTo(this.layer);
        }
        if (u.current_future && u.current_future.length > 1) {
            L.polyline(u.current_future, { color: BLUE, weight: 3, opacity: 0.5, dashArray: "8 10" }).addTo(this.layer);
        }
        for (const line of u.future) {
            if (line.length > 1) L.polyline(line, { color: "#9ca3af", weight: 3, opacity: 0.5, dashArray: "8 10" }).addTo(this.layer);
        }

        const lines = [...u.past, ...(u.current_past ? [u.current_past] : []),
            ...(u.current_future ? [u.current_future] : []), ...u.future];
        const firstPt = lines.length ? lines[0][0] : null;
        const lastLine = lines.length ? lines[lines.length - 1] : null;
        const lastPt = lastLine ? lastLine[lastLine.length - 1] : null;
        const boundaryPt = u.current_past && u.current_past.length
            ? u.current_past[u.current_past.length - 1]
            : (u.current_future && u.current_future.length ? u.current_future[0] : lastPt);

        // Puerto sin coordenadas en ShipsGo (pasa con Manzanillo): la ruta
        // sí empieza/termina ahí, así que se usa su primer/último punto.
        const port = (info, label, refPt, filled) => {
            if (!info) return;
            const loc = this.snapLng(info.loc, refPt ? refPt[1] : null) || refPt;
            if (!loc) return;
            const date = info.date ? somFormatDate(String(info.date).slice(0, 10)) : "";
            const html =
                `<div class="vrm-pop"><b>${label}</b><br/><b>${esc(info.name || "")}</b>` +
                (info.country ? `<br/>${esc(info.country)}` : "") +
                (date ? `<br/>${label === "Origen" ? "Salida" : "Llegada est."}: ${esc(date)}` : "") +
                "</div>";
            L.circleMarker(loc, {
                radius: filled ? 7 : 6, color: filled ? "#dc2626" : "#16a34a", weight: 2.5,
                fillColor: filled ? "#dc2626" : "#ffffff", fillOpacity: filled ? 0.9 : 1,
            }).bindTooltip(`${label}: ${esc(info.name || "?")}`).bindPopup(html).addTo(this.layer);
        };
        port(d.origin, "Origen", firstPt, false);
        port(d.destination, "Destino", lastPt, true);

        const shipLoc = this.snapLng(d.current_loc, boundaryPt ? boundaryPt[1] : null);
        if (shipLoc) {
            const icon = L.divIcon({
                className: "vrm-ship-icon",
                html: `<div class="vrm-ship">${SVG_SHIP}</div>`,
                iconSize: [30, 30],
                iconAnchor: [15, 15],
            });
            const html =
                `<div class="vrm-pop vrm-pop-ship"><b>${esc(d.container || "")}</b>` +
                (d.status ? `<br/><span class="vrm-badge">${esc(d.status)}</span>` : "") +
                (d.vessel ? `<br/>Buque: ${esc(d.vessel)}` : "") +
                (d.transit_pct !== undefined ? `<br/>Progreso: ${esc(d.transit_pct)}%` : "") +
                "</div>";
            L.marker(shipLoc, { icon, zIndexOffset: 1000 })
                .bindTooltip(`${esc(d.container || "")} · ${esc(d.status || "")}`)
                .bindPopup(html)
                .addTo(this.layer);
        }
    }
}

export const voyageRouteMap = {
    component: VoyageRouteMap,
    supportedTypes: ["text", "char"],
};

registry.category("fields").add("som_voyage_route_map", voyageRouteMap);
