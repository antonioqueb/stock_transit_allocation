# -*- coding: utf-8 -*-
"""Dashboard Ejecutivo SOM — página standalone protegida.

/som/analytics       (http, auth=user): shell HTML que monta el bundle React.
/som/analytics/rpc   (json, auth=user): pasarela a som.analytics (whitelist).

Seguridad: además de la sesión Odoo, AMBAS rutas exigen el grupo
Autorizador de Precios (misma regla que el resto de Analytics: la
utilidad con costo all-in es información restringida).
"""
import json

from markupsafe import Markup

from odoo import http
from odoo.http import request

GROUP = 'inventory_shopping_cart.group_price_authorizer'

_RPC_WHITELIST = {
    'dashboard': ('get_dashboard', 2),
    'drill': ('get_drill', 4),
    'exec': ('get_exec_summary', 0),
    'banks': ('get_bank_balances', 0),
    'order_lines': ('get_order_lines', 1),
    'time_to_sell': ('get_time_to_sell', 1),
}


class SomDashboardController(http.Controller):

    def _check_group(self):
        return request.env.user.has_group(GROUP)

    @http.route('/som/analytics', type='http', auth='user')
    def dashboard_page(self, **kw):
        if not self._check_group():
            return request.redirect('/odoo')
        user = request.env.user
        boot = {
            'user': user.name,
            'company': user.company_id.name,
            'uid': user.id,
        }
        # Markup + escape de '<' : el JSON entra crudo al <script> sin que
        # QWeb lo html-escapee (JSON.parse truena con &amp;).
        payload = json.dumps(boot).replace('<', '\\u003c')
        return request.render('stock_transit_allocation.som_dashboard_page', {
            'boot_json': Markup(payload),
        })

    @http.route('/som/analytics/rpc', type='jsonrpc', auth='user')
    def dashboard_rpc(self, method=None, args=None):
        if not self._check_group():
            return {'error': 'forbidden'}
        spec = _RPC_WHITELIST.get(method)
        if not spec:
            return {'error': 'unknown method'}
        fname, max_args = spec
        args = list(args or [])[:max_args]
        return getattr(request.env['som.analytics'], fname)(*args)
