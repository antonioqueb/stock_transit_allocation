# -*- coding: utf-8 -*-
"""Métricas de Torre de Control del embarque: flujo de material y cobertura.

Responde, con datos reales y sin N+1:
- ¿Cuánto se COMPRÓ? (líneas activas de la OC vinculada, solo productos del
  embarque, convertido a la UoM del producto)
- ¿Cuánto se EMBARCÓ? (líneas del viaje)
- ¿Cuánto se RECIBIÓ? (movimientos validados de la recepción física)
- Diferencias con etiqueta semántica (pendiente por embarcar / exceso).
- Cobertura comercial (asignado / libre / %).

Alcance documentado: "Comprado" = líneas no canceladas de la OC vinculada a
este viaje para los productos presentes en él. Si un producto del viaje no
está en la OC, se reporta como "sin vínculo con OC" (no se inventa un cero).
"""
from odoo import models, fields, api

import logging

_logger = logging.getLogger(__name__)


class StockTransitVoyageMetrics(models.Model):
    _inherit = 'stock.transit.voyage'

    tc_purchased_qty = fields.Float(
        string='Comprado',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
        help='Suma de las líneas activas de la OC vinculada a este embarque, '
             'solo para los productos presentes en él (UoM convertida).',
    )
    tc_received_qty = fields.Float(
        string='Recibido',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
        help='Cantidad validada en la recepción física del embarque.',
    )
    tc_pending_ship_qty = fields.Float(
        string='Pendiente por embarcar',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
    )
    tc_excess_ship_qty = fields.Float(
        string='Exceso embarcado',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
    )
    tc_pending_receive_qty = fields.Float(
        string='Pendiente por recibir',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
    )
    tc_free_qty = fields.Float(
        string='Sin asignar',
        compute='_compute_tc_material_flow',
        digits='Product Unit of Measure',
    )

    def _tc_purchased_map(self):
        """{product_id: qty comprada} de la OC vinculada (UoM del producto).

        Una consulta por viaje; ignora líneas canceladas/display y solo
        considera productos presentes en el viaje.
        """
        self.ensure_one()
        result = {}
        po = self.purchase_id
        if not po or po.state == 'cancel':
            return result

        voyage_products = set(self.line_ids.mapped('product_id').ids)
        if not voyage_products:
            return result

        for pl in po.order_line:
            if pl.display_type or not pl.product_id:
                continue
            if pl.product_id.id not in voyage_products:
                continue
            qty = pl.product_qty or 0.0
            # Conversión de UoM de la línea de compra → UoM del producto.
            if pl.product_uom_id and pl.product_id.uom_id and pl.product_uom_id != pl.product_id.uom_id:
                try:
                    qty = pl.product_uom_id._compute_quantity(
                        qty, pl.product_id.uom_id)
                except Exception:
                    pass
            result[pl.product_id.id] = result.get(pl.product_id.id, 0.0) + qty
        return result

    def _tc_received_map(self):
        """{product_id: qty recibida} desde la recepción física validada."""
        self.ensure_one()
        result = {}
        picking = self.reception_picking_id
        if not picking:
            return result
        for ml in picking.move_line_ids:
            if ml.state != 'done' or not ml.product_id:
                continue
            qty = ml.quantity if 'quantity' in ml._fields else getattr(ml, 'qty_done', 0.0)
            result[ml.product_id.id] = result.get(ml.product_id.id, 0.0) + (qty or 0.0)
        return result

    def _tc_shipped_map(self):
        """{product_id: qty embarcada} desde las líneas del viaje."""
        self.ensure_one()
        result = {}
        for line in self.line_ids:
            if not line.product_id:
                continue
            result[line.product_id.id] = (
                result.get(line.product_id.id, 0.0)
                + (line.product_uom_qty or 0.0)
            )
        return result

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status',
                 'purchase_id', 'reception_picking_id.state')
    def _compute_tc_material_flow(self):
        for voyage in self:
            purchased = voyage._tc_purchased_map()
            shipped = voyage._tc_shipped_map()
            received = voyage._tc_received_map()

            # Solo compara compra↔embarque en productos CON vínculo de compra.
            pend_ship = 0.0
            excess = 0.0
            for pid, bought in purchased.items():
                sh = shipped.get(pid, 0.0)
                if bought > sh:
                    pend_ship += bought - sh
                else:
                    excess += sh - bought

            total_shipped = sum(shipped.values())
            total_received = sum(received.values())

            voyage.tc_purchased_qty = sum(purchased.values())
            voyage.tc_received_qty = total_received
            voyage.tc_pending_ship_qty = pend_ship
            voyage.tc_excess_ship_qty = excess
            voyage.tc_pending_receive_qty = max(
                total_shipped - total_received, 0.0)
            voyage.tc_free_qty = max(
                (voyage.total_m2 or 0.0) - (voyage.allocated_m2 or 0.0), 0.0)

    @api.model
    def tc_get_product_flow_map(self, voyage_id):
        """Flujo por producto para las cabeceras de grupo del widget.

        UNA llamada por apertura de viaje (sin N+1):
        {product_id: {purchased, shipped, received, has_po_link,
                      pending_ship, excess_ship}}
        """
        voyage = self.browse(int(voyage_id or 0)).exists()
        if not voyage:
            return {}

        purchased = voyage._tc_purchased_map()
        shipped = voyage._tc_shipped_map()
        received = voyage._tc_received_map()

        result = {}
        for pid in set(list(shipped.keys()) + list(purchased.keys())):
            bought = purchased.get(pid)
            sh = shipped.get(pid, 0.0)
            has_link = pid in purchased
            result[pid] = {
                'purchased': bought or 0.0,
                'shipped': sh,
                'received': received.get(pid, 0.0),
                'has_po_link': has_link,
                'pending_ship': max((bought or 0.0) - sh, 0.0) if has_link else 0.0,
                'excess_ship': max(sh - (bought or 0.0), 0.0) if has_link else 0.0,
            }
        return result
