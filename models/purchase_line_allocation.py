# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PurchaseOrderLineAllocation(models.Model):
    """
    Modelo intermedio que relaciona UNA línea de compra con MÚLTIPLES líneas de venta.
    Esto permite consolidar productos en la OC pero mantener trazabilidad por cliente.
    """
    _name = 'purchase.order.line.allocation'
    _description = 'Asignación de Línea de Compra a Venta'
    _rec_name = 'display_name'

    purchase_line_id = fields.Many2one(
        'purchase.order.line', 
        string='Línea de Compra', 
        required=True, 
        ondelete='cascade',
        index=True
    )
    sale_line_id = fields.Many2one(
        'sale.order.line', 
        string='Línea de Venta', 
        required=True,
        ondelete='cascade',
        index=True
    )
    quantity = fields.Float(
        string='Cantidad Asignada', 
        digits='Product Unit of Measure',
        required=True
    )
    
    purchase_order_id = fields.Many2one(
        'purchase.order', 
        related='purchase_line_id.order_id', 
        store=True, 
        string='Orden de Compra'
    )
    sale_order_id = fields.Many2one(
        'sale.order', 
        related='sale_line_id.order_id', 
        store=True, 
        string='Orden de Venta'
    )
    partner_id = fields.Many2one(
        'res.partner', 
        related='sale_order_id.partner_id', 
        store=True, 
        string='Cliente'
    )
    product_id = fields.Many2one(
        'product.product', 
        related='purchase_line_id.product_id', 
        store=True, 
        string='Producto'
    )
    
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('in_transit', 'En Tránsito'),
        ('partial', 'Parcialmente Recibido'),
        ('done', 'Recibido Completo'),
        ('cancelled', 'Cancelado')
    ], string='Estado', default='pending', tracking=True)
    
    qty_received = fields.Float(
        string='Cantidad Recibida',
        digits='Product Unit of Measure',
        default=0.0
    )
    
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('sale_order_id', 'partner_id', 'quantity')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.sale_order_id.name or '?'} - {rec.partner_id.name or '?'} ({rec.quantity})"

    def action_mark_in_transit(self):
        self.write({'state': 'in_transit'})

    def action_mark_received(self, qty=0):
        for rec in self:
            new_received = rec.qty_received + qty
            if new_received >= rec.quantity:
                rec.write({'qty_received': rec.quantity, 'state': 'done'})
            elif new_received > 0:
                rec.write({'qty_received': new_received, 'state': 'partial'})


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    allocation_ids = fields.One2many(
        'purchase.order.line.allocation',
        'purchase_line_id',
        string='Asignaciones por Cliente'
    )
    
    allocation_summary = fields.Char(
        string='Clientes Asignados',
        compute='_compute_allocation_summary'
    )
    
    total_allocated = fields.Float(
        string='Total Asignado',
        compute='_compute_allocation_summary'
    )

    @api.depends('allocation_ids', 'allocation_ids.quantity', 'allocation_ids.partner_id')
    def _compute_allocation_summary(self):
        for line in self:
            if line.allocation_ids:
                partners = line.allocation_ids.mapped('partner_id.name')
                line.allocation_summary = ', '.join(filter(None, partners[:3]))
                if len(partners) > 3:
                    line.allocation_summary += f" (+{len(partners)-3})"
                line.total_allocated = sum(line.allocation_ids.mapped('quantity'))
            else:
                line.allocation_summary = 'Sin asignar'
                line.total_allocated = 0.0

    def _prepare_stock_moves(self, picking):
        res = super(PurchaseOrderLine, self)._prepare_stock_moves(picking)
        
        for move_vals in res:
            if self.allocation_ids:
                first_alloc = self.allocation_ids[0]
                move_vals['sale_line_id'] = first_alloc.sale_line_id.id
                
                order = first_alloc.sale_order_id
                if order and hasattr(order, 'procurement_group_id') and order.procurement_group_id:
                    move_vals['group_id'] = order.procurement_group_id.id
        
        return res