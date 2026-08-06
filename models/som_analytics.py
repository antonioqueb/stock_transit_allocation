# -*- coding: utf-8 -*-
"""SOM Analytics — motor de datos del BI interactivo (drill-down).

Un solo RPC (get_analytics_data) devuelve TODOS los agregados del tablero ya
normalizados a MXN con el TC del negocio:
  · Venta: subtotal de línea × TC de la orden (x_delivery_exchange_rate si la
    orden ya entregó — TC congelado —, si no x_exchange_rate Banorte).
  · Utilidad: SIEMPRE contra costo ALL-IN (product.template.x_costo_mayor, MXN).
  · m² y piezas jamás se mezclan: cada agregado separa por unidad.

La utilidad/costo es información sensible: el RPC exige el grupo Autorizador
de Precios (mismo criterio que product_cost_security).
"""
import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

_LEVEL_LABELS = {
    'high': 'N1', 'medium': 'N2', 'minimum': 'N3',
    'level_4': 'N4', 'level_5': 'N5', 'custom': 'Personalizado',
}


class SomAnalytics(models.AbstractModel):
    _name = 'som.analytics'
    _description = 'SOM Analytics — motor de datos BI'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @api.model
    def _is_area_uom(self, uom_name):
        n = (uom_name or '').lower()
        return 'm²' in n or 'm2' in n

    @api.model
    def _lot_series_key(self, lot_name):
        """(es_S, prefijo_num) — misma regla de antigüedad del inventario
        visual: numérico menor = más viejo; serie S = lo más nuevo."""
        m = re.match(r'^([A-Za-z]*)(\d+)', (lot_name or '').strip())
        if not m:
            return (0, -1)
        return (1 if m.group(1).upper() == 'S' else 0, int(m.group(2)))

    # ------------------------------------------------------------------
    # RPC principal
    # ------------------------------------------------------------------

    @api.model
    def get_analytics_data(self, filters=None):
        if not self.env.user.has_group(
                'inventory_shopping_cart.group_price_authorizer'):
            raise AccessError(_(
                'SOM Analytics muestra utilidad con costo all-in: requiere el '
                'permiso de Autorizador de Precios.'))

        filters = filters or {}
        rows = self._fetch_sale_rows(filters)

        data = {
            'kpis': {},
            'by_month': [],
            'levels': [],
            'top_products': [],
            'by_salesperson': [],
            'top_customers': [],
            'orders_detail': [],
            'inventory': {},
            'aging': [],
            'transit': [],
            'filters_echo': filters,
        }

        self._aggregate_sales(rows, data)

        try:
            self._aggregate_inventory(filters, data)
        except Exception:
            _logger.exception('[SOM Analytics] inventario')
        try:
            self._aggregate_transit(filters, data)
        except Exception:
            _logger.exception('[SOM Analytics] tránsito')

        return data

    # ------------------------------------------------------------------
    # Ventas (dataset central: líneas de órdenes confirmadas)
    # ------------------------------------------------------------------

    @api.model
    def _fetch_sale_rows(self, f):
        today = fields.Date.context_today(self)
        date_to = f.get('date_to') or fields.Date.to_string(today)
        date_from = f.get('date_from') or fields.Date.to_string(
            today - timedelta(days=365))

        domain = [
            ('order_id.state', '=', 'sale'),
            ('order_id.date_order', '>=', date_from),
            ('order_id.date_order', '<=', date_to + ' 23:59:59'),
            ('display_type', '=', False),
            ('product_id', '!=', False),
        ]
        if f.get('user_id'):
            domain.append(('order_id.user_id', '=', int(f['user_id'])))
        if f.get('partner_id'):
            domain.append(('order_id.partner_id', '=', int(f['partner_id'])))
        if f.get('product_id'):
            domain.append(('product_id.product_tmpl_id', '=', int(f['product_id'])))
        if f.get('level'):
            domain.append(('x_price_selector', '=', f['level']))
        if f.get('month'):
            domain += [
                ('order_id.date_order', '>=', f['month'] + '-01'),
                ('order_id.date_order', '<', self._next_month(f['month'])),
            ]

        Line = self.env['sale.order.line'].sudo()
        lines = Line.search(domain, order='id', limit=20000)

        rows = []
        for line in lines:
            order = line.order_id
            currency = order.pricelist_id.currency_id.name or 'MXN'
            if f.get('currency') and currency != f['currency']:
                continue
            rate = (
                order.x_delivery_exchange_rate
                or order.x_exchange_rate or 0.0
            ) if currency == 'USD' else 1.0
            if currency == 'USD' and rate <= 0:
                rate = 17.0  # último recurso; no debería ocurrir
            tmpl = line.product_id.product_tmpl_id
            qty = line.product_uom_qty or 0.0
            venta_mxn = (line.price_subtotal or 0.0) * rate
            costo_mxn = (tmpl.x_costo_mayor or 0.0) * qty
            is_area = self._is_area_uom(line.product_id.uom_id.name)
            rows.append({
                'order_id': order.id,
                'order_name': order.name,
                'date': fields.Date.to_string(order.date_order),
                'month': fields.Date.to_string(order.date_order)[:7],
                'user_id': order.user_id.id,
                'user_name': order.user_id.name or 'Sin vendedor',
                'partner_id': order.partner_id.id,
                'partner_name': order.partner_id.name or '',
                'currency': currency,
                'tmpl_id': tmpl.id,
                'product_name': tmpl.name or '',
                'level': line.x_price_selector or 'custom',
                'qty': qty,
                'is_area': is_area,
                'venta_mxn': venta_mxn,
                'costo_mxn': costo_mxn,
                'utilidad_mxn': venta_mxn - costo_mxn,
            })
        return rows

    @api.model
    def _next_month(self, month_str):
        y, m = int(month_str[:4]), int(month_str[5:7])
        return '%04d-%02d-01' % ((y + 1, 1) if m == 12 else (y, m + 1))

    @api.model
    def _aggregate_sales(self, rows, data):
        venta = sum(r['venta_mxn'] for r in rows)
        util = sum(r['utilidad_mxn'] for r in rows)
        m2 = sum(r['qty'] for r in rows if r['is_area'])
        pzas = sum(r['qty'] for r in rows if not r['is_area'])
        order_ids = {r['order_id'] for r in rows}

        data['kpis'].update({
            'venta_mxn': round(venta, 2),
            'utilidad_mxn': round(util, 2),
            'margen_pct': round(util / venta * 100, 1) if venta else 0.0,
            'm2_vendidos': round(m2, 1),
            'piezas_vendidas': round(pzas, 1),
            'ordenes': len(order_ids),
            'ticket_mxn': round(venta / len(order_ids), 2) if order_ids else 0,
        })

        def agg(key_fn, name_fn):
            acc = {}
            for r in rows:
                k = key_fn(r)
                a = acc.setdefault(k, {
                    'key': k, 'name': name_fn(r), 'venta': 0.0,
                    'utilidad': 0.0, 'm2': 0.0, 'count': 0,
                })
                a['venta'] += r['venta_mxn']
                a['utilidad'] += r['utilidad_mxn']
                if r['is_area']:
                    a['m2'] += r['qty']
                a['count'] += 1
            for a in acc.values():
                a['venta'] = round(a['venta'], 2)
                a['utilidad'] = round(a['utilidad'], 2)
                a['m2'] = round(a['m2'], 1)
                a['margen'] = round(
                    a['utilidad'] / a['venta'] * 100, 1) if a['venta'] else 0.0
            return acc

        months = agg(lambda r: r['month'], lambda r: r['month'])
        data['by_month'] = [months[k] for k in sorted(months)]

        levels = agg(lambda r: r['level'],
                     lambda r: _LEVEL_LABELS.get(r['level'], r['level']))
        order_lv = ['high', 'medium', 'minimum', 'level_4', 'level_5', 'custom']
        data['levels'] = [levels[k] for k in order_lv if k in levels]

        prods = agg(lambda r: r['tmpl_id'], lambda r: r['product_name'])
        data['top_products'] = sorted(
            prods.values(), key=lambda a: -a['utilidad'])[:12]

        sellers = agg(lambda r: r['user_id'], lambda r: r['user_name'])
        data['by_salesperson'] = sorted(
            sellers.values(), key=lambda a: -a['venta'])

        custs = agg(lambda r: r['partner_id'], lambda r: r['partner_name'])
        data['top_customers'] = sorted(
            custs.values(), key=lambda a: -a['venta'])[:10]

        # Detalle de órdenes (drill grid)
        orders = {}
        for r in rows:
            o = orders.setdefault(r['order_id'], {
                'id': r['order_id'], 'name': r['order_name'],
                'date': r['date'], 'partner': r['partner_name'],
                'seller': r['user_name'], 'currency': r['currency'],
                'venta': 0.0, 'utilidad': 0.0, 'm2': 0.0,
            })
            o['venta'] += r['venta_mxn']
            o['utilidad'] += r['utilidad_mxn']
            if r['is_area']:
                o['m2'] += r['qty']
        detail = sorted(orders.values(), key=lambda o: o['date'], reverse=True)
        for o in detail:
            o['venta'] = round(o['venta'], 2)
            o['utilidad'] = round(o['utilidad'], 2)
            o['m2'] = round(o['m2'], 1)
            o['margen'] = round(
                o['utilidad'] / o['venta'] * 100, 1) if o['venta'] else 0.0
        data['orders_detail'] = detail[:120]

    # ------------------------------------------------------------------
    # Inventario (snapshot)
    # ------------------------------------------------------------------

    @api.model
    def _aggregate_inventory(self, f, data):
        Quant = self.env['stock.quant'].sudo()
        domain = [('quantity', '>', 0), ('location_id.usage', '=', 'internal')]
        if f.get('product_id'):
            domain.append(
                ('product_id.product_tmpl_id', '=', int(f['product_id'])))
        quants = Quant.search(domain, limit=50000)

        disp_m2 = hold_m2 = disp_val = 0.0
        aging = defaultdict(lambda: {'m2': 0.0, 'lots': 0, 'valor': 0.0})
        prefixes = []

        for q in quants:
            if not self._is_area_uom(q.product_id.uom_id.name):
                continue
            qty = q.quantity or 0.0
            cost = (q.product_id.product_tmpl_id.x_costo_mayor or 0.0) * qty
            has_hold = bool(getattr(q, 'x_tiene_hold', False))
            if has_hold:
                hold_m2 += qty
            else:
                disp_m2 += qty
                disp_val += cost
            if q.lot_id:
                prefixes.append((self._lot_series_key(q.lot_id.name), qty, cost))

        # Tramos de antigüedad por prefijo (regla Stone Profit)
        numeric = sorted({k[1] for (k, _q, _c) in prefixes if k[0] == 0 and k[1] >= 0})
        cut_old = numeric[len(numeric) // 3] if numeric else 0
        cut_mid = numeric[2 * len(numeric) // 3] if numeric else 0

        def bucket(key):
            if key[0] == 1:
                return 'Serie S (recientes)'
            if key[1] < 0:
                return 'Sin folio estándar'
            if key[1] <= cut_old:
                return 'Antiguo (liquidar)'
            if key[1] <= cut_mid:
                return 'Medio'
            return 'Reciente'

        for key, qty, cost in prefixes:
            b = aging[bucket(key)]
            b['m2'] += qty
            b['lots'] += 1
            b['valor'] += cost

        order_b = ['Serie S (recientes)', 'Reciente', 'Medio',
                   'Antiguo (liquidar)', 'Sin folio estándar']
        data['aging'] = [
            {'bucket': b, 'm2': round(aging[b]['m2'], 1),
             'lots': aging[b]['lots'], 'valor': round(aging[b]['valor'], 2)}
            for b in order_b if b in aging
        ]
        data['inventory'] = {
            'disponible_m2': round(disp_m2, 1),
            'hold_m2': round(hold_m2, 1),
            'valor_disponible_mxn': round(disp_val, 2),
        }
        data['kpis']['inv_disponible_m2'] = round(disp_m2, 1)
        data['kpis']['inv_hold_m2'] = round(hold_m2, 1)

    # ------------------------------------------------------------------
    # Tránsito
    # ------------------------------------------------------------------

    @api.model
    def _aggregate_transit(self, f, data):
        Voyage = self.env['stock.transit.voyage'].sudo()
        voyages = Voyage.search([
            ('custom_status', 'not in', ('delivered', 'cancel')),
        ])
        labels = dict(
            Voyage._fields['custom_status']._description_selection(self.env))
        acc = defaultdict(lambda: {'m2': 0.0, 'count': 0, 'valor': 0.0})
        total_m2 = 0.0
        for v in voyages:
            for line in v.line_ids:
                if not line.product_id:
                    continue
                if f.get('product_id') and \
                        line.product_id.product_tmpl_id.id != int(f['product_id']):
                    continue
                if not self._is_area_uom(line.product_id.uom_id.name):
                    continue
                qty = line.product_uom_qty or 0.0
                a = acc[v.custom_status]
                a['m2'] += qty
                a['valor'] += (
                    line.product_id.product_tmpl_id.x_costo_mayor or 0.0) * qty
                total_m2 += qty
            acc[v.custom_status]['count'] += 1

        order_st = ['solicitud', 'production', 'booking', 'puerto_origen',
                    'on_sea', 'puerto_destino', 'arrived_port',
                    'reception_pending']
        data['transit'] = [
            {'status': st, 'label': labels.get(st, st),
             'm2': round(acc[st]['m2'], 1), 'count': acc[st]['count'],
             'valor': round(acc[st]['valor'], 2)}
            for st in order_st if st in acc
        ]
        data['kpis']['transit_m2'] = round(total_m2, 1)
