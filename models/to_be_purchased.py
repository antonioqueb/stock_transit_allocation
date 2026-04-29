# -*- coding: utf-8 -*-
from collections import defaultdict
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AllocationHubPaymentMixin(models.AbstractModel):
    _name = 'allocation.hub.payment.mixin'
    _description = 'Helpers de pago para hubs de asignación'

    def _get_payment_percent(self, order):
        if not order or not order.amount_total:
            return 0.0

        invoices = order.invoice_ids.filtered(
            lambda inv: inv.state == 'posted'
            and inv.move_type in ('out_invoice', 'out_refund')
        )

        if not invoices:
            return 0.0

        total = sum(invoices.mapped('amount_total'))
        residual = sum(invoices.mapped('amount_residual'))
        paid = max(total - residual, 0.0)

        if total <= 0:
            return 0.0

        return min(100.0, round((paid / order.amount_total) * 100, 2))

    def _display_value(self, value, fallback='N/A'):
        if not value:
            return fallback

        if isinstance(value, str):
            return value

        if isinstance(value, (int, float)):
            return str(value)

        if hasattr(value, 'display_name'):
            return value.display_name or fallback

        if hasattr(value, 'mapped'):
            names = value.mapped('display_name')
            return ', '.join(names) if names else fallback

        return str(value)

    def _get_active_allocation_info(self, sale_line):
        allocation = self.env['purchase.order.line.allocation'].search([
            ('sale_line_id', '=', sale_line.id),
            ('state', 'not in', ['cancelled', 'done']),
        ], order='id desc', limit=1)

        if not allocation:
            return {
                'allocation': False,
                'po_name': '',
                'po_qty': 0.0,
                'po_id': False,
                'po_state': '',
            }

        po_line = allocation.purchase_line_id
        po = po_line.order_id

        if not po or po.state == 'cancel':
            return {
                'allocation': False,
                'po_name': '',
                'po_qty': 0.0,
                'po_id': False,
                'po_state': '',
            }

        return {
            'allocation': allocation,
            'po_name': po.name,
            'po_qty': allocation.quantity,
            'po_id': po.id,
            'po_state': po.state,
        }

    def _get_free_internal_qty_for_product(self, product):
        if not product:
            return 0.0

        Quant = self.env['stock.quant'].sudo()

        domain = [
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('reserved_quantity', '=', 0),
        ]

        if 'x_tiene_hold' in Quant._fields:
            domain.append(('x_tiene_hold', '=', False))

        if hasattr(Quant, '_get_committed_lot_ids'):
            committed_lot_ids = Quant._get_committed_lot_ids(product.id)
            if committed_lot_ids:
                domain.append(('lot_id', 'not in', committed_lot_ids))

        quants = Quant.search(domain)
        return sum(quants.mapped('quantity'))


class ToBeAllocatedLogic(models.AbstractModel):
    _name = 'sale.allocation.manager.logic'
    _inherit = 'allocation.hub.payment.mixin'
    _description = 'Lógica para el Tablero To Be Allocated'

    @api.model
    def get_data(self):
        sale_lines = self.env['sale.order.line'].search([
            ('state', '=', 'sale'),
            ('display_type', '=', False),
            ('product_id', '!=', False),
        ])

        sale_lines = sale_lines.filtered(
            lambda line: line.tc_allocation_hub_state == 'to_be_allocated'
            and line.tc_qty_pending_allocation > 0
        )

        result = []

        for line in sale_lines:
            order = line.order_id
            payment_percent = self._get_payment_percent(order)

            result.append({
                'id': line.id,
                'so_id': order.id,
                'so_name': order.name,
                'date': order.date_order.strftime('%Y-%m-%d') if order.date_order else '',
                'commitment_date': order.commitment_date.strftime('%Y-%m-%d') if order.commitment_date else 'N/A',
                'customer': order.partner_id.name,
                'customer_id': order.partner_id.id,
                'salesperson': order.user_id.name if order.user_id else '',
                'product_id': line.product_id.id,
                'product_name': line.product_id.display_name,
                'description': line.name or '',
                'qty_ordered': line.product_uom_qty,
                'qty_assigned': line.tc_qty_assigned_lots,
                'qty_pending': line.tc_qty_pending_allocation,
                'qty_available': line.tc_available_internal_qty,
                'payment_percent': payment_percent,
                'note': order.note or '',
            })

        result.sort(
            key=lambda item: (
                -item.get('payment_percent', 0.0),
                item.get('commitment_date') or '',
                item.get('so_name') or '',
            )
        )

        return result

    @api.model
    def send_to_purchase(self, sale_line_ids, reason=False):
        lines = self.env['sale.order.line'].browse(sale_line_ids).exists()

        if not lines:
            return {'error': 'No se encontraron líneas válidas'}

        lines.action_tc_send_to_purchase(reason=reason)

        return {
            'success': True,
            'message': 'Línea(s) enviada(s) a To Be Purchased',
        }


class ToBePurchasedLogic(models.AbstractModel):
    _name = 'purchase.manager.logic'
    _inherit = 'allocation.hub.payment.mixin'
    _description = 'Lógica para el Tablero To Be Purchased'

    @api.model
    def get_data(self):
        all_sale_lines = self.env['sale.order.line'].search([
            ('state', '=', 'sale'),
            ('display_type', '=', False),
            ('product_id', '!=', False),
        ])

        sale_lines = all_sale_lines.filtered(
            lambda line: line.tc_allocation_hub_state == 'to_be_purchased'
            and line.tc_qty_pending_allocation > 0
        )

        product_ids = sale_lines.mapped('product_id.id')
        products = self.env['product.product'].browse(product_ids)

        result = []

        for product in products:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
            ])

            qty_a = self._get_free_internal_qty_for_product(product)

            qty_i = sum(
                quants.filtered(
                    lambda q: (
                        q.location_id.usage == 'transit'
                        or 'transit' in (q.location_id.name or '').lower()
                        or 'tránsito' in (q.location_id.name or '').lower()
                    )
                ).mapped('quantity')
            )

            all_po_lines = self.env['purchase.order.line'].search([
                ('product_id', '=', product.id),
                ('state', 'in', ['draft', 'sent', 'purchase']),
            ])

            po_lines_open = all_po_lines.filtered(
                lambda pol: pol.product_qty > pol.qty_received
            )

            qty_p = (
                sum(po_lines_open.mapped('product_qty'))
                - sum(po_lines_open.mapped('qty_received'))
            )

            product_sale_lines = sale_lines.filtered(
                lambda line: line.product_id.id == product.id
            )

            so_details = []
            total_demanded = 0.0

            for sol in product_sale_lines:
                pending = sol.tc_qty_pending_allocation

                if pending <= 0:
                    continue

                total_demanded += pending

                alloc_info = self._get_active_allocation_info(sol)
                payment_percent = self._get_payment_percent(sol.order_id)

                so_details.append({
                    'id': sol.id,
                    'so_name': sol.order_id.name,
                    'so_id': sol.order_id.id,
                    'date': sol.order_id.date_order.strftime('%Y-%m-%d') if sol.order_id.date_order else '',
                    'commitment_date': sol.order_id.commitment_date.strftime('%Y-%m-%d') if sol.order_id.commitment_date else 'N/A',
                    'customer': sol.order_id.partner_id.name,
                    'customer_id': sol.order_id.partner_id.id,
                    'location': sol.order_id.partner_shipping_id.city or '',
                    'description': sol.name or '',
                    'qty_orig': sol.product_uom_qty,
                    'qty_assigned': sol.tc_qty_assigned_lots,
                    'qty_pending': pending,
                    'note': sol.order_id.note or '',
                    'po_name': alloc_info['po_name'],
                    'po_qty': alloc_info['po_qty'],
                    'po_id': alloc_info['po_id'],
                    'po_state': alloc_info['po_state'],
                    'stock_rejected': sol.tc_stock_rejected,
                    'payment_percent': payment_percent,
                })

            if not so_details:
                continue

            so_details.sort(
                key=lambda item: (
                    -item.get('payment_percent', 0.0),
                    item.get('commitment_date') or '',
                    item.get('so_name') or '',
                )
            )

            vendors = []
            for seller in product.seller_ids:
                vendors.append({
                    'id': seller.partner_id.id,
                    'name': seller.partner_id.name,
                    'price': seller.price,
                })

            vendor_name = vendors[0]['name'] if vendors else 'SIN PROVEEDOR'

            # Regla:
            # En To Be Purchased NO se descuenta stock interno disponible,
            # porque la línea puede estar aquí por rechazo explícito del vendedor.
            # Solo se descuenta cantidad ya abierta en OC.
            qty_to_buy = max(0.0, total_demanded - qty_p)

            result.append({
                'id': product.id,
                'name': product.display_name,
                'type': self._display_value(getattr(product, 'x_unidad_del_producto', False)),
                'group': self._display_value(getattr(product, 'x_grupo', False)),
                'category': product.categ_id.name,
                'vendor': vendor_name,
                'vendors': vendors,
                'qty_a': qty_a,
                'qty_i': qty_i,
                'qty_p': qty_p,
                'qty_total': qty_a + qty_i + qty_p,
                'qty_so': total_demanded,
                'qty_to_buy': qty_to_buy,
                'so_lines': so_details,
            })

        result.sort(
            key=lambda product: (
                -max([
                    line.get('payment_percent', 0.0)
                    for line in product.get('so_lines', [])
                ] or [0.0]),
                product.get('name') or '',
            )
        )

        return result

    @api.model
    def get_open_purchase_orders(self, vendor_id):
        if not vendor_id:
            return []

        pos = self.env['purchase.order'].search([
            ('partner_id', '=', vendor_id),
            ('state', 'in', ['draft', 'sent']),
        ], order='create_date desc')

        return [{
            'id': po.id,
            'name': po.name,
            'date': po.date_order.strftime('%Y-%m-%d') if po.date_order else '',
            'origin': po.origin or '',
            'amount': po.amount_total,
            'lines_count': len(po.order_line),
        } for po in pos]

    @api.model
    def get_all_vendors(self):
        partners = self.env['res.partner'].search([
            ('supplier_rank', '>', 0),
            ('active', '=', True),
        ], order='name')

        return [{'id': partner.id, 'name': partner.name} for partner in partners]

    def _get_transit_picking_type(self):
        domain_loc = [
            ('company_id', '=', self.env.company.id),
            '|', '|',
            ('name', '=', 'SOM/Transit'),
            ('name', 'ilike', 'Transit'),
            ('usage', '=', 'transit'),
        ]

        transit_loc = self.env['stock.location'].search(
            domain_loc,
            limit=1,
            order='name desc',
        )

        if not transit_loc:
            _logger.warning(
                "To Be Purchased: No se encontró ubicación de tránsito. "
                "La OC usará la ubicación por defecto."
            )
            return False

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('default_location_dest_id', '=', transit_loc.id),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

        if picking_type:
            _logger.info(
                "To Be Purchased: Asignado Picking Type %s (Destino: %s) a nueva OC.",
                picking_type.name,
                transit_loc.name,
            )
            return picking_type

        _logger.warning(
            "To Be Purchased: Ubicación %s encontrada, pero no hay Tipo de Operación Incoming asociado.",
            transit_loc.name,
        )
        return False

    def _prepare_purchase_line_uom_vals(self, product):
        PurchaseLine = self.env['purchase.order.line']
        vals = {}

        if 'product_uom_id' in PurchaseLine._fields:
            vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in PurchaseLine._fields:
            vals['product_uom'] = product.uom_id.id

        return vals

    @api.model
    def create_purchase_orders(self, selected_line_ids, vendor_id=False, existing_po_id=False):
        """
        CONSOLIDACIÓN:
        Una línea por producto en la OC, múltiples allocations por línea de venta.
        """
        sale_lines = self.env['sale.order.line'].browse(selected_line_ids).exists()

        sale_lines = sale_lines.filtered(
            lambda line: line.tc_allocation_hub_state == 'to_be_purchased'
            and line.tc_qty_pending_allocation > 0
        )

        if not sale_lines:
            return {'error': 'No hay líneas válidas pendientes por comprar'}

        if not vendor_id:
            return {'error': 'Debe seleccionar un proveedor'}

        vendor = self.env['res.partner'].browse(vendor_id)
        if not vendor.exists():
            return {'error': 'Proveedor no encontrado'}

        po_vals = {
            'partner_id': vendor.id,
            'origin': ', '.join(list(set(sale_lines.mapped('order_id.name')))),
            'company_id': self.env.company.id,
        }

        picking_type = self._get_transit_picking_type()
        if picking_type:
            po_vals['picking_type_id'] = picking_type.id

        if existing_po_id:
            po = self.env['purchase.order'].browse(existing_po_id)

            if not po.exists() or po.state not in ['draft', 'sent']:
                return {'error': 'La orden de compra no existe o ya fue confirmada'}

            new_origins = sale_lines.mapped('order_id.name')
            current_origin = po.origin or ''

            for name in new_origins:
                if name not in current_origin:
                    current_origin += f", {name}" if current_origin else name

            po.write({'origin': current_origin})
        else:
            po = self.env['purchase.order'].create(po_vals)

        lines_by_product = defaultdict(list)

        for line in sale_lines:
            qty_pending = line.tc_qty_pending_allocation

            if qty_pending > 0:
                lines_by_product[line.product_id.id].append({
                    'sale_line': line,
                    'qty_pending': qty_pending,
                })

        for product_id, sale_line_data in lines_by_product.items():
            product = self.env['product.product'].browse(product_id)
            total_qty = sum(data['qty_pending'] for data in sale_line_data)

            existing_po_line = po.order_line.filtered(
                lambda purchase_line: purchase_line.product_id.id == product_id
            )

            if existing_po_line:
                po_line = existing_po_line[0]
                po_line.write({
                    'product_qty': po_line.product_qty + total_qty,
                })
            else:
                so_refs = ', '.join([
                    data['sale_line'].order_id.name
                    for data in sale_line_data
                ])

                po_line_vals = {
                    'order_id': po.id,
                    'product_id': product_id,
                    'product_qty': total_qty,
                    'price_unit': product.standard_price,
                    'name': f"[{so_refs}] {product.name}",
                    'date_planned': fields.Datetime.now(),
                }
                po_line_vals.update(self._prepare_purchase_line_uom_vals(product))

                po_line = self.env['purchase.order.line'].create(po_line_vals)

            for data in sale_line_data:
                sale_line = data['sale_line']

                self.env['purchase.order.line.allocation'].create({
                    'purchase_line_id': po_line.id,
                    'sale_line_id': sale_line.id,
                    'quantity': data['qty_pending'],
                    'state': 'pending',
                })

                sale_line.write({
                    'auto_transit_assign': True,
                })

        return {
            'name': 'Orden de Compra',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }