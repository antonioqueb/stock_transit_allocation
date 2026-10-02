# -*- coding: utf-8 -*-
"""Regenerar la liga del portal desde el embarque (1 oct 2026).

El proveedor completó el portal con errores y el material ya quedó validado
en tránsito. En lugar de corregirlo a mano en Odoo, el embarque se ECHA
ATRÁS en un solo paso:

  1. Todo lo que el embarque tiene en tránsito se devuelve al proveedor
     (una devolución por cada recepción que lo compone).
  2. Esas recepciones se liberan del embarque del portal y nacen las
     recepciones nuevas, sin validar, con lo ya capturado.
  3. El portal se reabre para el proveedor con toda su captura.
  4. El embarque del portal queda marcado 'requiere validación de Compras':
     al volver a completar, el PL se procesa pero NO pasa solo a tránsito.

Los lotes conservan su folio: al reprocesar el PL se reutilizan los mismos
lotes de la recepción devuelta (la serie S cuenta contenedores y no se
salta). Por eso las líneas del viaje —y sus asignaciones a pedidos— se
conservan; al validar de nuevo se actualizan y se retiran las de lotes que
ya no vengan en el PL corregido.
"""
import logging

from markupsafe import Markup

from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_RETURN_CTX = {
    'skip_backorder': True,
    'skip_sms': True,
    'skip_immediate_transfer': True,
    'skip_stock_whole_lot_removal': True,
    'skip_whole_lot_no_assign': True,
    'skip_date_sync': True,
}


class StockTransitVoyagePortalRegen(models.Model):
    _inherit = 'stock.transit.voyage'

    tc_portal_regen_pending = fields.Boolean(
        string='Liga regenerada: en corrección', copy=False, tracking=True,
        help='El embarque se regresó al proveedor para corregir su captura '
             'del portal. Sus placas no están en tránsito hasta que Compras '
             'valide la recepción corregida.',
    )

    # ------------------------------------------------------------------
    # Qué se va a echar atrás
    # ------------------------------------------------------------------

    def _tc_regen_pickings(self):
        """Recepciones a tránsito VALIDADAS que vienen del portal."""
        self.ensure_one()
        return self._tc_component_pickings().sudo().filtered(
            lambda p: p.state == 'done'
            and p.picking_type_code == 'incoming'
            and p.supplier_shipment_id
            and not ('return_id' in p._fields and p.return_id)
        ).sorted('id')

    def _tc_regen_open_physical_receptions(self):
        """Recepciones físicas del embarque aún sin validar."""
        self.ensure_one()
        Picking = self.env['stock.picking'].sudo()
        receptions = self.sudo().reception_picking_id
        if 'tc_reception_voyage_id' in Picking._fields:
            receptions |= Picking.search(
                [('tc_reception_voyage_id', '=', self.id)])
        return receptions.filtered(lambda p: p.state not in ('done', 'cancel'))

    def _tc_regen_check(self):
        """Valida que el embarque se pueda echar atrás. Devuelve las
        recepciones a devolver."""
        self.ensure_one()
        if self.custom_status in ('delivered', 'cancel'):
            raise UserError(_(
                'El embarque %s ya está entregado o cancelado; no se puede '
                'regenerar su liga.') % self.name)

        pickings = self._tc_regen_pickings()
        if not pickings:
            raise UserError(_(
                'El embarque %s no tiene recepciones a tránsito validadas que '
                'vengan del portal del proveedor. Si la recepción sigue '
                'abierta, el proveedor puede corregir el portal sin regenerar '
                'nada.') % self.name)

        # Todo lo recibido debe seguir en tránsito: si algo ya entró al
        # almacén (recepción física) o salió, echar atrás dejaría el
        # embarque a medias.
        Quant = self.env['stock.quant'].sudo()
        moved = []
        for pick in pickings:
            received = {}
            for ml in pick.move_line_ids:
                if ml.lot_id:
                    received[ml.lot_id] = (
                        received.get(ml.lot_id, 0.0) + (ml.quantity or 0.0))
            if not received:
                continue
            in_transit = {}
            for quant in Quant.search([
                ('lot_id', 'in', [lot.id for lot in received]),
                ('location_id', 'child_of', pick.location_dest_id.id),
                ('quantity', '>', 0),
            ]):
                in_transit[quant.lot_id] = (
                    in_transit.get(quant.lot_id, 0.0) + quant.quantity)
            for lot, qty in received.items():
                if in_transit.get(lot, 0.0) < qty - 0.005:
                    moved.append(lot.name)
        if moved:
            detail = ', '.join(moved[:15])
            if len(moved) > 15:
                detail += _(' y %d más') % (len(moved) - 15)
            raise UserError(_(
                'No se puede regenerar la liga: %(n)s lote(s) del embarque ya '
                'no están completos en tránsito (ya se recibieron en almacén, '
                'se devolvieron o salieron): %(detail)s.'
            ) % {'n': len(moved), 'detail': detail})
        return pickings

    def _tc_regen_summary(self):
        """Resumen para la confirmación: qué se devuelve y a quién afecta."""
        self.ensure_one()
        pickings = self._tc_regen_check()
        lots = pickings.mapped('move_line_ids.lot_id')
        qty = sum(pickings.mapped('move_line_ids.quantity'))
        assigned = self.sudo().line_ids.filtered(
            lambda l: l.order_id and l.lot_id in lots)
        orders = assigned.mapped('order_id')
        return {
            'pickings': pickings,
            'lot_count': len(lots),
            'qty': qty,
            'orders': orders,
            'assigned_count': len(assigned),
            'open_receptions': self._tc_regen_open_physical_receptions(),
        }

    def action_tc_open_regen_portal_wizard(self):
        self.ensure_one()
        self._tc_regen_assert_access()
        info = self._tc_regen_summary()
        wizard = self.env['tc.portal.regen.confirm'].create({
            'voyage_id': self.id,
            'picking_names': ', '.join(info['pickings'].mapped('name')),
            'lot_count': info['lot_count'],
            'qty': info['qty'],
            'order_names': ', '.join(info['orders'].mapped('name')) or False,
            'assigned_count': info['assigned_count'],
            'reception_names': ', '.join(
                info['open_receptions'].mapped('name')) or False,
        })
        return {
            'name': _('Regenerar liga del portal'),
            'type': 'ir.actions.act_window',
            'res_model': 'tc.portal.regen.confirm',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _tc_regen_assert_access(self):
        user = self.env.user
        if not (user.has_group('purchase.group_purchase_user')
                or user.has_group('stock_transit_allocation.group_transit_manager')
                or user.has_group('base.group_system')):
            raise UserError(_(
                'Solo Compras puede regenerar la liga del portal de un '
                'embarque.'))

    # ------------------------------------------------------------------
    # Echar atrás
    # ------------------------------------------------------------------

    def action_tc_regenerate_portal_link(self):
        """Devuelve el tránsito del embarque al proveedor, crea las
        recepciones nuevas y reabre el portal con lo ya capturado. Todo o
        nada: cualquier fallo revierte la operación completa."""
        self.ensure_one()
        self._tc_regen_assert_access()
        voyage = self.sudo()
        pickings = voyage._tc_regen_check()
        Picking = self.env['stock.picking'].sudo()

        # La recepción física abierta reserva los quants de tránsito:
        # se cancela (no tiene nada recibido; el candado de arriba ya lo
        # garantizó) y se regenera cuando el embarque vuelva a validarse.
        open_receptions = voyage._tc_regen_open_physical_receptions()
        if open_receptions:
            # Sin el cierre automático: cancelar la recepción abierta de un
            # embarque en 'Recepción' lo daría por entregado.
            open_receptions.with_context(
                tc_skip_auto_close_on_cancel=True).action_cancel()
            still_open = open_receptions.filtered(lambda p: p.state != 'cancel')
            if still_open:
                raise UserError(_(
                    'No se pudo cancelar la recepción física %s; cancélala a '
                    'mano y vuelve a intentar.'
                ) % ', '.join(still_open.mapped('name')))
            if voyage.reception_picking_id in open_receptions:
                voyage.write({'reception_picking_id': False})

        shipments = pickings.mapped('supplier_shipment_id')
        returns = Picking
        for pick in pickings:
            shipment = pick.supplier_shipment_id
            lots = pick.move_line_ids.mapped('lot_id')

            action = pick.action_som_return_all_transit()
            ret = Picking.browse(action['res_id'])
            ret.move_ids.write({'picked': True})
            res = ret.with_company(ret.company_id).with_context(
                **_RETURN_CTX).button_validate()
            if res is not True and isinstance(res, dict):
                raise UserError(_(
                    'La devolución de %(pick)s pidió intervención manual '
                    '(%(model)s); no se regeneró nada.'
                ) % {'pick': pick.name, 'model': res.get('res_model') or 'wizard'})
            if ret.state != 'done':
                raise UserError(_(
                    'La devolución de %s no quedó validada; no se regeneró '
                    'nada.') % pick.name)
            returns |= ret

            shipment.write({
                'som_requires_purchase_validation': True,
                'som_regen_count': (shipment.som_regen_count or 0) + 1,
                'som_regen_lot_ids': [(4, lot.id) for lot in lots],
            })
            # Libera la recepción devuelta y crea la nueva con las filas
            # del PL (sin validar).
            pick.action_som_release_shipment_link()

        # El viaje queda sellado en el embarque del portal: las recepciones
        # nuevas lo alimentarán al validarse.
        if 'voyage_id' in shipments._fields:
            shipments.filtered(lambda s: not s.voyage_id).write(
                {'voyage_id': voyage.id})

        # Portal reabierto para el proveedor.
        proformas = shipments.mapped('proforma_id')
        proformas.filtered(lambda p: p.status == 'complete').write(
            {'status': 'partial'})

        new_pickings = Picking.search([
            ('supplier_shipment_id', 'in', shipments.ids),
            ('state', 'not in', ('done', 'cancel')),
        ])
        links = []
        for po in proformas.mapped('purchase_id'):
            try:
                access = po._get_or_create_supplier_access()
                if access and access.portal_url:
                    links.append((po.name, access.portal_url))
            except Exception:
                _logger.exception(
                    '[TC_REGEN] No se pudo obtener la liga de %s.', po.name)

        voyage.write({'tc_portal_regen_pending': True})

        body = Markup(
            '↩️ <b>Liga del portal regenerada.</b> El embarque se echó atrás '
            'para que el proveedor corrija su captura.<br/>'
            'Devoluciones validadas: %s.<br/>'
            'Recepciones liberadas: %s.<br/>'
            'Recepciones nuevas (sin validar): %s.<br/>'
        ) % (
            ', '.join(returns.mapped('name')),
            ', '.join(pickings.mapped('name')),
            ', '.join(new_pickings.mapped('name')) or _('se crearán al guardar el portal'),
        )
        if open_receptions:
            body += Markup('Recepción física cancelada: %s.<br/>') % ', '.join(
                open_receptions.mapped('name'))
        for po_name, url in links:
            body += Markup('Liga de %s: <a href="%s" target="_blank">%s</a><br/>') % (
                po_name, url, url)
        body += Markup(
            'Los lotes conservan su folio y las asignaciones a pedidos se '
            'mantienen. Cuando el proveedor vuelva a completar, el PL se '
            'procesa y <b>Compras valida</b> la recepción para que el '
            'material regrese a tránsito.')
        voyage.message_post(body=body)
        for po in proformas.mapped('purchase_id'):
            po.sudo().message_post(body=body)

        _logger.info(
            '[TC_REGEN] %s: devueltas %s, nuevas %s.', voyage.name,
            pickings.mapped('name'), new_pickings.mapped('name'))
        return True

    # ------------------------------------------------------------------
    # Regreso a tránsito tras la corrección
    # ------------------------------------------------------------------

    def _tc_load_lines_from_picking(self, picking):
        res = super()._tc_load_lines_from_picking(picking)
        for voyage in self:
            if voyage.tc_portal_regen_pending:
                voyage._tc_finish_portal_regen()
        return res

    def _tc_finish_portal_regen(self):
        """Todas las recepciones corregidas ya están validadas: el embarque
        vuelve a la normalidad y se retiran las líneas de lotes que el
        proveedor quitó del PL."""
        self.ensure_one()
        voyage = self.sudo()
        expected = voyage._tc_expected_component_pickings().filtered(
            lambda p: any(
                (m.product_uom_qty or 0.0) > 0 and m.state != 'cancel'
                for m in p.move_ids))
        if not expected or any(p.state != 'done' for p in expected):
            return
        live_lots = expected.mapped('move_line_ids.lot_id')
        stale = voyage.line_ids.filtered(
            lambda l: l.lot_id and l.lot_id not in live_lots)
        note = Markup(
            '✅ <b>Embarque corregido y de vuelta en tránsito</b> '
            '(recepciones: %s).') % ', '.join(expected.mapped('name'))
        if stale:
            released = stale.filtered('order_id')
            detail = ', '.join(
                '%s (%s)' % (l.lot_id.name, l.order_id.name) for l in released[:30])
            names = ', '.join(stale.mapped('lot_id.name')[:40])
            try:
                with self.env.cr.savepoint():
                    stale.unlink()
                note += Markup(
                    '<br/>Lotes que ya no vienen en el PL corregido, retirados '
                    'del embarque: %s.') % names
                if released:
                    note += Markup(
                        '<br/>⚠️ Estaban asignados a pedidos y quedaron '
                        'liberados: %s.') % detail
            except Exception:
                _logger.exception(
                    '[TC_REGEN] %s: no se pudieron retirar las líneas '
                    'obsoletas.', voyage.name)
                note += Markup(
                    '<br/>⚠️ Estos lotes ya no vienen en el PL corregido y '
                    'no se pudieron retirar solos; retíralos a mano: %s.'
                ) % names
        voyage.write({'tc_portal_regen_pending': False})
        voyage.message_post(body=note)
