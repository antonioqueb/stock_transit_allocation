# -*- coding: utf-8 -*-
"""SOM Restock — radar de recompra y asesor de compra.

Correlaciona, por material (productos con tracking por lote):

  · Stock físico interno: total, apartado (hold) y libre real.
  · Tránsito: lo que viene en camino, separado en libre vs comprometido
    (stock.transit.line con allocation_status / partner), con la ETA más
    próxima del material libre.
  · Consumo: salidas reales a cliente (devoluciones descontadas) de los
    últimos meses → ritmo mensual.
  · Lead time: MEDIDO de la historia real (confirmación de OC → recepción
    física validada) por proveedor; con fallback a la ficha del proveedor
    (product.supplierinfo.delay) y a un default conservador.

Con eso calcula cobertura en meses, alerta de "ya toca pedir" (si el
material se acaba antes de lo que tarda en llegar) y cantidad sugerida de
pedido. El asesor de compra responde "voy a comprar N de X, ¿qué me
sugieres?" leyendo stock + tránsito, y propone con qué materiales del
mismo proveedor rellenar el pedido (los de cobertura más crítica).

Todo se calcula de la historia en cada carga: mientras más datos, más
afinados salen consumos y lead times.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SomRestock(models.AbstractModel):
    _name = 'som.restock'
    _description = 'SOM Restock — Radar de recompra'

    # Parámetros del cálculo (expuestos al front para transparencia)
    CONSUMPTION_WINDOW_DAYS = 180   # ventana de consumo histórico
    DEFAULT_LEAD_DAYS = 150         # sin historia ni ficha: 5 meses
    SAFETY_MONTHS = 1.0             # colchón sobre el lead time
    CYCLE_MONTHS = 1.5              # cobertura objetivo extra al pedir
    LEAD_MAX_SANE_DAYS = 400        # descarta mediciones absurdas

    # ------------------------------------------------------------------
    # BLOQUES DE DATOS
    # ------------------------------------------------------------------
    # Multiempresa: todo va con sudo() (plomería del radar), así que cada
    # bloque filtra a mano por las compañías SELECCIONADAS en el switcher.

    def _cids(self):
        return list(self.env.companies.ids) or [self.env.company.id]

    def _stock_by_product(self):
        """{product_id: {'on_hand', 'reserved', 'holds'}} de ubicaciones internas."""
        Quant = self.env['stock.quant'].sudo()
        base = [
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('product_id.tracking', '=', 'lot'),
            ('company_id', 'in', self._cids()),
        ]
        out = {}
        for product, qty, reserved in Quant._read_group(
                base, ['product_id'],
                ['quantity:sum', 'reserved_quantity:sum']):
            out[product.id] = {
                'on_hand': qty or 0.0,
                'reserved': reserved or 0.0,
                'holds': 0.0,
            }
        if 'x_tiene_hold' in Quant._fields:
            for product, qty in Quant._read_group(
                    base + [('x_tiene_hold', '=', True)],
                    ['product_id'], ['quantity:sum']):
                if product.id in out:
                    out[product.id]['holds'] = qty or 0.0
        return out

    def _transit_by_product(self):
        """{product_id: {'total', 'free', 'committed', 'next_eta'}} de viajes vivos."""
        Line = self.env['stock.transit.line'].sudo()
        lines = Line.search_read(
            [
                ('product_id', '!=', False),
                ('product_uom_qty', '>', 0),
                ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
                ('company_id', 'in', [False] + self._cids()),
            ],
            ['product_id', 'product_uom_qty', 'allocation_status',
             'partner_id', 'order_id', 'eta'],
        )
        out = {}
        for l in lines:
            pid = l['product_id'][0]
            rec = out.setdefault(pid, {
                'total': 0.0, 'free': 0.0, 'committed': 0.0, 'next_eta': False,
            })
            qty = l['product_uom_qty'] or 0.0
            rec['total'] += qty
            is_free = (
                l['allocation_status'] == 'available'
                and not l['partner_id'] and not l['order_id']
            )
            if is_free:
                rec['free'] += qty
                eta = l.get('eta')
                if eta and (not rec['next_eta'] or eta < rec['next_eta']):
                    rec['next_eta'] = eta
            else:
                rec['committed'] += qty
        return out

    def _consumption_by_product(self):
        """{product_id: consumo mensual neto} sobre la ventana histórica."""
        Ml = self.env['stock.move.line'].sudo()
        start = fields.Datetime.now() - timedelta(days=self.CONSUMPTION_WINDOW_DAYS)
        months = self.CONSUMPTION_WINDOW_DAYS / 30.0
        base = [
            ('state', '=', 'done'),
            ('date', '>=', start),
            ('product_id.tracking', '=', 'lot'),
            ('company_id', 'in', self._cids()),
        ]
        totals = {}
        for product, qty in Ml._read_group(
                base + [('location_dest_id.usage', '=', 'customer')],
                ['product_id'], ['quantity:sum']):
            totals[product.id] = qty or 0.0
        for product, qty in Ml._read_group(
                base + [('location_id.usage', '=', 'customer')],
                ['product_id'], ['quantity:sum']):
            totals[product.id] = totals.get(product.id, 0.0) - (qty or 0.0)
        return {pid: max(0.0, t) / months for pid, t in totals.items()}

    def _measured_lead_by_supplier(self):
        """{partner_id: días promedio} confirmación de OC → recepción validada,
        medido de los viajes ya Entregados. Aprende solo con cada ciclo."""
        voyages = self.env['stock.transit.voyage'].sudo().search([
            ('custom_status', '=', 'delivered'),
            ('reception_picking_id', '!=', False),
            ('company_id', 'in', [False] + self._cids()),
        ])
        samples = {}
        for v in voyages:
            done = v.reception_picking_id.date_done
            po = v.purchase_id or (v.picking_id and v.picking_id.purchase_id)
            if not done or not po:
                continue
            start = po.date_approve or po.date_order
            if not start:
                continue
            days = (done - start).days
            if days <= 0 or days > self.LEAD_MAX_SANE_DAYS:
                continue
            partner = (v.tc_supplier_id or po.partner_id)
            if not partner:
                continue
            samples.setdefault(partner.id, []).append(days)
        return {p: (sum(d) / len(d)) for p, d in samples.items()}

    def _main_supplier_by_product(self, product_ids):
        """{product_id: (partner_id, nombre)} — el proveedor con más volumen
        comprado (24 meses); fallback: primera ficha de proveedor."""
        out = {}
        if not product_ids:
            return out
        Pol = self.env['purchase.order.line'].sudo()
        start = fields.Datetime.now() - timedelta(days=730)
        best = {}
        for product, partner, qty in Pol._read_group(
                [
                    ('product_id', 'in', list(product_ids)),
                    ('order_id.state', 'in', ['purchase', 'done']),
                    ('order_id.date_order', '>=', start),
                    ('partner_id', '!=', False),
                    ('company_id', 'in', self._cids()),
                ],
                ['product_id', 'partner_id'], ['product_qty:sum']):
            cur = best.get(product.id)
            if not cur or (qty or 0.0) > cur[2]:
                best[product.id] = (partner.id, partner.display_name, qty or 0.0)
        for pid, (partner_id, name, _q) in best.items():
            out[pid] = (partner_id, name)

        missing = [p for p in product_ids if p not in out]
        if missing:
            products = self.env['product.product'].sudo().browse(missing)
            for prod in products:
                seller = prod.seller_ids[:1]
                if seller and seller.partner_id:
                    out[prod.id] = (seller.partner_id.id,
                                    seller.partner_id.display_name)
        return out

    def _supplierinfo_delay(self, product):
        seller = product.seller_ids[:1]
        return seller.delay if seller and seller.delay else 0

    # ------------------------------------------------------------------
    # ARMADO DE FILAS
    # ------------------------------------------------------------------

    def _build_rows(self):
        stock = self._stock_by_product()
        transit = self._transit_by_product()
        consumption = self._consumption_by_product()

        product_ids = set(stock) | set(transit) | set(consumption)
        if not product_ids:
            return []

        products = self.env['product.product'].sudo().browse(list(product_ids))
        products = products.exists().filtered(lambda p: p.tracking == 'lot')

        suppliers = self._main_supplier_by_product([p.id for p in products])
        lead_by_supplier = self._measured_lead_by_supplier()

        rows = []
        for prod in products:
            st = stock.get(prod.id, {'on_hand': 0.0, 'reserved': 0.0, 'holds': 0.0})
            tr = transit.get(prod.id,
                             {'total': 0.0, 'free': 0.0, 'committed': 0.0,
                              'next_eta': False})
            monthly = consumption.get(prod.id, 0.0)

            free_stock = max(
                0.0, st['on_hand'] - st['reserved'] - st['holds'])
            available = free_stock + tr['free']

            supplier = suppliers.get(prod.id)
            lead_days = 0.0
            lead_source = 'default'
            if supplier and supplier[0] in lead_by_supplier:
                lead_days = lead_by_supplier[supplier[0]]
                lead_source = 'medido'
            else:
                delay = self._supplierinfo_delay(prod)
                if delay:
                    lead_days = float(delay)
                    lead_source = 'ficha'
                else:
                    lead_days = float(self.DEFAULT_LEAD_DAYS)
            lead_months = lead_days / 30.0

            cover_months = (available / monthly) if monthly > 0 else -1.0
            target = monthly * (lead_months + self.CYCLE_MONTHS + self.SAFETY_MONTHS)
            suggested = max(0.0, target - available) if monthly > 0 else 0.0

            if monthly <= 0:
                status = 'no_data'
            elif cover_months <= lead_months:
                status = 'urgent'      # se acaba ANTES de que llegue un pedido
            elif cover_months <= lead_months + self.SAFETY_MONTHS:
                status = 'soon'        # toca pedir en este ciclo
            else:
                status = 'ok'

            rows.append({
                'product_id': prod.id,
                'name': prod.display_name,
                'code': prod.default_code or '',
                'uom': prod.uom_id.name or '',
                'on_hand': round(st['on_hand'], 2),
                'holds': round(st['holds'], 2),
                'reserved': round(st['reserved'], 2),
                'free_stock': round(free_stock, 2),
                'transit_free': round(tr['free'], 2),
                'transit_committed': round(tr['committed'], 2),
                'transit_total': round(tr['total'], 2),
                'next_eta': tr['next_eta'] and str(tr['next_eta']) or '',
                'available': round(available, 2),
                'monthly': round(monthly, 2),
                'cover_months': round(cover_months, 1) if monthly > 0 else None,
                'lead_days': round(lead_days),
                'lead_months': round(lead_months, 1),
                'lead_source': lead_source,
                'status': status,
                'suggested_qty': round(suggested, 2),
                'supplier_id': supplier and supplier[0] or False,
                'supplier_name': supplier and supplier[1] or '',
            })
        return rows

    # ------------------------------------------------------------------
    # API PÚBLICA (client actions)
    # ------------------------------------------------------------------

    @api.model
    def get_restock_dashboard(self):
        # Los productos SIN consumo medido no aportan al radar (no hay ritmo
        # con qué calcular cobertura); siguen viajando en 'rows' marcados
        # 'no_data' SOLO para que el verificador pueda analizarlos, pero el
        # front no los lista en radar ni en plan.
        rows = self._build_rows()

        order = {'urgent': 0, 'soon': 1, 'ok': 2, 'no_data': 3}
        rows.sort(key=lambda r: (order.get(r['status'], 9),
                                 r['cover_months'] if r['cover_months'] is not None else 999))

        seen, suppliers = set(), []
        for r in rows:
            if r['supplier_id'] and r['supplier_id'] not in seen:
                seen.add(r['supplier_id'])
                suppliers.append({'id': r['supplier_id'], 'name': r['supplier_name']})
        suppliers.sort(key=lambda s: s['name'])

        return {
            'generated_at': fields.Datetime.now().isoformat(),
            'params': {
                'window_days': self.CONSUMPTION_WINDOW_DAYS,
                'default_lead_days': self.DEFAULT_LEAD_DAYS,
                'safety_months': self.SAFETY_MONTHS,
                'cycle_months': self.CYCLE_MONTHS,
            },
            'rows': rows,
            'suppliers': suppliers,
        }

    @api.model
    def get_purchase_advice(self, supplier_id=None, product_id=None, needed_qty=0.0):
        """'Necesito N de X (o compraré a este proveedor): ¿qué me sugieres?'

        Lee stock libre + tránsito libre y responde cuánto realmente falta
        comprar del material pedido, y con qué materiales del MISMO proveedor
        conviene rellenar (los de cobertura más crítica primero).
        """
        rows = {r['product_id']: r for r in self._build_rows()}
        needed_qty = float(needed_qty or 0.0)

        focal = None
        if product_id and int(product_id) in rows:
            r = rows[int(product_id)]
            from_stock = min(needed_qty, r['free_stock'])
            from_transit = min(max(0.0, needed_qty - from_stock), r['transit_free'])
            to_buy = max(0.0, needed_qty - from_stock - from_transit)
            focal = dict(r,
                         needed_qty=round(needed_qty, 2),
                         cover_from_stock=round(from_stock, 2),
                         cover_from_transit=round(from_transit, 2),
                         to_buy=round(to_buy, 2))
            if not supplier_id:
                supplier_id = r['supplier_id']

        fill = []
        if supplier_id:
            fill = [
                r for r in rows.values()
                if r['supplier_id'] == int(supplier_id)
                and (not focal or r['product_id'] != focal['product_id'])
                and r['suggested_qty'] > 0
            ]
            order = {'urgent': 0, 'soon': 1, 'ok': 2, 'no_data': 3}
            fill.sort(key=lambda r: (order.get(r['status'], 9),
                                     r['cover_months'] if r['cover_months'] is not None else 999))
            fill = fill[:15]

        supplier_name = ''
        if supplier_id:
            supplier_name = self.env['res.partner'].sudo().browse(
                int(supplier_id)).display_name or ''

        return {
            'focal': focal,
            'supplier_id': supplier_id and int(supplier_id) or False,
            'supplier_name': supplier_name,
            'fill': fill,
            'fill_total': round(sum(f['suggested_qty'] for f in fill), 2),
        }
