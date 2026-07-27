# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class StockTransitVoyageAllocationGuard(models.Model):
    _inherit = 'stock.transit.voyage'

    def _tc_allocation_still_needs_material(self, allocation):
        """
        Evita que material recién llegado se amarre a un pedido que ya fue cubierto
        manualmente con placas existentes antes de que llegara la OC.
        """
        self.ensure_one()

        if not allocation or not allocation.exists():
            return False

        sale_line = allocation.sale_line_id

        if not sale_line:
            return True

        if hasattr(sale_line, '_tc_get_pending_allocation_qty'):
            pending_qty = sale_line._tc_get_pending_allocation_qty()
            return pending_qty > 0

        return True

    def _tc_get_pending_qty_for_allocation(self, allocation):
        """
        Devuelve cuánto sigue necesitando realmente la línea de venta.
        Si no existe el helper nuevo, se respeta la cantidad de la allocation.
        """
        self.ensure_one()

        if not allocation or not allocation.exists():
            return 0.0

        sale_line = allocation.sale_line_id

        if sale_line and hasattr(sale_line, '_tc_get_pending_allocation_qty'):
            return max(sale_line._tc_get_pending_allocation_qty(), 0.0)

        return max(allocation.quantity - allocation.qty_received, 0.0)

    # -------------------------------------------------------------------------
    # PROTECCIÓN AL CARGAR DESDE OC
    # -------------------------------------------------------------------------

    def action_load_from_purchase(self):
        """
        Override del método base para evitar que una OC siga reservando material
        para una SO que ya fue cubierta manualmente con placas existentes.

        Regla:
        - Si la línea de venta ya no tiene pendiente real, se cancela la allocation.
        - Si aún tiene pendiente parcial, la allocation se reduce al pendiente real.
        - La diferencia queda como stock disponible del viaje.

        Nota:
        - Esta carga desde OC puede conservar líneas esperadas sin lote como
          referencia comercial de la compra.
        - La asignación de LOTES reales se controla exclusivamente cuando Compras
          selecciona manualmente líneas de stock.transit.line.
        """
        self.ensure_one()

        if not self.purchase_id:
            return

        existing_alloc_ids = self.line_ids.mapped('allocation_id.id')

        allocations = self.env['purchase.order.line.allocation'].search([
            ('purchase_order_id', '=', self.purchase_id.id),
            ('id', 'not in', existing_alloc_ids),
            ('state', 'not in', ['done', 'cancelled']),
        ], order='id asc')

        transit_lines = []
        reserved_qty_by_po_line = {}

        # Primero contar lo que ya existe en el viaje como reservado por allocation.
        for existing_line in self.line_ids.filtered(lambda l: l.allocation_id):
            po_line = existing_line.allocation_id.purchase_line_id
            if not po_line:
                continue

            if existing_line.allocation_id.state == 'cancelled':
                continue

            reserved_qty_by_po_line.setdefault(po_line.id, 0.0)
            reserved_qty_by_po_line[po_line.id] += existing_line.product_uom_qty or 0.0

        # Reservas MANUALES (sin allocation: pedido ajeno a la OC o asignación
        # directa de lote) también comprometen material del viaje. Sin
        # descontarlas, el mismo metraje contaba como reservado Y como
        # disponible en la línea "Para Stock".
        manual_reserved_by_product = {}
        for manual_line in self.line_ids.filtered(
            lambda l: not l.allocation_id and (l.order_id or l.partner_id)
        ):
            pid = manual_line.product_id.id
            manual_reserved_by_product[pid] = (
                manual_reserved_by_product.get(pid, 0.0)
                + (manual_line.product_uom_qty or 0.0)
            )

        for alloc in allocations:
            pending_qty = self._tc_get_pending_qty_for_allocation(alloc)

            if pending_qty <= 0:
                alloc.write({'state': 'cancelled'})
                _logger.info(
                    "[TC_ALLOC_GUARD][PURCHASE] Allocation %s cancelada: "
                    "la SO %s / línea %s ya no tiene pendiente real.",
                    alloc.id,
                    alloc.sale_order_id.name if alloc.sale_order_id else 'N/A',
                    alloc.sale_line_id.id if alloc.sale_line_id else 'N/A',
                )
                continue

            qty_to_allocate = min(alloc.quantity or 0.0, pending_qty)

            if qty_to_allocate <= 0:
                alloc.write({'state': 'cancelled'})
                continue

            # Si el pendiente real bajó, reducimos la allocation para que la diferencia
            # quede disponible como stock.
            if qty_to_allocate < (alloc.quantity or 0.0):
                alloc.write({'quantity': qty_to_allocate})
                _logger.info(
                    "[TC_ALLOC_GUARD][PURCHASE] Allocation %s reducida a %.4f por pendiente real.",
                    alloc.id,
                    qty_to_allocate,
                )

            transit_lines.append({
                'voyage_id': self.id,
                'product_id': alloc.product_id.id,
                'product_uom_qty': qty_to_allocate,
                'partner_id': alloc.partner_id.id,
                'order_id': alloc.sale_order_id.id,
                'allocation_id': alloc.id,
                'allocation_status': 'reserved',
                'container_number': 'PENDIENTE',
            })

            po_line = alloc.purchase_line_id
            if po_line:
                reserved_qty_by_po_line.setdefault(po_line.id, 0.0)
                reserved_qty_by_po_line[po_line.id] += qty_to_allocate

        # SOLO placeholders "Para Stock" (sin lote): incluir líneas con lote
        # físico real permitía BORRARLAS del viaje o reescribirles la cantidad
        # con el excedente agregado de la OC (placa de 12 m² declarada 480).
        existing_stock_lines = self.line_ids.filtered(
            lambda line: not line.allocation_id
            and not line.partner_id
            and not line.order_id
            and not line.lot_id
        )

        existing_stock_by_product = {
            line.product_id.id: line
            for line in existing_stock_lines
        }

        for po_line in self.purchase_id.order_line:
            total_po_qty = po_line.product_qty or 0.0
            reserved_qty = reserved_qty_by_po_line.get(po_line.id, 0.0)
            extra_for_stock = total_po_qty - reserved_qty
            product_id = po_line.product_id.id

            # Descontar reservas manuales del mismo producto (bucket
            # compartido entre líneas de OC del mismo producto).
            manual_left = manual_reserved_by_product.get(product_id, 0.0)
            if manual_left > 0 and extra_for_stock > 0:
                take = min(manual_left, extra_for_stock)
                extra_for_stock -= take
                manual_reserved_by_product[product_id] = manual_left - take

            if extra_for_stock <= 0:
                if product_id in existing_stock_by_product:
                    existing_stock_by_product[product_id].unlink()
                continue

            if product_id in existing_stock_by_product:
                existing_line = existing_stock_by_product[product_id]

                if existing_line.product_uom_qty != extra_for_stock:
                    existing_line.write({
                        'product_uom_qty': extra_for_stock,
                    })
            else:
                transit_lines.append({
                    'voyage_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': extra_for_stock,
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'container_number': 'PENDIENTE',
                    'notes': 'Para Stock (cantidad extra en OC)',
                })

        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)

    # -------------------------------------------------------------------------
    # PROTECCIÓN AL CARGAR DESDE PICKING
    # -------------------------------------------------------------------------

    def _tc_get_move_line_quantity_for_transit_load(self, move_line):
        """Compatibilidad Odoo 17/18/19 para leer cantidad hecha de la recepción."""
        if 'quantity' in move_line._fields:
            return move_line.quantity or 0.0
        return move_line.qty_done or 0.0

    def _tc_get_lot_container_for_transit_load(self, lot):
        """Obtiene contenedor desde el lote sin asumir campos personalizados."""
        if not lot:
            return ''

        if hasattr(lot, 'x_contenedor') and lot.x_contenedor:
            return lot.x_contenedor

        if lot.ref:
            return lot.ref

        return ''

    def action_load_from_picking(self):
        """
        Carga los lotes reales recibidos en la ubicación de tránsito.

        Cambio funcional:
        - Antes: el método consumía allocations de la OC, elegía lotes por producto,
          asignaba cliente/pedido y generaba reservas automáticamente.
        - Ahora: los lotes reales entran como DISPONIBLES en tránsito.

        Regla de negocio:
        Compras debe seleccionar manualmente qué lote de stock.transit.line se
        asigna a qué pedido. La selección manual se hace desde Transit Allocation
        o desde el embarque, y solo entonces stock.transit.line.write() marcará
        la línea como reserved/committed.
        """
        self.ensure_one()

        if not self.picking_id:
            return

        placeholder_lines = self.line_ids.filtered(lambda line: not line.lot_id)

        if placeholder_lines:
            placeholder_lines.unlink()

        # RECORDSET por lote (no una sola línea): un lote con parcialidades
        # tiene GEMELAS y la cantidad física debe DISTRIBUIRSE entre ellas.
        # El dict de una-línea-por-lote (ganaba la última) escribía el total
        # físico en la gemela libre dejando metros fantasma (5+5 → 10+5).
        existing_by_lot = {}
        for line in self.line_ids:
            if line.lot_id:
                existing_by_lot.setdefault(
                    line.lot_id.id, self.env['stock.transit.line']
                )
                existing_by_lot[line.lot_id.id] |= line

        lines_to_create = []
        created_count = 0
        updated_count = 0

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue

            lot = move_line.lot_id
            product = move_line.product_id
            lot_id = lot.id

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', lot.id),
                ('product_id', '=', product.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id),
            ], limit=1)

            raw_qty_done = self._tc_get_move_line_quantity_for_transit_load(move_line)
            qty_done = self._normalize_product_qty(
                product,
                found_quant.quantity if found_quant else raw_qty_done,
            )

            if qty_done <= 0:
                continue

            lot_container = self._tc_get_lot_container_for_transit_load(lot)

            if lot_id in existing_by_lot:
                lot_lines = existing_by_lot[lot_id].exists()
                if not lot_lines:
                    continue

                existing_line = lot_lines.sorted('id')[0]
                update_vals = {}

                if len(lot_lines) == 1:
                    if self._qty_differs(product, existing_line.product_uom_qty, qty_done):
                        update_vals['product_uom_qty'] = qty_done
                else:
                    # GEMELAS (parcialidad): la cantidad física del lote se
                    # DISTRIBUYE — las líneas con orden conservan su
                    # parcialidad y la disponible absorbe el resto. Jamás se
                    # escribe el total del lote en cada gemela (eso duplicaba
                    # el lote con la cantidad completa en ambas). Mismo
                    # contrato que la versión base de action_load_from_picking.
                    reserved = lot_lines.filtered(lambda l: l.order_id)
                    free = (lot_lines - reserved).sorted('id')
                    reserved_qty = sum(reserved.mapped('product_uom_qty'))
                    rest = max(qty_done - reserved_qty, 0.0)

                    if free:
                        keeper = free[0]
                        extras = free - keeper
                        if extras:
                            extras.with_context(skip_reservation_logic=True).unlink()
                        if self._qty_differs(product, keeper.product_uom_qty, rest):
                            keeper.with_context(skip_reservation_logic=True).write({
                                'product_uom_qty': rest,
                            })
                        _logger.info(
                            "[TC_TWINS][GUARD] lote=%s fisico=%.3f reservado=%.3f "
                            "saldo_libre=%.3f (gemelas normalizadas)",
                            lot.name, qty_done, reserved_qty, rest,
                        )

                    existing_line = (reserved.sorted('id')[:1] or free[:1])[0]

                if found_quant and existing_line.quant_id.id != found_quant.id:
                    update_vals['quant_id'] = found_quant.id

                if lot_container and existing_line.container_number != lot_container:
                    update_vals['container_number'] = lot_container

                # Importante: no se toca partner_id/order_id/allocation_id aquí.
                # Si Compras ya seleccionó manualmente este lote para un pedido,
                # esa asignación debe sobrevivir a una recarga del picking.
                if update_vals:
                    existing_line.with_context(skip_reservation_logic=True).write(update_vals)
                    updated_count += 1

                continue

            lines_to_create.append({
                'voyage_id': self.id,
                'product_id': product.id,
                'lot_id': lot.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': False,
                'order_id': False,
                'allocation_id': False,
                'allocation_status': 'available',
                'container_number': lot_container,
                'notes': 'Disponible para asignación manual desde Compras. No autoasignado a pedido.',
            })

        if lines_to_create:
            created = self.env['stock.transit.line'].with_context(
                skip_reservation_logic=True,
            ).create(lines_to_create)
            created_count = len(created)

        if created_count or updated_count:
            self.message_post(body=(
                'Control Tower: lotes cargados desde recepción a tránsito.\n'
                'Lotes nuevos: %s\n'
                'Lotes actualizados: %s\n'
                'Regla aplicada: todos los lotes nuevos quedan disponibles; '
                'Compras debe seleccionar manualmente qué lote se asigna a cada pedido.'
            ) % (created_count, updated_count))

        _logger.info(
            '[TC_ALLOC_GUARD][PICKING] Viaje %s cargado desde picking %s sin autoasignar lotes. '
            'Nuevos=%s Actualizados=%s',
            self.name,
            self.picking_id.name,
            created_count,
            updated_count,
        )