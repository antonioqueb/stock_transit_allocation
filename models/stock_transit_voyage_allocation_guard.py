# -*- coding: utf-8 -*-
import logging

from odoo import models, fields

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

        existing_stock_lines = self.line_ids.filtered(
            lambda line: not line.allocation_id and not line.partner_id and not line.order_id
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

    def action_load_from_picking(self):
        """
        Override completo del método original para agregar una protección:
        si una allocation de OC ya no tiene pendiente real en su sale.order.line,
        se cancela la allocation y el material entra como stock libre.
        """
        self.ensure_one()

        if not self.picking_id:
            return

        placeholder_lines = self.line_ids.filtered(lambda line: not line.lot_id)

        if placeholder_lines:
            placeholder_lines.unlink()

        existing_by_lot = {
            line.lot_id.id: line
            for line in self.line_ids
            if line.lot_id
        }

        from .utils.transit_manager import TransitManager

        purchase = self.picking_id.purchase_id
        allocations_map = {}
        allocation_consumed = {}

        if purchase:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', purchase.id),
                ('state', 'not in', ['done', 'cancelled']),
            ], order='id asc')

            for alloc in allocations:
                allocations_map.setdefault(alloc.product_id.id, []).append(alloc)
                allocation_consumed[alloc.id] = 0.0

        lines_to_create = []
        hold_orders_map = {}

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue

            lot_id = move_line.lot_id.id
            product_id = move_line.product_id.id

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id),
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id),
            ], limit=1)

            raw_qty_done = move_line.quantity
            qty_done = self._normalize_product_qty(
                move_line.product_id,
                found_quant.quantity if found_quant else raw_qty_done,
            )

            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False

            if product_id in allocations_map:
                for alloc in allocations_map[product_id]:
                    already_received = alloc.qty_received
                    consumed_this_load = allocation_consumed.get(alloc.id, 0.0)
                    remaining = alloc.quantity - (already_received + consumed_this_load)

                    if remaining <= 0:
                        continue

                    pending_qty = self._tc_get_pending_qty_for_allocation(alloc)

                    if pending_qty <= 0:
                        alloc.write({'state': 'cancelled'})
                        _logger.info(
                            "[TC_ALLOC_GUARD][PICKING] Allocation %s cancelada: "
                            "la SO %s / línea %s ya no tiene pendiente real.",
                            alloc.id,
                            alloc.sale_order_id.name if alloc.sale_order_id else 'N/A',
                            alloc.sale_line_id.id if alloc.sale_line_id else 'N/A',
                        )
                        continue

                    if pending_qty < remaining:
                        alloc.write({'quantity': already_received + pending_qty})
                        remaining = pending_qty
                        _logger.info(
                            "[TC_ALLOC_GUARD][PICKING] Allocation %s reducida por pendiente real %.4f.",
                            alloc.id,
                            pending_qty,
                        )

                    allocation_to_use = alloc
                    partner_to_assign = alloc.partner_id
                    order_to_assign = alloc.sale_order_id

                    if alloc.sale_line_id:
                        auto_assign = getattr(alloc.sale_line_id, 'auto_transit_assign', True)

                        if not auto_assign:
                            partner_to_assign = False
                            order_to_assign = False
                            allocation_to_use = False
                            continue

                    allocation_consumed[alloc.id] = consumed_this_load + min(qty_done, remaining)
                    break

            lot_container = ''

            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref

            if lot_id in existing_by_lot:
                existing_line = existing_by_lot[lot_id]
                update_vals = {}

                if self._qty_differs(move_line.product_id, existing_line.product_uom_qty, qty_done):
                    update_vals['product_uom_qty'] = qty_done

                if found_quant and existing_line.quant_id.id != found_quant.id:
                    update_vals['quant_id'] = found_quant.id

                if lot_container and existing_line.container_number != lot_container:
                    update_vals['container_number'] = lot_container

                if allocation_to_use and not existing_line.allocation_id:
                    update_vals['allocation_id'] = allocation_to_use.id

                if not existing_line.partner_id and partner_to_assign:
                    update_vals['partner_id'] = partner_to_assign.id
                    update_vals['order_id'] = order_to_assign.id if order_to_assign else False
                    update_vals['allocation_status'] = 'reserved'

                if not partner_to_assign and not order_to_assign and existing_line.allocation_status != 'available':
                    update_vals['partner_id'] = False
                    update_vals['order_id'] = False
                    update_vals['allocation_id'] = False
                    update_vals['allocation_status'] = 'available'

                if update_vals:
                    existing_line.with_context(skip_reservation_logic=True).write(update_vals)

                continue

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container,
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }

            lines_to_create.append(line_vals)

            if partner_to_assign and order_to_assign:
                key = (partner_to_assign.id, order_to_assign.id)

                hold_orders_map.setdefault(key, {
                    'partner': partner_to_assign,
                    'order': order_to_assign,
                    'line_vals_indices': [],
                })
                hold_orders_map[key]['line_vals_indices'].append(len(lines_to_create) - 1)

        created_lines = self.env['stock.transit.line']

        if lines_to_create:
            created_lines = self.env['stock.transit.line'].create(lines_to_create)

        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)

                if not alloc.exists() or alloc.state == 'cancelled':
                    continue

                new_received = alloc.qty_received + qty_consumed
                alloc.write({
                    'qty_received': min(new_received, alloc.quantity),
                    'state': 'in_transit',
                })

        for key, data in hold_orders_map.items():
            partner = data['partner']
            order = data['order']
            indices = data['line_vals_indices']

            relevant_lines = [
                created_lines[index]
                for index in indices
                if index < len(created_lines)
            ]

            if not relevant_lines:
                continue

            hold_vals = {
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            }

            HoldOrder = self.env['stock.lot.hold.order']

            if 'fecha_orden' in HoldOrder._fields:
                hold_vals['fecha_orden'] = fields.Datetime.now()

            hold_order = HoldOrder.create(hold_vals)

            for line in relevant_lines:
                TransitManager.reassign_lot(
                    self.env,
                    line,
                    partner,
                    order,
                    notes=False,
                    hold_order_obj=hold_order,
                )

            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()