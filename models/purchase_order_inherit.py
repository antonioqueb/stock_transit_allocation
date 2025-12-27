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
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', po.id),
                ('state', '=', 'pending')
            ])
            allocations.action_mark_in_transit()
        
        return res