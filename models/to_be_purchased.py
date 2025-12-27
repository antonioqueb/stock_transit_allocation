# -*- coding: utf-8 -*-
from odoo import models, fields, api

class ToBePurchasedLogic(models.AbstractModel):
    _name = 'purchase.manager.logic'
    _description = 'Lógica para el Tablero To Be Purchased'

    @api.model
    def get_data(self):
        # 1. Buscar líneas de venta que tengan el check "Mandar Pedir" y no estén entregadas
        sale_lines = self.env['sale.order.line'].search([
            ('auto_transit_assign', '=', True),
            ('state', '=', 'sale'),
            ('qty_delivered', '<', ('product_uom_qty'))
        ])
        
        product_ids = sale_lines.mapped('product_id.id')
        products = self.env['product.product'].browse(product_ids)
        
        result = []
        for product in products:
            # Cálculos de stock (A, I, P)
            # A: Disponible (Internal)
            # I: En tránsito (Locations tipo transit)
            # P: En PO abierta (Purchase line qty - received)
            quants = self.env['stock.quant'].search([('product_id', '=', product.id)])
            qty_a = sum(quants.filtered(lambda q: q.location_id.usage == 'internal').mapped('quantity'))
            qty_i = sum(quants.filtered(lambda q: 'transit' in q.location_id.name.lower() or 'tránsito' in q.location_id.name.lower()).mapped('quantity'))
            
            # Cantidad en órdenes de compra (P)
            po_lines_open = self.env['purchase.order.line'].search([
                ('product_id', '=', product.id),
                ('state', 'in', ['draft', 'sent', 'purchase']),
                ('qty_received', '<', ('product_qty'))
            ])
            qty_p = sum(po_lines_open.mapped('product_qty')) - sum(po_lines_open.mapped('qty_received'))

            # Detalle de Pedidos (SO)
            product_sale_lines = sale_lines.filtered(lambda l: l.product_id.id == product.id)
            so_details = []
            total_demanded = 0
            
            for sol in product_sale_lines:
                pending = sol.product_uom_qty - sol.qty_delivered
                total_demanded += pending
                
                # Buscar si ya existe una PO vinculada a esta línea
                linked_po_line = self.env['purchase.order.line'].search([('sale_line_id', '=', sol.id)], limit=1)
                
                so_details.append({
                    'id': sol.id,
                    'so_name': sol.order_id.name,
                    'date': sol.order_id.date_order.strftime('%Y-%m-%d') if sol.order_id.date_order else '',
                    'commitment_date': sol.order_id.commitment_date.strftime('%Y-%m-%d') if sol.order_id.commitment_date else 'N/A',
                    'customer': sol.order_id.partner_id.name,
                    'location': sol.order_id.partner_shipping_id.city or '',
                    'description': sol.name or '',
                    'qty_orig': sol.product_uom_qty,
                    'qty_assigned': sol.qty_delivered, # O lógica de reserva según tu proceso
                    'qty_pending': pending,
                    'note': sol.order_id.note or '',
                    'po_name': linked_po_line.order_id.name if linked_po_line else '',
                    'po_qty': linked_po_line.product_qty if linked_po_line else 0,
                })

            result.append({
                'id': product.id,
                'name': product.display_name,
                'type': product.detailed_type,
                'group': getattr(product, 'x_grupo', 'N/A'), # Asumiendo campo de stock_lot_dimensions
                'category': product.categ_id.name,
                'vendor': product.seller_ids[0].partner_id.name if product.seller_ids else 'SIN PROVEEDOR',
                'qty_a': qty_a,
                'qty_i': qty_i,
                'qty_p': qty_p,
                'qty_total': qty_a + qty_i + qty_p,
                'qty_so': total_demanded,
                'qty_to_buy': max(0, total_demanded - (qty_a + qty_i + qty_p)),
                'so_lines': so_details
            })
        return result

    @api.model
    def create_purchase_orders(self, selected_line_ids):
        """
        Crea Órdenes de Compra agrupadas por proveedor desde las líneas de SO.
        CORRECCIÓN: Valida proveedores individuales por producto.
        """
        sale_lines = self.env['sale.order.line'].browse(selected_line_ids)
        vendor_map = {}
        
        for line in sale_lines:
            # Obtener el proveedor preferido del producto
            seller = line.product_id._select_seller(quantity=line.product_uom_qty)
            vendor = seller.partner_id if seller else False
            
            if not vendor:
                continue
                
            if vendor.id not in vendor_map:
                vendor_map[vendor.id] = self.env['sale.order.line']
            vendor_map[vendor.id] |= line
            
        po_ids = []
        for vendor_id, lines in vendor_map.items():
            po = self.env['purchase.order'].create({
                'partner_id': vendor_id,
                'origin': ', '.join(list(set(lines.mapped('order_id.name')))),
                'company_id': self.env.company.id,
            })
            for l in lines:
                self.env['purchase.order.line'].create({
                    'order_id': po.id,
                    'product_id': l.product_id.id,
                    'product_qty': l.product_uom_qty - l.qty_delivered,
                    'product_uom': l.product_uom.id,
                    'price_unit': l.product_id.standard_price,
                    'sale_line_id': l.id, # Vínculo para Tránsito (Control Tower)
                    'name': l.name,
                    'date_planned': fields.Datetime.now(),
                })
            po_ids.append(po.id)
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', po_ids)],
            'target': 'current',
        }