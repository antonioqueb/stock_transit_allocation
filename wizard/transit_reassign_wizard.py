# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..models.utils.transit_manager import TransitManager

class TransitReassignWizard(models.TransientModel):
    _name = 'transit.reassign.wizard'
    _description = 'Wizard de Reasignación en Tránsito'

    line_ids = fields.Many2many('stock.transit.line', string='Líneas a Reasignar')
    
    current_partner_id = fields.Many2one('res.partner', string='Cliente Actual', readonly=True)
    current_order_id = fields.Many2one('sale.order', string='Orden Actual', readonly=True)
    
    new_partner_id = fields.Many2one('res.partner', string='Nuevo Cliente', 
        help="Dejar vacío para liberar a Stock")
    
    # Campo nuevo obligatorio si hay partner
    new_order_id = fields.Many2one('sale.order', string='Asignar a Orden', 
        domain="[('partner_id', '=', new_partner_id), ('state', 'in', ['sale', 'done'])]",
        help="Seleccione la Orden de Venta abierta de este cliente.")
    
    reason = fields.Text(string='Motivo / Notas', required=True)

    def action_apply(self):
        """Aplica la reasignación con validaciones"""
        self.ensure_one()
        
        # Validación estricta solicitada
        if self.new_partner_id and not self.new_order_id:
            raise UserError(_("No puede asignar mercancía a un cliente sin especificar a qué Orden de Venta (Pedido) pertenece."))

        for line in self.line_ids:
            # Lógica de negocio encapsulada
            TransitManager.reassign_lot(self.env, line, self.new_partner_id, self.new_order_id, self.reason)
            
            # Log en el chatter
            msg = f"🔄 <b>Reasignación:</b> Lote {line.lot_id.name}<br/>"
            msg += f"De: {self.current_partner_id.name or 'Stock'} ({self.current_order_id.name or '-'})<br/>"
            msg += f"A: {self.new_partner_id.name or 'Stock'} ({self.new_order_id.name or '-'})<br/>"
            msg += f"Motivo: {self.reason}"
            line.voyage_id.message_post(body=msg)

        return {'type': 'ir.actions.act_window_close'}