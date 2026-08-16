# -*- coding: utf-8 -*-
"""Recolector de latidos de actividad del webclient.

/som/activity/ping (json, auth=user): recibe un lote de intervalos medidos
por el servicio `som_activity_tracker`.

Va por controlador y no por call_kw a propósito: al cerrar la pestaña el
único envío que el navegador garantiza es `navigator.sendBeacon`, que manda
un POST plano — no puede armar una llamada ORM.

Nunca revienta: si el cuerpo viene mal, se descarta en silencio. Un error
aquí no puede estorbarle a quien está trabajando.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SomActivityController(http.Controller):

    # Odoo 19: el tipo es 'jsonrpc' ('json' ya no existe). save_session
    # evita reescribir la sesión en cada latido.
    @http.route('/som/activity/ping', type='jsonrpc', auth='user',
                methods=['POST'], save_session=False)
    def activity_ping(self, **payload):
        try:
            # sendBeacon manda el JSON en la raíz; el rpc de Odoo lo entrega
            # como kwargs. Se aceptan ambas formas.
            data = payload.get('params') or payload
            return request.env['som.user.activity'].som_record_batch(data)
        except Exception:  # noqa: BLE001
            _logger.exception('[SOM ACTIVITY] Lote descartado.')
            return {'stored': 0, 'error': True}
