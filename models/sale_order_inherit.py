# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def unlink(self):
        for order in self:
            # Buscar si tiene líneas en tránsito que ya tengan lote (recepción física iniciada)
            transit_lines = self.env['stock.transit.line'].search([
                ('order_id', '=', order.id),
                ('lot_id', '!=', False)
            ])
            if transit_lines:
                raise UserError(_("No puede eliminar el pedido %s porque ya tiene mercancía recibida en tránsito (Torre de Control).") % order.name)
        return super(SaleOrder, self).unlink()

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    auto_transit_assign = fields.Boolean(
        string='Mandar Pedir', 
        default=True,
        help="Si está marcado, se considerará para la asignación automática en la Torre de Control "
             "cuando se genere la compra."
    )