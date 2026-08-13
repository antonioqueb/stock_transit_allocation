# -*- coding: utf-8 -*-
"""Confirmación explícita de recepción PARCIAL (una sola pregunta).

Cuando al validar una recepción física de embarque quedará saldo
pendiente, este diálogo resume cuánto entra a stock ahora y cuánto
quedará pendiente, y solo con la confirmación se valida (el remanente
genera su siguiente recepción pendiente automáticamente)."""
from odoo import models, fields, api


class TcPartialReceptionConfirm(models.TransientModel):
    _name = 'tc.partial.reception.confirm'
    _description = 'Confirmación de recepción parcial'

    picking_id = fields.Many2one(
        'stock.picking', string='Recepción', required=True)
    qty_now = fields.Float(string='Se recibe ahora', readonly=True)
    qty_pending = fields.Float(string='Quedará pendiente', readonly=True)

    def action_confirm(self):
        self.ensure_one()
        return self.picking_id.with_context(
            tc_partial_reception_confirmed=True).button_validate()
