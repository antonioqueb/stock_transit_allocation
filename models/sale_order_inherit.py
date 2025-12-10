# -*- coding: utf-8 -*-
from odoo import models, fields

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    auto_transit_assign = fields.Boolean(
        string='Mandar Pedir', 
        default=True,
        help="Si está marcado, se considerará para la asignación automática en la Torre de Control "
             "cuando se genere la compra. Desmarcar si es stock puro."
    )