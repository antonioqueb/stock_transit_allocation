# -*- coding: utf-8 -*-
"""To Be Loading — tablero de pedidos de compra pendientes por COMPLETAR.

Un pedido está "por completar" mientras le falte cualquiera de:
PI capturada, embarque del portal, contenedores, packing list,
worksheet o recepción validada. Cada tarjeta abre su pedido.
"""
import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class ToBeLoadingLogic(models.AbstractModel):
    _name = 'to.be.loading.logic'
    _description = 'Lógica para el Tablero To Be Loading'

    @api.model
    def get_data(self):
        Shipment = (self.env['supplier.shipment'].sudo()
                    if 'supplier.shipment' in self.env else None)
        Header = (self.env['supplier.proforma.header'].sudo()
                  if 'supplier.proforma.header' in self.env else None)
        Voyage = (self.env['stock.transit.voyage'].sudo()
                  if 'stock.transit.voyage' in self.env else None)

        pos = self.env['purchase.order'].sudo().search(
            [('state', '=', 'purchase')], order='date_approve asc')

        rows = []
        for po in pos:
            lines = po.order_line.filtered(
                lambda l: l.product_id
                and l.product_id.type in ('product', 'consu'))
            qty_ordered = sum(lines.mapped('product_qty'))
            qty_received = sum(lines.mapped('qty_received'))
            pct_received = (
                round(qty_received / qty_ordered * 100, 1)
                if qty_ordered else 0.0)

            incoming = po.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'incoming')
            recepcion_validada = any(p.state == 'done' for p in incoming)

            shipments = (Shipment.search([('purchase_id', '=', po.id)])
                         if Shipment else self.env['purchase.order'])
            containers = 0
            pl_importado = False
            ws_importado = False
            for sh in shipments:
                containers += len(sh.container_ids.filtered(
                    lambda c: c.container_number))
            for p in incoming:
                if getattr(p, 'packing_list_imported', False):
                    pl_importado = True
                if getattr(p, 'worksheet_imported', False):
                    ws_importado = True

            captura_pct = 0.0
            if Header:
                header = Header.search(
                    [('purchase_id', '=', po.id)], limit=1)
                if header:
                    try:
                        prog = header._portal_progress()
                        if isinstance(prog, dict):
                            prog = (prog.get('overall')
                                    or prog.get('percent') or 0)
                        captura_pct = round(float(prog or 0), 1)
                    except Exception:
                        _logger.debug(
                            '[TO BE LOADING] progreso portal OC %s',
                            po.name, exc_info=True)

            voyage_status = ''
            if Voyage:
                voyage = Voyage.search([
                    ('purchase_id', '=', po.id),
                    ('custom_status', 'not in', ('cancel',)),
                ], order='id desc', limit=1)
                if voyage:
                    voyage_status = dict(
                        voyage._fields['custom_status']
                        ._description_selection(self.env)
                    ).get(voyage.custom_status, voyage.custom_status)

            checklist = [
                {'key': 'pi', 'label': 'PI', 'ok': bool(po.partner_ref)},
                {'key': 'embarque', 'label': 'Embarque',
                 'ok': bool(shipments)},
                {'key': 'contenedores', 'label': 'Contenedores',
                 'ok': containers > 0},
                {'key': 'pl', 'label': 'Packing List', 'ok': pl_importado},
                {'key': 'ws', 'label': 'Worksheet', 'ok': ws_importado},
                {'key': 'recepcion', 'label': 'Recepción',
                 'ok': recepcion_validada},
            ]
            missing = [c['label'] for c in checklist if not c['ok']]

            # Completado del todo: recibido al 100 y sin faltantes → fuera
            # del tablero (esto es una bandeja de pendientes, no un archivo).
            if not missing and pct_received >= 99.99:
                continue

            rows.append({
                'po_id': po.id,
                'name': po.name,
                'partner': po.partner_id.name or '',
                'pi': po.partner_ref or '',
                'date': str(po.date_approve or '')[:10],
                'amount_total': po.amount_total,
                'currency': po.currency_id.name or 'MXN',
                'qty_ordered': round(qty_ordered, 1),
                'qty_received': round(qty_received, 1),
                'pct_received': pct_received,
                'captura_pct': captura_pct,
                'containers': containers,
                'voyage_status': voyage_status,
                'checklist': checklist,
                'missing': missing,
                'missing_count': len(missing),
            })

        # Los más incompletos primero; a igualdad, el más viejo primero.
        rows.sort(key=lambda r: (-r['missing_count'], r['date'] or ''))
        return {
            'rows': rows,
            'total': len(rows),
        }
