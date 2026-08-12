# -*- coding: utf-8 -*-
"""Open PO — pedidos con material YA EMBARCADO pero DIFERENTE a lo
solicitado, agrupados por PROFORMA (PI).

Por línea de OC (campos de stock_lot_packing_import):
- solicitado = x_qty_solicitada_original (congelada al procesar el primer
  Packing List) o, si aún no se congela, la cantidad de la línea.
- embarcado  = x_qty_embarcada (lo declarado en el Packing List, que es
  lo que se le paga al proveedor).

Acciones: abrir el pedido, o CERRAR LA DEMANDA por producto (aceptar la
diferencia: la cantidad de la línea se ajusta a lo embarcado, la
recepción pendiente se alinea y la línea sale del tablero).
"""
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PurchaseOrderLineOpenPO(models.Model):
    _inherit = 'purchase.order.line'

    x_open_po_closed = fields.Boolean(
        string='Demanda cerrada (Open PO)',
        copy=False,
        help='La diferencia solicitado vs embarcado fue aceptada desde el '
             'tablero Open PO: la línea ya no aparece como discrepancia.',
    )


class ToBeLoadingLogic(models.AbstractModel):
    _name = 'to.be.loading.logic'
    _description = 'Lógica para el Tablero Open PO'

    @api.model
    def get_data(self):
        pos = self.env['purchase.order'].sudo().search(
            [('state', 'in', ('purchase', 'done'))],
            order='date_approve asc')

        groups = []
        for po in pos:
            lines = po.order_line.filtered(
                lambda l: l.product_id
                and l.product_id.type in ('product', 'consu')
                and not l.x_open_po_closed)
            if not lines:
                continue

            products = []
            for line in lines:
                req = float(
                    getattr(line, 'x_qty_solicitada_original', 0.0) or 0.0
                ) or float(line.product_qty or 0.0)
                shipped = float(
                    getattr(line, 'x_qty_embarcada', 0.0) or 0.0)
                # SOLO FALTANTES: el tablero existe para perseguir material
                # embarcado POR DEBAJO de lo solicitado. Los excedentes
                # (embarcado > solicitado) no son deuda del proveedor y no
                # se muestran.
                if shipped > 0 and (req - shipped) > 0.01:
                    products.append({
                        'line_id': line.id,
                        'product': line.product_id.display_name,
                        'uom': line.product_uom_id.name or 'm²',
                        'solicitado': round(req, 2),
                        'embarcado': round(shipped, 2),
                        'diff': round(shipped - req, 2),
                    })

            if not products:
                continue

            # Totales SOLO de las líneas mostradas: sumar toda la OC hacía
            # que las píldoras de la cabecera no cuadraran con las filas.
            solicitado = sum(p['solicitado'] for p in products)
            embarcado = sum(p['embarcado'] for p in products)

            groups.append({
                'po_id': po.id,
                'po_name': po.name,
                'pi': po.partner_ref or '',
                'partner': po.partner_id.name or '',
                'date': str(po.date_approve or '')[:10],
                'solicitado': round(solicitado, 2),
                'embarcado': round(embarcado, 2),
                'diff': round(embarcado - solicitado, 2),
                'products': products,
            })

        groups.sort(key=lambda g: -abs(g['diff']))
        return {
            'groups': groups,
            'total_groups': len(groups),
            'total_products': sum(len(g['products']) for g in groups),
        }

    @api.model
    def close_demand(self, line_id):
        """Acepta la diferencia de UNA línea: la cantidad de la OC se
        ajusta a lo realmente embarcado (el core alinea la recepción
        pendiente) y la línea sale del tablero. Queda nota en la OC."""
        line = self.env['purchase.order.line'].browse(int(line_id))
        if not line.exists():
            raise UserError(_('La línea ya no existe.'))
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise UserError(_('Requiere permiso de Compras.'))

        line = line.sudo()
        shipped = float(getattr(line, 'x_qty_embarcada', 0.0) or 0.0)
        req = float(
            getattr(line, 'x_qty_solicitada_original', 0.0) or 0.0
        ) or float(line.product_qty or 0.0)

        vals = {'x_open_po_closed': True}
        if shipped > 0 and abs((line.product_qty or 0.0) - shipped) > 0.001:
            vals['product_qty'] = shipped
        line.write(vals)
        line.order_id.message_post(body=_(
            'Open PO — demanda CERRADA para %(prod)s: solicitado '
            '%(req).2f, embarcado %(shp).2f (dif. %(diff)+.2f). La '
            'cantidad de la línea se ajustó a lo embarcado.',
            prod=line.product_id.display_name,
            req=req, shp=shipped, diff=shipped - req,
        ))
        _logger.info(
            '[OPEN PO] %s cerró demanda de la línea %s (%s): %s → %s',
            self.env.user.login, line.id, line.order_id.name,
            req, shipped)
        return True
