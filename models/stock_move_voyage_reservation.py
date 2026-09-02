# -*- coding: utf-8 -*-
"""Candados de recepción de embarque y devoluciones.

CASO EMBARQUE/2026/0077 · lote 21232-6: el backorder de la recepción física
(INT/00268) nació como picking estándar —demanda por PRODUCTO, sin lotes— y
la reserva nativa de Odoo tomó CUALQUIER quant de ese producto en
SOM/TRANSIT: los lotes S81 del viaje y 350 bultos de un lote viejo que una
devolución de cliente mal enrutada había dejado ahí. Material que jamás
estuvo planificado en el embarque acabó dentro de su recepción.

1) RESERVA AMARRADA AL VIAJE (stock.move._action_assign)
   Todo movimiento de una recepción física ligada a un viaje —directa o
   backorder— reserva SOLO lotes que existan en las líneas del embarque para
   ese producto. Si el viaje no trae lotes del producto, NO reserva nada
   ajeno (queda en espera y se avisa), jamás "lo que haya en tránsito".

2) LIGA PERSISTENTE EN BACKORDERS (stock.picking._create_backorder_picking)
   tc_reception_voyage_id es copy=False; el backorder nativo la perdía. Se
   hereda explícitamente para que el candado 1 lo cubra siempre.

3) DEVOLUCIÓN DE CLIENTE JAMÁS SE ESTACIONA EN TRÁNSITO (stock.picking.create)
   Cinturón sobre el candado del wizard (stock_return_picking.py): cualquier
   picking con origen Clientes y destino tránsito, venga de donde venga,
   nace apuntando a existencias del almacén.
"""
import logging

from markupsafe import Markup, escape

from odoo import api, models
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)


class StockMoveVoyageReservation(models.Model):
    _inherit = 'stock.move'

    # ------------------------------------------------------------------
    def _tc_reception_voyage(self):
        """Viaje ligado a la recepción física de este movimiento, o False."""
        self.ensure_one()
        picking = self.picking_id
        if not picking or picking.picking_type_code != 'internal':
            return False
        if not self.location_id or not self.location_id._som_is_transit():
            return False
        voyage = picking.tc_reception_voyage_id
        if not voyage and hasattr(picking, '_get_linked_reception_voyage'):
            try:
                voyage = picking._get_linked_reception_voyage()
            except Exception:  # noqa: BLE001
                voyage = False
        # Backorders encadenados sin liga propia: subir por la cadena.
        parent = picking.backorder_id
        hops = 0
        while not voyage and parent and hops < 5:
            voyage = parent.tc_reception_voyage_id
            parent = parent.backorder_id
            hops += 1
        return voyage.sudo() if voyage else False

    def _tc_voyage_allowed_lots(self, voyage):
        self.ensure_one()
        lines = voyage.line_ids.filtered(
            lambda l: l.product_id == self.product_id and l.lot_id)
        return lines.mapped('lot_id')

    def _tc_assign_from_voyage_lots(self, voyage, force_qty=False):
        """Réplica acotada del núcleo de _action_assign: reserva por lote,
        SOLO con los lotes del viaje, y fija el estado como lo hace el core."""
        self.ensure_one()
        move = self.with_company(self.company_id)
        if not force_qty and (move.picked or move.state not in (
                'confirmed', 'waiting', 'partially_available')):
            return
        rounding = move.product_id.uom_id.rounding
        if force_qty:
            missing_uom = force_qty
        else:
            missing_uom = move.product_uom_qty - move.quantity
        if float_compare(missing_uom, 0, precision_rounding=rounding) <= 0:
            move.write({'state': 'assigned'})
            return
        need = move.product_uom._compute_quantity(
            missing_uom, move.product_id.uom_id, rounding_method='HALF-UP')

        lots = move._tc_voyage_allowed_lots(voyage)
        if not lots:
            _logger.warning(
                '[TC_RECEPTION] %s: el viaje %s no trae lotes de %s; no se '
                'reserva material ajeno en tránsito.',
                move.picking_id.name, voyage.name, move.product_id.display_name)
            move.picking_id._tc_notify_voyage_lot_guard(voyage, move.product_id)
            return

        taken_total = 0.0
        for lot in lots:
            if float_is_zero(need, precision_rounding=rounding):
                break
            taken = move._update_reserved_quantity(
                need, move.location_id, lot_id=lot, strict=False)
            if taken:
                need -= taken
                taken_total += taken

        if float_is_zero(need, precision_rounding=rounding):
            move.write({'state': 'assigned'})
        elif taken_total or not float_is_zero(move.quantity, precision_rounding=rounding):
            move.write({'state': 'partially_available'})

    def _action_assign(self, force_qty=False):
        guarded = {}
        for move in self:
            try:
                voyage = move._tc_reception_voyage()
            except Exception:  # noqa: BLE001 - el candado jamás tumba una reserva normal
                _logger.exception('[TC_RECEPTION] resolviendo viaje de %s', move.id)
                voyage = False
            if voyage and move.product_id.tracking in ('lot', 'serial'):
                guarded[move.id] = voyage
        regular = self.filtered(lambda m: m.id not in guarded)
        res = None
        if regular:
            res = super(StockMoveVoyageReservation, regular)._action_assign(force_qty=force_qty)
        for move in self.filtered(lambda m: m.id in guarded):
            move._tc_assign_from_voyage_lots(guarded[move.id], force_qty=force_qty)
        if guarded:
            self.filtered(lambda m: m.id in guarded).picking_id._check_entire_pack()
        return res


class StockPickingVoyageGuards(models.Model):
    _inherit = 'stock.picking'

    # ---- 2) liga persistente en backorders ----------------------------
    def _create_backorder_picking(self):
        backorder = super()._create_backorder_picking()
        try:
            voyage = self.tc_reception_voyage_id or self._get_linked_reception_voyage()
            if voyage and backorder and not backorder.tc_reception_voyage_id:
                backorder.tc_reception_voyage_id = voyage.id
        except Exception:  # noqa: BLE001
            _logger.exception('[TC_RECEPTION] no se heredó la liga al viaje en el backorder')
        return backorder

    def _tc_notify_voyage_lot_guard(self, voyage, product):
        self.ensure_one()
        key = 'tc_lot_guard_%s' % product.id
        if self.env.context.get(key):
            return
        try:
            self.with_context(**{key: True}).sudo().message_post(body=Markup(
                '<p>⚠️ <b>Reserva bloqueada por candado de embarque.</b> El viaje '
                '<b>%s</b> no trae lotes de <b>%s</b>; esta recepción no toma '
                'material ajeno que esté en tránsito.</p>') % (
                escape(voyage.name), escape(product.display_name)))
        except Exception:  # noqa: BLE001
            pass

    # ---- 3) devolución de cliente jamás se estaciona en tránsito -------
    @api.model
    def _tc_redirect_customer_return_dest(self, vals):
        Location = self.env['stock.location']
        src = Location.browse(vals.get('location_id')) if vals.get('location_id') else Location
        dst = Location.browse(vals.get('location_dest_id')) if vals.get('location_dest_id') else Location
        if not src or not dst or src.usage != 'customer':
            return vals
        try:
            if not dst._som_is_transit():
                return vals
        except Exception:  # noqa: BLE001
            return vals
        picking_type = self.env['stock.picking.type'].browse(
            vals.get('picking_type_id')) if vals.get('picking_type_id') else False
        warehouse = picking_type.warehouse_id if picking_type else False
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search(
                [('company_id', '=', vals.get('company_id') or self.env.company.id)], limit=1)
        target = warehouse.lot_stock_id if warehouse else False
        if target and not target._som_is_transit():
            _logger.info('[TC_RETURN_GUARD] devolución de cliente redirigida de %s a %s',
                         dst.complete_name, target.complete_name)
            vals = dict(vals, location_dest_id=target.id)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._tc_redirect_customer_return_dest(v) for v in vals_list]
        pickings = super().create(vals_list)
        # Los movimientos creados en el mismo vals pudieron traer el destino
        # viejo: se alinean al del picking (solo si aún no están hechos).
        for picking in pickings:
            if picking.location_id.usage != 'customer':
                continue
            wrong = picking.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
                and m.location_dest_id != picking.location_dest_id
                and m.location_dest_id._som_is_transit())
            if wrong:
                wrong.write({'location_dest_id': picking.location_dest_id.id})
                wrong.move_line_ids.filtered(lambda ml: ml.state not in ('done', 'cancel')).write(
                    {'location_dest_id': picking.location_dest_id.id})
        return pickings
