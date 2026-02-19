# -*- coding: utf-8 -*-
"""
Parche al modelo stock.transit.line para agregar el campo dummy
que sirve de gancho para el widget transit_propagate_btn.

Este campo NO se guarda en base de datos (compute sin store, o simplemente
un campo Boolean compute). Su único propósito es ser el "host" del widget
de propagación en la vista de lista editable.
"""
from odoo import models, fields


class StockTransitLinePropagate(models.Model):
    _inherit = 'stock.transit.line'

    # Campo dummy para hospedar el widget de propagación.
    # Es un Boolean compute (siempre False) — no se guarda, no afecta lógica.
    # El widget transit_propagate_btn lo ignora como valor y solo usa
    # props.record para navegar el form padre y propagar.
    propagate_btn = fields.Boolean(
        string='Propagar',
        compute='_compute_propagate_btn',
        store=False,
        help="Columna de botones para propagar cliente/pedido hacia abajo."
    )

    def _compute_propagate_btn(self):
        for rec in self:
            rec.propagate_btn = False