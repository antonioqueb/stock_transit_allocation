# -*- coding: utf-8 -*-
"""TARIMAS QUE NUNCA LLEGARON (caso S157 · EMBARQUE/2026/0177, 28 sep 2026).

Compras registra el embarque con el packing list del proveedor (15 tarimas
de 34.1 m² = 511.5). Llega el MISMO metraje en MENOS paquetes (4 tarimas de
~128 m²). El worksheet mide las 4, sube la demanda a lo medido (512.69),
capturado = demanda → no hay parcialidad, no nace backorder y el viaje se
cierra solo. Las otras 11 tarimas se quedan para siempre en SOM/TRANSIT
(375.10 m² fantasma), apartadas al cliente y dentro de la venta.

"✂ Cerrar pendiente" no aplica (no hay recepción abierta) y la purga de
tránsito huérfano respeta lo comprometido con pedidos. Esta acción es la
decisión CONSCIENTE de dar por no llegadas esas tarimas:

1. Cancela los apartados (stock.lot.hold) de sus quants de tránsito.
2. Las retira de las líneas de venta (lot_ids + desglose) SIN mover el
   Solicitado (plomería: tc_skip_qty_ratchet). Si el cliente solo se lleva
   lo que llegó, el vendedor ajusta la cantidad con 'Ajustar'.
3. Ajuste de inventario a 0 de su existencia en tránsito.
4. Archiva los lotes que ya no tienen existencia en ninguna ubicación.
5. Marca las líneas del viaje como liquidadas (estado 'No llegó').
Deja constancia en el embarque, la última recepción y cada venta. Se
revierte con '↻ Reabrir demanda cerrada' (revive lotes y tránsito).
"""
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockTransitLinePhantom(models.Model):
    _inherit = 'stock.transit.line'

    tc_liquidated = fields.Boolean(
        string='No llegó (liquidada)', readonly=True, copy=False,
        help='El lote nunca llegó físicamente y se dio de baja del tránsito '
             'con "Liquidar tarimas no llegadas".')
    tc_liquidated_by = fields.Many2one(
        'res.users', string='Liquidada por', readonly=True, copy=False)
    tc_liquidated_at = fields.Datetime(
        string='Liquidada el', readonly=True, copy=False)
    tc_reception_state = fields.Selection(
        selection_add=[('liquidated', 'No llegó (liquidada)')],
        ondelete={'liquidated': 'set default'})

    @api.depends('tc_liquidated')
    def _compute_tc_reception_state(self):
        super()._compute_tc_reception_state()
        for line in self:
            if line.tc_liquidated and line.tc_reception_state != 'received':
                line.tc_reception_state = 'liquidated'


class StockTransitVoyagePhantom(models.Model):
    _inherit = 'stock.transit.voyage'

    tc_phantom_lot_count = fields.Integer(
        string='Tarimas no llegadas en tránsito',
        compute='_compute_tc_phantom_transit')
    tc_phantom_qty = fields.Float(
        string='Cantidad no llegada en tránsito',
        compute='_compute_tc_phantom_transit')

    def _tc_phantom_transit_report(self):
        """Lotes del viaje que siguen en tránsito sin forma de recibirse:
        ya hubo ≥1 recepción validada, NO hay recepción abierta en la
        cadena y la línea no se recibió completa."""
        self.ensure_one()
        Quant = self.env['stock.quant'].sudo()
        empty = {'lines': self.env['stock.transit.line'], 'quants': Quant}
        if self.custom_status == 'cancel' or not self.reception_picking_id:
            return empty
        totals = self._tc_reception_totals()
        if totals['open'] or not totals['done_count']:
            return empty
        lines = self.line_ids.filtered(
            lambda l: l.lot_id and not l.tc_liquidated
            and l.tc_reception_state in ('pending', 'partial'))
        if not lines:
            return empty
        leaf = self.env['stock.location']._som_transit_quant_leaf()
        quants = Quant.search(
            [('company_id', '=', self.company_id.id),
             ('lot_id', 'in', lines.mapped('lot_id').ids),
             ('quantity', '>', 0)] + leaf)
        lines = lines.filtered(lambda l: l.lot_id in quants.mapped('lot_id'))
        return {'lines': lines, 'quants': quants}

    def _compute_tc_phantom_transit(self):
        for rec in self:
            rec.tc_phantom_lot_count = 0
            rec.tc_phantom_qty = 0.0
            if not isinstance(rec.id, int):
                continue
            rep = rec._tc_phantom_transit_report()
            rec.tc_phantom_lot_count = len(rep['quants'].mapped('lot_id'))
            rec.tc_phantom_qty = sum(rep['quants'].mapped('quantity'))

    def _tc_phantom_product_summary(self, phantom_lines):
        """Por producto: esperado según el PL del viaje, recibido real y lo
        que se liquida — para que la decisión quede documentada."""
        self.ensure_one()
        rows = []
        for product in phantom_lines.mapped('product_id'):
            plines = self.line_ids.filtered(
                lambda l, p=product: l.product_id == p and l.lot_id)
            expected = sum(plines.mapped('product_uom_qty'))
            received = sum(plines.mapped('tc_received_qty'))
            rows.append((product, expected, received))
        return rows

    def action_tc_liquidate_phantom_lots(self):
        """✂ Liquidar tarimas no llegadas (decisión explícita, con
        confirmación en el botón)."""
        Hold = self.env['stock.lot.hold'].sudo()
        SaleLine = self.env['sale.order.line'].sudo()
        Lot = self.env['stock.lot'].sudo()
        now = fields.Datetime.now()
        for rec in self:
            rec = rec.sudo()
            totals = rec._tc_reception_totals()
            if totals['open']:
                raise UserError(_(
                    'El embarque %(v)s aún tiene la recepción %(p)s abierta. '
                    'Si lo que falta no va a llegar, usa "✂ Cerrar pendiente '
                    'de recepción" y después liquida lo que quede en '
                    'tránsito.') % {'v': rec.name,
                                    'p': totals['open'][0].name})
            rep = rec._tc_phantom_transit_report()
            lines, quants = rep['lines'], rep['quants']
            if not quants:
                raise UserError(_(
                    'El embarque %s no tiene tarimas pendientes en tránsito '
                    'que liquidar.') % rec.name)
            lots = quants.mapped('lot_id')
            total = sum(quants.mapped('quantity'))
            summary = rec._tc_phantom_product_summary(lines)

            # 1) Apartados de esas tarimas: sin quant no hay nada que apartar.
            cancelled_holds = Hold.search([
                ('estado', '=', 'activo'),
                ('quant_id', 'in', quants.ids),
            ])
            if cancelled_holds:
                cancelled_holds.write({'estado': 'cancelado'})

            # 2) Fuera del tránsito (ajuste de inventario a 0).
            quants.with_context(inventory_mode=True).write(
                {'inventory_quantity': 0})
            quants.action_apply_inventory()

            # 3) Lotes muertos = sin existencia real en ninguna parte (el
            #    quant negativo de Proveedores no cuenta). Un formato con
            #    parte ya en almacén sigue vivo: no sale de la venta.
            dead = Lot
            for lot in lots:
                alive = self.env['stock.quant'].sudo().search_count([
                    ('lot_id', '=', lot.id),
                    ('quantity', '!=', 0),
                    ('location_id.usage', 'in', ('internal', 'transit')),
                ])
                if not alive:
                    dead |= lot

            # 4) Los muertos salen de las ventas, sin mover el Solicitado.
            orders = self.env['sale.order']
            if dead and 'lot_ids' in SaleLine._fields:
                sale_lines = SaleLine.search([('lot_ids', 'in', dead.ids)])
                for sl in sale_lines:
                    gone = sl.lot_ids & dead
                    vals = {'lot_ids': [(3, lid) for lid in gone.ids]}
                    if 'x_lot_breakdown_json' in sl._fields \
                            and sl.x_lot_breakdown_json:
                        bd = dict(sl._tc_read_lot_breakdown() or {})
                        for lid in gone.ids:
                            bd.pop(str(lid), None)
                            bd.pop(lid, None)
                        vals['x_lot_breakdown_json'] = \
                            sl._tc_prepare_breakdown_value_for_line(bd)
                    sl.with_context(
                        tc_skip_qty_ratchet=True,
                        skip_stone_sync_picking=True,
                        skip_stone_sync_so=True,
                        skip_hold_validation=True,
                        skip_picking_clean=True,
                        skip_transit_sale_sync=True,
                        skip_stone_dup_plate_check=True,
                    ).write(vals)
                    orders |= sl.order_id
                    sl.order_id.message_post(body=Markup(
                        '✂ <b>Tarimas no llegadas retiradas</b> de %s '
                        '(embarque %s): %s. El Solicitado NO cambió '
                        '(%.2f); si el cliente solo se lleva lo que llegó, '
                        'ajusta la cantidad con <b>Ajustar</b>.') % (
                            sl.product_id.display_name, rec.name,
                            ', '.join(gone.mapped('name')),
                            sl.product_uom_qty))

            archived = Lot
            if dead and 'active' in Lot._fields:
                dead.write({'active': False})
                archived = dead

            # 5) Líneas del viaje: estado 'No llegó'.
            lines.with_context(
                skip_reservation_logic=True,
                skip_transit_publication_sync=True,
            ).write({
                'tc_liquidated': True,
                'tc_liquidated_by': self.env.user.id,
                'tc_liquidated_at': now,
            })

            detail = Markup('').join(
                Markup('<li>%s: esperado %.2f · recibido %.2f</li>') % (
                    p.display_name, exp, got)
                for p, exp, got in summary)
            body = Markup(
                '✂ <b>Tarimas no llegadas LIQUIDADAS</b> por %s: %s lote(s) '
                'con <b>%.2f</b> retirados de tránsito (%s).<ul>%s</ul>'
                'Apartados cancelados: %s · ventas ajustadas: %s · lotes '
                'archivados: %s. Se revierte con "↻ Reabrir demanda '
                'cerrada".') % (
                    self.env.user.name, len(lots), total,
                    ', '.join(lots.mapped('name')), detail,
                    len(cancelled_holds),
                    ', '.join(orders.mapped('name')) or '-',
                    len(archived))
            rec.message_post(body=body)
            last_done = totals['done'].sorted(
                lambda p: (p.date_done or p.write_date, p.id))[-1:]
            if last_done:
                last_done.message_post(body=body)
            _logger.info(
                '[TC_PHANTOM] %s: liquidadas %s tarimas (%.2f) por uid %s: %s',
                rec.name, len(lots), total, self.env.uid,
                ', '.join(lots.mapped('name')))
        return True

    def _tc_warn_phantom_after_close(self):
        """Aviso al cerrarse el viaje con tarimas que siguen en tránsito:
        la demanda JAMÁS muere sola, pero el usuario tiene que saber que
        existe el botón."""
        for rec in self:
            rep = rec._tc_phantom_transit_report()
            if not rep['quants']:
                continue
            lots = rep['quants'].mapped('lot_id')
            body = Markup(
                '⚠️ El embarque se cerró con <b>%s tarima(s)</b> sin recibir '
                'que siguen en tránsito (<b>%.2f</b>): %s. Si ya llegó todo '
                'el metraje en menos paquetes o esas tarimas no van a '
                'llegar, usa <b>✂ Liquidar tarimas no llegadas</b>.') % (
                    len(lots), sum(rep['quants'].mapped('quantity')),
                    ', '.join(lots.mapped('name')))
            rec.message_post(body=body)
            if rec.reception_picking_id:
                rec.reception_picking_id.message_post(body=body)

    def _auto_finalize_after_reception(self):
        before = {r.id: r.custom_status for r in self}
        res = super()._auto_finalize_after_reception()
        closed = self.filtered(
            lambda r: r.custom_status == 'delivered'
            and before.get(r.id) != 'delivered')
        if closed:
            try:
                with self.env.cr.savepoint():
                    closed._tc_warn_phantom_after_close()
            except Exception:  # noqa: BLE001 — un aviso jamás tumba el cierre
                _logger.exception('[TC_PHANTOM] aviso post-cierre falló')
        return res

    def action_reopen_closed_demand(self):
        # Reabrir revive las liquidadas: deja de marcarlas como 'No llegó'.
        self.mapped('line_ids').filtered('tc_liquidated').sudo().with_context(
            skip_reservation_logic=True,
            skip_transit_publication_sync=True,
        ).write({'tc_liquidated': False, 'tc_liquidated_by': False,
                 'tc_liquidated_at': False})
        return super().action_reopen_closed_demand()


class SupplierShipmentPhantom(models.Model):
    _inherit = 'supplier.shipment'

    tc_phantom_lot_count = fields.Integer(
        related='voyage_id.tc_phantom_lot_count', readonly=True)

    def action_tc_liquidate_phantom_lots(self):
        self.ensure_one()
        if not self.voyage_id:
            raise UserError(_(
                'Este embarque no tiene viaje en Torre de Control.'))
        return self.voyage_id.action_tc_liquidate_phantom_lots()


class StockPickingPhantom(models.Model):
    _inherit = 'stock.picking'

    tc_phantom_lot_count = fields.Integer(
        related='tc_reception_voyage_id.tc_phantom_lot_count', readonly=True)

    def action_tc_liquidate_phantom_lots(self):
        """Desde la recepción validada: el almacenista no entra al
        embarque, todo el ciclo de recepción vive en la recepción."""
        self.ensure_one()
        if not self.tc_reception_voyage_id:
            raise UserError(_(
                'Esta recepción no está ligada a ningún embarque.'))
        return self.tc_reception_voyage_id.action_tc_liquidate_phantom_lots()
