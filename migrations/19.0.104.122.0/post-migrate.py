"""Mapas de seguimiento de viajes sin CARTO (27 sep 2026).

El mapa de cada viaje se guarda ya armado en `shipsgo_map_html` y los 55
existentes traían el fondo de CARTO, que ahora pinta "API key required".
Se regeneran con OpenStreetMap desde `shipsgo_payload` (los mismos datos
de la última sincronización: no se llama a ShipsGo). Si un viaje no tiene
datos o el generador falla, solo se cambia la URL de los mosaicos.
SQL directo al guardar: no dispara el write() del viaje.
"""
import json
import logging
import re

from odoo import api, SUPERUSER_ID
from odoo.addons.stock_transit_allocation.models.stock_transit_voyage import OSM_TILES

_logger = logging.getLogger(__name__)

CARTO_TILES = re.compile(
    r'https://(?:\{s\}\.basemaps\.cartocdn\.com|cartodb-basemaps-\{s\}\.global\.ssl\.fastly\.net)'
    r'/[A-Za-z_/]+/\{z\}/\{x\}/\{y\}(?:\{r\})?\.png')


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Voyage = env['stock.transit.voyage']
    cr.execute("""
        SELECT id, shipsgo_payload, shipsgo_map_html
          FROM stock_transit_voyage
         WHERE shipsgo_map_html ILIKE '%%carto%%'
    """)
    regenerated = patched = 0
    for voyage_id, payload, old_html in cr.fetchall():
        html = None
        if payload:
            try:
                html = Voyage.browse(voyage_id)._generate_folium_map(json.loads(payload))
            except Exception:  # noqa: BLE001 — un viaje raro no tumba el -u
                _logger.exception('[MAPAS] No se pudo regenerar el mapa del viaje %s', voyage_id)
        if html:
            regenerated += 1
        else:
            html = CARTO_TILES.sub(OSM_TILES, old_html or '')
            patched += 1
        cr.execute("UPDATE stock_transit_voyage SET shipsgo_map_html = %s WHERE id = %s",
                   (html, voyage_id))
    _logger.info('[MAPAS] Viajes sin CARTO: %s regenerados, %s con URL reemplazada.',
                 regenerated, patched)
