# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sale_order_ids = fields.Many2many(
        'sale.order',
        string='Órdenes de Venta Vinculadas',
        compute='_compute_sale_order_links',
        compute_sudo=True,
        help='Campo de compatibilidad para vistas heredadas que esperan las SO vinculadas a la OC.',
    )

    sale_order_count = fields.Integer(
        string='Cantidad de Órdenes de Venta',
        compute='_compute_sale_order_links',
        compute_sudo=True,
    )

    @api.depends('order_line.allocation_ids.sale_order_id')
    def _compute_sale_order_links(self):
        for po in self:
            sale_orders = po.order_line.mapped('allocation_ids.sale_order_id')
            po.sale_order_ids = sale_orders
            po.sale_order_count = len(sale_orders)

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        for po in self:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', po.id)
            ])
            if allocations:
                existing_voyage = self.env['stock.transit.voyage'].search([
                    ('purchase_id', '=', po.id),
                    ('custom_status', '!=', 'cancel'),
                ], limit=1)

                if not existing_voyage:
                    voyage = self.env['stock.transit.voyage'].create({
                        'purchase_id': po.id,
                        'custom_status': 'solicitud',
                        'vessel_name': 'Por Definir',
                        'bl_number': po.partner_ref or po.name,
                    })
                    voyage.action_load_from_purchase()

                allocations.write({'state': 'pending'})
        return res