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

    def _tc_covered_purchase_orders(self):
        """POs que amparan el material de este viaje.

        Con factura de carga (embarque multi-PO): TODAS las PO de la carga.
        Sin carga: la OC vinculada al viaje (flujo clásico, intacto).
        """
        self.ensure_one()
        po = self.purchase_id
        if not po:
            return po
        header = self.env['supplier.proforma.header'].sudo().search(
            [('purchase_id', '=', po.id)], limit=1)
        access = header.access_id if header else False
        if access and access.cargo_invoice_id and access.cargo_invoice_id.purchase_ids:
            return access.cargo_invoice_id.purchase_ids
        return po

    def _tc_purchased_map(self):
        """{product_id: qty comprada} de las OCs amparadas (UoM del producto).

        Una consulta por viaje; ignora líneas canceladas/display y solo
        considera productos presentes en el viaje. Con factura de carga suma
        las líneas de TODAS las PO amparadas.
        """
        self.ensure_one()
        result = {}
        pos = self._tc_covered_purchase_orders().filtered(
            lambda p: p.state != 'cancel')
        if not pos:
            return result

        voyage_products = set(self.line_ids.mapped('product_id').ids)
        if not voyage_products:
            return result

        for pl in pos.order_line:
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

    # ------------------------------------------------------------------
    # Displays con SEMÁNTICA DE UNIDADES (nunca sumar incompatibles)
    # ------------------------------------------------------------------
    tc_purchased_display = fields.Char(compute='_compute_tc_flow_displays')
    tc_shipped_display = fields.Char(compute='_compute_tc_flow_displays')
    tc_received_display = fields.Char(compute='_compute_tc_flow_displays')
    tc_flow_status = fields.Char(compute='_compute_tc_flow_displays')
    tc_free_percent = fields.Float(compute='_compute_tc_flow_displays', digits=(16, 0))
    tc_coverage_secondary = fields.Char(compute='_compute_tc_flow_displays')
    tc_lots_summary = fields.Char(compute='_compute_tc_flow_displays')
    tc_lots_available = fields.Char(compute='_compute_tc_flow_displays')

    @staticmethod
    def _tc_fmt_qty(qty, uom):
        if abs(qty - round(qty)) < 0.005:
            num = '%d' % round(qty)
        else:
            num = ('%.2f' % qty).rstrip('0').rstrip('.')
        return '%s %s' % (num, uom) if uom else num

    def _tc_group_by_uom(self, qty_map):
        """{uom_name: qty} agrupando SOLO unidades iguales."""
        self.ensure_one()
        Product = self.env['product.product']
        out = {}
        for pid, qty in qty_map.items():
            uom = Product.browse(pid).uom_id.name or '?'
            out[uom] = out.get(uom, 0.0) + qty
        return out

    def _tc_uom_display(self, qty_map):
        groups = self._tc_group_by_uom(qty_map)
        if not groups:
            return '—'
        parts = [self._tc_fmt_qty(q, u) for u, q in sorted(groups.items())]
        if len(parts) > 2:
            return ' · '.join(parts[:2]) + ' · +%d unidades' % (len(parts) - 2)
        return ' · '.join(parts)

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status',
                 'purchase_id', 'reception_picking_id.state',
                 'allocation_percent')
    def _compute_tc_flow_displays(self):
        for voyage in self:
            purchased = voyage._tc_purchased_map()
            shipped = voyage._tc_shipped_map()
            received = voyage._tc_received_map()

            voyage.tc_purchased_display = voyage._tc_uom_display(purchased)
            voyage.tc_shipped_display = voyage._tc_uom_display(shipped)
            voyage.tc_received_display = voyage._tc_uom_display(received)

            # Un ÚNICO mensaje de estado del flujo (no una fila de métricas).
            msgs = []
            pend_by_uom = {}
            exc_by_uom = {}
            for pid, bought in purchased.items():
                sh = shipped.get(pid, 0.0)
                uom = self.env['product.product'].browse(pid).uom_id.name or '?'
                if bought > sh + 0.005:
                    pend_by_uom[uom] = pend_by_uom.get(uom, 0.0) + (bought - sh)
                elif sh > bought + 0.005:
                    exc_by_uom[uom] = exc_by_uom.get(uom, 0.0) + (sh - bought)
            for uom, q in sorted(pend_by_uom.items()):
                msgs.append('Pendiente por embarcar: %s' % voyage._tc_fmt_qty(q, uom))
            for uom, q in sorted(exc_by_uom.items()):
                msgs.append('Exceso embarcado: %s' % voyage._tc_fmt_qty(q, uom))
            recv_pend = {}
            for pid, sh in shipped.items():
                rec = received.get(pid, 0.0)
                if sh > rec + 0.005:
                    uom = self.env['product.product'].browse(pid).uom_id.name or '?'
                    recv_pend[uom] = recv_pend.get(uom, 0.0) + (sh - rec)
            for uom, q in sorted(recv_pend.items()):
                msgs.append('Pendiente por recibir: %s' % voyage._tc_fmt_qty(q, uom))

            voyage.tc_flow_status = ' · '.join(msgs) if msgs else 'Flujo completo, sin diferencias.'

            # Cobertura: porcentaje protagonista, físico SOLO con unidad común.
            pct = voyage.allocation_percent or 0.0
            voyage.tc_free_percent = max(0.0, 100.0 - pct)

            uoms = set(self._tc_group_by_uom(shipped).keys()) if shipped else set()
            if len(uoms) == 1:
                uom = list(uoms)[0]
                voyage.tc_coverage_secondary = '%s asignados de %s' % (
                    voyage._tc_fmt_qty(voyage.allocated_m2 or 0.0, uom),
                    voyage._tc_fmt_qty(voyage.total_m2 or 0.0, uom),
                )
            else:
                assigned_lots = len(voyage.line_ids.filtered(
                    lambda l: l.allocation_status == 'reserved'))
                voyage.tc_coverage_secondary = '%d de %d lotes con asignación' % (
                    assigned_lots, len(voyage.line_ids))

            total_lots = len(voyage.line_ids)
            assigned = len(voyage.line_ids.filtered(
                lambda l: l.allocation_status == 'reserved'))
            voyage.tc_lots_summary = '%d de %d lotes' % (assigned, total_lots)
            free_lots = total_lots - assigned
            voyage.tc_lots_available = (
                '%d lote%s disponible%s' % (
                    free_lots, 's' if free_lots != 1 else '',
                    's' if free_lots != 1 else '')
                if free_lots else 'Todos los lotes asignados'
            )

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
            product = self.env['product.product'].browse(pid)
            result[pid] = {
                'purchased': bought or 0.0,
                'shipped': sh,
                'received': received.get(pid, 0.0),
                'has_po_link': has_link,
                'pending_ship': max((bought or 0.0) - sh, 0.0) if has_link else 0.0,
                'excess_ship': max(sh - (bought or 0.0), 0.0) if has_link else 0.0,
                # Unidad REAL del producto: nunca asumir m².
                'uom': product.uom_id.name or '',
            }
        return result
