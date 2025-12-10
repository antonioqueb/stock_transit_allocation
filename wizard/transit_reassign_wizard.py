# -*- coding: utf-8 -*-
from odoo import models, fields, api
from ..models.utils.transit_manager import TransitManager

class TransitReassignWizard(models.TransientModel):
    _name = 'transit.reassign.wizard'
    _description = 'Wizard de Reasignación en Tránsito'

    line_ids = fields.Many2many('stock.transit.line', string='Líneas a Reasignar')
    current_partner_id = fields.Many2one('res.partner', string='Cliente Actual', readonly=True)
    
    new_partner_id = fields.Many2one('res.partner', string='Nuevo Cliente', 
        help="Dejar vacío para liberar a Stock")
    
    reason = fields.Text(string='Motivo / Notas', required=True)

    def action_apply(self):
        """Aplica la reasignación usando el Utils Manager"""
        self.ensure_one()
        for line in self.line_ids:
            # Lógica de negocio encapsulada
            TransitManager.reassign_lot(self.env, line, self.new_partner_id, self.reason)
            
            # Log en el chatter del Viaje
            msg = f"🔄 <b>Reasignación:</b> Lote {line.lot_id.name}<br/>"
            msg += f"De: {self.current_partner_id.name or 'Stock'}<br/>"
            msg += f"A: {self.new_partner_id.name or 'Stock'}<br/>"
            msg += f"Motivo: {self.reason}"
            line.voyage_id.message_post(body=msg)

        return {'type': 'ir.actions.act_window_close'}
