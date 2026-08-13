# -*- coding: utf-8 -*-
"""Candado global: una devolución de CLIENTE jamás entra a tránsito.

El tipo de retorno de las entregas (SOM: Recepciones) tiene como destino
por defecto SOM/TRANSIT — correcto para importaciones de Torre de
Control, no para devoluciones. El caso V/086 dejó 13 lotes en el limbo
de tránsito y quants negativos en Salida. sale_delivery_wizard ya
protege SU flujo; este candado cubre TODOS los caminos (incluido el
botón Devolver manual de la entrega) porque vive en el wizard nativo.
"""
from odoo import api, models


class StockReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'

    def _som_fix_transit_return_location(self):
        for wiz in self:
            picking = wiz.picking_id
            if not picking or picking.picking_type_id.code != 'outgoing':
                continue  # solo devoluciones de ENTREGAS a cliente
            loc = wiz.location_id
            if loc and not loc._som_is_transit():
                continue
            src = picking.location_id
            if src and not src._som_is_transit():
                wiz.location_id = src.id

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        wizards._som_fix_transit_return_location()
        return wizards

    def action_create_returns(self):
        # Segunda pasada por si el usuario (u otro módulo) re-escribió la
        # ubicación después del create.
        self._som_fix_transit_return_location()
        return super().action_create_returns()
