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
from datetime import timedelta

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
        if not self.env.user.has_group(
                'inventory_shopping_cart.group_price_authorizer'):
            raise AccessError(_(
                'SOM Analytics muestra utilidad con costo all-in: requiere el '
                'permiso de Autorizador de Precios.'))

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
        params = {
            'date_from': date_from,
            'date_to': date_to + ' 23:59:59',
            'rate': self._current_usd_rate(),
            'area_uoms': tuple(self._area_uom_ids()),
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
        if f.get('level'):
            where.append("COALESCE(sol.x_price_selector,'custom') = %(level)s")
            params['level'] = f['level']
        if f.get('month'):
            where.append("to_char(so.date_order,'YYYY-MM') = %(month)s")
            params['month'] = f['month']
        if f.get('currency'):
            where.append('rc.name = %(currency)s')
            params['currency'] = f['currency']
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
                COALESCE(sol.x_price_selector, 'custom') AS level,
                COALESCE(sol.product_uom_qty, 0) AS qty,
                (sol.product_uom_id IN %(area_uoms)s) AS is_area,
                CASE WHEN COALESCE(rc.name,'MXN') = 'USD'
                     THEN COALESCE(sol.price_subtotal,0)
                          * COALESCE(NULLIF(so.x_delivery_exchange_rate,0), %(rate)s)
                     ELSE COALESCE(sol.price_subtotal,0)
                END AS venta_mxn,
                COALESCE(sol.product_uom_qty,0) * COALESCE(pt.x_costo_mayor,0)
                    AS costo_mxn
            FROM sale_order_line sol
            JOIN sale_order so        ON so.id = sol.order_id
            JOIN product_product pp   ON pp.id = sol.product_id
            JOIN product_template pt  ON pt.id = pp.product_tmpl_id
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
        for r in rows:
            r['utilidad_mxn'] = r['venta_mxn'] - r['costo_mxn']
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
                rows, lambda r: r['month'], lambda r: r['month'], order='key'),
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
            'orders': self._orders_from(rows),
        }

    # ==================================================================
    # RPC 1: tablero por dominio
    # ==================================================================

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
            'entregas': self._dom_entregas,
            'financiero': self._dom_financiero,
        }.get(domain, self._dom_resumen)
        try:
            return fn(f)
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
        self.env.cr.execute("""
            SELECT state, COUNT(*) FROM sale_order
            WHERE date_order >= %s AND date_order <= %s
              AND COALESCE(x_is_quote_backup, false) = false
            GROUP BY state
        """, (date_from, date_to + ' 23:59:59'))
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
        """, (date_from, date_to + ' 23:59:59'))
        desc, desc_auth = self.env.cr.fetchone()
        pack['kpis']['descuento_mxn'] = round(desc or 0.0, 2)
        pack['kpis']['descuentos_con_auth'] = desc_auth or 0
        return pack

    # ── INVENTARIO ─────────────────────────────────────────────────────
    def _inventory_pack(self, f):
        params = {'area_uoms': tuple(self._area_uom_ids())}
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
                SUM(q.quantity * COALESCE(pt.x_costo_mayor,0)) AS valor
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

        return {
            'kpis': {
                'disponible_m2': round(disp, 1),
                'hold_m2': round(holdm, 1),
                'valor_mxn': round(valor, 2),
                'lotes': len(rows),
            },
            'aging': [
                {'bucket': b, 'm2': round(ag[b]['m2'], 1),
                 'lots': ag[b]['lots'], 'valor': round(ag[b]['valor'], 2)}
                for b in order_b if b in ag],
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
        try:
            self.env.cr.execute(
                "SELECT COUNT(*) FROM stock_lot_hold_order WHERE state NOT IN "
                "('cancelled','cancel','done')")
            pack['kpis']['holds_activos'] = self.env.cr.fetchone()[0]
        except Exception:
            self.env.cr.execute('SELECT 1')
            pack['kpis']['holds_activos'] = 0
        return pack

    # ── COMPRAS ────────────────────────────────────────────────────────
    def _dom_compras(self, f):
        date_from, date_to = self._dates(f)
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
        """, (date_from, date_to + ' 23:59:59'))
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

        return {
            'kpis': {
                'compras_mxn': round(total_norm, 2),
                'ordenes': len({(r['partner_id'], r['month']) for r in rows}),
                'proveedores': len(provs),
                'tc_usado': rate,
            },
            'by_month': by_month,
            'top_suppliers': top_prov,
            'allocations': alloc,
        }

    # ── TRÁNSITO ───────────────────────────────────────────────────────
    def _transit_pack(self, f):
        Voyage = self.env['stock.transit.voyage'].sudo()
        voyages = Voyage.search_read(
            [('custom_status', 'not in', ('delivered', 'cancel'))],
            ['name', 'custom_status', 'total_m2', 'allocated_m2',
             'allocation_percent', 'eta', 'container_number',
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
            })
        order_st = ['solicitud', 'production', 'booking', 'puerto_origen',
                    'on_sea', 'puerto_destino', 'arrived_port',
                    'reception_pending']
        return {
            'kpis': {
                'total_m2': round(total, 1),
                'embarques': len(voyages),
                'pendientes_publicar': pending_pub,
                'prevendido_pct': round(
                    presold_num / presold_den * 100, 1) if presold_den else 0.0,
            },
            'by_status': [
                {'status': st, 'label': labels.get(st, st),
                 'm2': round(acc[st]['m2'], 1), 'count': acc[st]['count']}
                for st in order_st if st in acc],
            'voyages': sorted(cards, key=lambda c: c['eta'] or '9999')[:30],
        }

    def _dom_transito(self, f):
        return self._transit_pack(f)

    # ── ENTREGAS ───────────────────────────────────────────────────────
    def _dom_entregas(self, f):
        if 'sale.delivery.document' not in self.env:
            return {'kpis': {}, 'unavailable': True}
        date_from, date_to = self._dates(f)

        self.env.cr.execute("""
            SELECT COALESCE(delivery_status,'preparacion'), COUNT(*)
            FROM sale_delivery_document
            WHERE document_type = 'remission'
              AND create_date >= %s AND create_date <= %s
            GROUP BY 1
        """, (date_from, date_to + ' 23:59:59'))
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
        """, (date_from, date_to + ' 23:59:59'))
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

        return {
            'kpis': {
                'firmadas_app': app_signed or 0,
                'manuales': manual or 0,
                'en_ruta': en_ruta or 0,
                'credito_informal_mxn': round(auth_total, 2),
            },
            'by_status': by_status,
            'auth_sin_pago': auth_rows,
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

        return {
            'kpis': {
                'por_cobrar': tot['por_cobrar'],
                'por_pagar': tot['por_pagar'],
                'neto': round(tot['por_cobrar'] - tot['por_pagar'], 2),
                'clientes_deudores': len(ar_top),
            },
            'ar_buckets': ar_buckets,
            'ap_buckets': ap_buckets,
            'ar_top': ar_top,
            'ap_top': ap_top,
            'by_month': by_month,
        }

    # ==================================================================
    # RPC 2: PROFUNDIZACIÓN (drill real de un elemento)
    # ==================================================================

    @api.model
    def get_drill(self, entity, value, label, filters=None):
        self._check_access()
        f = dict(filters or {})
        where_map = {
            'month': ("to_char(so.date_order,'YYYY-MM') = %(dv)s", str(value)),
            'product': ('pt.id = %(dv)s', int(value)),
            'seller': ('so.user_id = %(dv)s', int(value)),
            'customer': ('so.partner_id = %(dv)s', int(value)),
            'level': ("COALESCE(sol.x_price_selector,'custom') = %(dv)s",
                      str(value)),
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
            'by_month': self._agg(rows, lambda r: r['month'],
                                  lambda r: r['month'], order='key'),
            'by_seller': self._agg(rows, lambda r: r['user_id'],
                                   lambda r: r['user_name'], order='venta',
                                   limit=10),
            'by_product': self._agg(rows, lambda r: r['tmpl_id'],
                                    lambda r: r['product_name'],
                                    order='utilidad', limit=10),
            'by_customer': self._agg(rows, lambda r: r['partner_id'],
                                     lambda r: r['partner_name'],
                                     order='venta', limit=10),
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
        return out
