# -*- coding: utf-8 -*-
"""SOM Analytics v2 — motor de datos del BI por dominios con drill-down real.

Arquitectura:
  · get_dashboard(domain, filters): payload de UNA pestaña (resumen, comercial,
    inventario, compras, transito, entregas, financiero). Cada pestaña se
    calcula con SQL puro (una consulta base + agregación en Python en un solo
    paso) — nada de iterar recordsets con accesos relacionales.
  · get_drill(entity, value, filters): PROFUNDIZACIÓN de un elemento clickeado
    (mes, material, vendedor, cliente, nivel): su tendencia mensual, sus
    cortes por las demás dimensiones y sus órdenes.

Reglas de negocio:
  · Venta normalizada a MXN: USD × TC congelado en entrega
    (x_delivery_exchange_rate) o, si aún no entrega, TC Banorte vigente.
  · Utilidad SIEMPRE contra costo all-in (product.template.x_costo_mayor, MXN).
  · m² y piezas separados (uoms de área precalculadas).
  · Utilidad/costos = información sensible: exige Autorizador de Precios.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

_LEVELS = {
    'high': 'N1', 'medium': 'N2', 'minimum': 'N3',
    'level_4': 'N4', 'level_5': 'N5', 'custom': 'Personalizado',
}
_LEVEL_ORDER = ['high', 'medium', 'minimum', 'level_4', 'level_5', 'custom']


class SomAnalytics(models.AbstractModel):
    _name = 'som.analytics'
    _description = 'SOM Analytics — motor de datos BI'

    # ==================================================================
    # Helpers
    # ==================================================================

    def _check_access(self):
        # Visor del Dashboard (nivel 1) o Autorizador (nivel 2, lo implica).
        # El doble check cubre bases con el implied aún sin aplicar.
        u = self.env.user
        if not (u.has_group('inventory_shopping_cart.group_dashboard_viewer')
                or u.has_group(
                    'inventory_shopping_cart.group_price_authorizer')):
            raise AccessError(_(
                'SOM Analytics muestra utilidad con costo all-in: requiere '
                'el permiso Visor del Dashboard Personalizado o Autorizador '
                'de Precios.'))

    def _area_uom_ids(self):
        ids = []
        for uom in self.env['uom.uom'].sudo().search([]):
            n = (uom.name or '').lower()
            if 'm²' in n or 'm2' in n:
                ids.append(uom.id)
        return ids or [0]

    def _current_usd_rate(self):
        try:
            return self.env['sale.order'].sudo()._get_banorte_rate() or 17.0
        except Exception:
            return 17.0

    def _dates(self, f):
        today = fields.Date.context_today(self)
        date_to = f.get('date_to') or fields.Date.to_string(today)
        date_from = f.get('date_from') or fields.Date.to_string(
            today - timedelta(days=365))
        return date_from, date_to

    def _bounds(self, f):
        """Límites UTC del rango date_from..date_to interpretado en la zona
        horaria del usuario (default America/Monterrey). Odoo guarda los
        timestamps en UTC: comparar contra fechas 'planas' corría el día
        con 6 horas y HOY salía vacío/incompleto en el dashboard."""
        date_from, date_to = self._dates(f)
        tz = pytz.timezone(self.env.user.tz or 'America/Monterrey')
        start = tz.localize(
            datetime.combine(fields.Date.from_string(date_from), time.min)
        ).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(
            datetime.combine(fields.Date.from_string(date_to),
                             time(23, 59, 59))
        ).astimezone(pytz.utc).replace(tzinfo=None)
        return (fields.Datetime.to_string(start),
                fields.Datetime.to_string(end))

    def _cd(self, column):
        """Expresión SQL para leer un campo company_dependent (jsonb
        {company_id: valor} en Odoo 17+) como float de la compañía activa.
        Requiere %(cid)s en los params."""
        return "COALESCE((%s->>%%(cid)s)::float, 0)" % column

    def _cid(self):
        return str(self.env.company.id)

    def _sq(self, sql, params=None, default=None):
        """Consulta segura: corre dentro de un SAVEPOINT. Si falla (tabla
        de módulo opcional, columna versionada), el savepoint se revierte y
        la transacción sigue sana — sin esto, un solo error abortaba la
        transacción y TODO lo posterior tronaba con 'current transaction
        is aborted'."""
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(sql, params or ())
                return self.env.cr.fetchall()
        except Exception as exc:
            _logger.warning('[SOM Analytics] SQL omitida: %s', exc)
            return default if default is not None else []

    @api.model
    def _lot_bucket_key(self, lot_name):
        m = re.match(r'^([A-Za-z]*)(\d+)', (lot_name or '').strip())
        if not m:
            return (0, -1)
        return (1 if m.group(1).upper() == 'S' else 0, int(m.group(2)))

    # ==================================================================
    # Dataset central de VENTAS (una sola consulta SQL)
    # ==================================================================

    def _sale_rows(self, f, extra_where='', extra_params=None):
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        params = {
            'date_from': dt_from,
            'date_to': dt_to,
            'rate': self._current_usd_rate(),
            'area_uoms': tuple(self._area_uom_ids()),
            'cid': self._cid(),
        }
        where = []
        if f.get('user_id'):
            where.append('so.user_id = %(user_id)s')
            params['user_id'] = int(f['user_id'])
        if f.get('partner_id'):
            where.append('so.partner_id = %(partner_id)s')
            params['partner_id'] = int(f['partner_id'])
        if f.get('product_id'):
            where.append('pt.id = %(tmpl_id)s')
            params['tmpl_id'] = int(f['product_id'])
        if f.get('categ_id'):
            where.append('pt.categ_id = %(categ_id)s')
            params['categ_id'] = int(f['categ_id'])
        if f.get('level'):
            where.append("COALESCE(sol.x_price_selector,'custom') = %(level)s")
            params['level'] = f['level']
        if f.get('month'):
            where.append("to_char(so.date_order,'YYYY-MM') = %(month)s")
            params['month'] = f['month']
        if f.get('currency'):
            where.append('rc.name = %(currency)s')
            params['currency'] = f['currency']
        # Origen de la venta: 'odoo' = nacida en Odoo (referencia vacía);
        # 'sps' = LEGADO Stone Profit (la referencia del cliente trae el
        # folio del sistema anterior). Sin el filtro se mezclan ambas.
        if f.get('source') == 'sps':
            where.append("COALESCE(so.client_order_ref, '') != ''")
        elif f.get('source') == 'odoo':
            where.append("COALESCE(so.client_order_ref, '') = ''")
        if extra_where:
            where.append(extra_where)
            params.update(extra_params or {})

        sql = """
            SELECT
                so.id            AS order_id,
                so.name          AS order_name,
                so.date_order::date::text AS d,
                to_char(so.date_order,'YYYY-MM') AS month,
                COALESCE(so.user_id, 0) AS user_id,
                COALESCE(sp.name, 'Sin vendedor') AS user_name,
                so.partner_id,
                COALESCE(cp.name, '') AS partner_name,
                COALESCE(rc.name, 'MXN') AS currency,
                pt.id            AS tmpl_id,
                COALESCE(pt.name->>'es_MX', pt.name->>'en_US', '') AS product_name,
                COALESCE(pt.categ_id, 0) AS categ_id,
                COALESCE(NULLIF(regexp_replace(pc.complete_name, '^[^/]+ / ', ''), ''),
                         pc.complete_name, 'Sin categoría') AS categ_name,
                COALESCE(sol.x_price_selector, 'custom') AS level,
                COALESCE(sol.product_uom_qty, 0) AS qty,
                (sol.product_uom_id IN %(area_uoms)s) AS is_area,
                CASE WHEN COALESCE(rc.name,'MXN') = 'USD'
                     THEN COALESCE(sol.price_subtotal,0)
                          * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                     ELSE COALESCE(sol.price_subtotal,0)
                END AS venta_mxn,
                COALESCE(sol.product_uom_qty,0)
                    * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0) AS costo_mxn
            FROM sale_order_line sol
            JOIN sale_order so        ON so.id = sol.order_id
            JOIN product_product pp   ON pp.id = sol.product_id
            JOIN product_template pt  ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_category pc ON pc.id = pt.categ_id
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            LEFT JOIN res_users ru    ON ru.id = so.user_id
            LEFT JOIN res_partner sp  ON sp.id = ru.partner_id
            LEFT JOIN res_partner cp  ON cp.id = so.partner_id
            WHERE so.state = 'sale'
              AND sol.display_type IS NULL
              AND so.date_order >= %(date_from)s
              AND so.date_order <= %(date_to)s
              {extra}
        """.format(extra=(' AND ' + ' AND '.join(where)) if where else '')
        self.env.cr.execute(sql, params)
        rows = self.env.cr.dictfetchall()
        # Clave de PERIODO según la granularidad pedida (day|week|month):
        # con poca historia mensual, las series pueden cortarse por día o
        # semana ISO. 'month' sigue siendo el default y la clave 'month'
        # se conserva para los consumidores que no cambian.
        gran = (f or {}).get('granularity') or 'month'
        for r in rows:
            r['utilidad_mxn'] = r['venta_mxn'] - r['costo_mxn']
            if gran == 'day':
                r['period'] = r['d']
            elif gran == 'week':
                iso = datetime.strptime(r['d'], '%Y-%m-%d').isocalendar()
                r['period'] = '%dW%02d' % (iso[0], iso[1])
            else:
                r['period'] = r['month']
        return rows

    def _agg(self, rows, key_fn, name_fn, order=None, limit=None):
        acc = {}
        for r in rows:
            k = key_fn(r)
            a = acc.get(k)
            if a is None:
                a = acc[k] = {'key': k, 'name': name_fn(r), 'venta': 0.0,
                              'utilidad': 0.0, 'm2': 0.0, 'piezas': 0.0,
                              'count': 0}
            a['venta'] += r['venta_mxn']
            a['utilidad'] += r['utilidad_mxn']
            if r['is_area']:
                a['m2'] += r['qty']
            else:
                a['piezas'] += r['qty']
            a['count'] += 1
        out = list(acc.values())
        for a in out:
            a['venta'] = round(a['venta'], 2)
            a['utilidad'] = round(a['utilidad'], 2)
            a['m2'] = round(a['m2'], 1)
            a['piezas'] = round(a['piezas'], 1)
            a['margen'] = round(
                a['utilidad'] / a['venta'] * 100, 1) if a['venta'] else 0.0
        if order == 'key':
            out.sort(key=lambda a: str(a['key']))
        elif order == 'venta':
            out.sort(key=lambda a: -a['venta'])
        elif order == 'utilidad':
            out.sort(key=lambda a: -a['utilidad'])
        return out[:limit] if limit else out

    def _orders_from(self, rows, limit=100):
        orders = {}
        for r in rows:
            o = orders.get(r['order_id'])
            if o is None:
                o = orders[r['order_id']] = {
                    'id': r['order_id'], 'name': r['order_name'],
                    'date': r['d'], 'partner': r['partner_name'],
                    'seller': r['user_name'], 'currency': r['currency'],
                    'venta': 0.0, 'utilidad': 0.0, 'm2': 0.0}
            o['venta'] += r['venta_mxn']
            o['utilidad'] += r['utilidad_mxn']
            if r['is_area']:
                o['m2'] += r['qty']
        out = sorted(orders.values(), key=lambda o: o['date'], reverse=True)
        for o in out:
            o['venta'] = round(o['venta'], 2)
            o['utilidad'] = round(o['utilidad'], 2)
            o['m2'] = round(o['m2'], 1)
            o['margen'] = round(
                o['utilidad'] / o['venta'] * 100, 1) if o['venta'] else 0.0
        return out[:limit]

    def _sales_pack(self, rows):
        venta = sum(r['venta_mxn'] for r in rows)
        util = sum(r['utilidad_mxn'] for r in rows)
        return {
            'kpis': {
                'venta_mxn': round(venta, 2),
                'utilidad_mxn': round(util, 2),
                'margen_pct': round(util / venta * 100, 1) if venta else 0.0,
                'm2_vendidos': round(sum(
                    r['qty'] for r in rows if r['is_area']), 1),
                'piezas_vendidas': round(sum(
                    r['qty'] for r in rows if not r['is_area']), 1),
                'ordenes': len({r['order_id'] for r in rows}),
            },
            'by_month': self._agg(
                rows, lambda r: r['period'], lambda r: r['period'],
                order='key'),
            'levels': sorted(
                self._agg(rows, lambda r: r['level'],
                          lambda r: _LEVELS.get(r['level'], r['level'])),
                key=lambda a: _LEVEL_ORDER.index(a['key'])
                if a['key'] in _LEVEL_ORDER else 99),
            'by_seller': self._agg(
                rows, lambda r: r['user_id'], lambda r: r['user_name'],
                order='venta'),
            'top_products': self._agg(
                rows, lambda r: r['tmpl_id'], lambda r: r['product_name'],
                order='utilidad', limit=12),
            'top_customers': self._agg(
                rows, lambda r: r['partner_id'], lambda r: r['partner_name'],
                order='venta', limit=10),
            'by_category': self._agg(
                rows, lambda r: r['categ_id'], lambda r: r['categ_name'],
                order='venta', limit=14),
            'orders': self._orders_from(rows),
        }

    # ==================================================================
    # RPC 1: tablero por dominio
    # ==================================================================

    # ── F2.1: privacidad de utilidad/costos EN EL PAYLOAD ────────────────
    # El acceso al tablero es de dos niveles: Visor (nivel 1) y Autorizador
    # de Precios (nivel 2). La utilidad, el margen y todo derivado del costo
    # all-in solo pueden viajar al nivel 2; para el nivel 1 se enmascaran en
    # el SERVIDOR (no ocultamiento CSS). Los valores enmascarados viajan
    # como null y el front decide no pintarlos (jamás ceros falsos).
    _PROFIT_KEYS = frozenset({
        'utilidad', 'utilidad_mxn', 'utilidad_mes', 'margen', 'margen_pct',
        'margen_mes', 'costo', 'costo_base_usd', 'cost_allin_usd',
        # Valor de inventario = m² × costo all-in → permite reconstruir costo
        'valor', 'valor_mxn', 'inv_valor_mxn',
    })

    def _profit_allowed(self):
        return self.env.user.has_group(
            'inventory_shopping_cart.group_price_authorizer')

    def _scrub_profit(self, data):
        allowed = self._profit_allowed()
        if allowed:
            if isinstance(data, dict):
                data['perm_profit'] = True
            return data

        keys = self._PROFIT_KEYS

        def walk(x):
            if isinstance(x, dict):
                return {k: (None if k in keys else walk(v))
                        for k, v in x.items()}
            if isinstance(x, list):
                return [walk(i) for i in x]
            return x

        out = walk(data)
        if isinstance(out, dict):
            out['perm_profit'] = False
        return out

    @api.model
    def get_dashboard(self, domain, filters=None):
        self._check_access()
        f = filters or {}
        fn = {
            'resumen': self._dom_resumen,
            'comercial': self._dom_comercial,
            'inventario': self._dom_inventario,
            'compras': self._dom_compras,
            'transito': self._dom_transito,
            'recepciones': self._dom_recepciones,
            'taller': self._dom_taller,
            'entregas': self._dom_entregas,
            'control': self._dom_control,
            'financiero': self._dom_financiero,
            'pronosticos': self._dom_pronosticos,
        }.get(domain, self._dom_resumen)
        try:
            return self._scrub_profit(fn(f))
        except AccessError:
            raise
        except Exception as exc:
            _logger.exception('[SOM Analytics] dominio %s', domain)
            return {'error': str(exc)}

    # ── RESUMEN ────────────────────────────────────────────────────────
    def _dom_resumen(self, f):
        rows = self._sale_rows(f)
        pack = self._sales_pack(rows)
        inv = self._inventory_pack(f)
        fin = self._finance_totals()
        tr = self._transit_pack(f)
        pack['kpis'].update({
            'inv_disponible_m2': inv['kpis']['disponible_m2'],
            'inv_valor_mxn': inv['kpis']['valor_mxn'],
            'transit_m2': tr['kpis']['total_m2'],
            'por_cobrar': fin['por_cobrar'],
            'por_pagar': fin['por_pagar'],
        })
        pack['aging'] = inv['aging']
        pack['transit_status'] = tr['by_status']
        pack['finance'] = fin
        return pack

    # ── COMERCIAL ──────────────────────────────────────────────────────
    def _dom_comercial(self, f):
        rows = self._sale_rows(f)
        pack = self._sales_pack(rows)

        # Conversión cotización → orden (mismo periodo, sin respaldos)
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        self.env.cr.execute("""
            SELECT state, COUNT(*) FROM sale_order
            WHERE date_order >= %s AND date_order <= %s
              AND COALESCE(x_is_quote_backup, false) = false
            GROUP BY state
        """, (dt_from, dt_to))
        st = dict(self.env.cr.fetchall())
        quotes = st.get('draft', 0) + st.get('sent', 0) + st.get('sale', 0)
        pack['kpis']['conversion_pct'] = round(
            st.get('sale', 0) / quotes * 100, 1) if quotes else 0.0
        pack['kpis']['cotizaciones_abiertas'] = (
            st.get('draft', 0) + st.get('sent', 0))

        # Descuentos MXN del periodo
        self.env.cr.execute("""
            SELECT COALESCE(SUM(x_discount_amount_mxn),0),
                   COUNT(*) FILTER (WHERE COALESCE(x_discount_needs_auth,false))
            FROM sale_order
            WHERE state='sale' AND date_order >= %s AND date_order <= %s
        """, (dt_from, dt_to))
        desc, desc_auth = self.env.cr.fetchone()
        pack['kpis']['descuento_mxn'] = round(desc or 0.0, 2)
        pack['kpis']['descuentos_con_auth'] = desc_auth or 0

        dt = (dt_from, dt_to)

        # 1.4 Canal embajador: % de la venta y top especificadores
        arch = self._sq("""
            SELECT COALESCE(rp.name,''), COUNT(DISTINCT so.id),
                   SUM(so.amount_total)
            FROM sale_order so
            JOIN res_partner rp ON rp.id = so.x_architect_id
            WHERE so.state='sale' AND so.x_architect_id IS NOT NULL
              AND so.date_order >= %s AND so.date_order <= %s
            GROUP BY 1 ORDER BY 3 DESC LIMIT 8
        """, dt)
        pack['architects'] = [
            {'name': a, 'ordenes': b, 'monto': round(c or 0, 2)}
            for (a, b, c) in arch]
        row = self._sq("""
            SELECT COUNT(*) FILTER (WHERE x_architect_id IS NOT NULL), COUNT(*)
            FROM sale_order WHERE state='sale'
              AND date_order >= %s AND date_order <= %s
        """, dt, default=[(0, 0)])[0]
        pack['kpis']['pct_via_arquitecto'] = round(
            row[0] / row[1] * 100, 1) if row[1] else 0.0

        # 1.6 Órdenes bloqueadas por control de precios (vivas)
        row = self._sq("""
            SELECT COUNT(*), COALESCE(SUM(amount_total),0) FROM sale_order
            WHERE COALESCE(x_has_low_prices,false) AND state != 'cancel'
        """, default=[(0, 0)])[0]
        pack['kpis']['bloqueadas_precio'] = row[0]
        pack['kpis']['bloqueadas_monto'] = round(row[1] or 0, 2)

        # 1.7 Exenciones de IVA
        iva = dict(self._sq("""
            SELECT COALESCE(x_iva_exempt_state,'none'), COUNT(*)
            FROM sale_order
            WHERE date_order >= %s AND date_order <= %s
              AND COALESCE(x_iva_exempt_state,'none') != 'none'
            GROUP BY 1
        """, dt))
        pack['kpis']['iva_solicitadas'] = sum(iva.values())
        pack['kpis']['iva_aprobadas'] = iva.get('approved', 0)

        # 2.2 Exposición cambiaria: USD confirmado SIN TC congelado
        row = self._sq("""
            SELECT COALESCE(SUM(so.amount_total),0), COUNT(*)
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state='sale' AND rc.name='USD'
              AND COALESCE(so.x_delivery_exchange_rate,0) = 0
        """, default=[(0, 0)])[0]
        pack['kpis']['exposicion_usd'] = round(row[0] or 0, 2)
        pack['kpis']['exposicion_mxn'] = round(
            (row[0] or 0) * self._current_usd_rate(), 2)
        pack['kpis']['exposicion_ordenes'] = row[1]

        # 2.3 Autorizaciones de precio: volumen y agilidad
        row = self._sq("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE state='approved'),
                   COUNT(*) FILTER (WHERE state='pending'),
                   AVG(EXTRACT(EPOCH FROM (authorization_date - create_date))
                       / 3600.0) FILTER (WHERE authorization_date IS NOT NULL)
            FROM price_authorization
            WHERE create_date >= %s AND create_date <= %s
        """, dt, default=[(0, 0, 0, None)])[0]
        pack['kpis']['auth_solicitudes'] = row[0]
        pack['kpis']['auth_aprobadas'] = row[1]
        pack['kpis']['auth_pendientes'] = row[2]
        pack['kpis']['auth_horas_resolucion'] = round(row[3] or 0.0, 1)

        # 1.8 Comisiones devengadas (commission.move, normalizado a MXN)
        rate = self._current_usd_rate()
        com = self._sq("""
            SELECT COALESCE(p.name,''), COALESCE(rc.name,'MXN'),
                   SUM(cm.amount)
            FROM commission_move cm
            LEFT JOIN res_partner p ON p.id = cm.partner_id
            LEFT JOIN res_currency rc ON rc.id = cm.currency_id
            WHERE cm.date >= %s AND cm.date <= %s
            GROUP BY 1, 2 ORDER BY 3 DESC
        """, (date_from, date_to))
        com_map = {}
        for (name, cur, amt) in com:
            mxn = (amt or 0.0) * (rate if cur == 'USD' else 1.0)
            com_map[name] = com_map.get(name, 0.0) + mxn
        pack['commissions'] = sorted(
            ({'name': k, 'mxn': round(v, 2)} for k, v in com_map.items()),
            key=lambda x: -x['mxn'])[:10]
        pack['kpis']['comisiones_mxn'] = round(sum(com_map.values()), 2)

        # 2.4 Índice de realización de precio (precio cobrado vs N1)
        row = self._sq("""
            SELECT SUM(sol.price_unit * sol.product_uom_qty),
                   SUM(CASE WHEN rc.name='USD'
                            THEN COALESCE((pt.x_price_usd_1->>%(cid)s)::float, 0)
                            ELSE COALESCE((pt.x_price_mxn_1->>%(cid)s)::float, 0)
                       END * sol.product_uom_qty)
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id AND so.state='sale'
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE sol.display_type IS NULL
              AND so.date_order >= %(d1)s AND so.date_order <= %(d2)s
              AND CASE WHEN rc.name='USD'
                       THEN COALESCE((pt.x_price_usd_1->>%(cid)s)::float, 0)
                       ELSE COALESCE((pt.x_price_mxn_1->>%(cid)s)::float, 0)
                  END > 0
        """, {'cid': self._cid(), 'd1': dt[0], 'd2': dt[1]},
            default=[(0, 0)])[0]
        pack['kpis']['realizacion_pct'] = round(
            (row[0] or 0) / row[1] * 100, 1) if row[1] else 0.0

        # 1.6b Reincidencias del piso autorizado (bajar de lo ya autorizado)
        pack['kpis']['reincidencias_piso'] = (lambda r: r[0])(self._sq("""
            SELECT COUNT(*) FROM mail_message
            WHERE model = 'sale.order'
              AND body ILIKE '%%Precio por debajo de lo autorizado%%'
              AND create_date >= %s AND create_date <= %s
        """, dt, default=[(0,)])[0])

        # 2.3b Delta promedio precio solicitado vs autorizado
        row = self._sq("""
            SELECT AVG((requested_price - authorized_price)
                / NULLIF(requested_price, 0)) * 100
            FROM price_authorization_line
            WHERE requested_price > 0 AND authorized_price > 0
              AND create_date >= %s AND create_date <= %s
        """, dt, default=[(None,)])[0]
        pack['kpis']['auth_delta_pct'] = round(row[0] or 0.0, 1)

        # 2.2b Ganancia/pérdida cambiaria realizada (TC entrega vs TC confirmación)
        row = self._sq("""
            SELECT COALESCE(SUM(so.amount_total
                * (so.x_delivery_exchange_rate - so.x_confirm_exchange_rate)), 0),
                   COUNT(*)
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state = 'sale' AND rc.name = 'USD'
              AND COALESCE(so.x_confirm_exchange_rate, 0) > 0
              AND COALESCE(so.x_delivery_exchange_rate, 0) > 0
              AND so.date_order >= %s AND so.date_order <= %s
        """, dt, default=[(0, 0)])[0]
        pack['kpis']['fx_realizado_mxn'] = round(row[0] or 0.0, 2)
        pack['kpis']['fx_ordenes'] = row[1]

        # 1.5b Resultado de autorizaciones de descuento + descuento evitado
        row = self._sq("""
            SELECT COUNT(*) FILTER (WHERE x_discount_auth_result = 'approved'),
                   COUNT(*) FILTER (WHERE x_discount_auth_result = 'rejected'),
                   COALESCE(SUM(x_discount_rejected_amount), 0)
            FROM sale_order
            WHERE date_order >= %s AND date_order <= %s
        """, dt, default=[(0, 0, 0)])[0]
        pack['kpis']['desc_aprobados'] = row[0]
        pack['kpis']['desc_rechazados'] = row[1]
        pack['kpis']['descuento_evitado_mxn'] = round(row[2] or 0.0, 2)

        # 1.3b Antigüedad media de cotizaciones abiertas + m² cotizados vs
        # confirmados en el periodo
        row = self._sq("""
            SELECT AVG(EXTRACT(EPOCH FROM (NOW() - date_order)) / 86400.0)
            FROM sale_order
            WHERE state IN ('draft','sent')
              AND COALESCE(x_is_quote_backup, false) = false
        """, default=[(None,)])[0]
        pack['kpis']['cotiz_edad_media'] = round(row[0] or 0.0, 1)

        row = self._sq("""
            SELECT
              COALESCE(SUM(sol.product_uom_qty) FILTER (
                  WHERE so.state IN ('draft','sent')), 0),
              COALESCE(SUM(sol.product_uom_qty) FILTER (
                  WHERE so.state = 'sale'), 0)
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE sol.display_type IS NULL
              AND sol.product_uom_id IN %s
              AND COALESCE(so.x_is_quote_backup, false) = false
              AND so.date_order >= %s AND so.date_order <= %s
        """, (tuple(self._area_uom_ids()), dt[0], dt[1]),
            default=[(0, 0)])[0]
        pack['kpis']['m2_cotizados'] = round(row[0] or 0.0, 1)
        pack['kpis']['m2_confirmados'] = round(row[1] or 0.0, 1)

        # 1.6c Edad promedio de los bloqueos de precio vivos
        row = self._sq("""
            SELECT AVG(EXTRACT(EPOCH FROM (NOW() - write_date)) / 86400.0)
            FROM sale_order
            WHERE COALESCE(x_has_low_prices, false) AND state != 'cancel'
        """, default=[(None,)])[0]
        pack['kpis']['bloqueo_edad_dias'] = round(row[0] or 0.0, 1)

        # Rendimiento del CATÁLOGO compartido (galería pública):
        # links creados, reservas cerradas desde el catálogo y cuántas
        # terminaron en venta del cliente dentro de los 30 días.
        cat_row = self._sq("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE expiration_date > NOW()),
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM sale_order so
                       WHERE so.partner_id = gs.partner_id
                         AND so.state = 'sale'
                         AND so.date_order >= gs.create_date
                         AND so.date_order
                             <= gs.create_date + INTERVAL '30 days'))
            FROM gallery_share gs
            WHERE gs.create_date >= %s AND gs.create_date <= %s
        """, (dt[0], dt[1]), default=[(0, 0, 0)])[0]
        res_row = self._sq("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE state = 'done')
            FROM stock_lot_hold_order
            WHERE notas LIKE
                'Reserva creada automáticamente desde Galería Pública%%'
              AND create_date >= %s AND create_date <= %s
        """, (dt[0], dt[1]), default=[(0, 0)])[0]
        links = cat_row[0] or 0
        pack['catalogo'] = {
            'links': links,
            'links_activos': cat_row[1] or 0,
            'clientes_compraron': cat_row[2] or 0,
            'conversion_pct': round(
                (cat_row[2] or 0) / links * 100, 1) if links else 0.0,
            'reservas': res_row[0] or 0,
            'reservas_concretadas': res_row[1] or 0,
        }
        pack['catalogo_by_seller'] = [
            {'name': a, 'links': b, 'compraron': c}
            for (a, b, c) in self._sq("""
                SELECT COALESCE(p.name, u.login, 'Sin vendedor'),
                       COUNT(*),
                       COUNT(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM sale_order so
                           WHERE so.partner_id = gs.partner_id
                             AND so.state = 'sale'
                             AND so.date_order >= gs.create_date
                             AND so.date_order
                                 <= gs.create_date + INTERVAL '30 days'))
                FROM gallery_share gs
                LEFT JOIN res_users u ON u.id = gs.user_id
                LEFT JOIN res_partner p ON p.id = u.partner_id
                WHERE gs.create_date >= %s AND gs.create_date <= %s
                GROUP BY 1 ORDER BY 2 DESC LIMIT 10
            """, (dt[0], dt[1]), default=[])]

        # ── F2.1: series para el storytelling de conversión y autorizaciones ──
        dtf, dtt = self._bounds(f)

        # Funnel de cotizaciones CREADAS en el periodo (solo etapas que el
        # sistema registra: borrador → enviada → confirmada; canceladas aparte).
        stage_rows = self._sq("""
            SELECT so.state, COUNT(*), COALESCE(SUM(so.amount_total), 0)
            FROM sale_order so
            WHERE so.create_date >= %s AND so.create_date <= %s
              AND so.state IN ('draft', 'sent', 'sale', 'cancel')
            GROUP BY so.state
        """, (dtf, dtt), default=[])
        smap = {a: (b, c) for (a, b, c) in stage_rows}
        pack['funnel'] = [
            {'stage': lbl, 'count': smap.get(st, (0, 0))[0],
             'amount': round(smap.get(st, (0, 0))[1] or 0.0, 2)}
            for (st, lbl) in [('draft', 'Borrador'), ('sent', 'Enviada'),
                              ('sale', 'Confirmada')]
        ]

        # Etapa COBRADO: dinero realmente pagado de las órdenes confirmadas
        # creadas en el periodo (delivery_paid_amount de sale_delivery_auth:
        # pagos aplicados en facturas posteadas, topado al total de la
        # orden). El conteo son órdenes pagadas al 100%.
        if 'delivery_paid_amount' in self.env['sale.order']._fields:
            paid_row = self._sq("""
                SELECT COUNT(*) FILTER (
                           WHERE so.delivery_paid_amount >= so.amount_total
                             AND so.amount_total > 0),
                       COALESCE(SUM(LEAST(so.delivery_paid_amount,
                                          so.amount_total)), 0)
                FROM sale_order so
                WHERE so.create_date >= %s AND so.create_date <= %s
                  AND so.state = 'sale'
            """, (dtf, dtt), default=[(0, 0)])[0]
            pack['funnel'].append({
                'stage': 'Cobrado',
                'count': paid_row[0] or 0,
                'amount': round(paid_row[1] or 0.0, 2),
            })

        pack['funnel'].append({
            'stage': 'Cancelada',
            'count': smap.get('cancel', (0, 0))[0],
            'amount': round(smap.get('cancel', (0, 0))[1] or 0.0, 2),
        })

        # Aging de cotizaciones ABIERTAS hoy (borrador/enviada, sin filtro de
        # periodo: el backlog vivo es lo accionable).
        pack['quotes_aging'] = [
            {'bucket': b, 'count': c, 'amount': round(m or 0.0, 2)}
            for (b, c, m) in self._sq("""
                SELECT CASE
                        WHEN CURRENT_DATE - so.create_date::date <= 7 THEN '0-7 días'
                        WHEN CURRENT_DATE - so.create_date::date <= 14 THEN '8-14 días'
                        WHEN CURRENT_DATE - so.create_date::date <= 30 THEN '15-30 días'
                        ELSE '> 30 días' END AS bucket,
                       COUNT(*), COALESCE(SUM(so.amount_total), 0)
                FROM sale_order so
                WHERE so.state IN ('draft', 'sent')
                GROUP BY 1
            """, default=[])
        ]
        _AGING_ORDER = ['0-7 días', '8-14 días', '15-30 días', '> 30 días']
        pack['quotes_aging'].sort(key=lambda r: _AGING_ORDER.index(r['bucket']))

        # Cotizaciones estancadas: mayores montos abiertos con su antigüedad.
        pack['stalled_quotes'] = [
            {'name': a, 'partner': b, 'seller': c,
             'amount': round(d or 0.0, 2), 'days': int(e or 0)}
            for (a, b, c, d, e) in self._sq("""
                SELECT so.name, COALESCE(rp.name, ''), COALESCE(sp.name, ''),
                       so.amount_total,
                       CURRENT_DATE - so.create_date::date
                FROM sale_order so
                LEFT JOIN res_partner rp ON rp.id = so.partner_id
                LEFT JOIN res_users ru ON ru.id = so.user_id
                LEFT JOIN res_partner sp ON sp.id = ru.partner_id
                WHERE so.state IN ('draft', 'sent')
                ORDER BY so.amount_total DESC
                LIMIT 15
            """, default=[])
        ]

        # Flujo semanal de autorizaciones: entradas vs resueltas (la
        # resolución usa write_date de los estados terminales — mismo criterio
        # que auth_horas_resolucion).
        created = dict((a, b) for (a, b) in self._sq("""
            SELECT to_char(create_date, 'IYYY-IW'), COUNT(*)
            FROM price_authorization
            WHERE create_date >= %s AND create_date <= %s
            GROUP BY 1
        """, (dtf, dtt), default=[]))
        resolved = dict((a, b) for (a, b) in self._sq("""
            SELECT to_char(write_date, 'IYYY-IW'), COUNT(*)
            FROM price_authorization
            WHERE state IN ('approved', 'rejected', 'expired')
              AND write_date >= %s AND write_date <= %s
            GROUP BY 1
        """, (dtf, dtt), default=[]))
        weeks = sorted(set(created) | set(resolved))
        pack['auth_weekly'] = [
            {'week': w[5:], 'created': created.get(w, 0),
             'resolved': resolved.get(w, 0)}
            for w in weeks
        ]

        # Percentiles de resolución (mediana/P75/P90 en horas): el promedio
        # solo esconde los extremos.
        prow = self._sq("""
            SELECT
                percentile_cont(0.5) WITHIN GROUP (ORDER BY horas),
                percentile_cont(0.75) WITHIN GROUP (ORDER BY horas),
                percentile_cont(0.9) WITHIN GROUP (ORDER BY horas)
            FROM (
                SELECT EXTRACT(EPOCH FROM (write_date - create_date)) / 3600.0
                       AS horas
                FROM price_authorization
                WHERE state IN ('approved', 'rejected')
                  AND write_date >= %s AND write_date <= %s
            ) t
        """, (dtf, dtt), default=[(None, None, None)])[0]
        # ── Series F2.3 (visuales ECharts 6) ────────────────────────────
        # Venta DIARIA del periodo (calendar heatmap) — misma conversión USD.
        _rate = self._current_usd_rate()
        pack['daily_sales'] = [
            {'date': str(a), 'amount': round(b or 0.0, 2), 'orders': c}
            for (a, b, c) in self._sq("""
                SELECT so.date_order::date,
                       COALESCE(SUM(CASE WHEN rc.name = 'USD'
                           THEN so.amount_total
                                * COALESCE(NULLIF(so.x_delivery_exchange_rate, 0), %(rate)s)
                           ELSE so.amount_total END), 0),
                       COUNT(*)
                FROM sale_order so
                LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
                LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
                WHERE so.state = 'sale'
                  AND so.date_order >= %(f)s AND so.date_order <= %(t)s
                GROUP BY 1
            """, {'rate': _rate, 'f': dtf, 't': dtt}, default=[])
        ]

        # Chord clientes ↔ categorías y jerarquía categoría → productos:
        # derivadas de las MISMAS rows del pack (sin SQL adicional).
        _pairs = {}
        _hier = {}
        for r in rows:
            ckey = (r['partner_name'] or 'Sin cliente',
                    r['categ_name'] or 'Sin categoría')
            _pairs[ckey] = _pairs.get(ckey, 0.0) + r['venta_mxn']
            hkey = r['categ_name'] or 'Sin categoría'
            _hier.setdefault(hkey, {})
            pname = r['product_name'] or 'Sin producto'
            _hier[hkey][pname] = _hier[hkey].get(pname, 0.0) + r['venta_mxn']

        pack['client_categ'] = [
            {'customer': a, 'categ': b, 'venta': round(v, 2)}
            for ((a, b), v) in sorted(
                _pairs.items(), key=lambda kv: -kv[1])[:30]
        ]

        _top_categs = sorted(
            _hier.items(),
            key=lambda kv: -sum(kv[1].values()))[:6]
        pack['categ_products'] = [
            {'categ': categ,
             'total': round(sum(prods.values()), 2),
             'products': [
                 {'name': pn, 'venta': round(pv, 2)}
                 for pn, pv in sorted(prods.items(), key=lambda x: -x[1])[:5]
             ]}
            for categ, prods in _top_categs
        ]

        # Series por ENTIDAD (matrix ejecutiva y themeriver): derivadas de
        # las mismas rows — venta mensual por vendedor y por categoría.
        _sm = {}
        _cm = {}
        _months_seen = set()
        for r in rows:
            m = r['period']
            _months_seen.add(m)
            sk = r['user_name'] or 'Sin vendedor'
            _sm.setdefault(sk, {})
            _sm[sk][m] = _sm[sk].get(m, 0.0) + r['venta_mxn']
            ck = r['categ_name'] or 'Sin categoría'
            _cm.setdefault(ck, {})
            _cm[ck][m] = _cm[ck].get(m, 0.0) + r['venta_mxn']

        _months_sorted = sorted(_months_seen)
        _top_sellers = sorted(
            _sm.items(), key=lambda kv: -sum(kv[1].values()))[:6]
        pack['seller_monthly'] = {
            'months': _months_sorted,
            'sellers': [
                {'name': name,
                 'total': round(sum(mm.values()), 2),
                 'serie': [round(mm.get(m, 0.0), 2) for m in _months_sorted]}
                for name, mm in _top_sellers
            ],
        }
        _pm = {}
        for r in rows:
            pk = r['product_name'] or 'Sin producto'
            _pm.setdefault(pk, {})
            _pm[pk][r['period']] = _pm[pk].get(r['period'], 0.0) + r['venta_mxn']
        _top_pm = sorted(_pm.items(), key=lambda kv: -sum(kv[1].values()))[:6]
        pack['product_monthly'] = {
            'months': _months_sorted,
            'products': [
                {'name': name, 'total': round(sum(mm.values()), 2),
                 'serie': [round(mm.get(m, 0.0), 2) for m in _months_sorted]}
                for name, mm in _top_pm
            ],
        }

        _top_cm = sorted(_cm.items(), key=lambda kv: -sum(kv[1].values()))[:6]
        pack['categ_monthly'] = [
            {'month': m, 'categ': name, 'venta': round(mm.get(m, 0.0), 2)}
            for name, mm in _top_cm for m in _months_sorted
        ]
        pack['granularity'] = (f or {}).get('granularity') or 'month'

        pack['auth_percentiles'] = {
            'p50': round(float(prow[0]), 1) if prow[0] is not None else None,
            'p75': round(float(prow[1]), 1) if prow[1] is not None else None,
            'p90': round(float(prow[2]), 1) if prow[2] is not None else None,
        }

        return pack

    # ── INVENTARIO ─────────────────────────────────────────────────────
    def _inventory_pack(self, f):
        params = {'area_uoms': tuple(self._area_uom_ids()),
                  'cid': self._cid()}
        prod_where = ''
        if f.get('product_id'):
            prod_where = ' AND pt.id = %(tmpl_id)s'
            params['tmpl_id'] = int(f['product_id'])

        hold_field = self.env['stock.quant']._fields.get('x_tiene_hold')
        hold_expr = ('COALESCE(q.x_tiene_hold, false)'
                     if hold_field is not None and hold_field.store
                     else 'false')

        self.env.cr.execute("""
            SELECT
                pt.id AS tmpl_id,
                COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS product_name,
                COALESCE(sl.name, '') AS lot_name,
                {hold} AS has_hold,
                SUM(q.quantity) AS m2,
                SUM(q.quantity
                    * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0))
                    AS valor
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.uom_id IN %(area_uoms)s
            LEFT JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0 {pw}
            GROUP BY pt.id, product_name, lot_name, has_hold
        """.format(hold=hold_expr, pw=prod_where), params)
        rows = self.env.cr.dictfetchall()

        disp = holdm = valor = 0.0
        prods = defaultdict(lambda: {'m2': 0.0, 'valor': 0.0, 'lots': 0})
        pref = []
        for r in rows:
            if r['has_hold']:
                holdm += r['m2']
            else:
                disp += r['m2']
            valor += r['valor']
            p = prods[(r['tmpl_id'], r['product_name'])]
            p['m2'] += r['m2']
            p['valor'] += r['valor']
            p['lots'] += 1
            pref.append((self._lot_bucket_key(r['lot_name']), r['m2'], r['valor']))

        numeric = sorted({k[1] for (k, _m, _v) in pref if k[0] == 0 and k[1] >= 0})
        cut_old = numeric[len(numeric) // 3] if numeric else 0
        cut_mid = numeric[2 * len(numeric) // 3] if numeric else 0

        def bucket(k):
            if k[0] == 1:
                return 'Serie S (recientes)'
            if k[1] < 0:
                return 'Sin folio'
            if k[1] <= cut_old:
                return 'Antiguo (liquidar)'
            if k[1] <= cut_mid:
                return 'Medio'
            return 'Reciente'

        ag = defaultdict(lambda: {'m2': 0.0, 'valor': 0.0, 'lots': 0})
        for k, m2, val in pref:
            b = ag[bucket(k)]
            b['m2'] += m2
            b['valor'] += val
            b['lots'] += 1
        order_b = ['Serie S (recientes)', 'Reciente', 'Medio',
                   'Antiguo (liquidar)', 'Sin folio']

        top = sorted(
            ({'key': k[0], 'name': k[1], 'm2': round(v['m2'], 1),
              'valor': round(v['valor'], 2), 'lots': v['lots']}
             for k, v in prods.items()),
            key=lambda x: -x['m2'])[:12]

        # Antigüedad REAL del inventario: días desde la creación del lote
        # (stock.lot.create_date). Promedio ponderado por m² + buckets
        # extendidos hasta +10 años (capital dormido de años atrás).
        edad_rows = self._sq("""
            SELECT {age} AS bucket,
                   SUM(q.quantity) AS m2,
                   COUNT(DISTINCT sl.id) AS lots
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
            GROUP BY 1
        """.format(age=self._AGE_CASE), default=[])
        order_edad = self._AGE_ORDER
        edad_map = {a: (b, c) for (a, b, c) in edad_rows}

        # Hold m²: la fuente canónica es stock.lot.hold (estado activo),
        # no el flag del quant (no está almacenado y siempre daba 0).
        hold_real = self._sq("""
            SELECT COALESCE(SUM(q.quantity), 0)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot_hold h ON h.quant_id = q.id
                 AND h.estado = 'activo'
            WHERE q.quantity > 0
        """, default=[(0,)])[0][0]
        if hold_real:
            holdm = float(hold_real)
            disp = max(sum(r['m2'] for r in rows) - holdm, 0.0)

        # % de LOTES en stock con fotografía propia (stock.lot.image)
        foto_lote = self._sq("""
            SELECT COUNT(DISTINCT sl.id),
                   COUNT(DISTINCT sl.id) FILTER (WHERE EXISTS (
                       SELECT 1 FROM stock_lot_image li
                       WHERE li.lot_id = sl.id))
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0, 0)])[0]
        fl_total, fl_con = foto_lote[0] or 0, foto_lote[1] or 0
        edad_prom = self._sq("""
            SELECT COALESCE(
                SUM(q.quantity * EXTRACT(EPOCH FROM (NOW() - sl.create_date))
                    / 86400.0) / NULLIF(SUM(q.quantity), 0), 0)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0,)])[0][0]

        return {
            'kpis': {
                'disponible_m2': round(disp, 1),
                'hold_m2': round(holdm, 1),
                'valor_mxn': round(valor, 2),
                'lotes': len(rows),
                'edad_prom_dias': round(float(edad_prom or 0.0), 1),
                'lotes_foto_pct': round(
                    fl_con / fl_total * 100, 1) if fl_total else 0.0,
                'lotes_con_foto': fl_con,
                'lotes_sin_foto': fl_total - fl_con,
            },
            'aging': [
                {'bucket': b, 'm2': round(ag[b]['m2'], 1),
                 'lots': ag[b]['lots'], 'valor': round(ag[b]['valor'], 2)}
                for b in order_b if b in ag],
            'aging_by_date': [
                {'bucket': b, 'm2': round(edad_map[b][0] or 0.0, 1),
                 'lots': edad_map[b][1]}
                for b in order_edad if b in edad_map],
            'top_stock': top,
        }

    def _dom_inventario(self, f):
        pack = self._inventory_pack(f)
        # Rotación: m² entregados 12m (salidas done) vs stock actual
        self.env.cr.execute("""
            SELECT COALESCE(SUM(ml.quantity),0)
            FROM stock_move_line ml
            JOIN stock_location src ON src.id = ml.location_id
                 AND src.usage = 'internal'
            JOIN stock_location dst ON dst.id = ml.location_dest_id
                 AND dst.usage = 'customer'
            JOIN product_product pp ON pp.id = ml.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.uom_id IN %s
            WHERE ml.state = 'done'
              AND ml.date >= (CURRENT_DATE - INTERVAL '12 months')
        """, (tuple(self._area_uom_ids()),))
        out_12m = self.env.cr.fetchone()[0] or 0.0
        stock = pack['kpis']['disponible_m2'] + pack['kpis']['hold_m2']
        pack['kpis']['salidas_12m_m2'] = round(out_12m, 1)
        pack['kpis']['rotacion'] = round(out_12m / stock, 2) if stock else 0.0
        pack['kpis']['meses_inventario'] = round(
            stock / (out_12m / 12), 1) if out_12m else 0.0

        # Holds activos (conteo de órdenes de apartado confirmadas)
        row = self._sq("""
            SELECT COUNT(*),
                   AVG(EXTRACT(EPOCH FROM (NOW() - create_date)) / 86400.0),
                   COUNT(*) FILTER (WHERE create_date
                       < NOW() - INTERVAL '30 days')
            FROM stock_lot_hold_order
            WHERE state NOT IN ('cancelled','cancel','done')
        """, default=[(0, None, 0)])[0]
        pack['kpis']['holds_activos'] = row[0]
        pack['kpis']['holds_edad_dias'] = round(row[1] or 0.0, 1)
        pack['kpis']['holds_eternos'] = row[2]

        # 3.5 Cobertura fotográfica: bloques en stock con foto
        blocks_stock = {b for (b,) in self._sq("""
            SELECT DISTINCT UPPER(sl.x_bloque)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage='internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0 AND COALESCE(sl.x_bloque,'') != ''
        """)}
        blocks_photo = {b for (b,) in self._sq(
            "SELECT DISTINCT UPPER(block_name) "
            "FROM supplier_shipment_block_image "
            "WHERE COALESCE(block_name,'') != ''")}
        pack['kpis']['bloques_stock'] = len(blocks_stock)
        pack['kpis']['bloques_con_foto_pct'] = round(
            len(blocks_stock & blocks_photo) / len(blocks_stock) * 100, 1
        ) if blocks_stock else 0.0

        # 3.8 MERMA DIMENSIONAL: PL del proveedor vs medidas reales del WS
        merma = self._sq("""
            SELECT COALESCE(rp.name,'Sin proveedor'),
                   SUM(ml.x_alto_temp * ml.x_ancho_temp) AS teorico,
                   SUM(sl.x_alto * sl.x_ancho) AS real
            FROM stock_move_line ml
            JOIN stock_picking sp ON sp.id = ml.picking_id
            LEFT JOIN res_partner rp ON rp.id = sp.partner_id
            JOIN stock_lot sl ON sl.id = ml.lot_id
            WHERE ml.state = 'done'
              AND COALESCE(ml.x_alto_temp,0) > 0
              AND COALESCE(ml.x_ancho_temp,0) > 0
              AND COALESCE(sl.x_alto,0) > 0
              AND COALESCE(sl.x_ancho,0) > 0
            GROUP BY 1 HAVING SUM(ml.x_alto_temp * ml.x_ancho_temp) > 0
            ORDER BY 2 DESC LIMIT 10
        """)
        # 3.4 Integridad del bloque: bloques con ventas parciales
        row = self._sq("""
            WITH block_lots AS (
                SELECT sl.x_bloque AS b, sl.id AS lot_id,
                       COALESCE(SUM(q.quantity) FILTER (
                           WHERE loc.usage='internal'), 0) AS stock
                FROM stock_lot sl
                LEFT JOIN stock_quant q ON q.lot_id = sl.id AND q.quantity > 0
                LEFT JOIN stock_location loc ON loc.id = q.location_id
                WHERE COALESCE(sl.x_bloque,'') != ''
                GROUP BY sl.x_bloque, sl.id
            ), agg AS (
                SELECT b,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE stock > 0) AS con_stock,
                       SUM(stock) AS m2_rem
                FROM block_lots GROUP BY b
            )
            SELECT COUNT(*) FILTER (WHERE con_stock > 0
                       AND con_stock < total),
                   COALESCE(SUM(m2_rem) FILTER (WHERE con_stock > 0
                       AND con_stock < total), 0),
                   COUNT(*) FILTER (WHERE con_stock > 0)
            FROM agg
        """, default=[(0, 0, 0)])[0]
        pack['kpis']['bloques_rotos'] = row[0]
        pack['kpis']['bloques_rotos_m2'] = round(row[1] or 0, 1)
        pack['kpis']['bloques_activos'] = row[2]

        # 3.1b Committed: m² internos seleccionados en OVs confirmadas
        row = self._sq("""
            SELECT COALESCE(SUM(q.quantity), 0)
            FROM sale_order_line_stock_quant_rel rel
            JOIN sale_order_line sol ON sol.id = rel.sale_order_line_id
            JOIN sale_order so ON so.id = sol.order_id AND so.state = 'sale'
            JOIN stock_quant q ON q.id = rel.stock_quant_id AND q.quantity > 0
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
        """, default=[(0,)])[0]
        pack['kpis']['committed_m2'] = round(row[0] or 0.0, 1)

        # 3.1c Tránsito publicado: T.Committed (con pedido) vs T.Available
        row = self._sq("""
            SELECT
              COALESCE(SUM(l.product_uom_qty) FILTER (
                  WHERE l.order_id IS NOT NULL), 0),
              COALESCE(SUM(l.product_uom_qty) FILTER (
                  WHERE l.order_id IS NULL), 0)
            FROM stock_transit_line l
            JOIN stock_transit_voyage v ON v.id = l.voyage_id
                 AND v.custom_status NOT IN ('delivered','cancel')
            WHERE v.transit_inventory_published = true
        """, default=[(0, 0)])[0]
        pack['kpis']['t_committed_m2'] = round(row[0] or 0.0, 1)
        pack['kpis']['t_available_m2'] = round(row[1] or 0.0, 1)

        # 3.5b m² disponibles "invisibles" (bloque sin foto)
        m2_sin_foto = self._sq("""
            SELECT COALESCE(SUM(q.quantity), 0)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage='internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0 AND COALESCE(sl.x_bloque,'') != ''
              AND UPPER(sl.x_bloque) NOT IN (
                  SELECT DISTINCT UPPER(block_name)
                  FROM supplier_shipment_block_image
                  WHERE COALESCE(block_name,'') != '')
        """, default=[(0,)])[0][0]
        pack['kpis']['m2_sin_foto'] = round(m2_sin_foto or 0.0, 1)

        # 3.6b Conversión de holds: vendidos vs liberados (histórico)
        hrow = self._sq("""
            SELECT COUNT(*) FILTER (WHERE state = 'done'),
                   COUNT(*) FILTER (WHERE state IN ('cancelled','cancel'))
            FROM stock_lot_hold_order
        """, default=[(0, 0)])[0]
        closed = (hrow[0] or 0) + (hrow[1] or 0)
        pack['kpis']['holds_conversion_pct'] = round(
            (hrow[0] or 0) / closed * 100, 1) if closed else 0.0

        # 3.7 Reservas débiles de carrito desplazadas por venta (periodo)
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        row = self._sq("""
            SELECT COUNT(*) FROM mail_message
            WHERE model = 'stock.picking'
              AND body ILIKE '%%Reservas re-ancladas%%'
              AND create_date >= %s AND create_date <= %s
        """, (dt_from, dt_to), default=[(0,)])[0]
        pack['kpis']['reservas_desplazadas'] = row[0]

        pack['merma'] = [
            {'name': a, 'teorico': round(b or 0, 1), 'real': round(c or 0, 1),
             'merma_m2': round((b or 0) - (c or 0), 1),
             'merma_pct': round(((b or 0) - (c or 0)) / b * 100, 2) if b else 0}
            for (a, b, c) in merma]
        return pack

    # ── COMPRAS ────────────────────────────────────────────────────────
    def _dom_compras(self, f):
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        rate = self._current_usd_rate()
        self.env.cr.execute("""
            SELECT to_char(po.date_approve,'YYYY-MM') AS month,
                   COALESCE(rc.name,'MXN') AS currency,
                   COALESCE(p.name,'') AS partner_name,
                   po.partner_id,
                   SUM(po.amount_total) AS total
            FROM purchase_order po
            LEFT JOIN res_currency rc ON rc.id = po.currency_id
            LEFT JOIN res_partner p ON p.id = po.partner_id
            WHERE po.state IN ('purchase','done')
              AND po.date_approve >= %s AND po.date_approve <= %s
            GROUP BY 1, 2, 3, 4
        """, (dt_from, dt_to))
        rows = self.env.cr.dictfetchall()

        months = defaultdict(lambda: {'USD': 0.0, 'MXN': 0.0, 'mxn_norm': 0.0})
        provs = defaultdict(lambda: {'mxn': 0.0})
        total_norm = 0.0
        for r in rows:
            cur = 'USD' if r['currency'] == 'USD' else 'MXN'
            norm = r['total'] * (rate if cur == 'USD' else 1.0)
            months[r['month']][cur] += r['total']
            months[r['month']]['mxn_norm'] += norm
            provs[(r['partner_id'], r['partner_name'])]['mxn'] += norm
            total_norm += norm

        by_month = [
            {'key': k, 'usd': round(v['USD'], 2), 'mxn': round(v['MXN'], 2),
             'mxn_norm': round(v['mxn_norm'], 2)}
            for k, v in sorted(months.items())]
        top_prov = sorted(
            ({'key': k[0], 'name': k[1], 'mxn': round(v['mxn'], 2)}
             for k, v in provs.items()), key=lambda x: -x['mxn'])[:10]

        # Pipeline de allocations (To Be Purchased)
        alloc = []
        try:
            self.env.cr.execute("""
                SELECT COALESCE(state,'draft'), COUNT(*),
                       COALESCE(SUM(quantity),0)
                FROM purchase_order_line_allocation GROUP BY 1
            """)
            alloc = [{'state': a, 'count': b, 'qty': round(c or 0, 1)}
                     for (a, b, c) in self.env.cr.fetchall()]
        except Exception:
            self.env.cr.execute('SELECT 1')

        # 4.3 Lead time OC confirmada → primera recepción validada
        row = self._sq("""
            SELECT AVG(dias), COUNT(*) FROM (
                SELECT EXTRACT(EPOCH FROM (MIN(sp.date_done)
                    - po.date_approve)) / 86400.0 AS dias
                FROM purchase_order po
                JOIN stock_picking sp ON sp.purchase_id = po.id
                    AND sp.state = 'done'
                JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                    AND spt.code = 'incoming'
                WHERE po.date_approve >= %s AND po.date_approve <= %s
                GROUP BY po.id, po.date_approve
            ) t
        """, (dt_from, dt_to), default=[(None, 0)])[0]
        lead_days = round(row[0] or 0.0, 1)
        lead_count = row[1]

        # 4.6 Discrepancias PL portal vs recibido
        disc = self._sq(
            'SELECT COUNT(*) FROM purchase_discrepancy',
            default=[(0,)])[0][0]

        # Top materiales comprados en el periodo (normalizado a MXN)
        top_products = [
            {'key': a, 'name': b, 'qty': round(c or 0, 1),
             'mxn': round(d or 0, 2)}
            for (a, b, c, d) in self._sq("""
                SELECT pt.id,
                       COALESCE(pt.name->>'es_MX', pt.name->>'en_US',''),
                       SUM(pol.product_qty),
                       SUM(pol.price_subtotal
                           * CASE WHEN rc.name = 'USD' THEN %s ELSE 1 END)
                FROM purchase_order_line pol
                JOIN purchase_order po ON po.id = pol.order_id
                     AND po.state IN ('purchase', 'done')
                LEFT JOIN res_currency rc ON rc.id = po.currency_id
                JOIN product_product pp ON pp.id = pol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE po.date_approve >= %s AND po.date_approve <= %s
                GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 12
            """, (rate, dt_from, dt_to), default=[])]

        # OCs abiertas con material pendiente de recibir (backlog vivo)
        open_pos = [
            {'id': a, 'name': b, 'partner': c_, 'date': d_,
             'total': round(e or 0, 2), 'currency': f_,
             'recibido_pct': round((h or 0) / g * 100, 1) if g else 0.0}
            for (a, b, c_, d_, e, f_, g, h) in self._sq("""
                SELECT po.id, po.name, COALESCE(p.name, ''),
                       po.date_approve::date::text,
                       po.amount_total, COALESCE(rc.name, 'MXN'),
                       SUM(pol.product_qty), SUM(pol.qty_received)
                FROM purchase_order po
                JOIN purchase_order_line pol ON pol.order_id = po.id
                LEFT JOIN res_partner p ON p.id = po.partner_id
                LEFT JOIN res_currency rc ON rc.id = po.currency_id
                WHERE po.state = 'purchase'
                GROUP BY po.id, po.name, p.name, po.date_approve,
                         po.amount_total, rc.name
                HAVING SUM(pol.product_qty) > SUM(pol.qty_received) + 0.01
                ORDER BY po.date_approve LIMIT 15
            """, default=[])]

        return {
            'kpis': {
                'compras_mxn': round(total_norm, 2),
                'ordenes': len({(r['partner_id'], r['month']) for r in rows}),
                'proveedores': len(provs),
                'tc_usado': rate,
                'lead_time_dias': lead_days,
                'lead_time_ocs': lead_count,
                'discrepancias': disc,
                'costo_log_m2_mxn': (lambda r: round(r[0] or 0.0, 2))(
                    self._sq("""
                    SELECT AVG(t.all_in * %s / NULLIF(m2.total, 0))
                    FROM supplier_shipment sh
                    JOIN LATERAL (
                        SELECT ft.all_in
                        FROM freight_tariff ft
                        WHERE ft.pol_id = sh.pol_id AND ft.pod_id = sh.pod_id
                          AND COALESCE(ft.all_in, 0) > 0
                        ORDER BY ft.id DESC LIMIT 1
                    ) t ON true
                    JOIN LATERAL (
                        SELECT SUM(ml.quantity) AS total
                        FROM stock_picking sp
                        JOIN stock_move_line ml ON ml.picking_id = sp.id
                             AND ml.state = 'done'
                        WHERE sp.supplier_shipment_id = sh.id
                          AND sp.state = 'done'
                          AND ml.product_uom_id IN %s
                    ) m2 ON m2.total > 0
                    WHERE sh.pol_id IS NOT NULL AND sh.pod_id IS NOT NULL
                """, (rate, tuple(self._area_uom_ids())),
                    default=[(None,)])[0]),
                'pipeline_edad_dias': (lambda r: round(r[0] or 0.0, 1))(
                    self._sq("""
                    SELECT AVG(EXTRACT(EPOCH FROM (NOW() - create_date))
                        / 86400.0)
                    FROM purchase_order_line_allocation
                    WHERE COALESCE(state,'draft') IN ('draft','pending')
                """, default=[(None,)])[0]),
            },
            'by_month': by_month,
            'top_suppliers': top_prov,
            'allocations': alloc,
            'top_products': top_products,
            'open_pos': open_pos,
        }

    # ── RECEPCIONES ────────────────────────────────────────────────────
    def _dom_recepciones(self, f):
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        dt = (dt_from, dt_to)
        area = tuple(self._area_uom_ids())

        weekly = self._sq("""
            SELECT to_char(date_trunc('week', sp.date_done), 'YYYY-MM-DD'),
                   COUNT(DISTINCT sp.id),
                   COALESCE(SUM(ml.quantity) FILTER (
                       WHERE ml.product_uom_id IN %s), 0)
            FROM stock_picking sp
            JOIN stock_move_line ml ON ml.picking_id = sp.id
                 AND ml.state = 'done'
            WHERE sp.state = 'done'
              AND sp.origin LIKE %s
              AND sp.date_done >= %s AND sp.date_done <= %s
            GROUP BY 1 ORDER BY 1
        """, (area, '%(Recepción Física)', dt[0], dt[1]))
        by_week = [{'week': a[5:], 'count': b, 'm2': round(c or 0, 1)}
                   for (a, b, c) in weekly]

        # 6.5 Pedimento completo (lotes de área en stock interno)
        row = self._sq("""
            SELECT COUNT(DISTINCT sl.id),
                   COUNT(DISTINCT sl.id) FILTER (
                       WHERE COALESCE(sl.x_pedimento,'') != '')
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.uom_id IN %s
            WHERE q.quantity > 0
        """, (area,), default=[(0, 0)])[0]
        total_lots, with_ped = row

        # Recepciones de compra validadas (contenedores absorbidos)
        rec = self._sq("""
            SELECT COUNT(*) FROM stock_picking sp
            JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                 AND spt.code = 'incoming'
            WHERE sp.state = 'done'
              AND sp.date_done >= %s AND sp.date_done <= %s
        """, dt, default=[(0,)])[0][0]

        # 6.3 Exactitud: recepciones del periodo CON devolución posterior
        row = self._sq("""
            SELECT COUNT(DISTINCT sp.id) FILTER (WHERE ret.id IS NOT NULL)
            FROM stock_picking sp
            JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                 AND spt.code = 'incoming'
            LEFT JOIN stock_picking ret ON ret.return_id = sp.id
                 AND ret.state != 'cancel'
            WHERE sp.state = 'done'
              AND sp.date_done >= %s AND sp.date_done <= %s
        """, dt, default=[(0,)])[0]
        con_devolucion = row[0] or 0

        # 6.4 Etiquetado ZPL: lotes recibidos en el periodo con etiqueta
        # impresa dentro de las 24 h siguientes a su creación.
        row = self._sq("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE sl.x_zpl_printed_at IS NOT NULL
                       AND sl.x_zpl_printed_at
                           <= sl.create_date + INTERVAL '24 hours')
            FROM stock_lot sl
            WHERE sl.create_date >= %s AND sl.create_date <= %s
        """, dt, default=[(0, 0)])[0]
        lots_period, lots_labeled = row

        return {
            'kpis': {
                'fisicas_periodo': sum(w['count'] for w in by_week),
                'm2_recibidos': round(sum(w['m2'] for w in by_week), 1),
                'entradas_compra': rec,
                'pedimento_pct': round(
                    with_ped / total_lots * 100, 1) if total_lots else 0.0,
                'lotes_sin_pedimento': total_lots - with_ped,
                'exactitud_pct': round(
                    (rec - con_devolucion) / rec * 100, 1) if rec else 100.0,
                'con_devolucion': con_devolucion,
                'etiquetado_24h_pct': round(
                    lots_labeled / lots_period * 100, 1) if lots_period else 0.0,
                'lotes_periodo': lots_period,
                'faltantes_piezas': (lambda r: round(r[0] or 0, 1))(self._sq("""
                    SELECT SUM(COALESCE(x_ws_missing_pieces,0))
                    FROM stock_picking
                    WHERE date_done >= %s AND date_done <= %s
                """, dt, default=[(0,)])[0]),
                'faltantes_m2': (lambda r: round(r[0] or 0, 1))(self._sq("""
                    SELECT SUM(COALESCE(x_ws_missing_m2,0))
                    FROM stock_picking
                    WHERE date_done >= %s AND date_done <= %s
                """, dt, default=[(0,)])[0]),
                'bajas_scrap': (lambda r: round(r[0] or 0, 1))(self._sq("""
                    SELECT SUM(COALESCE(scrap_qty,0)) FROM stock_scrap
                    WHERE state = 'done'
                      AND write_date >= %s AND write_date <= %s
                """, dt, default=[(0,)])[0]),
                'puerto_a_stock_dias': (lambda r: round(r[0] or 0.0, 1))(
                    self._sq("""
                    SELECT AVG(EXTRACT(EPOCH FROM (sp.date_done
                        - arr.arrived_at)) / 86400.0)
                    FROM (
                        SELECT mm.res_id AS voyage_id,
                               MIN(mm.create_date) AS arrived_at
                        FROM mail_message mm
                        JOIN mail_tracking_value tv ON tv.mail_message_id = mm.id
                        WHERE mm.model = 'stock.transit.voyage'
                          AND (tv.new_value_char ILIKE '%%puerto destino%%'
                               OR tv.new_value_char ILIKE '%%arrived%%')
                        GROUP BY mm.res_id
                    ) arr
                    JOIN stock_transit_voyage v ON v.id = arr.voyage_id
                    JOIN stock_picking sp ON sp.id = v.picking_id
                        AND sp.state = 'done'
                    WHERE sp.date_done >= arr.arrived_at
                      AND sp.date_done >= %s AND sp.date_done <= %s
                """, dt, default=[(None,)])[0]),
            },
            'by_week': by_week,
            'by_supplier': [
                {'name': a, 'count': b, 'm2': round(c or 0, 1)}
                for (a, b, c) in self._sq("""
                    SELECT COALESCE(p.name, 'Sin proveedor'),
                           COUNT(DISTINCT sp.id),
                           SUM(ml.quantity) FILTER (
                               WHERE ml.product_uom_id IN %s)
                    FROM stock_picking sp
                    JOIN stock_picking_type spt
                         ON spt.id = sp.picking_type_id
                         AND spt.code = 'incoming'
                    JOIN stock_move_line ml ON ml.picking_id = sp.id
                         AND ml.state = 'done'
                    LEFT JOIN res_partner p ON p.id = sp.partner_id
                    WHERE sp.state = 'done'
                      AND sp.date_done >= %s AND sp.date_done <= %s
                    GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 10
                """, (area, dt[0], dt[1]), default=[])],
            'recent': [
                {'id': a, 'name': b, 'partner': c, 'date': d,
                 'm2': round(e or 0, 1), 'origin': f_ or ''}
                for (a, b, c, d, e, f_) in self._sq("""
                    SELECT sp.id, sp.name, COALESCE(p.name, ''),
                           sp.date_done::date::text,
                           SUM(ml.quantity) FILTER (
                               WHERE ml.product_uom_id IN %s),
                           sp.origin
                    FROM stock_picking sp
                    JOIN stock_picking_type spt
                         ON spt.id = sp.picking_type_id
                         AND spt.code = 'incoming'
                    JOIN stock_move_line ml ON ml.picking_id = sp.id
                         AND ml.state = 'done'
                    LEFT JOIN res_partner p ON p.id = sp.partner_id
                    WHERE sp.state = 'done'
                    GROUP BY sp.id, sp.name, p.name, sp.date_done, sp.origin
                    ORDER BY sp.date_done DESC LIMIT 12
                """, (area,), default=[])],
            'by_month12': [
                {'key': a, 'm2': round(b or 0, 1), 'count': c}
                for (a, b, c) in self._sq("""
                    SELECT to_char(sp.date_done, 'YYYY-MM'),
                           SUM(ml.quantity) FILTER (
                               WHERE ml.product_uom_id IN %s),
                           COUNT(DISTINCT sp.id)
                    FROM stock_picking sp
                    JOIN stock_picking_type spt
                         ON spt.id = sp.picking_type_id
                         AND spt.code = 'incoming'
                    JOIN stock_move_line ml ON ml.picking_id = sp.id
                         AND ml.state = 'done'
                    WHERE sp.state = 'done'
                      AND sp.date_done
                          >= (CURRENT_DATE - INTERVAL '12 months')
                    GROUP BY 1 ORDER BY 1
                """, (area,), default=[])],
        }

    # ── TALLER ─────────────────────────────────────────────────────────
    def _dom_taller(self, f):
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        dt = (dt_from, dt_to)

        by_state = self._sq("""
            SELECT state, COUNT(*) FROM workshop_order
            WHERE state != 'cancel' GROUP BY 1
        """)
        labels = {'draft': 'Borrador', 'in_workshop': 'En taller',
                  'done': 'Terminada'}
        states = [{'state': labels.get(a, a), 'count': b}
                  for (a, b) in by_state]

        row = self._sq("""
            SELECT AVG(EXTRACT(EPOCH FROM (date_done - create_date))
                / 86400.0), COUNT(*)
            FROM workshop_order
            WHERE state = 'done' AND date_done IS NOT NULL
              AND date_done >= %s AND date_done <= %s
        """, dt, default=[(None, 0)])[0]

        row2 = self._sq("""
            SELECT AVG(EXTRACT(EPOCH FROM (NOW() - create_date)) / 86400.0),
                   COUNT(*)
            FROM workshop_order WHERE state = 'in_workshop'
        """, default=[(None, 0)])[0]

        reclas = self._sq("""
            SELECT COUNT(*) FROM stock_lot_reclassification
            WHERE create_date >= %s AND create_date <= %s
        """, dt, default=[(0,)])[0][0]

        weekly = self._sq("""
            SELECT to_char(date_trunc('week', date_done), 'YYYY-MM-DD'),
                   COUNT(*)
            FROM workshop_order
            WHERE state='done' AND date_done >= %s AND date_done <= %s
            GROUP BY 1 ORDER BY 1
        """, dt)

        # 8.3 Merma de proceso: áreas almacenadas de OTs terminadas
        row3 = self._sq("""
            SELECT SUM(COALESCE(area_in_total,0)),
                   SUM(COALESCE(area_out_total,0)),
                   SUM(COALESCE(area_loss_total,0))
            FROM workshop_order
            WHERE state = 'done' AND date_done >= %s AND date_done <= %s
        """, dt, default=[(0, 0, 0)])[0]
        area_in = row3[0] or 0.0

        return {
            'kpis': {
                'area_in_m2': round(area_in, 1),
                'area_out_m2': round(row3[1] or 0.0, 1),
                'merma_m2': round(row3[2] or 0.0, 1),
                'merma_pct': round(
                    (row3[2] or 0.0) / area_in * 100, 1) if area_in else 0.0,
                'en_taller': next((x['count'] for x in states
                                   if x['state'] == 'En taller'), 0),
                'backlog_dias': round(row2[0] or 0.0, 1),
                'lead_time_dias': round(row[0] or 0.0, 1),
                'terminadas': row[1],
                'reclasificaciones': reclas,
            },
            'by_state': states,
            'weekly_done': [{'week': a[5:], 'count': b}
                            for (a, b) in weekly],
            'pasadas': [
                {'label': lbl, 'count': (lambda r: r[0])(self._sq(
                    "SELECT COUNT(*) FROM stock_lot WHERE name ~ %s",
                    (rx,), default=[(0,)])[0])}
                for (lbl, rx) in [
                    ('2ª pasada (-R2)', '-R2$'),
                    ('3ª pasada (-R3)', '-R3$'),
                    ('4ª o más (-R4+)', '-R([4-9]|[1-9][0-9])$'),
                ]
            ],
        }

    # ── CONTROL (dominio 10: bandeja cero y calidad de datos) ──────────
    def _dom_control(self, f):
        pend = []

        def add(label, sql, params=None):
            row = self._sq(sql, params, default=[(0, None)])[0]
            count = row[0] or 0
            age = round(row[1] or 0.0, 1)
            pend.append({'label': label, 'count': count, 'age': age})

        add('Autorizaciones de precio pendientes', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - create_date))
                / 86400.0)
            FROM price_authorization WHERE state = 'pending'""")
        add('Solicitudes de entrega sin resolver', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - request_date))
                / 86400.0)
            FROM delivery_auth_request WHERE state = 'requested'""")
        add('Exenciones de IVA en espera', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - write_date))
                / 86400.0)
            FROM sale_order WHERE x_iva_exempt_state = 'requested'""")
        add('Órdenes bloqueadas por precios', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - write_date))
                / 86400.0)
            FROM sale_order
            WHERE COALESCE(x_has_low_prices,false) AND state != 'cancel'""")
        add('Descuentos esperando autorización', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - write_date))
                / 86400.0)
            FROM sale_order
            WHERE COALESCE(x_discount_auth_requested,false)
              AND state != 'cancel'""")
        add('Cotizaciones abiertas +30 días', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - date_order))
                / 86400.0)
            FROM sale_order
            WHERE state IN ('draft','sent')
              AND COALESCE(x_is_quote_backup,false) = false
              AND date_order < NOW() - INTERVAL '30 days'""")

        add('Worksheets sin procesar', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - write_date))
                / 86400.0)
            FROM stock_picking
            WHERE COALESCE(packing_list_imported, false)
              AND COALESCE(worksheet_imported, false) = false
              AND state NOT IN ('done','cancel')""")
        add('Alertas de inventario duplicado (quant > PL)', """
            SELECT COUNT(*), MAX(EXTRACT(EPOCH FROM (NOW() - create_date))
                / 86400.0)
            FROM mail_message
            WHERE model = 'stock.transit.voyage'
              AND body ILIKE '%%excede el Packing List%%'
              AND create_date >= NOW() - INTERVAL '90 days'""")

        # Viajes sin publicar (compute con search propio → ORM)
        try:
            pend_pub = self.env['stock.transit.voyage'].sudo().search_count(
                [('tc_publication_pending', '=', True)])
        except Exception:
            pend_pub = 0
        pend.append({'label': 'Viajes con inventario sin publicar',
                     'count': pend_pub, 'age': 0.0})

        # 10.1 Completitud de ficha de lote (lotes en stock interno)
        row = self._sq("""
            SELECT COUNT(DISTINCT sl.id),
              COUNT(DISTINCT sl.id) FILTER (WHERE COALESCE(sl.x_bloque,'') != ''),
              COUNT(DISTINCT sl.id) FILTER (
                  WHERE COALESCE(sl.x_alto,0) > 0 AND COALESCE(sl.x_ancho,0) > 0),
              COUNT(DISTINCT sl.id) FILTER (WHERE COALESCE(sl.x_color,'') != ''),
              COUNT(DISTINCT sl.id) FILTER (
                  WHERE COALESCE(sl.x_contenedor,'') != ''),
              COUNT(DISTINCT sl.id) FILTER (
                  WHERE COALESCE(sl.x_pedimento,'') != '')
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0, 0, 0, 0, 0, 0)])[0]
        total = row[0] or 1
        ficha = [
            {'campo': 'Bloque', 'pct': round(row[1] / total * 100, 1)},
            {'campo': 'Dimensiones', 'pct': round(row[2] / total * 100, 1)},
            {'campo': 'Color', 'pct': round(row[3] / total * 100, 1)},
            {'campo': 'Contenedor', 'pct': round(row[4] / total * 100, 1)},
            {'campo': 'Pedimento', 'pct': round(row[5] / total * 100, 1)},
        ]
        foto = self._sq("""
            SELECT COUNT(DISTINCT sl.id) FILTER (
                WHERE UPPER(COALESCE(sl.x_bloque,'')) IN (
                    SELECT DISTINCT UPPER(block_name)
                    FROM supplier_shipment_block_image
                    WHERE COALESCE(block_name,'') != ''))
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0,)])[0][0]
        ficha.append({'campo': 'Foto de bloque',
                      'pct': round((foto or 0) / total * 100, 1)})

        # 10.2 Órdenes sin proyecto / sin referencia del cliente (periodo)
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        row = self._sq("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE x_project_id IS NULL),
                   COUNT(*) FILTER (WHERE COALESCE(client_order_ref,'') = '')
            FROM sale_order
            WHERE state = 'sale' AND date_order >= %s AND date_order <= %s
        """, (dt_from, dt_to), default=[(0, 0, 0)])[0]

        # FOTOS: % de lotes de placa (UdM de área) en stock con fotografía
        # de su bloque + quién sube las fotos (ranking y serie mensual).
        area = tuple(self._area_uom_ids())
        foto_row = self._sq("""
            SELECT COUNT(DISTINCT sl.id),
                   COUNT(DISTINCT sl.id) FILTER (
                       WHERE UPPER(COALESCE(sl.x_bloque,'')) IN (
                           SELECT DISTINCT UPPER(block_name)
                           FROM supplier_shipment_block_image
                           WHERE COALESCE(block_name,'') != ''))
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.uom_id IN %s
            WHERE q.quantity > 0
        """, (area,), default=[(0, 0)])[0]
        placas, placas_foto = foto_row[0] or 0, foto_row[1] or 0

        def uploader_rank(table):
            return [
                {'name': a, 'total': b, 'mes': c, 'ultima': d}
                for (a, b, c, d) in self._sq("""
                    SELECT COALESCE(p.name, u.login, 'Sistema / Portal'),
                           COUNT(*),
                           COUNT(*) FILTER (WHERE i.create_date
                               >= date_trunc('month', CURRENT_DATE)),
                           MAX(i.create_date)::date::text
                    FROM {t} i
                    LEFT JOIN res_users u ON u.id = i.create_uid
                    LEFT JOIN res_partner p ON p.id = u.partner_id
                    GROUP BY 1 ORDER BY 3 DESC, 2 DESC LIMIT 12
                """.format(t=table), default=[])]

        def photos_monthly(table):
            return [
                {'key': a, 'fotos': b}
                for (a, b) in self._sq("""
                    SELECT to_char(create_date, 'YYYY-MM'), COUNT(*)
                    FROM {t}
                    WHERE create_date
                        >= (CURRENT_DATE - INTERVAL '12 months')
                    GROUP BY 1 ORDER BY 1
                """.format(t=table), default=[])]

        # FOTOS DE LOTE: la métrica a premiar — quién sube más fotos de
        # lote (stock.lot.image). Las de bloque quedan como secundarias.
        lot_uploaders = uploader_rank('stock_lot_image')
        lot_photos_by_month = photos_monthly('stock_lot_image')
        photo_uploaders = uploader_rank('supplier_shipment_block_image')
        photos_by_month = photos_monthly('supplier_shipment_block_image')

        lot_foto = self._sq("""
            SELECT COUNT(DISTINCT sl.id),
                   COUNT(DISTINCT sl.id) FILTER (WHERE EXISTS (
                       SELECT 1 FROM stock_lot_image li
                       WHERE li.lot_id = sl.id))
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0, 0)])[0]
        lotes_stock, lotes_foto = lot_foto[0] or 0, lot_foto[1] or 0

        # AJUSTE DE PRECIOS: productos vendidos a precio DISTINTO de N1 y
        # N2 en el periodo — con su escalera N1/N2/N3, costo all-in y
        # diferencia, para corregir lista de precios o costo.
        pa_rows = self._sq("""
            SELECT pt.id,
                   COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS name,
                   COALESCE(rc.name, 'MXN') AS cur,
                   SUM(sol.price_subtotal) AS venta,
                   SUM(sol.product_uom_qty) AS qty,
                   COUNT(DISTINCT so.id) AS ordenes,
                   (pt.x_price_usd_1->>%(cid)s)::float AS u1,
                   (pt.x_price_usd_2->>%(cid)s)::float AS u2,
                   (pt.x_price_usd_3->>%(cid)s)::float AS u3,
                   (pt.x_price_mxn_1->>%(cid)s)::float AS m1,
                   (pt.x_price_mxn_2->>%(cid)s)::float AS m2,
                   (pt.x_price_mxn_3->>%(cid)s)::float AS m3,
                   (pt.x_costo_mayor->>%(cid)s)::float AS costo,
                   COALESCE(pt.x_costo_divisa, 'USD') AS cdiv
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
                 AND so.state IN ('sale', 'done')
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.type != 'service'
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE sol.display_type IS NULL
              AND sol.product_uom_qty > 0
              AND so.date_order >= %(d1)s AND so.date_order <= %(d2)s
            GROUP BY pt.id, pt.name, COALESCE(rc.name, 'MXN'),
                     pt.x_price_usd_1, pt.x_price_usd_2, pt.x_price_usd_3,
                     pt.x_price_mxn_1, pt.x_price_mxn_2, pt.x_price_mxn_3,
                     pt.x_costo_mayor, COALESCE(pt.x_costo_divisa, 'USD')
        """, {'cid': self._cid(), 'd1': dt_from,
              'd2': dt_to}, default=[])
        price_adjust = []
        for (tid, name, cur, venta, qty, ordenes,
             u1, u2, u3, m1, m2, m3, costo, cdiv) in pa_rows:
            qty = float(qty or 0)
            if qty <= 0:
                continue
            avg = float(venta or 0) / qty
            if cur == 'USD':
                n1, n2, n3 = u1 or 0.0, u2 or 0.0, u3 or 0.0
            else:
                n1, n2, n3 = m1 or 0.0, m2 or 0.0, m3 or 0.0
            if not n1:
                continue
            tol1 = max(0.01, n1 * 0.005)
            tol2 = max(0.01, (n2 or n1) * 0.005)
            if abs(avg - n1) <= tol1 or (n2 and abs(avg - n2) <= tol2):
                continue
            price_adjust.append({
                'key': tid,
                'name': name,
                'cur': cur,
                'qty': round(qty, 1),
                'ordenes': ordenes,
                'venta': round(float(venta or 0), 2),
                'avg': round(avg, 2),
                'n1': round(n1, 2),
                'n2': round(n2, 2),
                'n3': round(n3, 2),
                'costo': round(float(costo or 0), 2),
                'costo_divisa': cdiv or 'USD',
                'diff_n1': round(avg - n1, 2),
                'diff_pct': round(
                    (avg - n1) / n1 * 100, 1) if n1 else 0.0,
            })
        price_adjust.sort(key=lambda r: -r['venta'])
        price_adjust = price_adjust[:80]
        fx_usd = self._current_usd_rate()
        fx_eur_usd = self._eur_usd_rate()

        return {
            'kpis': {
                'pendientes_total': sum(p['count'] for p in pend),
                'productos_ajuste_precio': len(price_adjust),
                'lotes_en_stock': row and total or 0,
                'sin_proyecto_pct': round(
                    row[1] / row[0] * 100, 1) if row[0] else 0.0,
                'sin_referencia_pct': round(
                    row[2] / row[0] * 100, 1) if row[0] else 0.0,
                'placas': placas,
                'placas_con_foto': placas_foto,
                'placas_foto_pct': round(
                    placas_foto / placas * 100, 1) if placas else 0.0,
                'fotos_total': sum(u['total'] for u in photo_uploaders),
                'fotos_mes': sum(u['mes'] for u in photo_uploaders),
                'lotes_foto_pct': round(
                    lotes_foto / lotes_stock * 100, 1) if lotes_stock else 0.0,
                'lotes_con_foto': lotes_foto,
                'fotos_lote_total': sum(u['total'] for u in lot_uploaders),
                'fotos_lote_mes': sum(u['mes'] for u in lot_uploaders),
            },
            'bandeja': pend,
            'ficha': ficha,
            'photo_uploaders': photo_uploaders,
            'photos_by_month': photos_by_month,
            'lot_photo_uploaders': lot_uploaders,
            'lot_photos_by_month': lot_photos_by_month,
            'price_adjust': price_adjust,
            'fx': {
                'usd_mxn': fx_usd,
                'eur_usd': fx_eur_usd,
                'eur_mxn': round(fx_eur_usd * fx_usd, 4)
                if fx_eur_usd and fx_usd else 0.0,
            },
        }

    def _eur_usd_rate(self):
        """EUR→USD desde las tasas de res.currency (el tramo USD→MXN
        siempre es Banorte). 0.0 si EUR no está configurado."""
        try:
            eur = self.env.ref('base.EUR', raise_if_not_found=False)
            usd = self.env.ref('base.USD', raise_if_not_found=False)
            if eur and usd and eur.active and eur.rate and usd.rate:
                return round(usd.rate / eur.rate, 4)
        except Exception:
            _logger.debug('[SOM Analytics] tasa EUR', exc_info=True)
        return 0.0

    @api.model
    def set_product_cost(self, tmpl_id, cost, currency='MXN'):
        """Ajusta el costo all-in DESDE el dashboard, capturado en la
        divisa del producto. Se guarda SIEMPRE en MXN (x_costo_mayor):
        USD × TC Banorte; EUR primero a USD (tasa EUR/USD) y luego a MXN
        con Banorte. La divisa capturada queda como preferida del
        producto (x_costo_divisa). Solo Autorizadores de Precios."""
        if not self.env.user.has_group(
                'inventory_shopping_cart.group_price_authorizer'):
            raise AccessError(_(
                'Ajustar el costo requiere el permiso de Autorizador '
                'de Precios.'))
        currency = (currency or 'MXN').upper()
        if currency not in ('MXN', 'USD', 'EUR'):
            return {'error': 'Divisa no soportada: %s' % currency}
        tmpl = self.env['product.template'].sudo().browse(int(tmpl_id))
        if not tmpl.exists():
            return {'error': 'El producto ya no existe.'}

        amount = round(float(cost or 0.0), 4)
        usd_mxn = self._current_usd_rate()
        eur_usd = self._eur_usd_rate()
        if currency == 'USD':
            if usd_mxn <= 0:
                return {'error': 'No hay TC Banorte USD/MXN disponible.'}
            new_cost = amount * usd_mxn
            detail = '%.2f USD × %.4f' % (amount, usd_mxn)
        elif currency == 'EUR':
            if eur_usd <= 0:
                return {'error': 'No hay tasa EUR/USD configurada en '
                                 'divisas.'}
            if usd_mxn <= 0:
                return {'error': 'No hay TC Banorte USD/MXN disponible.'}
            new_cost = amount * eur_usd * usd_mxn
            detail = ('%.2f EUR × %.4f (EUR→USD) × %.4f (Banorte)'
                      % (amount, eur_usd, usd_mxn))
        else:
            new_cost = amount
            detail = '%.2f MXN directo' % amount

        new_cost = round(new_cost, 2)
        old_cost = float(tmpl.x_costo_mayor or 0.0)
        tmpl.write({
            'x_costo_mayor': new_cost,
            'x_costo_divisa': currency,
        })
        tmpl.message_post(body=_(
            'Costo all-in ajustado desde SOM Analytics: %(old).2f → '
            '%(new).2f MXN [%(detail)s] (por %(user)s).',
            old=old_cost, new=new_cost, detail=detail,
            user=self.env.user.name))
        _logger.info(
            '[SOM Analytics] %s ajustó costo all-in de %s: %s → %s MXN '
            '(%s)', self.env.user.login, tmpl.display_name, old_cost,
            new_cost, detail)
        return {'ok': True, 'cost_mxn': new_cost, 'currency': currency}


    # ── TRÁNSITO ───────────────────────────────────────────────────────
    def _transit_pack(self, f):
        portal_avance, portal_terminadas = self._portal_avg_progress()
        Voyage = self.env['stock.transit.voyage'].sudo()
        voyages = Voyage.search_read(
            [('custom_status', 'not in', ('delivered', 'cancel'))],
            ['name', 'custom_status', 'total_m2', 'allocated_m2',
             'allocation_percent', 'eta', 'etd', 'container_number',
             'tc_supplier_id', 'tc_publication_pending'],
            limit=400)
        labels = dict(
            Voyage._fields['custom_status']._description_selection(self.env))
        acc = defaultdict(lambda: {'m2': 0.0, 'count': 0})
        total = 0.0
        pending_pub = 0
        presold_num = presold_den = 0.0
        cards = []
        for v in voyages:
            st = v['custom_status']
            m2 = v['total_m2'] or 0.0
            acc[st]['m2'] += m2
            acc[st]['count'] += 1
            total += m2
            if v.get('tc_publication_pending'):
                pending_pub += 1
            presold_num += v['allocated_m2'] or 0.0
            presold_den += m2
            cards.append({
                'id': v['id'], 'name': v['name'],
                'status': labels.get(st, st),
                'supplier': v['tc_supplier_id'][1] if v['tc_supplier_id'] else '',
                'container': v['container_number'] or '',
                'm2': round(m2, 1),
                'alloc_pct': round(v['allocation_percent'] or 0.0, 1),
                'eta': str(v['eta'] or ''),
                'etd': str(v.get('etd') or ''),
            })
        order_st = ['solicitud', 'production', 'booking', 'puerto_origen',
                    'on_sea', 'puerto_destino', 'arrived_port',
                    'reception_pending']
        row = self._sq("""
            SELECT AVG(ABS(eta - eta_original)), COUNT(*)
            FROM stock_transit_voyage
            WHERE eta IS NOT NULL AND eta_original IS NOT NULL
              AND eta != eta_original
              AND custom_status NOT IN ('cancel')
        """, default=[(None, 0)])[0]

        # m² activos por proveedor + arribos por mes de ETA (de los mismos
        # viajes ya cargados — sin SQL extra)
        sup_acc = defaultdict(lambda: {'m2': 0.0, 'count': 0})
        eta_acc = defaultdict(lambda: {'m2': 0.0, 'count': 0})
        for c in cards:
            s = sup_acc[c['supplier'] or 'Sin proveedor']
            s['m2'] += c['m2']
            s['count'] += 1
            if c['eta']:
                e = eta_acc[c['eta'][:7]]
                e['m2'] += c['m2']
                e['count'] += 1
        by_supplier = sorted(
            ({'name': k, 'm2': round(v['m2'], 1), 'count': v['count']}
             for k, v in sup_acc.items()), key=lambda x: -x['m2'])[:12]
        eta_months = [
            {'key': k, 'm2': round(eta_acc[k]['m2'], 1),
             'count': eta_acc[k]['count']}
            for k in sorted(eta_acc)]

        # m² que YA llegaron por mes (12 meses): la película histórica
        arrived_monthly = [
            {'key': a, 'm2': round(b or 0, 1), 'count': c}
            for (a, b, c) in self._sq("""
                SELECT to_char(sp.date_done, 'YYYY-MM'),
                       SUM(ml.quantity) FILTER (
                           WHERE ml.product_uom_id IN %s),
                       COUNT(DISTINCT sp.id)
                FROM stock_picking sp
                JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                     AND spt.code = 'incoming'
                JOIN stock_move_line ml ON ml.picking_id = sp.id
                     AND ml.state = 'done'
                WHERE sp.state = 'done'
                  AND sp.date_done >= (CURRENT_DATE - INTERVAL '12 months')
                GROUP BY 1 ORDER BY 1
            """, (tuple(self._area_uom_ids()),), default=[])]

        return {
            'kpis': {
                'total_m2': round(total, 1),
                'embarques': len(voyages),
                'pendientes_publicar': pending_pub,
                'prevendido_pct': round(
                    presold_num / presold_den * 100, 1) if presold_den else 0.0,
                'eta_desviacion_dias': round(float(row[0] or 0), 1),
                'eta_desviados': row[1],
                'dias_a_publicar': (lambda r: round(r[0] or 0.0, 1))(self._sq("""
                    SELECT AVG(EXTRACT(EPOCH FROM
                        (transit_inventory_published_at - create_date)) / 86400.0)
                    FROM stock_transit_voyage
                    WHERE transit_inventory_published_at IS NOT NULL
                """, default=[(None,)])[0]),
                'ligas_portal': (lambda r: r[0])(self._sq(
                    'SELECT COUNT(*) FROM supplier_access',
                    default=[(0,)])[0]),
                'ligas_sin_acceso_7d': (lambda r: r[0])(self._sq("""
                    SELECT COUNT(*) FROM supplier_access
                    WHERE last_access IS NULL
                       OR last_access < NOW() - INTERVAL '7 days'
                """, default=[(0,)])[0]),
                'ligas_avance_pct': portal_avance,
                'ligas_terminadas': portal_terminadas,
            },
            'by_status': [
                {'status': st, 'label': labels.get(st, st),
                 'm2': round(acc[st]['m2'], 1), 'count': acc[st]['count']}
                for st in order_st if st in acc],
            'voyages': sorted(cards, key=lambda c: c['eta'] or '9999')[:30],
            'by_supplier': by_supplier,
            'eta_months': eta_months,
            'arrived_monthly': arrived_monthly,
        }

    def _portal_avg_progress(self):
        """5.4: % de avance promedio de las ligas de portal activas y
        cuántas están terminadas (avance 100). El avance vive en un compute
        del proforma header, por eso se resuelve vía ORM acotado."""
        result = (0.0, 0)
        try:
            Access = self.env['supplier.access'].sudo()
            Header = self.env['supplier.proforma.header'].sudo()
            headers = Header.search([], order='id desc', limit=60)
            vals = []
            for h in headers:
                try:
                    p = h._portal_progress()
                    if isinstance(p, dict):
                        p = p.get('overall') or p.get('percent') or 0
                    vals.append(float(p or 0))
                except Exception:
                    continue
            if vals:
                result = (
                    round(sum(vals) / len(vals), 1),
                    len([v for v in vals if v >= 100]),
                )
            elif Access.search_count([]) == 0:
                result = (0.0, 0)
        except Exception:
            _logger.exception('[SOM Analytics] portal progress')
        return result

    def _dom_transito(self, f):
        return self._transit_pack(f)

    # ── ENTREGAS ───────────────────────────────────────────────────────
    def _dom_entregas(self, f):
        if 'sale.delivery.document' not in self.env:
            return {'kpis': {}, 'unavailable': True}
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)

        self.env.cr.execute("""
            SELECT COALESCE(delivery_status,'preparacion'), COUNT(*)
            FROM sale_delivery_document
            WHERE document_type = 'remission'
              AND create_date >= %s AND create_date <= %s
            GROUP BY 1
        """, (dt_from, dt_to))
        by_status = [{'status': a, 'count': b}
                     for (a, b) in self.env.cr.fetchall()]

        self.env.cr.execute("""
            SELECT
                COUNT(*) FILTER (WHERE signed_at IS NOT NULL
                    AND COALESCE(signed_by,'') NOT LIKE 'ENTREGA MANUAL%%'),
                COUNT(*) FILTER (WHERE COALESCE(signed_by,'')
                    LIKE 'ENTREGA MANUAL%%'),
                COUNT(*) FILTER (WHERE state='confirmed'
                    AND signed_at IS NULL)
            FROM sale_delivery_document
            WHERE document_type = 'remission'
              AND create_date >= %s AND create_date <= %s
        """, (dt_from, dt_to))
        app_signed, manual, en_ruta = self.env.cr.fetchone()

        # Autorizaciones de entrega sin pago completo (crédito informal)
        auth_rows = []
        auth_total = 0.0
        if 'delivery.auth.request' in self.env:
            for req in self.env['delivery.auth.request'].sudo().search(
                    [('state', '=', 'approved')], limit=200,
                    order='approval_date desc'):
                residual = (req.sale_order_id.amount_total or 0.0) - (
                    getattr(req.sale_order_id, 'delivery_paid_amount', 0.0) or 0.0)
                if residual <= 0:
                    continue
                auth_total += residual
                if len(auth_rows) < 15:
                    auth_rows.append({
                        'order': req.sale_order_id.name,
                        'order_id': req.sale_order_id.id,
                        'partner': req.partner_id.name or '',
                        'residual': round(residual, 2),
                        'approver': req.approved_by_id.name or '',
                        'date': str(req.approval_date or '')[:10],
                    })

        # 7.1 Ciclo pedido → entrega (confirmación OV → firma/entrega)
        row = self._sq("""
            SELECT AVG(EXTRACT(EPOCH FROM (d.signed_at - so.date_order))
                / 86400.0), COUNT(*)
            FROM sale_delivery_document d
            JOIN sale_order so ON so.id = d.sale_order_id
            WHERE d.document_type = 'remission' AND d.signed_at IS NOT NULL
              AND d.signed_at >= %s AND d.signed_at <= %s
        """, (dt_from, dt_to), default=[(None, 0)])[0]

        # 7.5 Devoluciones por motivo. Cubre DOS caminos: documentos de
        # devolución del módulo de entregas Y devoluciones hechas directo
        # en almacén (picking de retorno sobre una salida a cliente).
        devs = self._sq("""
            SELECT COALESCE(r.name, 'Sin motivo'), COUNT(*)
            FROM sale_delivery_document d
            LEFT JOIN sale_return_reason r ON r.id = d.return_reason_id
            WHERE d.document_type = 'return' AND d.state != 'cancelled'
              AND d.create_date >= %s AND d.create_date <= %s
            GROUP BY 1 ORDER BY 2 DESC LIMIT 8
        """, (dt_from, dt_to))
        dev_pickings = self._sq("""
            SELECT COUNT(*) FROM stock_picking ret
            JOIN stock_picking orig ON orig.id = ret.return_id
            JOIN stock_picking_type spt ON spt.id = orig.picking_type_id
                 AND spt.code = 'outgoing'
            WHERE ret.state = 'done'
              AND ret.date_done >= %s AND ret.date_done <= %s
        """, (dt_from, dt_to), default=[(0,)])[0][0] or 0
        dev_docs = sum(b for (_a, b) in devs)
        if dev_pickings > dev_docs:
            devs = list(devs) + [(
                'Devolución de almacén (sin motivo capturado)',
                dev_pickings - dev_docs)]

        return {
            'kpis': {
                'firmadas_app': app_signed or 0,
                'manuales': manual or 0,
                'en_ruta': en_ruta or 0,
                'credito_informal_mxn': round(auth_total, 2),
                'ciclo_dias': round(row[0] or 0.0, 1),
                'ciclo_muestras': row[1],
                'devoluciones': sum(b for (_a, b) in devs),
                'ocupacion_pct': (lambda r: round(r[0] or 0.0, 1))(self._sq("""
                    SELECT AVG(carga / NULLIF(cap, 0)) * 100 FROM (
                        SELECT d.id, d.vehicle_capacity_sqm AS cap,
                               SUM(l.qty_done) AS carga
                        FROM sale_delivery_document d
                        JOIN sale_delivery_document_line l
                             ON l.document_id = d.id
                        JOIN product_product pp ON pp.id = l.product_id
                        JOIN product_template pt ON pt.id = pp.product_tmpl_id
                             AND pt.uom_id IN %s
                        WHERE d.document_type = 'remission'
                          AND d.state = 'confirmed'
                          AND COALESCE(d.vehicle_capacity_sqm, 0) > 0
                          AND d.create_date >= %s AND d.create_date <= %s
                        GROUP BY d.id, d.vehicle_capacity_sqm
                    ) t
                """, (tuple(self._area_uom_ids()), dt_from,
                       dt_to), default=[(None,)])[0]),
                'paradas_gps': (lambda r: r[0])(self._sq("""
                    SELECT COUNT(*) FROM sale_delivery_route_point
                    WHERE create_date >= %s AND create_date <= %s
                """, (dt_from, dt_to),
                    default=[(0,)])[0]),
                'cobrado_al_entregar_pct': (lambda r: round(r[0] or 0.0, 1))(
                    self._sq("""
                    SELECT AVG(LEAST(so.delivery_paid_amount
                        / NULLIF(so.amount_total, 0), 1)) * 100
                    FROM sale_delivery_document d
                    JOIN sale_order so ON so.id = d.sale_order_id
                    WHERE d.document_type = 'remission'
                      AND d.signed_at IS NOT NULL
                      AND d.signed_at >= %s AND d.signed_at <= %s
                """, (dt_from, dt_to),
                    default=[(None,)])[0]),
            },
            'by_status': by_status,
            'auth_sin_pago': auth_rows,
            'returns': [{'reason': a, 'count': b} for (a, b) in devs],
        }

    # ── FINANCIERO ─────────────────────────────────────────────────────
    def _finance_totals(self):
        self.env.cr.execute("""
            SELECT
              COALESCE(SUM(amount_residual_signed) FILTER (
                WHERE move_type = 'out_invoice'), 0),
              COALESCE(-SUM(amount_residual_signed) FILTER (
                WHERE move_type = 'in_invoice'), 0)
            FROM account_move
            WHERE state = 'posted' AND amount_residual != 0
        """)
        ar, ap = self.env.cr.fetchone()
        return {'por_cobrar': round(ar or 0.0, 2),
                'por_pagar': round(ap or 0.0, 2)}

    def _dom_financiero(self, f):
        tot = self._finance_totals()

        def buckets_and_top_safe(move_type, sign):
            try:
                self.env.cr.execute("""
                    SELECT
                      CASE
                        WHEN COALESCE(m.invoice_date_due, m.date) >= CURRENT_DATE
                            THEN 'Al corriente'
                        WHEN CURRENT_DATE - COALESCE(m.invoice_date_due, m.date)
                            <= 30 THEN '1-30 días'
                        WHEN CURRENT_DATE - COALESCE(m.invoice_date_due, m.date)
                            <= 60 THEN '31-60 días'
                        WHEN CURRENT_DATE - COALESCE(m.invoice_date_due, m.date)
                            <= 90 THEN '61-90 días'
                        ELSE '90+ días'
                      END AS bucket,
                      SUM({s} m.amount_residual_signed) AS monto
                    FROM account_move m
                    WHERE m.state = 'posted' AND m.move_type = %s
                      AND m.amount_residual != 0
                    GROUP BY 1
                """.format(s=sign), (move_type,))
                b = dict(self.env.cr.fetchall())
            except Exception:
                self.env.cr.execute('SELECT 1')
                b = {}
            order = ['Al corriente', '1-30 días', '31-60 días',
                     '61-90 días', '90+ días']
            buckets = [{'bucket': k, 'monto': round(b.get(k, 0.0), 2)}
                       for k in order if k in b]
            try:
                self.env.cr.execute("""
                    SELECT p.id, COALESCE(p.name,''),
                           SUM({s} m.amount_residual_signed) AS monto,
                           COUNT(*) AS facturas,
                           MIN(COALESCE(m.invoice_date_due, m.date))::text
                    FROM account_move m
                    JOIN res_partner p ON p.id = m.partner_id
                    WHERE m.state = 'posted' AND m.move_type = %s
                      AND m.amount_residual != 0
                    GROUP BY p.id, p.name ORDER BY monto DESC LIMIT 12
                """.format(s=sign), (move_type,))
                top = [{'key': a, 'name': b_, 'monto': round(c or 0, 2),
                        'facturas': d, 'oldest': e}
                       for (a, b_, c, d, e) in self.env.cr.fetchall()]
            except Exception:
                self.env.cr.execute('SELECT 1')
                top = []
            return buckets, top

        ar_buckets, ar_top = buckets_and_top_safe('out_invoice', '')
        ap_buckets, ap_top = buckets_and_top_safe('in_invoice', '-')

        # Facturación y cobranza mensual (12 meses)
        self.env.cr.execute("""
            SELECT to_char(m.date,'YYYY-MM') AS month,
                   COALESCE(SUM(m.amount_total_signed) FILTER (
                       WHERE m.move_type='out_invoice'),0) AS facturado,
                   COALESCE(-SUM(m.amount_total_signed) FILTER (
                       WHERE m.move_type='in_invoice'),0) AS comprado
            FROM account_move m
            WHERE m.state='posted'
              AND m.date >= (CURRENT_DATE - INTERVAL '12 months')
              AND m.move_type IN ('out_invoice','in_invoice')
            GROUP BY 1 ORDER BY 1
        """)
        by_month = [{'key': a, 'facturado': round(b, 2), 'comprado': round(c, 2)}
                    for (a, b, c) in self.env.cr.fetchall()]

        # 9.1 Efectivo: recibos entregados al cliente vs con pago aplicado
        date_from, date_to = self._dates(f)
        dt_from, dt_to = self._bounds(f)
        cash = self._sq("""
            SELECT state, COUNT(*), COALESCE(SUM(amount_mxn), 0)
            FROM cash_receipt
            WHERE date >= %s AND date <= %s AND state != 'cancelled'
            GROUP BY 1
        """, (dt_from, dt_to))
        cash_map = {a: (b, c) for (a, b, c) in cash}
        sin_aplicar = cash_map.get('delivered', (0, 0.0))
        aplicado = cash_map.get('paid', (0, 0.0))

        # Cartera por DIVISA ORIGINAL: residual en la divisa de la factura
        # + su equivalente MXN al TC del día de registro (el residual
        # signed ya quedó congelado a ese TC — no se re-convierte).
        def by_currency(move_type, sign):
            return [
                {'currency': a, 'monto_divisa': round(b or 0, 2),
                 'monto_mxn': round(c or 0, 2), 'facturas': d}
                for (a, b, c, d) in self._sq("""
                    SELECT COALESCE(rc.name, 'MXN'),
                           SUM(m.amount_residual),
                           SUM({s} m.amount_residual_signed),
                           COUNT(*)
                    FROM account_move m
                    LEFT JOIN res_currency rc ON rc.id = m.currency_id
                    WHERE m.state = 'posted' AND m.move_type = %s
                      AND m.amount_residual != 0
                    GROUP BY 1 ORDER BY 3 DESC
                """.format(s=sign), (move_type,), default=[])]

        ar_currency = by_currency('out_invoice', '')
        ap_currency = by_currency('in_invoice', '-')

        # Flujo por vencimiento: cuánto ENTRA (por cobrar) y cuánto SALE
        # (por pagar) según cuándo vence — la película del efectivo.
        due_rows = self._sq("""
            SELECT CASE
                     WHEN COALESCE(m.invoice_date_due, m.date) < CURRENT_DATE
                         THEN 'Ya venció'
                     WHEN COALESCE(m.invoice_date_due, m.date)
                         <= CURRENT_DATE + 7 THEN 'Esta semana'
                     WHEN COALESCE(m.invoice_date_due, m.date)
                         <= CURRENT_DATE + 30 THEN '8-30 días'
                     WHEN COALESCE(m.invoice_date_due, m.date)
                         <= CURRENT_DATE + 60 THEN '31-60 días'
                     WHEN COALESCE(m.invoice_date_due, m.date)
                         <= CURRENT_DATE + 90 THEN '61-90 días'
                     ELSE 'Más de 90 días'
                   END AS bucket,
                   COALESCE(SUM(m.amount_residual_signed) FILTER (
                       WHERE m.move_type = 'out_invoice'), 0) AS entra,
                   COALESCE(SUM(-m.amount_residual_signed) FILTER (
                       WHERE m.move_type = 'in_invoice'), 0) AS sale
            FROM account_move m
            WHERE m.state = 'posted'
              AND m.move_type IN ('out_invoice', 'in_invoice')
              AND m.amount_residual != 0
            GROUP BY 1
        """, default=[])
        due_order = ['Ya venció', 'Esta semana', '8-30 días', '31-60 días',
                     '61-90 días', 'Más de 90 días']
        due_map = {a: (b, c) for (a, b, c) in due_rows}
        due_flow = [
            {'bucket': b, 'entra': round(due_map[b][0] or 0, 2),
             'sale': round(due_map[b][1] or 0, 2)}
            for b in due_order if b in due_map]

        # DSO (días de calle de la cartera) y % vencida
        fact_12m = self._sq("""
            SELECT COALESCE(SUM(amount_total_signed), 0)
            FROM account_move
            WHERE state = 'posted' AND move_type = 'out_invoice'
              AND date >= (CURRENT_DATE - INTERVAL '12 months')
        """, default=[(0,)])[0][0] or 0.0
        ar_vencido = self._sq("""
            SELECT COALESCE(SUM(amount_residual_signed), 0)
            FROM account_move
            WHERE state = 'posted' AND move_type = 'out_invoice'
              AND amount_residual != 0
              AND COALESCE(invoice_date_due, date) < CURRENT_DATE
        """, default=[(0,)])[0][0] or 0.0
        fact_mes = by_month[-1]['facturado'] if by_month else 0.0
        fact_prev = by_month[-2]['facturado'] if len(by_month) > 1 else 0.0

        return {
            'kpis': {
                'por_cobrar': tot['por_cobrar'],
                'por_pagar': tot['por_pagar'],
                'neto': round(tot['por_cobrar'] - tot['por_pagar'], 2),
                'clientes_deudores': len(ar_top),
                'dso_dias': round(
                    tot['por_cobrar'] / fact_12m * 365, 1) if fact_12m else 0.0,
                'vencido_mxn': round(ar_vencido, 2),
                'vencido_pct': round(
                    ar_vencido / tot['por_cobrar'] * 100, 1
                ) if tot['por_cobrar'] else 0.0,
                'facturado_mes': round(fact_mes, 2),
                'facturado_mes_prev': round(fact_prev, 2),
                'facturado_mom_pct': round(
                    (fact_mes - fact_prev) / fact_prev * 100, 1
                ) if fact_prev else 0.0,
                'efectivo_sin_aplicar': round(sin_aplicar[1], 2),
                'recibos_sin_aplicar': sin_aplicar[0],
                'efectivo_aplicado': round(aplicado[1], 2),
                'comprobantes_pendientes': (lambda r: r[0])(self._sq("""
                    SELECT COUNT(*) FROM sale_payment_proof
                    WHERE state = 'pending'
                """, default=[(0,)])[0]),
                'comprobantes_monto': (lambda r: round(r[0] or 0, 2))(
                    self._sq("""
                    SELECT COALESCE(SUM(amount),0) FROM sale_payment_proof
                    WHERE state = 'pending'
                """, default=[(0,)])[0]),
            },
            'pago_post_entrega': [
                {'name': a, 'dias': round(float(b or 0), 1), 'entregas': c}
                for (a, b, c) in self._sq("""
                SELECT p.name,
                       AVG(EXTRACT(EPOCH FROM (pr.paid_at - d.signed_at))
                           / 86400.0) AS dias,
                       COUNT(*) AS entregas
                FROM sale_delivery_document d
                JOIN sale_order so ON so.id = d.sale_order_id
                JOIN res_partner p ON p.id = so.partner_id
                JOIN account_move m ON m.invoice_origin = so.name
                    AND m.move_type = 'out_invoice' AND m.state = 'posted'
                    AND m.payment_state IN ('paid','in_payment')
                JOIN LATERAL (
                    SELECT MAX(apr.create_date) AS paid_at
                    FROM account_partial_reconcile apr
                    JOIN account_move_line aml
                         ON aml.id = apr.debit_move_id
                    WHERE aml.move_id = m.id
                ) pr ON pr.paid_at IS NOT NULL
                WHERE d.document_type = 'remission'
                  AND d.signed_at IS NOT NULL
                  AND pr.paid_at > d.signed_at
                GROUP BY p.name
                ORDER BY dias DESC LIMIT 10
            """, default=[])],
            'cash_month': self._cash_flow_months(),
            'ar_buckets': ar_buckets,
            'ap_buckets': ap_buckets,
            'ar_top': ar_top,
            'ap_top': ap_top,
            'by_month': by_month,
            'ar_currency': ar_currency,
            'ap_currency': ap_currency,
            'due_flow': due_flow,
        }

    def _cash_flow_months(self):
        """9.3 Caja manual: entradas (cash.entry) vs salidas
        (cash.disbursement) por mes, en MXN."""
        ent = dict(self._sq("""
            SELECT to_char(date,'YYYY-MM'), SUM(COALESCE(amount,0))
            FROM cash_entry WHERE state='done'
              AND date >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY 1
        """))
        sal = dict(self._sq("""
            SELECT to_char(date,'YYYY-MM'), SUM(COALESCE(amount_mxn,0))
            FROM cash_disbursement WHERE state NOT IN ('cancelled','cancel')
              AND date >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY 1
        """))
        months = sorted(set(ent) | set(sal))
        return [{'key': m, 'entradas': round(ent.get(m, 0) or 0, 2),
                 'salidas': round(sal.get(m, 0) or 0, 2)} for m in months]

    # ── PRONÓSTICOS ────────────────────────────────────────────────────
    def _dom_pronosticos(self, f):
        """Proyecciones simples y honestas sobre la historia disponible:
        tendencia lineal de venta (12m) a 3 meses con banda de ±1 desv.
        de los residuos, flujo de caja a 90 días por vencimientos y
        cobertura de inventario por material. Mejoran solas conforme se
        acumule más historia."""
        rate = self._current_usd_rate()

        hist = self._sq("""
            SELECT to_char(so.date_order, 'YYYY-MM') AS m,
                   SUM(CASE WHEN rc.name = 'USD'
                       THEN so.amount_total
                            * COALESCE(NULLIF(so.x_delivery_exchange_rate, 0),
                                       %s)
                       ELSE so.amount_total END) AS venta
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state = 'sale'
              AND so.date_order >= date_trunc(
                  'month', CURRENT_DATE - INTERVAL '11 months')
            GROUP BY 1 ORDER BY 1
        """, (rate,), default=[])

        ys = [float(v or 0) for (_m, v) in hist]
        n = len(ys)
        forecast = [{'key': m, 'real': round(v or 0, 2)} for (m, v) in hist]
        proy = []
        if n >= 3:
            xm = (n - 1) / 2.0
            ym = sum(ys) / n
            den = sum((i - xm) ** 2 for i in range(n)) or 1.0
            slope = sum((i - xm) * (ys[i] - ym) for i in range(n)) / den
            intercept = ym - slope * xm
            resid = [ys[i] - (intercept + slope * i) for i in range(n)]
            std = (sum(r * r for r in resid) / n) ** 0.5
            last = hist[-1][0]
            y_, m_ = int(last[:4]), int(last[5:7])
            for step in range(1, 4):
                m_ += 1
                if m_ > 12:
                    m_, y_ = 1, y_ + 1
                val = max(intercept + slope * (n - 1 + step), 0.0)
                proy.append({
                    'key': '%04d-%02d' % (y_, m_),
                    'proyectado': round(val, 2),
                    'banda_sup': round(val + std, 2),
                    'banda_inf': round(max(val - std, 0.0), 2),
                })
        tendencia = 0.0
        if n >= 3 and ys[-3:] and sum(ys[:3]):
            prom_ini = sum(ys[:3]) / 3
            prom_fin = sum(ys[-3:]) / 3
            tendencia = round(
                (prom_fin - prom_ini) / prom_ini * 100, 1) if prom_ini else 0.0

        flujo = self._sq("""
            SELECT
              COALESCE(SUM(m.amount_residual_signed) FILTER (
                  WHERE m.move_type = 'out_invoice'
                    AND COALESCE(m.invoice_date_due, m.date)
                        <= CURRENT_DATE + 90), 0),
              COALESCE(SUM(-m.amount_residual_signed) FILTER (
                  WHERE m.move_type = 'in_invoice'
                    AND COALESCE(m.invoice_date_due, m.date)
                        <= CURRENT_DATE + 90), 0)
            FROM account_move m
            WHERE m.state = 'posted'
              AND m.move_type IN ('out_invoice', 'in_invoice')
              AND m.amount_residual != 0
        """, default=[(0, 0)])[0]
        entra90, sale90 = float(flujo[0] or 0), float(flujo[1] or 0)

        # Cobertura por material: a este ritmo de venta, ¿para cuántos
        # meses alcanza el stock? Los que se agotan primero = comprar.
        area = tuple(self._area_uom_ids())
        cobertura = [
            {'key': a, 'name': b, 'm2_stock': round(c or 0, 1),
             'venta_mensual': round((d or 0) / 12.0, 1),
             'meses': round((c or 0) / ((d or 0) / 12.0), 1)
             if d else None}
            for (a, b, c, d) in self._sq("""
                SELECT pt.id,
                       COALESCE(pt.name->>'es_MX', pt.name->>'en_US',''),
                       stock.m2, ventas.m2
                FROM product_template pt
                JOIN LATERAL (
                    SELECT SUM(q.quantity) AS m2
                    FROM stock_quant q
                    JOIN stock_location loc ON loc.id = q.location_id
                         AND loc.usage = 'internal'
                    JOIN product_product pp ON pp.id = q.product_id
                    WHERE pp.product_tmpl_id = pt.id AND q.quantity > 0
                ) stock ON stock.m2 > 0
                JOIN LATERAL (
                    SELECT SUM(ml.quantity) AS m2
                    FROM stock_move_line ml
                    JOIN stock_location src ON src.id = ml.location_id
                         AND src.usage = 'internal'
                    JOIN stock_location dst ON dst.id = ml.location_dest_id
                         AND dst.usage = 'customer'
                    JOIN product_product pp ON pp.id = ml.product_id
                    WHERE pp.product_tmpl_id = pt.id AND ml.state = 'done'
                      AND ml.date >= (CURRENT_DATE - INTERVAL '12 months')
                ) ventas ON ventas.m2 > 0
                WHERE pt.uom_id IN %s
                ORDER BY stock.m2 / (ventas.m2 / 12.0) ASC
                LIMIT 15
            """, (area,), default=[])]

        prox = proy[0]['proyectado'] if proy else 0.0
        return {
            'kpis': {
                'venta_proximo_mes': round(prox, 2),
                'venta_3m': round(sum(p['proyectado'] for p in proy), 2),
                'tendencia_pct': tendencia,
                'entra_90d': round(entra90, 2),
                'sale_90d': round(sale90, 2),
                'flujo_90d': round(entra90 - sale90, 2),
                'meses_historia': n,
            },
            'forecast': forecast,
            'proyeccion': proy,
            'cobertura': cobertura,
        }

    # ==================================================================
    # RPC 2: PROFUNDIZACIÓN (drill real de un elemento)
    # ==================================================================

    @api.model
    def get_drill(self, entity, value, label, filters=None):
        self._check_access()
        f = dict(filters or {})
        if entity == 'aging_date':
            return self._scrub_profit(self._drill_aging_date(str(value)))
        if entity == 'aging_folio':
            return self._scrub_profit(self._drill_aging_folio(str(value)))
        if entity in ('partner_ar', 'partner_ap'):
            return self._scrub_profit(self._drill_partner_fin(
                int(value),
                'out_invoice' if entity == 'partner_ar' else 'in_invoice'))
        # Coerción SEGURA: el dict evalúa TODAS sus entradas al construirse,
        # así que un drill de nivel con valor 'custom' tronaba en el
        # int(value) de las entradas numéricas antes de elegir la entidad.
        def _as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        _gran = f.get('granularity') or 'month'
        _period_fmt = {'day': 'YYYY-MM-DD', 'week': 'IYYY"W"IW',
                       'month': 'YYYY-MM'}.get(_gran, 'YYYY-MM')
        where_map = {
            'month': ("to_char(so.date_order,'" + _period_fmt + "') = %(dv)s",
                      str(value)),
            'product': ('pt.id = %(dv)s', _as_int(value)),
            'seller': ('so.user_id = %(dv)s', _as_int(value)),
            'customer': ('so.partner_id = %(dv)s', _as_int(value)),
            'level': ("COALESCE(sol.x_price_selector,'custom') = %(dv)s",
                      str(value)),
            'category': ('pt.categ_id = %(dv)s', _as_int(value)),
        }
        if entity not in where_map:
            return {'error': 'entidad desconocida'}
        clause, val = where_map[entity]
        # Para el drill de mes, quitar el filtro month para no duplicar.
        if entity == 'month':
            f.pop('month', None)
        rows = self._sale_rows(f, extra_where=clause,
                               extra_params={'dv': val})

        venta = sum(r['venta_mxn'] for r in rows)
        util = sum(r['utilidad_mxn'] for r in rows)
        out = {
            'entity': entity,
            'value': value,
            'label': label,
            'kpis': {
                'venta_mxn': round(venta, 2),
                'utilidad_mxn': round(util, 2),
                'margen_pct': round(util / venta * 100, 1) if venta else 0.0,
                'm2': round(sum(r['qty'] for r in rows if r['is_area']), 1),
                'ordenes': len({r['order_id'] for r in rows}),
            },
            'by_month': self._agg(rows, lambda r: r['period'],
                                  lambda r: r['period'], order='key'),
            'by_seller': self._agg(rows, lambda r: r['user_id'],
                                   lambda r: r['user_name'], order='venta',
                                   limit=10),
            'by_product': self._agg(rows, lambda r: r['tmpl_id'],
                                    lambda r: r['product_name'],
                                    order='utilidad', limit=10),
            'by_customer': self._agg(rows, lambda r: r['partner_id'],
                                     lambda r: r['partner_name'],
                                     order='venta', limit=10),
            'by_category': self._agg(rows, lambda r: r['categ_id'],
                                     lambda r: r['categ_name'],
                                     order='venta', limit=12),
            'levels': sorted(
                self._agg(rows, lambda r: r['level'],
                          lambda r: _LEVELS.get(r['level'], r['level'])),
                key=lambda a: _LEVEL_ORDER.index(a['key'])
                if a['key'] in _LEVEL_ORDER else 99),
            'orders': self._orders_from(rows, limit=60),
        }

        # Contexto extra según la entidad
        if entity == 'product':
            inv = self._inventory_pack({'product_id': value})
            out['stock'] = inv['kpis']
            out['stock_aging'] = inv['aging']
        if entity == 'customer':
            try:
                self.env.cr.execute("""
                    SELECT COALESCE(SUM(amount_residual_signed),0)
                    FROM account_move
                    WHERE state='posted' AND move_type='out_invoice'
                      AND amount_residual != 0 AND partner_id = %s
                """, (int(value),))
                out['por_cobrar'] = round(
                    self.env.cr.fetchone()[0] or 0.0, 2)
            except Exception:
                self.env.cr.execute('SELECT 1')
                out['por_cobrar'] = 0.0
        return self._scrub_profit(out)

    _AGE_CASE = """CASE
        WHEN NOW() - sl.create_date <= INTERVAL '30 days' THEN '0-30 días'
        WHEN NOW() - sl.create_date <= INTERVAL '90 days' THEN '31-90 días'
        WHEN NOW() - sl.create_date <= INTERVAL '180 days' THEN '91-180 días'
        WHEN NOW() - sl.create_date <= INTERVAL '365 days' THEN '181-365 días'
        WHEN NOW() - sl.create_date <= INTERVAL '2 years' THEN '1-2 años'
        WHEN NOW() - sl.create_date <= INTERVAL '5 years' THEN '2-5 años'
        WHEN NOW() - sl.create_date <= INTERVAL '10 years' THEN '5-10 años'
        ELSE 'Más de 10 años'
    END"""
    _AGE_ORDER = ['0-30 días', '31-90 días', '91-180 días', '181-365 días',
                  '1-2 años', '2-5 años', '5-10 años', 'Más de 10 años']

    def _drill_aging_date(self, bucket):
        """Qué hay adentro de un bucket de antigüedad (por fecha de
        creación del lote): materiales, categorías y totales."""
        self.env.cr.execute("""
            SELECT pt.id,
                   COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS pname,
                   COALESCE(NULLIF(regexp_replace(pc.complete_name, '^[^/]+ / ', ''), ''),
                            pc.complete_name, pc.name, 'Sin categoría')
                       AS cname,
                   SUM(q.quantity) AS m2,
                   COUNT(DISTINCT sl.id) AS lots,
                   SUM(q.quantity
                       * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0))
                       AS valor
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_category pc ON pc.id = pt.categ_id
            WHERE q.quantity > 0 AND {age} = %(bucket)s
            GROUP BY pt.id, pname, cname
        """.format(age=self._AGE_CASE),
            {'cid': self._cid(), 'bucket': bucket})
        rows = self.env.cr.dictfetchall()
        return self._aging_drill_pack(bucket, rows)

    def _drill_aging_folio(self, bucket):
        """Qué hay adentro de un bucket Stone Profit (por folio del lote):
        los cortes numéricos se recalculan igual que en el pack."""
        self.env.cr.execute("""
            SELECT pt.id,
                   COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS pname,
                   COALESCE(NULLIF(regexp_replace(pc.complete_name, '^[^/]+ / ', ''), ''),
                            pc.complete_name, pc.name, 'Sin categoría')
                       AS cname,
                   COALESCE(sl.name, '') AS lot_name,
                   SUM(q.quantity) AS m2,
                   SUM(q.quantity
                       * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0))
                       AS valor
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_category pc ON pc.id = pt.categ_id
            WHERE q.quantity > 0
            GROUP BY pt.id, pname, cname, lot_name
        """, {'cid': self._cid()})
        raw = self.env.cr.dictfetchall()
        keys = [self._lot_bucket_key(r['lot_name']) for r in raw]
        numeric = sorted({k[1] for k in keys if k[0] == 0 and k[1] >= 0})
        cut_old = numeric[len(numeric) // 3] if numeric else 0
        cut_mid = numeric[2 * len(numeric) // 3] if numeric else 0

        def bname(k):
            if k[0] == 1:
                return 'Serie S (recientes)'
            if k[1] < 0:
                return 'Sin folio'
            if k[1] <= cut_old:
                return 'Antiguo (liquidar)'
            if k[1] <= cut_mid:
                return 'Medio'
            return 'Reciente'

        rows = []
        for r, k in zip(raw, keys):
            if bname(k) == bucket:
                r['lots'] = 1
                rows.append(r)
        return self._aging_drill_pack(bucket, rows)

    def _aging_drill_pack(self, bucket, rows):
        mats = defaultdict(lambda: {'m2': 0.0, 'lots': 0, 'valor': 0.0})
        cats = defaultdict(lambda: {'m2': 0.0, 'valor': 0.0})
        for r in rows:
            m = mats[(r['id'], r['pname'])]
            m['m2'] += r['m2'] or 0
            m['lots'] += r['lots'] or 0
            m['valor'] += r['valor'] or 0
            c = cats[r['cname']]
            c['m2'] += r['m2'] or 0
            c['valor'] += r['valor'] or 0
        return {
            'bucket': bucket,
            'kpis': {
                'm2': round(sum(v['m2'] for v in mats.values()), 1),
                'lots': sum(v['lots'] for v in mats.values()),
                'valor': round(sum(v['valor'] for v in mats.values()), 2),
                'materiales': len(mats),
            },
            'materials': sorted(
                ({'key': k[0], 'name': k[1], 'm2': round(v['m2'], 1),
                  'lots': v['lots'], 'valor': round(v['valor'], 2)}
                 for k, v in mats.items()), key=lambda x: -x['m2'])[:40],
            'categories': sorted(
                ({'name': k, 'm2': round(v['m2'], 1),
                  'valor': round(v['valor'], 2)}
                 for k, v in cats.items()), key=lambda x: -x['m2'])[:12],
        }

    def _drill_partner_fin(self, partner_id, move_type):
        """Cartera viva de un cliente/proveedor: factura por factura, con
        edad, divisa original y MXN al TC del día de registro."""
        sign = '' if move_type == 'out_invoice' else '-'
        self.env.cr.execute("""
            SELECT m.name, m.invoice_date::text,
                   COALESCE(m.invoice_date_due, m.date)::text,
                   GREATEST((CURRENT_DATE
                       - COALESCE(m.invoice_date_due, m.date)), 0) AS atraso,
                   {s} m.amount_residual_signed AS residual_mxn,
                   m.amount_residual AS residual_divisa,
                   COALESCE(rc.name, 'MXN') AS currency,
                   {s} m.amount_total_signed AS total_mxn
            FROM account_move m
            LEFT JOIN res_currency rc ON rc.id = m.currency_id
            WHERE m.state = 'posted' AND m.move_type = %s
              AND m.amount_residual != 0 AND m.partner_id = %s
            ORDER BY COALESCE(m.invoice_date_due, m.date)
        """.format(s=sign), (move_type, partner_id))
        invs = [{'name': a, 'date': b, 'due': c, 'atraso': int(d or 0),
                 'residual_mxn': round(e or 0, 2),
                 'residual_divisa': round(f_ or 0, 2), 'currency': g,
                 'total_mxn': round(h or 0, 2)}
                for (a, b, c, d, e, f_, g, h) in self.env.cr.fetchall()]
        total = sum(i['residual_mxn'] for i in invs)
        vencido = sum(i['residual_mxn'] for i in invs if i['atraso'] > 0)
        pago_prom = self._sq("""
            SELECT AVG(pr.paid_at::date - m.invoice_date)
            FROM account_move m
            JOIN LATERAL (
                SELECT MAX(apr.create_date) AS paid_at
                FROM account_partial_reconcile apr
                JOIN account_move_line aml ON aml.id = {col}
                WHERE aml.move_id = m.id
            ) pr ON pr.paid_at IS NOT NULL
            WHERE m.state = 'posted' AND m.move_type = %s
              AND m.partner_id = %s
              AND m.payment_state IN ('paid', 'in_payment')
              AND m.invoice_date >= (CURRENT_DATE - INTERVAL '12 months')
        """.format(col='apr.debit_move_id'
                   if move_type == 'out_invoice' else 'apr.credit_move_id'),
            (move_type, partner_id), default=[(None,)])[0][0]
        return {
            'fin_side': 'ar' if move_type == 'out_invoice' else 'ap',
            'kpis': {
                'total_mxn': round(total, 2),
                'vencido_mxn': round(vencido, 2),
                'vencido_pct': round(
                    vencido / total * 100, 1) if total else 0.0,
                'facturas': len(invs),
                'pago_prom_dias': round(float(pago_prom or 0), 1),
            },
            'invoices': invs[:60],
        }

    # ==================================================================
    # ENDPOINTS DEL DASHBOARD EJECUTIVO STANDALONE (/som/analytics)
    # ==================================================================

    @api.model
    def get_exec_summary(self):
        """Ticker ejecutivo: los números vitales del negocio en una sola
        llamada ligera. Se refresca solo en el dashboard."""
        self._check_access()
        cid = self._cid()
        today = fields.Date.to_string(fields.Date.context_today(self))
        month = today[:7]
        rate = self._current_usd_rate()

        def one(sql, params=None, default=0.0):
            row = self._sq(sql, params, default=[(default,)])[0]
            return row[0] or default

        venta_hoy = one("""
            SELECT COALESCE(SUM(CASE WHEN rc.name='USD'
                THEN so.amount_total * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                ELSE so.amount_total END), 0)
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state='sale' AND so.date_order::date = %(today)s
        """, {'rate': rate, 'today': today})
        venta_mes = one("""
            SELECT COALESCE(SUM(CASE WHEN rc.name='USD'
                THEN so.amount_total * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                ELSE so.amount_total END), 0)
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state='sale' AND to_char(so.date_order,'YYYY-MM') = %(m)s
        """, {'rate': rate, 'm': month})
        row = self._sq("""
            SELECT COALESCE(SUM(sol.product_uom_qty) FILTER (
                       WHERE sol.product_uom_id IN %(uoms)s), 0),
                   COALESCE(SUM(
                       (CASE WHEN rc.name='USD'
                        THEN sol.price_subtotal * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                        ELSE sol.price_subtotal END)
                       - sol.product_uom_qty
                         * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0)), 0)
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id AND so.state='sale'
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE sol.display_type IS NULL
              AND to_char(so.date_order,'YYYY-MM') = %(m)s
        """, {'uoms': tuple(self._area_uom_ids()), 'rate': rate,
              'cid': cid, 'm': month}, default=[(0, 0)])[0]
        m2_mes, utilidad_mes = row[0] or 0.0, row[1] or 0.0

        prev_month = ('%04d-%02d' % ((int(month[:4]) - 1, 12)
                      if month[5:7] == '01'
                      else (int(month[:4]), int(month[5:7]) - 1)))
        venta_mes_prev = one("""
            SELECT COALESCE(SUM(CASE WHEN rc.name='USD'
                THEN so.amount_total * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                ELSE so.amount_total END), 0)
            FROM sale_order so
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.state='sale' AND to_char(so.date_order,'YYYY-MM') = %(m)s
        """, {'rate': rate, 'm': prev_month})

        fin = self._finance_totals()
        banks = self.get_bank_balances()

        agua = self._sq("""
            SELECT COALESCE(SUM(l.product_uom_qty), 0),
                   COUNT(DISTINCT NULLIF(v.container_number, ''))
            FROM stock_transit_voyage v
            LEFT JOIN stock_transit_line l ON l.voyage_id = v.id
            WHERE v.custom_status NOT IN ('delivered','cancel')
        """, default=[(0, 0)])[0]

        inv = self._sq("""
            SELECT COALESCE(SUM(q.quantity), 0)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 AND pt.uom_id IN %s
            WHERE q.quantity > 0
        """, (tuple(self._area_uom_ids()),), default=[(0,)])[0][0]

        # Antigüedad de inventario: días desde la CREACIÓN del lote
        # (stock.lot.create_date), promedio ponderado por m² en stock.
        inv_edad = self._sq("""
            SELECT COALESCE(
                SUM(q.quantity * EXTRACT(EPOCH FROM (NOW() - sl.create_date))
                    / 86400.0) / NULLIF(SUM(q.quantity), 0), 0)
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            WHERE q.quantity > 0
        """, default=[(0,)])[0][0]

        holds = self._sq(
            "SELECT COUNT(*) FROM stock_lot_hold_order "
            "WHERE state NOT IN ('cancelled','cancel','done')",
            default=[(0,)])[0][0]
        pend_precio = self._sq(
            "SELECT COUNT(*) FROM price_authorization WHERE state='pending'",
            default=[(0,)])[0][0]

        # ── Medición mensual diaria (2026-08-13) ──────────────────────────
        # Facturación REAL (timbrada = publicada) vs PREVIAS sin timbrar
        # (borradores), en moneda de compañía (amount_total_signed netea
        # notas de crédito). Los pedidos del sistema (venta_mes) se comparan
        # aparte: no garantizan cierre en el mes.
        fact = self._sq("""
            SELECT
                COALESCE(SUM(am.amount_total_signed)
                         FILTER (WHERE am.state = 'posted'), 0),
                COALESCE(SUM(am.amount_total_signed)
                         FILTER (WHERE am.state = 'draft'), 0),
                COUNT(*) FILTER (WHERE am.state = 'draft')
            FROM account_move am
            WHERE am.move_type IN ('out_invoice', 'out_refund')
              AND to_char(COALESCE(am.invoice_date, am.date,
                                   am.create_date::date), 'YYYY-MM') = %(m)s
        """, {'m': month}, default=[(0, 0, 0)])[0]
        fact_real_mes = float(fact[0] or 0.0)
        fact_previa_mes = float(fact[1] or 0.0)
        fact_previa_count = int(fact[2] or 0)

        # Venta de CAJAS nacionales: líneas confirmadas del mes vendidas por
        # empaque estándar (standard_pack_som) — no. de cajas y monto MXN.
        cajas_mes = venta_cajas_mes = 0.0
        if 'pack_qty' in self.env['sale.order.line']._fields:
            row = self._sq("""
                SELECT COALESCE(SUM(sol.pack_qty), 0),
                       COALESCE(SUM(CASE WHEN rc.name='USD'
                           THEN sol.price_subtotal
                                * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                           ELSE sol.price_subtotal END), 0)
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id AND so.state='sale'
                LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
                LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
                WHERE sol.display_type IS NULL
                  AND sol.standard_pack_id IS NOT NULL
                  AND to_char(so.date_order,'YYYY-MM') = %(m)s
            """, {'rate': rate, 'm': month}, default=[(0, 0)])[0]
            cajas_mes = float(row[0] or 0.0)
            venta_cajas_mes = float(row[1] or 0.0)

        return self._scrub_profit({
            'fact_real_mes': round(fact_real_mes, 2),
            'fact_previa_mes': round(fact_previa_mes, 2),
            'fact_previa_count': fact_previa_count,
            'cajas_mes': round(cajas_mes, 1),
            'venta_cajas_mes': round(venta_cajas_mes, 2),
            'venta_hoy': round(venta_hoy, 2),
            'venta_mes': round(venta_mes, 2),
            'venta_mes_prev': round(venta_mes_prev, 2),
            'venta_mom_pct': round(
                (venta_mes - venta_mes_prev) / venta_mes_prev * 100, 1
            ) if venta_mes_prev else 0.0,
            'utilidad_mes': round(utilidad_mes, 2),
            'margen_mes': round(
                utilidad_mes / venta_mes * 100, 1) if venta_mes else 0.0,
            'm2_mes': round(m2_mes, 1),
            'bancos_mxn': banks['total'],
            'por_cobrar': fin['por_cobrar'],
            'por_pagar': fin['por_pagar'],
            'contenedores_agua': agua[1] or 0,
            'm2_agua': round(agua[0] or 0.0, 1),
            'inv_m2': round(inv or 0.0, 1),
            'inv_edad_dias': round(float(inv_edad or 0.0), 1),
            'holds_activos': holds,
            'auth_pendientes': pend_precio,
            'tc_banorte': rate,
            'ts': fields.Datetime.now().isoformat(),
        })

    @api.model
    def get_bank_balances(self):
        """Dinero en bancos y cajas: balance contable por diario.

        Suma la cuenta default del diario Y las cuentas puente de pago
        (outstanding receipts/payments de sus métodos): los pagos
        registrados viven ahí hasta conciliarse con el estado de cuenta —
        contarlas es lo que hace que el saldo refleje los pagos ya
        registrados y no marque 0."""
        self._check_access()
        rows = self._sq("""
            SELECT j.id,
                   COALESCE(j.name->>'es_MX', j.name->>'en_US', '') AS name,
                   j.type,
                   COALESCE((
                       SELECT SUM(aml.balance)
                       FROM account_move_line aml
                       WHERE aml.parent_state = 'posted'
                         AND (aml.account_id = j.default_account_id
                              OR aml.account_id IN (
                                  SELECT apml.payment_account_id
                                  FROM account_payment_method_line apml
                                  WHERE apml.journal_id = j.id
                                    AND apml.payment_account_id IS NOT NULL))
                   ), 0) AS balance
            FROM account_journal j
            WHERE j.type IN ('bank', 'cash') AND j.active
            ORDER BY balance DESC
        """)
        out = [{'id': a, 'name': b, 'type': c, 'balance': round(d or 0, 2)}
               for (a, b, c, d) in rows]
        return {
            'journals': out,
            'total': round(sum(x['balance'] for x in out), 2),
        }

    @api.model
    def get_order_lines(self, order_id):
        """Drill venta → POR QUÉ: cada línea con su utilidad all-in."""
        self._check_access()
        rows = self._sq("""
            SELECT
                COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS product,
                COALESCE(NULLIF(regexp_replace(pc.complete_name, '^[^/]+ / ', ''), ''),
                         pc.complete_name, '') AS categ,
                COALESCE(sol.product_uom_qty, 0) AS qty,
                (sol.product_uom_id IN %(uoms)s) AS is_area,
                COALESCE(sol.x_price_selector, 'custom') AS level,
                COALESCE(sol.price_unit, 0) AS price_unit,
                CASE WHEN rc.name='USD'
                     THEN sol.price_subtotal
                          * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                     ELSE sol.price_subtotal END AS venta_mxn,
                COALESCE(sol.product_uom_qty, 0)
                    * COALESCE((pt.x_costo_mayor->>%(cid)s)::float, 0)
                    AS costo_mxn,
                pt.id AS tmpl_id
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_category pc ON pc.id = pt.categ_id
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE sol.order_id = %(oid)s AND sol.display_type IS NULL
            ORDER BY venta_mxn DESC
        """, {'oid': int(order_id), 'uoms': tuple(self._area_uom_ids()),
              'rate': self._current_usd_rate(), 'cid': self._cid()})
        lines = []
        for r in rows:
            venta, costo = r[6] or 0.0, r[7] or 0.0
            lines.append({
                'product': r[0], 'categ': r[1],
                'qty': round(r[2] or 0, 2), 'is_area': bool(r[3]),
                'level': _LEVELS.get(r[4], r[4]),
                'price_unit': round(r[5] or 0, 2),
                'venta': round(venta, 2), 'costo': round(costo, 2),
                'utilidad': round(venta - costo, 2),
                'margen': round(
                    (venta - costo) / venta * 100, 1) if venta else 0.0,
                'tmpl_id': r[8],
            })
        head = self._sq("""
            SELECT so.name, COALESCE(p.name,''), so.date_order::date::text,
                   COALESCE(sp.name,''), COALESCE(rc.name,'MXN'),
                   so.amount_total
            FROM sale_order so
            LEFT JOIN res_partner p ON p.id = so.partner_id
            LEFT JOIN res_users ru ON ru.id = so.user_id
            LEFT JOIN res_partner sp ON sp.id = ru.partner_id
            LEFT JOIN product_pricelist ppl ON ppl.id = so.pricelist_id
            LEFT JOIN res_currency rc ON rc.id = ppl.currency_id
            WHERE so.id = %s
        """, (int(order_id),), default=[('', '', '', '', 'MXN', 0)])[0]
        return self._scrub_profit({
            'order': {'id': int(order_id), 'name': head[0],
                      'partner': head[1], 'date': head[2],
                      'seller': head[3], 'currency': head[4],
                      'amount_total': round(head[5] or 0, 2)},
            'lines': lines,
        })

    @api.model
    def get_time_to_sell(self, filters=None):
        """Tiempo de venta por material: días promedio del lote en patio
        antes de salir a cliente (12 meses) + edad y m² del stock actual.
        Los materiales más lentos primero — capital estancado."""
        self._check_access()
        # Filtro de UdM de área TOLERANTE: si no se identifica ninguna UdM
        # m², no se filtra (mejor mostrar todo que mostrar cero). Y las
        # consultas corren SIN _sq: si algo falla, el error llega al
        # dashboard con su mensaje real en vez de degradar a ceros mudos.
        uoms = tuple(self._area_uom_ids())
        uom_filter = 'AND pt.uom_id IN %(uoms)s' if uoms != (0,) else ''
        params = {'uoms': uoms}
        self.env.cr.execute("""
            SELECT pt.id,
                   COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS name,
                   AVG(GREATEST(EXTRACT(EPOCH FROM (ml.date - sl.create_date))
                       / 86400.0, 0)) AS dias,
                   SUM(ml.quantity) AS m2,
                   COUNT(DISTINCT sl.id) AS lots
            FROM stock_move_line ml
            JOIN stock_location src ON src.id = ml.location_id
                 AND src.usage = 'internal'
            JOIN stock_location dst ON dst.id = ml.location_dest_id
                 AND dst.usage = 'customer'
            JOIN stock_lot sl ON sl.id = ml.lot_id
            JOIN product_product pp ON pp.id = ml.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 {uom_filter}
            WHERE ml.state = 'done'
              AND ml.date >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY pt.id, pt.name
        """.format(uom_filter=uom_filter), params)
        sold = {r[0]: r for r in self.env.cr.fetchall()}
        self.env.cr.execute("""
            SELECT pt.id,
                   COALESCE(pt.name->>'es_MX', pt.name->>'en_US','') AS name,
                   AVG(EXTRACT(EPOCH FROM (NOW() - sl.create_date))
                       / 86400.0) AS edad,
                   SUM(q.quantity) AS m2,
                   COUNT(DISTINCT sl.id) AS lots
            FROM stock_quant q
            JOIN stock_location loc ON loc.id = q.location_id
                 AND loc.usage = 'internal'
            JOIN stock_lot sl ON sl.id = q.lot_id
            JOIN product_product pp ON pp.id = q.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 {uom_filter}
            WHERE q.quantity > 0
            GROUP BY pt.id, pt.name
        """.format(uom_filter=uom_filter), params)
        stock = {r[0]: r for r in self.env.cr.fetchall()}
        out = []
        for tid in set(sold) | set(stock):
            sr, qr = sold.get(tid), stock.get(tid)
            out.append({
                'tmpl_id': tid,
                'name': (sr or qr)[1],
                'dias_venta': round(float(sr[2] or 0), 1) if sr else None,
                'm2_vendidos': round(float(sr[3] or 0), 1) if sr else 0.0,
                'lots_vendidos': sr[4] if sr else 0,
                'edad_stock': round(float(qr[2] or 0), 1) if qr else None,
                'm2_stock': round(float(qr[3] or 0), 1) if qr else 0.0,
                'lots_stock': qr[4] if qr else 0,
            })
        out.sort(key=lambda x: -(x['edad_stock'] or x['dias_venta'] or 0))
        return self._scrub_profit(out[:200])

class ProductTemplateCostCurrency(models.Model):
    _inherit = 'product.template'

    x_costo_divisa = fields.Selection([
        ('MXN', 'MXN'),
        ('USD', 'USD'),
        ('EUR', 'EUR'),
    ], string='Divisa del costo all-in', default='USD',
        help='Divisa en la que se captura/visualiza el costo all-in en el '
             'dashboard. El costo siempre se GUARDA en MXN (EUR convierte '
             'primero a USD y luego a MXN con TC Banorte).')
