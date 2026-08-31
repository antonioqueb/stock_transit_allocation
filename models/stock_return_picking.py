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

from markupsafe import Markup

from odoo import api, models, _
from odoo.exceptions import UserError

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


class StockPickingReturnRescue(models.Model):
    _inherit = 'stock.picking'

    def action_som_rescue_return_from_transit(self):
        """Rescata el material de una devolución de cliente que se validó
        con destino SOM/TRANSIT (retorno nativo previo al candado): crea un
        traslado interno Tránsito → almacén con los lotes que siguen
        varados ahí, listo para que el usuario lo valide. Después de
        validarlo, el material vuelve a ser entregable."""
        self.ensure_one()
        pk = self.sudo()

        is_return = bool(
            ('return_id' in pk._fields and pk.return_id)
            or (pk.origin or '').lower().startswith(
                ('devolución de', 'devolucion de', 'return of'))
        )
        if not is_return:
            raise UserError(_('Esta operación no es una devolución.'))
        if pk.state != 'done':
            raise UserError(_(
                'La devolución aún no está validada: corrige su ubicación '
                'de destino directamente en lugar de rescatar.'))

        dest = pk.location_dest_id
        if not dest or not dest._som_is_transit():
            raise UserError(_(
                'Esta devolución no dejó material en tránsito; '
                'no hay nada que rescatar.'))

        origin_pick = (
            pk.return_id
            if ('return_id' in pk._fields and pk.return_id)
            else self.env['stock.picking'].sudo()
        )
        target_loc = origin_pick.location_id if origin_pick else False
        if not target_loc or target_loc._som_is_transit():
            wh = (
                (origin_pick.picking_type_id.warehouse_id if origin_pick else False)
                or pk.picking_type_id.warehouse_id
            )
            target_loc = wh.lot_stock_id if wh else False
        if not target_loc:
            raise UserError(_(
                'No se pudo determinar la ubicación de almacén destino '
                'del rescate.'))

        lots = pk.move_line_ids.mapped('lot_id')
        if not lots:
            raise UserError(_('La devolución no tiene lotes registrados.'))

        quants = self.env['stock.quant'].sudo().search([
            ('lot_id', 'in', lots.ids),
            ('location_id', 'child_of', dest.id),
            ('quantity', '>', 0),
        ])
        if not quants:
            raise UserError(_(
                'Ya no hay existencias de esos lotes en tránsito '
                '(probablemente ya fueron rescatados).'))

        int_type = (
            origin_pick.picking_type_id.warehouse_id.int_type_id
            if origin_pick and origin_pick.picking_type_id.warehouse_id
            else False
        )
        if not int_type:
            int_type = self.env['stock.picking.type'].sudo().search([
                ('code', '=', 'internal'),
                ('company_id', '=', pk.company_id.id),
            ], limit=1)
        if not int_type:
            raise UserError(_('No hay tipo de operación interna disponible.'))

        MoveLine = self.env['stock.move.line'].sudo()
        qty_key = 'quantity' if 'quantity' in MoveLine._fields else 'qty_done'

        rescue = self.env['stock.picking'].sudo().with_company(pk.company_id).create({
            'picking_type_id': int_type.id,
            'location_id': dest.id,
            'location_dest_id': target_loc.id,
            'origin': _('Rescate devolución %s') % pk.name,
            'company_id': pk.company_id.id,
        })

        by_product = {}
        for quant in quants:
            by_product.setdefault(quant.product_id, []).append(quant)

        for product, product_quants in by_product.items():
            move = self.env['stock.move'].sudo().create({
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': sum(q.quantity for q in product_quants),
                'picking_id': rescue.id,
                'location_id': dest.id,
                'location_dest_id': target_loc.id,
                'company_id': pk.company_id.id,
            })
            for quant in product_quants:
                MoveLine.create({
                    'move_id': move.id,
                    'picking_id': rescue.id,
                    'company_id': pk.company_id.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'lot_id': quant.lot_id.id,
                    'location_id': quant.location_id.id,
                    'location_dest_id': target_loc.id,
                    qty_key: quant.quantity,
                })

        body = Markup(
            '🛟 <b>Rescate de devolución en tránsito</b>: se preparó el '
            'traslado interno %s con %s lote(s) hacia %s. Valídalo para '
            'que el material vuelva a ser entregable.'
        ) % (rescue.name, len(quants), target_loc.complete_name)
        pk.message_post(body=body)
        rescue.message_post(body=Markup(
            '🛟 Creado por rescate de la devolución %s (material varado '
            'en tránsito por retorno nativo).') % pk.name)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': rescue.id,
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_som_return_all_transit(self):
        """Devolución TOTAL al proveedor de una recepción validada a
        tránsito: arma el retorno con TODOS los lotes de la recepción que
        sigan con existencia en tránsito — sin selectores. Los selectores
        del wizard nativo aquí no sirven: stock_whole_lot_removal re-asigna
        lotes completos con su propia estrategia y pisa la elección manual;
        este flujo escribe las move lines exactas y desactiva esa
        reservación automática. El usuario revisa y valida."""
        self.ensure_one()
        pk = self.sudo()

        if pk.picking_type_code != 'incoming':
            raise UserError(_('Solo aplica a recepciones.'))
        if pk.state != 'done':
            raise UserError(_('La recepción aún no está validada; cancélala '
                              'o ajústala directamente.'))

        source = pk.location_dest_id
        if not source or not source._som_is_transit():
            raise UserError(_(
                'Esta recepción no dejó el material en tránsito; usa el '
                'flujo de devolución normal.'))

        # Recibido por lote en ESTA recepción.
        received = {}
        for ml in pk.move_line_ids:
            if not ml.lot_id:
                continue
            received[ml.lot_id.id] = (
                received.get(ml.lot_id.id, 0.0) + (ml.quantity or 0.0))
        if not received:
            raise UserError(_('La recepción no tiene lotes registrados.'))

        quants = self.env['stock.quant'].sudo().search([
            ('lot_id', 'in', list(received.keys())),
            ('location_id', 'child_of', source.id),
            ('quantity', '>', 0),
        ])
        if not quants:
            raise UserError(_(
                'Ya no queda existencia en tránsito de los lotes de esta '
                'recepción (quizá ya se devolvieron o se recibieron).'))

        # property_stock_supplier es company_dependent: se lee con la
        # compañía de la recepción.
        supplier_loc = (
            pk.partner_id.with_company(pk.company_id).property_stock_supplier
            if pk.partner_id else False
        ) or self.env.ref('stock.stock_location_suppliers',
                          raise_if_not_found=False)
        if not supplier_loc:
            raise UserError(_('No se encontró la ubicación de proveedores.'))

        ret_type = pk.picking_type_id.return_picking_type_id \
            or pk.picking_type_id
        MoveLine = self.env['stock.move.line'].sudo()
        qty_key = 'quantity' if 'quantity' in MoveLine._fields else 'qty_done'

        ret_vals = {
            'picking_type_id': ret_type.id,
            'partner_id': pk.partner_id.id if pk.partner_id else False,
            'location_id': source.id,
            'location_dest_id': supplier_loc.id,
            'origin': _('Devolución de %s') % pk.name,
            'company_id': pk.company_id.id,
        }
        if 'return_id' in pk._fields:
            ret_vals['return_id'] = pk.id
        ctx = {'skip_whole_lot_no_assign': True, 'skip_date_sync': True}
        ret = self.env['stock.picking'].sudo().with_context(**ctx).with_company(
            pk.company_id).create(ret_vals)

        by_product = {}
        for quant in quants:
            by_product.setdefault(quant.product_id, []).append(quant)

        total_lots = 0
        for product, product_quants in by_product.items():
            move_qty = 0.0
            line_plan = []
            for quant in product_quants:
                # Se devuelve lo que sigue en tránsito, capado a lo recibido.
                take = min(quant.quantity,
                           received.get(quant.lot_id.id, quant.quantity))
                if take <= 0:
                    continue
                line_plan.append((quant, take))
                move_qty += take
            if not line_plan:
                continue
            move = self.env['stock.move'].sudo().with_context(**ctx).create({
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': move_qty,
                'picking_id': ret.id,
                'location_id': source.id,
                'location_dest_id': supplier_loc.id,
                'company_id': pk.company_id.id,
            })
            for quant, take in line_plan:
                ml_vals = {
                    'move_id': move.id,
                    'picking_id': ret.id,
                    'company_id': pk.company_id.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'lot_id': quant.lot_id.id,
                    'location_id': quant.location_id.id,
                    'location_dest_id': supplier_loc.id,
                    qty_key: take,
                }
                if 'picked' in MoveLine._fields:
                    ml_vals['picked'] = True
                MoveLine.with_context(**ctx).create(ml_vals)
                total_lots += 1

        body = Markup(
            '↩️ <b>Devolución total del tránsito</b>: se preparó %s con '
            '%s lote(s) de regreso al proveedor. Revísala y valídala.'
        ) % (ret.name, total_lots)
        pk.message_post(body=body)
        ret.message_post(body=Markup(
            '↩️ Devolución TOTAL generada desde la recepción %s '
            '(todos los lotes que seguían en tránsito).') % pk.name)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': ret.id,
            'views': [[False, 'form']],
            'target': 'current',
        }
