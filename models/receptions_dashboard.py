# -*- coding: utf-8 -*-
"""Servicio de datos del tablero de Recepciones.

La UI es una client action OWL (receptions_dashboard.js). Aquí solo se
agrega y estructura el pipeline operativo del almacén: En puerto, Listos
para recibir y Recepcionados. Todo con sudo(): el tablero es para el
personal de almacén, que NO necesita el grupo de Torre de Control (ver
regla grupo-tránsito-solo-UI).

Reglas del tablero:
- "En camino" no se muestra: al almacén solo le importa lo operable.
- Recepcionados = SOLO recepciones físicas validadas (picking done), con
  ventana de 7 días — es registro administrativo, no historial.
- En puerto y Listos para recibir no caducan jamás: ahí viven hasta que
  alguien los procese.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Fases del tablero
_PORT = ('puerto_destino', 'arrived_port')
_READY = ('reception_pending',)

# Días de gracia tras ETA/publicación antes de marcar en ROJO
_LATE_AFTER_DAYS = 5

# Ventana de visibilidad de "Recepcionados"
_DONE_WINDOW_DAYS = 7


class StockTransitVoyageReceptionsDash(models.Model):
    _inherit = 'stock.transit.voyage'

    @api.model
    def get_receptions_dashboard_data(self):
        Voyage = self.env['stock.transit.voyage'].sudo()
        today = fields.Date.context_today(self)

        active = Voyage.search([
            ('custom_status', 'not in', ('delivered', 'cancel')),
        ], order='eta asc, id desc')

        status_labels = dict(
            Voyage._fields['custom_status']._description_selection(self.env))

        def fmt_dt(ts):
            if not ts:
                return ''
            return fields.Datetime.context_timestamp(
                self, ts).strftime('%d/%m %H:%M')

        def voyage_card(v):
            lines = v.line_ids
            products = len(set(lines.mapped('product_id').ids))
            lots = len(lines.filtered(lambda l: l.lot_id))

            # Desglose de materiales para el popup del tablero (el personal
            # de almacén NUNCA entra al viaje/embarque: esto es todo lo que
            # necesita ver de lo que viene). OJO: NO todo son m² — también
            # llegan piezas/formatos, así que cada material lleva SU unidad
            # (la del producto) y los totales se separan por unidad.
            mat_map = {}
            for line in lines:
                if not line.product_id:
                    continue
                uom = line.product_id.uom_id.name or ''
                key = (line.product_id.display_name, uom)
                mat_map[key] = mat_map.get(key, 0.0) + (line.product_uom_qty or 0.0)
            materials = sorted(
                (
                    {'product': k[0], 'qty': round(qty, 1), 'uom': k[1]}
                    for k, qty in mat_map.items()
                ),
                key=lambda x: -x['qty'],
            )[:40]

            totals_by_uom = {}
            for m in materials:
                totals_by_uom[m['uom']] = totals_by_uom.get(m['uom'], 0.0) + m['qty']
            qty_label = ' · '.join(
                '%g %s' % (round(q, 1), u or 'uds')
                for u, q in sorted(totals_by_uom.items(), key=lambda x: -x[1])
            )

            picking = v.reception_picking_id
            eta = v.eta
            days_to_eta = (eta - today).days if eta else None

            published = bool(v.transit_inventory_published)
            pub_at = v.transit_inventory_published_at
            pub_by = v.transit_inventory_published_by.name or ''

            # Fase del tablero. "Recepcionado" SOLO cuando la recepción
            # física está validada — el estatus del viaje no basta.
            st = v.custom_status
            if picking and picking.state == 'done':
                phase = 'done'
            elif st == 'delivered':
                phase = 'done'
            elif published or st in _READY:
                phase = 'ready'
            elif st in _PORT:
                phase = 'port'
            else:
                phase = 'sailing'

            # Semáforo de atraso:
            # - listo para recibir: días desde publicación/arribo sin validar
            # - en puerto: ETA ya vencida
            late_days = 0
            if phase == 'ready':
                anchor = None
                if pub_at:
                    anchor = fields.Datetime.context_timestamp(
                        self, pub_at).date()
                elif eta and eta <= today:
                    anchor = eta
                if anchor:
                    late_days = max((today - anchor).days - _LATE_AFTER_DAYS, 0)
            elif phase in ('sailing', 'port') and eta and eta < today:
                late_days = (today - eta).days

            return {
                'id': v.id,
                'name': v.name or '',
                'bl': v.bl_number or '',
                'po': v.purchase_id.name or '',
                'supplier': v.purchase_id.partner_id.name or '',
                'vessel': v.vessel_name or '',
                'containers': v.container_number or '',
                'status': st,
                'status_label': status_labels.get(st, st),
                'phase': phase,
                'eta': eta.strftime('%d/%m/%Y') if eta else '',
                'etd': v.etd.strftime('%d/%m/%Y') if v.etd else '',
                'days_to_eta': days_to_eta,
                'published': published,
                'published_at': fmt_dt(pub_at),
                'published_by': pub_by,
                'reception_id': picking.id or False,
                'reception_name': picking.name or '',
                'reception_state': picking.state if picking else '',
                'products': products,
                'lots': lots,
                'late_days': late_days,
                'materials': materials,
                'qty_label': qty_label,
            }

        cards = [voyage_card(v) for v in active]
        port = [c for c in cards if c['phase'] == 'port']
        ready = [c for c in cards if c['phase'] == 'ready']
        ready.sort(key=lambda c: -c['late_days'])

        # ── Recepcionados: mini-lista de folios validados (7 días) ──
        cutoff = today - timedelta(days=_DONE_WINDOW_DAYS)
        done_pairs = []
        for v in Voyage.search([
            ('reception_picking_id.state', '=', 'done'),
        ], order='id desc', limit=120):
            picking = v.reception_picking_id
            if not picking.date_done:
                continue
            local = fields.Datetime.context_timestamp(
                self, picking.date_done).date()
            if local < cutoff:
                continue
            total_m2 = v.total_m2 or 0.0
            done_pairs.append((picking.date_done, {
                'id': v.id,
                'reception_id': picking.id,
                'folio': picking.name or v.name or '',
                'embarque': v.name or '',
                'po': v.purchase_id.name or '',
                'supplier': v.purchase_id.partner_id.name or '',
                'containers': v.container_number or '',
                'qty_label': ('%s m²' % ('{:,.1f}'.format(total_m2)))
                if total_m2 else '',
                'done': fmt_dt(picking.date_done),
            }))
        done_pairs.sort(key=lambda t: t[0], reverse=True)
        done_rows = [row for _ts, row in done_pairs]

        return {
            'port': port,
            'ready': ready,
            'done': done_rows,
        }
