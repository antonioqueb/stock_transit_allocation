# -*- coding: utf-8 -*-
"""Servicio de datos del tablero de Recepciones.

La UI es una client action OWL (receptions_dashboard.js). Aquí solo se
agrega y estructura el pipeline operativo del almacén: En puerto, Listos
para recibir y Recepcionados. Los datos se leen con sudo() porque el
personal de almacén NO necesita el grupo de Torre de Control (ver regla
grupo-tránsito-solo-UI) — pero sudo NO es "abierto a todos": la entrada
exige el grupo Recepciones, si no cualquier usuario interno se traía el
pipeline completo por RPC aunque no viera el menú.

Reglas del tablero:
- "En camino" no se muestra: al almacén solo le importa lo operable.
- Recepcionados = SOLO recepciones físicas validadas (picking done), con
  ventana de 7 días — es registro administrativo, no historial.
- En puerto y Listos para recibir no caducan jamás: ahí viven hasta que
  alguien los procese.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.stock_transit_allocation.models.som_date_format import (
    som_format_date,
)

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

    _RECEPTIONS_GROUP = 'stock_transit_allocation.group_transit_receptions'

    @api.model
    def _rcp_check_access(self):
        """Candado de TODO lo que cuelga del tablero de Recepciones.

        El menú por sí solo no protege nada: estos métodos son llamables
        por RPC desde cualquier sesión interna, y adentro corren con
        sudo() — sin esta puerta, quien no ve el menú igual podía leer el
        pipeline, crear backorders o marcar etiquetado."""
        if not self.env.user.has_group(self._RECEPTIONS_GROUP):
            raise AccessError(_(
                'El tablero de Recepciones es del personal de almacén. '
                'Pide el permiso Torre de Control = Recepciones.'))

    @api.model
    def get_receptions_dashboard_data(self):
        self._rcp_check_access()
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
            return som_format_date(
                fields.Datetime.context_timestamp(self, ts),
                empty='', with_time=True)

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
            st = v.custom_status

            # PARCIALES — el material manda: si la recepción apuntada ya
            # validó pero la cadena sigue abierta o queda pendiente, la
            # tarjeta JAMÁS se va completa a Recepcionados. Se re-apunta a
            # la recepción abierta y permanece en Listos con su avance; lo
            # ya validado aparece abajo como SU PROPIA fila (división por
            # tandas). Cerrar de verdad = delivered (incluye el cierre
            # forzado, que es una decisión consciente).
            totals = None
            if st != 'delivered' and picking and picking.state == 'done':
                totals = v._tc_reception_totals()
                if totals['open']:
                    picking = totals['open'].sorted('id')[0]

            eta = v.eta
            days_to_eta = (eta - today).days if eta else None

            published = bool(v.transit_inventory_published)
            pub_at = v.transit_inventory_published_at
            pub_by = v.transit_inventory_published_by.name or ''

            # Fase del tablero. "Recepcionado" SOLO cuando la recepción
            # física está validada — el estatus del viaje no basta.
            # "Listo para recibir" = el viaje fue movido a Entrega en Sitio
            # (estatus En Recepción); PUBLICAR ya no alista recepciones —
            # es un acto comercial, no operativo.
            # Pendiente REAL: la demanda de recepciones abiertas Y el hueco
            # contra las líneas del viaje (si el backorder no nació — p. ej.
            # demanda borrada — las abiertas dicen 0 pero el hueco no miente).
            pending_material = 0.0
            if totals is not None:
                pending_material = totals['pending'] or 0.0
                if not totals['open']:
                    expected = sum(v.line_ids.mapped('product_uom_qty'))
                    gap = expected - (totals['received'] or 0.0)
                    if gap > 0.01:
                        pending_material = max(pending_material, gap)

            needs_reopen = False
            if st == 'delivered':
                phase = 'done'
            elif picking and picking.state == 'done':
                # Sin recepción abierta: solo pasa a Recepcionados si NO
                # queda material pendiente; con faltante la tarjeta se queda
                # en Listos y ofrece REABRIR la siguiente recepción.
                phase = 'done' if pending_material <= 0.01 else 'ready'
                needs_reopen = phase == 'ready'
            elif st in _READY:
                phase = 'ready'
            elif st in _PORT:
                phase = 'port'
            else:
                phase = 'sailing'

            # Semáforo de atraso:
            # - listo para recibir: días desde que se creó la recepción
            #   (movimiento a Entrega en Sitio) sin validar
            # - en puerto: ETA ya vencida
            late_days = 0
            if phase == 'ready':
                anchor = None
                if picking and picking.create_date:
                    anchor = fields.Datetime.context_timestamp(
                        self, picking.create_date).date()
                elif pub_at:
                    anchor = fields.Datetime.context_timestamp(
                        self, pub_at).date()
                elif eta and eta <= today:
                    anchor = eta
                if anchor:
                    late_days = max((today - anchor).days - _LATE_AFTER_DAYS, 0)
            elif phase in ('sailing', 'port') and eta and eta < today:
                late_days = (today - eta).days

            # Parcialidad: si ya hubo recepciones validadas y sigue abierta
            # la siguiente, la tarjeta lo dice con números claros y su
            # barra de avance — el almacenista NUNCA entra al embarque:
            # todo su contexto vive en esta tarjeta.
            partial_info = ''
            partial_pct = 0
            if phase == 'ready' and picking:
                t = totals or v._tc_reception_totals()
                pend_show = max(t['pending'] or 0.0, pending_material)
                if t['done_count'] and (t['open'] or pend_show > 0.01):
                    total = t['received'] + pend_show
                    partial_pct = int(round(
                        t['received'] / total * 100)) if total else 0
                    partial_info = (
                        '%sª recepción · recibido %s · '
                        'pendiente %s%s' % (
                            t['seq'],
                            '{:,.1f}'.format(t['received']),
                            '{:,.1f}'.format(pend_show),
                            ' · SIN RECEPCIÓN ABIERTA' if needs_reopen else ''))

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
                'partial_info': partial_info,
                'partial_pct': partial_pct,
                'eta': som_format_date(eta, empty=''),
                'etd': som_format_date(v.etd, empty=''),
                'days_to_eta': days_to_eta,
                'published': published,
                'published_at': fmt_dt(pub_at),
                'published_by': pub_by,
                'needs_reopen': needs_reopen,
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
        # CADA recepción validada es su propia fila — un embarque de 3
        # contenedores recibido en 3 tandas aparece 3 veces, con su folio
        # y sus m² reales. Se unen la liga persistente
        # (tc_reception_voyage_id) y el camino legado (reception_picking_id
        # apuntando a un done).
        cutoff = today - timedelta(days=_DONE_WINDOW_DAYS)
        Picking = self.env['stock.picking'].sudo()
        done_picks = Picking.search([
            ('tc_reception_voyage_id', '!=', False),
            ('state', '=', 'done'),
        ], order='id desc', limit=200)
        pick_voyage = {p.id: p.tc_reception_voyage_id for p in done_picks}
        for v in Voyage.search([
            ('reception_picking_id.state', '=', 'done'),
        ], order='id desc', limit=120):
            if v.reception_picking_id.id not in pick_voyage:
                done_picks |= v.reception_picking_id
                pick_voyage[v.reception_picking_id.id] = v

        # UNIFICACIÓN: un embarque completo (sin abiertas ni pendiente)
        # con TODAS sus parcialidades etiquetadas vuelve a ser UNA sola
        # fila; mientras no, cada parcialidad validada es su propia fila
        # con su worksheet y su check de etiquetado independiente.
        done_pairs = []
        unified_emitted = set()
        for picking in done_picks:
            v = pick_voyage[picking.id]
            if not picking.date_done:
                continue
            local = fields.Datetime.context_timestamp(
                self, picking.date_done).date()
            if local < cutoff:
                continue
            t = v._tc_reception_totals()
            done_sorted = list(t['done'].sorted('date_done'))
            complete = bool(done_sorted) and not t['open'] and (
                t['pending'] or 0.0) <= 0.01
            all_labeled = bool(done_sorted) and all(
                (pk.tc_labeling_status or 'none') == 'labeled'
                for pk in done_sorted)

            if (complete and all_labeled and len(done_sorted) > 1):
                if v.id in unified_emitted:
                    continue
                unified_emitted.add(v.id)
                total_m2 = sum(
                    ml.quantity for pk in done_sorted
                    for ml in pk.move_line_ids)
                last = done_sorted[-1]
                done_pairs.append((last.date_done, {
                    'id': v.id,
                    'reception_id': last.id,
                    'folio': v.name or last.name or '',
                    'embarque': v.name or '',
                    'po': v.purchase_id.name or '',
                    'supplier': v.purchase_id.partner_id.name or '',
                    'containers': v.container_number or '',
                    'qty_label': ('%s m²' % ('{:,.1f}'.format(total_m2)))
                    if total_m2 else '',
                    'done': fmt_dt(last.date_done),
                    'partial_tag': 'Unificado · %s parciales' % len(
                        done_sorted),
                    'unified': True,
                    'labeling_status': 'labeled',
                    'label_print_count': v.tc_label_print_count or 0,
                }))
                continue

            received_m2 = sum(picking.move_line_ids.mapped('quantity'))
            partial_tag = ''
            if t['done_count'] > 1 or t['open']:
                idx = done_sorted.index(picking) + 1 \
                    if picking in t['done'] else t['done_count']
                partial_tag = 'Parcial %s/%s' % (
                    idx, t['done_count'] + len(t['open']))
            plab = picking.tc_labeling_status or 'none'
            if plab == 'none' and (v.tc_label_print_count or 0):
                plab = 'printing'
            done_pairs.append((picking.date_done, {
                'id': v.id,
                'reception_id': picking.id,
                'folio': picking.name or v.name or '',
                'embarque': v.name or '',
                'po': v.purchase_id.name or '',
                'supplier': v.purchase_id.partner_id.name or '',
                'containers': v.container_number or '',
                'qty_label': ('%s m²' % ('{:,.1f}'.format(received_m2)))
                if received_m2 else '',
                'done': fmt_dt(picking.date_done),
                'partial_tag': partial_tag,
                # Etiquetado POR PARCIALIDAD (el del viaje solo aplica a
                # embarques de una sola recepción / legado).
                'labeling_status': plab if t['done_count'] > 1
                else (v.tc_labeling_status or plab or 'none'),
                'label_print_count': v.tc_label_print_count or 0,
            }))
        done_pairs.sort(key=lambda t: t[0], reverse=True)
        done_rows = [row for _ts, row in done_pairs]

        return {
            'port': port,
            'ready': ready,
            'done': done_rows,
        }

    @api.model
    def rcp_reopen_next_reception(self, voyage_id):
        """Crea la SIGUIENTE recepción de un embarque con faltante cuya
        cadena quedó sin recepción abierta (p. ej. la demanda del backorder
        fue destruida). Calcula el pendiente por producto contra las líneas
        del viaje y lo materializa como backorder de la última validada.
        Con sudo(): lo dispara el almacén desde el tablero."""
        self._rcp_check_access()
        v = self.sudo().browse(voyage_id)
        if not v.exists():
            return False
        t = v._tc_reception_totals()
        if t['open']:
            return t['open'].sorted('id')[0].id
        done = t['done'].sorted('date_done')
        if not done:
            return False
        last = done[-1]

        expected = {}
        for line in v.line_ids:
            if line.product_id:
                expected[line.product_id] = expected.get(
                    line.product_id, 0.0) + (line.product_uom_qty or 0.0)
        received = {}
        for ml in done.mapped('move_line_ids'):
            received[ml.product_id] = received.get(
                ml.product_id, 0.0) + ml.quantity
        missing = {p: round(q - received.get(p, 0.0), 3)
                   for p, q in expected.items()
                   if q - received.get(p, 0.0) > 0.01}
        if not missing:
            return False

        ctx = v._tc_reception_safe_context()
        new = last.with_context(ctx).copy({
            'name': '/', 'move_ids': [], 'move_line_ids': [],
            'backorder_id': last.id, 'tc_reception_voyage_id': v.id,
        })
        reset = {}
        if 'packing_list_imported' in new._fields:
            reset['packing_list_imported'] = False
        if 'worksheet_imported' in new._fields:
            reset['worksheet_imported'] = False
        if reset:
            new.with_context(ctx).write(reset)
        Move = self.env['stock.move'].sudo()
        for p, q in missing.items():
            Move.with_context(ctx).create({
                'picking_id': new.id, 'product_id': p.id,
                'product_uom_qty': q, 'product_uom': p.uom_id.id,
                'location_id': last.location_id.id,
                'location_dest_id': last.location_dest_id.id,
                'company_id': last.company_id.id,
                'picking_type_id': last.picking_type_id.id,
                'origin': last.origin or v.name,
                'description_picking': p.display_name,
            })
        new.move_ids.with_context(ctx)._action_confirm()
        v.write({'reception_picking_id': new.id,
                 'custom_status': 'reception_pending'})
        v.message_post(body=_(
            '🔁 Recepción REABIERTA desde el tablero por %(user)s: '
            '%(new)s con el pendiente del embarque (%(qty)s).') % {
            'user': self.env.user.name, 'new': new.name,
            'qty': ', '.join('%s: %.2f' % (p.display_name, q)
                             for p, q in missing.items())})
        new.message_post(body=_(
            '🔁 Recepción del PENDIENTE del embarque %(v)s, creada desde '
            'el tablero de Recepciones (continúa a %(prev)s).') % {
            'v': v.name or '', 'prev': last.name})
        return new.id

    @api.model
    def rcp_toggle_labeled(self, voyage_id, picking_id=False):
        """Check de etiquetado desde el tablero de Recepciones.

        Con picking_id: alterna el etiquetado de ESA PARCIALIDAD y el
        viaje se refresca (si todas quedan etiquetadas y no hay
        pendiente → unificación; si se desmarca una → separación).
        Sin picking_id (legado / embarque de una sola recepción):
        alterna el viaje completo. Todo con sudo(): el almacén no tiene
        el grupo de Torre de Control."""
        self._rcp_check_access()
        rec = self.sudo().browse(voyage_id)
        if not rec.exists():
            return False
        t = rec._tc_reception_totals()
        pk = self.env['stock.picking'].sudo().browse(picking_id) \
            if picking_id else self.env['stock.picking']
        multi = t['done_count'] > 1 or bool(t['open'])
        if pk and pk.exists() and multi:
            if pk.tc_labeling_status == 'labeled':
                pk.write({'tc_labeling_status': 'printing'})
                pk.message_post(body=Markup(
                    '↩️ Etiquetado de la parcialidad desmarcado por %s.')
                    % self.env.user.name)
            else:
                pk.write({'tc_labeling_status': 'labeled'})
                pk.message_post(body=Markup(
                    '✅ Parcialidad ETIQUETADA por %s.')
                    % self.env.user.name)
            rec._tc_labeling_refresh_from_partials()
            return pk.tc_labeling_status
        if rec.tc_labeling_status == 'labeled':
            rec.action_unmark_labeled()
            # separar: las parcialidades vuelven a su check individual
            if t['done']:
                t['done'].sudo().filtered(
                    lambda p2: p2.tc_labeling_status == 'labeled'
                ).write({'tc_labeling_status': 'printing'})
        else:
            rec.action_mark_labeled()
            if t['done']:
                t['done'].sudo().write({'tc_labeling_status': 'labeled'})
        return rec.tc_labeling_status
