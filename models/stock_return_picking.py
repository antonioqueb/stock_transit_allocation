# -*- coding: utf-8 -*-
"""Candado global: una devolución de CLIENTE jamás entra a tránsito.

El tipo de retorno de las entregas (SOM: Recepciones) tiene como destino
por defecto SOM/TRANSIT — correcto para importaciones de Torre de
Control, no para devoluciones. El caso V/086 dejó 13 lotes en el limbo
de tránsito y quants negativos en Salida.

Odoo 19 ELIMINÓ location_id del wizard stock.return.picking (leerlo
truena con AttributeError): el destino nace del tipo de retorno al
crear el picking. Por eso el candado se aplica SOBRE EL PICKING CREADO,
después de action_create_returns — cubre el botón Devolver manual y el
wizard de sale_delivery_wizard por igual.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class StockReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'

    def _som_fix_transit_return_wizard(self):
        """Compatibilidad con builds donde el wizard SÍ expone location_id."""
        for wiz in self:
            if 'location_id' not in wiz._fields:
                continue
            picking = wiz.picking_id
            if not picking or picking.picking_type_id.code != 'outgoing':
                continue
            loc = wiz.location_id
            src = picking.location_id
            if (src and not src._som_is_transit()
                    and (not loc or loc._som_is_transit())):
                wiz.location_id = src.id

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        wizards._som_fix_transit_return_wizard()
        return wizards

    def action_create_returns(self):
        res = super().action_create_returns()
        self._som_fix_created_return_destination(res)
        return res

    def _som_fix_created_return_destination(self, res):
        """Re-enruta al ORIGEN de la entrega cualquier devolución de
        cliente cuyo destino haya nacido apuntando a tránsito."""
        Picking = self.env['stock.picking']
        returns = Picking.browse()
        if isinstance(res, dict) and res.get('res_id'):
            returns |= Picking.browse(res['res_id'])
        for wiz in self:
            origin = wiz.picking_id
            if not origin or origin.picking_type_id.code != 'outgoing':
                continue
            candidates = returns
            if not candidates and 'return_id' in Picking._fields:
                candidates = Picking.search([
                    ('return_id', '=', origin.id),
                    ('state', 'not in', ('done', 'cancel')),
                ], order='id desc', limit=1)
            for ret in candidates:
                dest = ret.location_dest_id
                src = origin.location_id
                if not dest or not src:
                    continue
                if dest._som_is_transit() and not src._som_is_transit():
                    ret.write({'location_dest_id': src.id})
                    ret.move_ids.filtered(
                        lambda m: m.state not in ('done', 'cancel')
                    ).write({'location_dest_id': src.id})
                    ret.move_line_ids.filtered(
                        lambda ml: ml.state not in ('done', 'cancel')
                    ).write({'location_dest_id': src.id})
                    _logger.info(
                        '[SOM RETURN] Devolución %s re-enrutada de tránsito '
                        'a %s (origen %s).',
                        ret.name, src.complete_name, origin.name)
