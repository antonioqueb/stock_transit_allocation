/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillUpdateProps, useRef } from "@odoo/owl";

export class TransitMapWidget extends Component {
    static template = "stock_transit_allocation.TransitMapWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.mapContainer = useRef("mapContainer");
        this.mapInstance = null;

        onMounted(() => {
            this.renderMap();
        });

        onWillUpdateProps((nextProps) => {
            // Renderizar si el payload cambia
            if (nextProps.record.data[this.props.name] !== this.props.record.data[this.props.name]) {
                this.renderMap(nextProps.record.data[this.props.name]);
            }
        });
    }

    isValidLatLng(loc) {
        return Array.isArray(loc) && loc.length === 2 && loc[0] != null && loc[1] != null && !isNaN(loc[0]) && !isNaN(loc[1]);
    }

    renderMap(payloadData = null) {
        if (!this.mapContainer.el) return;

        // 1. Obtener y parsear datos
        const rawData = payloadData || this.props.record.data[this.props.name];
        if (!rawData) {
            if (this.mapInstance) {
                this.mapInstance.remove();
                this.mapInstance = null;
            }
            return;
        }

        let data;
        try {
            data = JSON.parse(rawData);
        } catch (e) {
            console.error("[TransitMap] Invalid JSON", e);
            return;
        }

        // 2. Inicializar mapa si no existe
        if (!this.mapInstance && typeof L !== 'undefined') {
            this.mapInstance = L.map(this.mapContainer.el).setView([20, -40], 2);
            
            L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
                maxZoom: 19
            }).addTo(this.mapInstance);
        }

        if (!this.mapInstance) {
            console.warn("[TransitMap] Leaflet not loaded globally.");
            return;
        }

        // 3. Limpiar capas anteriores
        this.mapInstance.eachLayer((layer) => {
            if (!layer._url) this.mapInstance.removeLayer(layer);
        });

        const bounds = [];
        
        // Iconos
        const shipIcon = L.divIcon({html: '🚢', className: 'map-ship-icon', iconSize: [24, 24]});
        const portIcon = L.divIcon({html: '⚓', className: 'map-port-icon', iconSize: [20, 20]});

        // --- Puntos ---
        const origin = data.origin && this.isValidLatLng(data.origin.loc) ? data.origin.loc : null;
        const dest = data.destination && this.isValidLatLng(data.destination.loc) ? data.destination.loc : null;
        const current = data.current_loc && this.isValidLatLng(data.current_loc) ? data.current_loc : null;

        // Marcador Origen
        if (origin) {
            L.marker(origin, {icon: portIcon})
                .addTo(this.mapInstance)
                .bindPopup(`<b>Origen:</b> ${data.origin.name || 'Puerto Salida'}`);
            bounds.push(origin);
        }

        // Marcador Destino
        if (dest) {
            L.marker(dest, {icon: portIcon})
                .addTo(this.mapInstance)
                .bindPopup(`<b>Destino:</b> ${data.destination.name || 'Puerto Llegada'}`);
            bounds.push(dest);
        }

        // Marcador Barco (Actual)
        if (current) {
            L.marker(current, {icon: shipIcon})
                .addTo(this.mapInstance)
                .bindPopup(`
                    <div class="text-center">
                        <b>${data.container || 'Contenedor'}</b><br/>
                        <span class="badge bg-primary">${data.status_text || 'En tránsito'}</span><br/>
                        <small>Buque: ${data.vessel || 'N/A'}</small>
                    </div>
                `)
                .openPopup();
            bounds.push(current);
        }

        // --- Rutas (Polylines) ---
        // Lógica flexible: Dibuja lo que tenga disponible
        if (current && origin) {
            // Ruta recorrida (Sólida Azul)
            L.polyline([origin, current], {color: '#2563eb', weight: 4, opacity: 0.8}).addTo(this.mapInstance);
        }

        if (current && dest) {
            // Ruta restante (Punteada Gris)
            L.polyline([current, dest], {color: '#6b7280', weight: 3, dashArray: '5, 10', opacity: 0.7}).addTo(this.mapInstance);
        } else if (origin && dest && !current) {
            // Si no hay posición actual, dibujar ruta teórica Origen -> Destino
            L.polyline([origin, dest], {color: '#9ca3af', weight: 3, dashArray: '5, 10'}).addTo(this.mapInstance);
        }

        // Ajustar vista
        if (bounds.length > 0) {
            // Pequeño timeout para asegurar que el contenedor tiene tamaño antes de ajustar bounds
            setTimeout(() => {
                this.mapInstance.invalidateSize(); // CRÍTICO para corregir renderizado en pestañas
                this.mapInstance.fitBounds(bounds, {padding: [50, 50]});
            }, 250);
        } else {
            // Vista por defecto si no hay coordenadas válidas
            this.mapInstance.setView([20, -40], 2);
        }
    }
}

registry.category("fields").add("transit_map_widget", {
    component: TransitMapWidget,
    supportedTypes: ["text"],
});