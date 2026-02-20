# -*- coding: utf-8 -*-
"""
Parche a stock.transit.line para exponer x_bloque y x_atado
desde el stock.lot correspondiente, junto con los campos existentes
x_grosor, x_alto, x_ancho.
"""
from odoo import models, fields


class StockTransitLineBlockAtado(models.Model):
    _inherit = 'stock.transit.line'

    x_bloque = fields.Char(
        related='lot_id.x_bloque',
        string='Bloque',
        readonly=True,
        store=True,
    )

    x_atado = fields.Char(
        related='lot_id.x_atado',
        string='Atado',
        readonly=True,
        store=True,
    )