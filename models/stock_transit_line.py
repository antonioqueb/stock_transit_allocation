# -*- coding: utf-8 -*-
from odoo import models, fields

class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    
    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    
    # Datos del Lote
    product_id = fields.Many2one('product.product', string='Producto', required=True)
    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=True)
    
    # Aquí guardamos el contenedor específico de ESTA línea/placa
    container_number = fields.Char(string='Contenedor', help="Contenedor específico donde viaja este lote")

    # Vinculación técnica
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    # Dimensiones
    x_grosor = fields.Float(related='lot_id.x_grosor', string='Grosor')
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto')
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho')
    
    product_uom_qty = fields.Float(string='Metros (m²)', digits='Product Unit of Measure')
    
    # Asignación
    partner_id = fields.Many2one('res.partner', string='Asignado a', tracking=True, index=True)
    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido')
    ], string='Estado Asignación', default='available', required=True)

    def action_reassign_wizard(self):
        return {
            'name': 'Reasignar en Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'transit.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_ids': self.ids,
                'default_current_partner_id': self.partner_id.id
            }
        }