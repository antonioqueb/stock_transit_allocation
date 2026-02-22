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
            if (nextProps.record.data[this.props.name] !== this.props.record.data[this.props.name]) {
                this.renderMap(nextProps.record.data[this.props.name]);
            }
        });
    }

    renderMap(payloadData = null) {
        if (!this.mapContainer.el) return;

        // 1. Obtener datos
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
            console.error("Invalid ShipsGo JSON", e);
            return;
        }

        // 2. Inicializar mapa si no existe (aseguramos que L existe globalmente)
        if (!this.mapInstance && typeof L !== 'undefined') {
            this.mapInstance = L.map(this.mapContainer.el).setView([20, -40], 2);
            
            L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
                maxZoom: 19
            }).addTo(this.mapInstance);
        }

        if (!this.mapInstance) return;

        // Limpiar capas anteriores
        this.mapInstance.eachLayer((layer) => {
            if (!layer._url) this.mapInstance.removeLayer(layer);
        });

        // 3. Dibujar marcadores y ruta
        const bounds = [];
        
        const shipIcon = L.divIcon({html: '🚢', className: 'map-ship-icon', iconSize: [24, 24]});
        const portIcon = L.divIcon({html: '⚓', className: 'map-port-icon', iconSize: [20, 20]});

        // Origen
        if (data.origin && data.origin.loc && data.origin.loc[0]) {
            L.marker(data.origin.loc, {icon: portIcon})
                .addTo(this.mapInstance)
                .bindPopup(`<b>Origen:</b> ${data.origin.name}`);
            bounds.push(data.origin.loc);
        }

        // Destino
        if (data.destination && data.destination.loc && data.destination.loc[0]) {
            L.marker(data.destination.loc, {icon: portIcon})
                .addTo(this.mapInstance)
                .bindPopup(`<b>Destino:</b> ${data.destination.name}`);
            bounds.push(data.destination.loc);
        }

        // Posición Actual
        if (data.current_loc && data.current_loc[0]) {
            L.marker(data.current_loc, {icon: shipIcon})
                .addTo(this.mapInstance)
                .bindPopup(`<b>${data.container}</b><br/>${data.status_text}<br/>Buque: ${data.vessel}`)
                .openPopup();
            bounds.push(data.current_loc);
        }

        // Línea de ruta
        if (data.origin.loc && data.current_loc && data.destination.loc) {
            // Recorrido
            L.polyline([data.origin.loc, data.current_loc], {color: '#2563eb', weight: 3}).addTo(this.mapInstance);
            // Restante
            L.polyline([data.current_loc, data.destination.loc], {color: '#9ca3af', weight: 3, dashArray: '5, 10'}).addTo(this.mapInstance);
        }

        // Ajustar zoom
        if (bounds.length > 0) {
            this.mapInstance.fitBounds(bounds, {padding: [50, 50]});
        }
    }
}

registry.category("fields").add("transit_map_widget", {
    component: TransitMapWidget,
    supportedTypes: ["text"],
});