/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillUnmount, onWillUpdateProps, useRef } from "@odoo/owl";

export class TransitMapWidget extends Component {
    static template = "stock_transit_allocation.TransitMapWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.mapContainer = useRef("mapContainer");
        this.mapInstance = null;
        this._destroyed = false;

        onMounted(() => {
            // Pequeño delay para asegurar que el DOM está listo y Leaflet cargado
            setTimeout(() => {
                if (!this._destroyed) this._renderMap();
            }, 300);
        });

        onWillUpdateProps((nextProps) => {
            const oldVal = this.props.record.data[this.props.name];
            const newVal = nextProps.record.data[nextProps.name];
            if (newVal !== oldVal) {
                setTimeout(() => {
                    if (!this._destroyed) this._renderMap(newVal);
                }, 100);
            }
        });

        onWillUnmount(() => {
            this._destroyed = true;
            if (this.mapInstance) {
                this.mapInstance.remove();
                this.mapInstance = null;
            }
        });
    }

    _isValidLatLng(loc) {
        return (
            Array.isArray(loc) &&
            loc.length === 2 &&
            loc[0] != null && loc[1] != null &&
            !isNaN(parseFloat(loc[0])) && !isNaN(parseFloat(loc[1])) &&
            !(parseFloat(loc[0]) === 0 && parseFloat(loc[1]) === 0)
        );
    }

    _renderMap(payloadOverride = null) {
        if (this._destroyed || !this.mapContainer.el) return;

        // 1. Obtener payload
        const raw = payloadOverride !== null
            ? payloadOverride
            : this.props.record.data[this.props.name];

        // Sin datos → destruir mapa si existía y salir
        if (!raw) {
            if (this.mapInstance) {
                this.mapInstance.remove();
                this.mapInstance = null;
            }
            return;
        }

        // 2. Parsear JSON
        let data;
        try {
            data = typeof raw === "string" ? JSON.parse(raw) : raw;
        } catch (e) {
            console.error("[TransitMap] JSON inválido:", e, raw);
            return;
        }

        // 3. Verificar que Leaflet esté disponible globalmente
        if (typeof L === "undefined") {
            console.error("[TransitMap] Leaflet (L) no está disponible. ¿Cargó el CDN?");
            return;
        }

        // 4. Asegurarse de que el contenedor tiene dimensiones
        const container = this.mapContainer.el;
        if (container.offsetWidth === 0 || container.offsetHeight === 0) {
            // Reintentar en 500ms si no tiene dimensiones aún (tab oculta, etc.)
            setTimeout(() => { if (!this._destroyed) this._renderMap(payloadOverride); }, 500);
            return;
        }

        // 5. Inicializar mapa si no existe
        if (!this.mapInstance) {
            this.mapInstance = L.map(container, { zoomControl: true }).setView([20, -40], 2);
            L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
                attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
                maxZoom: 19,
            }).addTo(this.mapInstance);
        }

        // 6. Limpiar capas anteriores (NO el tile layer)
        this.mapInstance.eachLayer((layer) => {
            if (!layer._url) this.mapInstance.removeLayer(layer);
        });

        // 7. Iconos
        const shipIcon = L.divIcon({
            html: "🚢",
            className: "map-ship-icon",
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
        const portIcon = L.divIcon({
            html: "⚓",
            className: "map-port-icon",
            iconSize: [22, 22],
            iconAnchor: [11, 11],
        });

        // 8. Extraer coordenadas
        const origin  = data.origin?.loc  && this._isValidLatLng(data.origin.loc)       ? data.origin.loc       : null;
        const dest    = data.destination?.loc && this._isValidLatLng(data.destination.loc) ? data.destination.loc : null;
        const current = data.current_loc  && this._isValidLatLng(data.current_loc)       ? data.current_loc      : null;

        const bounds = [];

        // 9. Marcadores
        if (origin) {
            L.marker(origin, { icon: portIcon })
                .addTo(this.mapInstance)
                .bindPopup(`<b>Origen:</b> ${data.origin?.name || "Puerto Salida"}`);
            bounds.push(origin);
        }

        if (dest) {
            L.marker(dest, { icon: portIcon })
                .addTo(this.mapInstance)
                .bindPopup(`<b>Destino:</b> ${data.destination?.name || "Puerto Llegada"}`);
            bounds.push(dest);
        }

        if (current) {
            L.marker(current, { icon: shipIcon })
                .addTo(this.mapInstance)
                .bindPopup(
                    `<div style="text-align:center">
                        <b>${data.container || "Contenedor"}</b><br/>
                        <span style="background:#2563eb;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px">
                            ${data.status_text || "En tránsito"}
                        </span><br/>
                        <small>Buque: ${data.vessel || "N/A"}</small>
                    </div>`
                )
                .openPopup();
            bounds.push(current);
        }

        // 10. Polylines
        if (origin && current) {
            L.polyline([origin, current], { color: "#2563eb", weight: 4, opacity: 0.85 })
                .addTo(this.mapInstance);
        }
        if (current && dest) {
            L.polyline([current, dest], { color: "#6b7280", weight: 3, dashArray: "8, 10", opacity: 0.65 })
                .addTo(this.mapInstance);
        } else if (origin && dest && !current) {
            L.polyline([origin, dest], { color: "#9ca3af", weight: 3, dashArray: "8, 10" })
                .addTo(this.mapInstance);
        }

        // 11. Ajustar vista
        this.mapInstance.invalidateSize();
        if (bounds.length > 1) {
            this.mapInstance.fitBounds(bounds, { padding: [50, 50], maxZoom: 8 });
        } else if (bounds.length === 1) {
            this.mapInstance.setView(bounds[0], 5);
        } else {
            this.mapInstance.setView([20, -40], 2);
        }
    }
}

registry.category("fields").add("transit_map_widget", {
    component: TransitMapWidget,
    supportedTypes: ["text"],
});