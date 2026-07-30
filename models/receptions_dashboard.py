# -*- coding: utf-8 -*-
"""Servicio de datos del tablero de Recepciones.

La UI es una client action OWL (receptions_dashboard.js). Aquí solo se
agrega y estructura: pipeline de viajes (ETA de la API ShipsGo), publicación
(la señal de que compras ya asignó el material), orden de recepción y
reportería de recepciones. Todo con sudo(): el tablero es para el personal
de almacén, que NO necesita el grupo de Torre de Control (ver regla
grupo-tránsito-solo-UI).
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Fases del tablero
_SAILING = ('solicitud', 'production', 'booking', 'puerto_origen', 'on_sea')
_PORT = ('puerto_destino', 'arrived_port')
_READY = ('reception_pending',)

# Días de gracia tras ETA/publicación antes de marcar en ROJO
_LATE_AFTER_DAYS = 5


class StockTransitVoyageReceptionsDash(models.Model):
    _inherit = 'stock.transit.voyage'

    @api.model
    def get_receptions_dashboard_data(self):
        Voyage = self.env['stock.transit.voyage'].sudo()
        today = fields.Date.context_today(self)

        active = Voyage.search([
            ('custom_status', 'not in', ('delivered', 'cancel')),
        ], order='eta asc, id desc')
        recent_done = Voyage.search([
            ('custom_status', '=', 'delivered'),
        ], order='write_date desc', limit=40)

        status_labels = dict(
            Voyage._fields['custom_status']._description_selection(self.env))

        def fmt_dt(ts):
            if not ts:
                return ''
            return fields.Datetime.context_timestamp(
                self, ts).strftime('%d/%m %H:%M')

        def voyage_card(v):
            lines = v.line_ids
            m2 = sum(lines.mapped('product_uom_qty'))
            products = len(set(lines.mapped('product_id').ids))
            lots = len(lines.filtered(lambda l: l.lot_id))

            picking = v.reception_picking_id
            eta = v.eta
            days_to_eta = (eta - today).days if eta else None

            published = bool(v.transit_inventory_published)
            pub_at = v.transit_inventory_published_at
            pub_by = v.transit_inventory_published_by.name or ''

            # Fase del tablero
            st = v.custom_status
            if st == 'delivered':
                phase = 'done'
            elif published or st in _READY:
                phase = 'ready'
            elif st in _PORT:
                phase = 'port'
            else:
                phase = 'sailing'

            # Semáforo de atraso:
            # - listo para recibir: días desde publicación/arribo sin validar
            # - navegando/puerto: ETA ya vencida
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

            done_date = ''
            if picking and picking.state == 'done' and picking.date_done:
                done_date = fmt_dt(picking.date_done)

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
                'reception_done': done_date,
                'm2': round(m2, 1),
                'products': products,
                'lots': lots,
                'late_days': late_days,
            }

        cards = [voyage_card(v) for v in active]
        done_cards = []
        cutoff = today - timedelta(days=30)
        for v in recent_done:
            picking = v.reception_picking_id
            ref = (picking.date_done.date()
                   if picking and picking.date_done else None)
            if ref and ref < cutoff:
                continue
            done_cards.append(voyage_card(v))

        sailing = [c for c in cards if c['phase'] == 'sailing']
        port = [c for c in cards if c['phase'] == 'port']
        ready = [c for c in cards if c['phase'] == 'ready']
        ready.sort(key=lambda c: -c['late_days'])

        # ── Reportería: recepciones validadas ───────────────────
        Picking = self.env['stock.picking'].sudo()
        since_dt = fields.Datetime.to_datetime(
            (today - timedelta(days=84)).isoformat())
        done_receptions = Picking.search([
            ('origin', '=like', '%(Recepción Física)'),
            ('state', '=', 'done'),
            ('date_done', '>=', since_dt),
        ])
        weekly = {}
        for p in done_receptions:
            local = fields.Datetime.context_timestamp(self, p.date_done).date()
            monday = local - timedelta(days=local.weekday())
            key = monday.isoformat()
            w = weekly.setdefault(key, {
                'week': monday.strftime('%d/%m'), 'm2': 0.0, 'count': 0})
            qty = sum(
                ml.quantity for ml in p.move_line_ids if ml.state == 'done')
            w['m2'] += qty
            w['count'] += 1
        weekly_list = [weekly[k] for k in sorted(weekly)]
        for w in weekly_list:
            w['m2'] = round(w['m2'], 1)

        # Lead time ETA → recepción validada (viajes entregados, 6 meses)
        leads = []
        for v in Voyage.search([
            ('custom_status', '=', 'delivered'),
            ('eta', '!=', False),
        ], order='id desc', limit=120):
            picking = v.reception_picking_id
            if picking and picking.date_done:
                done_local = fields.Datetime.context_timestamp(
                    self, picking.date_done).date()
                delta = (done_local - v.eta).days
                if -30 <= delta <= 120:
                    leads.append(delta)
        avg_lead = round(sum(leads) / len(leads), 1) if leads else 0

        month_m2 = sum(
            w['m2'] for w in weekly_list[-5:]
        )

        return {
            'kpis': {
                'sailing': len(sailing),
                'arriving_week': len([
                    c for c in cards
                    if c['days_to_eta'] is not None
                    and 0 <= c['days_to_eta'] <= 7
                    and c['phase'] in ('sailing', 'port')
                ]),
                'port': len(port),
                'ready': len(ready),
                'late': len([c for c in cards if c['late_days'] > 0]),
                'ready_m2': round(sum(c['m2'] for c in ready), 1),
                'done_30d': len(done_cards),
                'done_30d_m2': round(sum(c['m2'] for c in done_cards), 1),
                'avg_lead_days': avg_lead,
            },
            'sailing': sailing,
            'port': port,
            'ready': ready,
            'done': done_cards,
            'weekly': weekly_list,
        }
