# -*- coding: utf-8 -*-
"""Estampa lo ya recibido POR LÍNEA (tc_received_qty y compañía) en los
viajes con recepción física ligada. Antes solo existía el related
voyage_status: en una cadena parcial las 11 líneas seguían
'reception_pending' aunque 10 placas ya estuvieran en almacén."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Voyage = env['stock.transit.voyage'].with_context(active_test=False)
    # Todos los que siguen en recepción + los cerrados recientes (acota el
    # costo del -u: cada viaje recorre su cadena de recepciones).
    voyages = Voyage.search([
        ('reception_picking_id', '!=', False),
        '|',
        ('custom_status', '=', 'reception_pending'),
        '&', ('custom_status', '=', 'delivered'),
        ('write_date', '>=', '2026-06-01'),
    ])
    done = 0
    for voyage in voyages:
        try:
            voyage._tc_sync_received_lines()
            done += 1
        except Exception:
            _logger.exception(
                '[TC_MIGRATE] No se pudo estampar lo recibido del viaje %s.',
                voyage.name)
    _logger.info('[TC_MIGRATE] Recibido por línea estampado en %s/%s viajes.',
                 done, len(voyages))
