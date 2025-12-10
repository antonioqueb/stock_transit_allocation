# -*- coding: utf-8 -*-
from odoo import models, fields

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    auto_transit_assign = fields.Boolean(
        string='Asignar en Tránsito', 
        default=True,
        help="Si está marcado, cuando se genere la compra y posterior recepción, "
             "la mercancía quedará asignada a este cliente automáticamente en el módulo de Tránsito."
    )