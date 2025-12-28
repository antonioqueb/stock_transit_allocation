# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sale_order_ids = fields.Many2many(
        'sale.order',
        string='Pedidos Vinculados',
        compute='_compute_sale_order_ids',
        store=True
    )

    @api.depends('order_line.allocation_ids.sale_order_id')
    def _compute_sale_order_ids(self):
        for po in self:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', po.id)
            ])
            po.sale_order_ids = allocations.mapped('sale_order_id')

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        
        for po in self:
            # 1. Crear el Viaje/Contenedor en estatus Solicitud automáticamente
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', po.id)
            ])
            
            if allocations:
                voyage = self.env['stock.transit.voyage'].create({
                    'purchase_id': po.id,
                    'custom_status': 'solicitud',
                    'state': 'draft',
                    'container_number': 'TBD (En Solicitud)',
                    'vessel_name': 'Por Definir',
                    'bl_number': po.partner_ref or po.name,
                })
                # Cargar líneas preventivas para que se vean en la sábana
                voyage.action_load_from_purchase()
                
                # Marcar allocations como pendientes de tránsito real
                allocations.write({'state': 'pending'})
        
        return res