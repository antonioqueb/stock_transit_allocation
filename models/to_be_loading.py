# -*- coding: utf-8 -*-
"""To Load — lista de pedidos con material YA EMBARCADO pero DIFERENTE
a lo solicitado.

Fuente de verdad por línea de OC (campos de stock_lot_packing_import):
- solicitado = x_qty_solicitada_original (congelada al procesar el primer
  Packing List) o, si aún no se congela, la cantidad de la línea.
- embarcado  = x_qty_embarcada (lo declarado en el Packing List, que es
  lo que se le paga al proveedor).

Solo entran pedidos con algo embarcado (> 0) cuya suma embarcada difiere
de la solicitada. Cada fila abre su pedido.
"""
import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class ToBeLoadingLogic(models.AbstractModel):
    _name = 'to.be.loading.logic'
    _description = 'Lógica para el Tablero To Load'

    @api.model
    def get_data(self):
        pos = self.env['purchase.order'].sudo().search(
            [('state', 'in', ('purchase', 'done'))],
            order='date_approve asc')

        rows = []
        for po in pos:
            lines = po.order_line.filtered(
                lambda l: l.product_id
                and l.product_id.type in ('product', 'consu'))
            if not lines:
                continue

            solicitado = embarcado = 0.0
            diff_lines = []
            for line in lines:
                req = float(
                    getattr(line, 'x_qty_solicitada_original', 0.0) or 0.0
                ) or float(line.product_qty or 0.0)
                shipped = float(
                    getattr(line, 'x_qty_embarcada', 0.0) or 0.0)
                solicitado += req
                embarcado += shipped
                if shipped > 0 and abs(shipped - req) > 0.01:
                    diff_lines.append({
                        'product': line.product_id.display_name,
                        'solicitado': round(req, 2),
                        'embarcado': round(shipped, 2),
                        'diff': round(shipped - req, 2),
                    })

            # Regla del tablero: algo YA embarcado, pero distinto a lo
            # solicitado (por línea o en el total).
            if embarcado <= 0:
                continue
            if abs(embarcado - solicitado) <= 0.01 and not diff_lines:
                continue

            rows.append({
                'po_id': po.id,
                'name': po.name,
                'partner': po.partner_id.name or '',
                'pi': po.partner_ref or '',
                'date': str(po.date_approve or '')[:10],
                'solicitado': round(solicitado, 2),
                'embarcado': round(embarcado, 2),
                'diff': round(embarcado - solicitado, 2),
                'diff_lines': diff_lines,
                'diff_count': len(diff_lines),
            })

        # Mayor discrepancia absoluta primero.
        rows.sort(key=lambda r: -abs(r['diff']))
        return {
            'rows': rows,
            'total': len(rows),
        }
