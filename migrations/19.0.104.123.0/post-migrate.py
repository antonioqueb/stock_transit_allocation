"""Mapa de seguimiento del viaje: Leaflet nativo (27 sep 2026).

Antes el servidor armaba el mapa con Folium (fondo CARTO, que ahora pinta
"API key required") y lo guardaba como HTML en `shipsgo_map_html`. Ahora lo
dibuja el widget Leaflet `som_voyage_route_map` directo desde
`shipsgo_payload`, así que el HTML guardado ya no se usa: se vacía para no
cargar ~55 iframes muertos en la base.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE stock_transit_voyage
           SET shipsgo_map_html = NULL
         WHERE shipsgo_map_html IS NOT NULL
    """)
    _logger.info('[MAPAS] Mapas Folium obsoletos vaciados en %s viajes.', cr.rowcount)
