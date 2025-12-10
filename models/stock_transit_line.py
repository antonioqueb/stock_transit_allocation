# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    
    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    
    product_id = fields.Many2one('product.product', string='Producto', required=True)
    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=True)
    container_number = fields.Char(string='Contenedor', help="Contenedor específico")
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    x_grosor = fields.Float(related='lot_id.x_grosor', string='Grosor')
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto')
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho')
    product_uom_qty = fields.Float(string='Metros (m²)', digits='Product Unit of Measure')
    
    # --- ASIGNACIÓN ---
    partner_id = fields.Many2one('res.partner', string='Asignado a', tracking=True, index=True)
    
    # NUEVO CAMPO: Orden de Venta Específica
    order_id = fields.Many2one('sale.order', string='Orden de Venta', 
        domain="[('partner_id', '=', partner_id), ('state', 'in', ['sale', 'done'])]",
        tracking=True, help="Pedido específico del cliente al que pertenece esta mercancía.")

    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido')
    ], string='Estado Asignación', default='available', required=True)

    @api.constrains('partner_id', 'order_id')
    def _check_order_assignment(self):
        """
        Regla de Negocio:
        Si se asigna a un cliente (partner_id), OBLIGATORIAMENTE debe tener una Orden de Venta (order_id).
        Si no, no se permite guardar.
        """
        for record in self:
            if record.partner_id and not record.order_id:
                raise ValidationError(_(
                    "Error de Integridad: La línea con lote %s está asignada al cliente %s "
                    "pero NO tiene una Orden de Venta vinculada. Debe seleccionar la Orden específica." 
                    % (record.lot_id.name, record.partner_id.name)
                ))

    def action_reassign_wizard(self):
        return {
            'name': 'Reasignar en Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'transit.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_ids': self.ids,
                'default_current_partner_id': self.partner_id.id,
                'default_current_order_id': self.order_id.id, # Pasar orden actual
            }
        }