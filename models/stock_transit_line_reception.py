# -*- coding: utf-8 -*-
"""Estado de recepción POR LÍNEA del viaje (recepciones parciales).

`voyage_status` es un related de `voyage_id.custom_status`: mientras el
embarque siga EN RECEPCIÓN por una parcialidad pendiente, TODAS sus
líneas se ven 'reception_pending' aunque 10 de 11 placas ya estén en
almacén (caso EMBARQUE/2026/0132). Estos campos llevan lo realmente
recibido por línea, calculado SIEMPRE desde las recepciones físicas
hechas de la cadena del viaje (`voyage._tc_sync_received_lines`).
"""
from odoo import api, fields, models
from odoo.tools.float_utils import float_compare


class StockTransitLineReception(models.Model):
    _inherit = 'stock.transit.line'

    tc_received_qty = fields.Float(
        string='Recibido en almacén',
        digits='Product Unit of Measure',
        readonly=True, copy=False,
        help='Cantidad de esta línea ya movida de tránsito a almacén por '
             'recepciones físicas validadas del viaje.',
    )
    tc_received_picking_id = fields.Many2one(
        'stock.picking', string='Recibido en',
        readonly=True, copy=False, ondelete='set null',
        help='Última recepción física validada que movió este lote.',
    )
    tc_received_date = fields.Datetime(
        string='Fecha de recepción física', readonly=True, copy=False,
    )
    tc_reception_state = fields.Selection([
        ('pending', 'En tránsito'),
        ('partial', 'Recibida parcial'),
        ('received', 'Recibida en almacén'),
    ], string='Recepción', compute='_compute_tc_reception_state',
        store=True, readonly=True)

    @api.depends('tc_received_qty', 'product_uom_qty',
                 'voyage_id.custom_status',
                 'voyage_id.line_ids.tc_received_qty')
    def _compute_tc_reception_state(self):
        for line in self:
            demand = line.product_uom_qty or 0.0
            received = line.tc_received_qty or 0.0
            rounding = line.product_id.uom_id.rounding or 0.01
            if received > 0 and float_compare(
                    received, demand, precision_rounding=rounding) >= 0:
                state = 'received'
            elif received > 0:
                state = 'partial'
            elif (line.voyage_id.custom_status == 'delivered'
                    and demand > 0
                    and not any(line.voyage_id.line_ids.mapped(
                        'tc_received_qty'))):
                # Histórico: viaje cerrado antes de que existiera el
                # estampado por línea (sin recepción física ligada).
                state = 'received'
            else:
                state = 'pending'
            line.tc_reception_state = state
