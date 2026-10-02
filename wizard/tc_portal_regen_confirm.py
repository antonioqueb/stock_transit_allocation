# -*- coding: utf-8 -*-
"""Confirmación de 'Regenerar liga del portal' de un embarque: resume qué
se devuelve al proveedor y a qué pedidos toca antes de echarlo atrás."""
from odoo import models, fields


class TcPortalRegenConfirm(models.TransientModel):
    _name = 'tc.portal.regen.confirm'
    _description = 'Confirmación de regenerar liga del portal'

    voyage_id = fields.Many2one(
        'stock.transit.voyage', string='Embarque', required=True, readonly=True)
    picking_names = fields.Char(string='Recepciones a devolver', readonly=True)
    lot_count = fields.Integer(string='Lotes', readonly=True)
    qty = fields.Float(string='Cantidad', readonly=True)
    order_names = fields.Char(string='Pedidos con placas asignadas', readonly=True)
    assigned_count = fields.Integer(string='Placas asignadas', readonly=True)
    reception_names = fields.Char(string='Recepción física a cancelar', readonly=True)

    def action_confirm(self):
        self.ensure_one()
        self.voyage_id.action_tc_regenerate_portal_link()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.transit.voyage',
            'res_id': self.voyage_id.id,
            'views': [[False, 'form']],
            'target': 'current',
        }
