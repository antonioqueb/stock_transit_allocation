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
        index=True,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de Venta',
        required=True,
        ondelete='cascade',
        index=True,
    )
    quantity = fields.Float(
        string='Cantidad Asignada',
        digits='Product Unit of Measure',
        required=True,
    )

    purchase_order_id = fields.Many2one(
        'purchase.order',
        related='purchase_line_id.order_id',
        store=True,
        string='Orden de Compra',
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        related='sale_line_id.order_id',
        store=True,
        string='Orden de Venta',
    )
    partner_id = fields.Many2one(
        'res.partner',
        related='sale_order_id.partner_id',
        store=True,
        string='Cliente',
    )
    product_id = fields.Many2one(
        'product.product',
        related='purchase_line_id.product_id',
        store=True,
        string='Producto',
    )

    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('in_transit', 'En Tránsito'),
        ('partial', 'Parcialmente Recibido'),
        ('done', 'Recibido Completo'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='pending')

    qty_received = fields.Float(
        string='Cantidad Recibida',
        digits='Product Unit of Measure',
        default=0.0,
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
        string='Asignaciones por Cliente',
    )

    allocation_summary = fields.Char(
        string='Clientes Asignados',
        compute='_compute_allocation_summary',
    )

    total_allocated = fields.Float(
        string='Total Asignado',
        compute='_compute_allocation_summary',
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

    # =========================================================================
    # FIX: Se eliminó el override de _prepare_stock_moves.
    #
    # El problema: cuando una línea de compra tiene allocations de MÚLTIPLES
    # órdenes de venta (consolidación), asignar sale_line_id al stock.move
    # causa que el picking tenga moves apuntando a diferentes SO. El campo
    # computado picking.sale_id (Many2one) explota porque recibe múltiples SO.
    #
    # La trazabilidad SO <-> PO se mantiene a través de las allocations y
    # la Torre de Control (Voyage), NO a nivel de stock.move.
    # =========================================================================