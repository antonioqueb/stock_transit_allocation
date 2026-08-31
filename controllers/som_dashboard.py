# -*- coding: utf-8 -*-
"""Dashboard Ejecutivo SOM — página standalone protegida.

/som/analytics       (http, auth=user): shell HTML que monta el bundle React.
/som/analytics/rpc   (json, auth=user): pasarela a som.analytics (whitelist).

Seguridad: además de la sesión Odoo, AMBAS rutas exigen el grupo
Autorizador de Precios (misma regla que el resto de Analytics: la
utilidad con costo all-in es información restringida).
"""
import json
import re

from markupsafe import Markup

from odoo import http
from odoo.http import request

GROUPS = ('inventory_shopping_cart.group_dashboard_viewer',
          'inventory_shopping_cart.group_price_authorizer')

_RPC_WHITELIST = {
    'dashboard': ('get_dashboard', 2),
    'drill': ('get_drill', 4),
    'exec': ('get_exec_summary', 1),
    'banks': ('get_bank_balances', 0),
    'order_lines': ('get_order_lines', 1),
    'time_to_sell': ('get_time_to_sell', 1),
    'set_cost': ('set_product_cost', 3),
}


class SomDashboardController(http.Controller):

    def _check_group(self):
        return any(request.env.user.has_group(g) for g in GROUPS)

    def _apply_company_context(self):
        """Multiempresa: la página standalone NO pasa por el webclient, así
        que su RPC llega sin allowed_company_ids y env.companies caería a
        TODAS las compañías del usuario. Se toma la selección del switcher
        del backend (cookie `cids`, que es donde el webclient la guarda),
        acotada a las compañías permitidas; sin cookie válida, la activa."""
        user = request.env.user
        allowed = user.company_ids.ids
        raw = request.httprequest.cookies.get('cids') or ''
        cids = [int(x) for x in re.findall(r'\d+', raw) if int(x) in allowed]
        if not cids:
            cids = [user.company_id.id]
        request.update_context(allowed_company_ids=cids)
        return request.env['res.company'].browse(cids)

    @http.route('/som/analytics', type='http', auth='user')
    def dashboard_page(self, **kw):
        if not self._check_group():
            return request.redirect('/odoo')
        user = request.env.user
        companies = self._apply_company_context()
        boot = {
            'user': user.name,
            'company': ' + '.join(companies.mapped('name')),
            'uid': user.id,
        }
        # Cache-bust: la URL del bundle lleva la versión instalada del
        # módulo; sin esto el navegador retiene el JS/CSS viejo aunque el
        # servidor ya tenga el nuevo (los /static/ se sirven cacheables).
        mod = request.env['ir.module.module'].sudo().search(
            [('name', '=', 'stock_transit_allocation')], limit=1)
        asset_v = (mod.installed_version or mod.latest_version or '0')
        # Markup + escape de '<' : el JSON entra crudo al <script> sin que
        # QWeb lo html-escapee (JSON.parse truena con &amp;).
        payload = json.dumps(boot).replace('<', '\\u003c')
        return request.render('stock_transit_allocation.som_dashboard_page', {
            'boot_json': Markup(payload),
            'asset_v': asset_v,
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
        self._apply_company_context()
        return getattr(request.env['som.analytics'], fname)(*args)
