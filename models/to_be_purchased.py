# -*- coding: utf-8 -*-
from odoo import models, fields, api
from collections import defaultdict

class ToBePurchasedLogic(models.AbstractModel):
    _name = 'purchase.manager.logic'
    _description = 'Lógica para el Tablero To Be Purchased'

    @api.model
    def get_data(self):
        all_sale_lines = self.env['sale.order.line'].search([
            ('auto_transit_assign', '=', True),
            ('state', '=', 'sale'),
            ('display_type', '=', False)
        ])
        
        sale_lines = all_sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
        
        product_ids = sale_lines.mapped('product_id.id')
        products = self.env['product.product'].browse(product_ids)
        
        result = []
        for product in products:
            quants = self.env['stock.quant'].search([('product_id', '=', product.id)])
            
            qty_a = sum(quants.filtered(lambda q: q.location_id.usage == 'internal').mapped('quantity'))
            
            qty_i = sum(quants.filtered(lambda q: q.location_id.usage == 'transit' or 
                                               'transit' in q.location_id.name.lower() or 
                                               'tránsito' in q.location_id.name.lower()).mapped('quantity'))
            
            all_po_lines = self.env['purchase.order.line'].search([
                ('product_id', '=', product.id),
                ('state', 'in', ['draft', 'sent', 'purchase'])
            ])
            po_lines_open = all_po_lines.filtered(lambda pol: pol.product_qty > pol.qty_received)
            qty_p = sum(po_lines_open.mapped('product_qty')) - sum(po_lines_open.mapped('qty_received'))

            product_sale_lines = sale_lines.filtered(lambda l: l.product_id.id == product.id)
            so_details = []
            total_demanded = 0
            
            for sol in product_sale_lines:
                pending = sol.product_uom_qty - sol.qty_delivered
                total_demanded += pending
                
                # Buscar allocation vinculada (nuevo sistema)
                allocation = self.env['purchase.order.line.allocation'].search([
                    ('sale_line_id', '=', sol.id),
                    ('state', 'not in', ['cancelled', 'done'])
                ], limit=1)
                
                po_name = ''
                po_qty = 0
                po_id = False
                po_state = ''
                
                if allocation:
                    po_line = allocation.purchase_line_id
                    if po_line.order_id.state != 'cancel':
                        po_name = po_line.order_id.name
                        po_qty = allocation.quantity
                        po_id = po_line.order_id.id
                        po_state = po_line.order_id.state
                
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
                    'qty_assigned': sol.qty_delivered,
                    'qty_pending': pending,
                    'note': sol.order_id.note or '',
                    'po_name': po_name,
                    'po_qty': po_qty,
                    'po_id': po_id,
                    'po_state': po_state,
                })

            vendors = []
            for seller in product.seller_ids:
                vendors.append({
                    'id': seller.partner_id.id,
                    'name': seller.partner_id.name,
                    'price': seller.price,
                })
            
            vendor_name = vendors[0]['name'] if vendors else 'SIN PROVEEDOR'

            result.append({
                'id': product.id,
                'name': product.display_name,
                'type': product.type,
                'group': getattr(product, 'x_grupo', 'N/A'),
                'category': product.categ_id.name,
                'vendor': vendor_name,
                'vendors': vendors,
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
    def get_open_purchase_orders(self, vendor_id):
        if not vendor_id:
            return []
        
        pos = self.env['purchase.order'].search([
            ('partner_id', '=', vendor_id),
            ('state', 'in', ['draft', 'sent'])
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
            ('active', '=', True)
        ], order='name')
        
        return [{'id': p.id, 'name': p.name} for p in partners]

    @api.model
    def create_purchase_orders(self, selected_line_ids, vendor_id=False, existing_po_id=False):
        """CONSOLIDACIÓN: Una línea por producto, múltiples allocations por cliente."""
        sale_lines = self.env['sale.order.line'].browse(selected_line_ids)
        
        if not sale_lines:
            return {'error': 'No hay líneas seleccionadas'}

        if not vendor_id:
            return {'error': 'Debe seleccionar un proveedor'}

        vendor = self.env['res.partner'].browse(vendor_id)
        if not vendor.exists():
            return {'error': 'Proveedor no encontrado'}
        
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
            po = self.env['purchase.order'].create({
                'partner_id': vendor.id,
                'origin': ', '.join(list(set(sale_lines.mapped('order_id.name')))),
                'company_id': self.env.company.id,
            })
        
        # CONSOLIDACIÓN POR PRODUCTO
        lines_by_product = defaultdict(list)
        for line in sale_lines:
            qty_pending = line.product_uom_qty - line.qty_delivered
            if qty_pending > 0:
                lines_by_product[line.product_id.id].append({
                    'sale_line': line,
                    'qty_pending': qty_pending
                })
        
        for product_id, sale_line_data in lines_by_product.items():
            product = self.env['product.product'].browse(product_id)
            total_qty = sum(d['qty_pending'] for d in sale_line_data)
            
            existing_po_line = po.order_line.filtered(lambda l: l.product_id.id == product_id)
            
            if existing_po_line:
                po_line = existing_po_line[0]
                new_qty = po_line.product_qty + total_qty
                po_line.write({'product_qty': new_qty})
            else:
                uom_id = product.uom_po_id.id if product.uom_po_id else product.uom_id.id
                so_refs = ', '.join([d['sale_line'].order_id.name for d in sale_line_data])
                
                po_line = self.env['purchase.order.line'].create({
                    'order_id': po.id,
                    'product_id': product_id,
                    'product_qty': total_qty,
                    'product_uom_id': uom_id,
                    'price_unit': product.standard_price,
                    'name': f"[{so_refs}] {product.name}",
                    'date_planned': fields.Datetime.now(),
                })
            
            # Crear ALLOCATIONS para cada línea de venta
            for data in sale_line_data:
                self.env['purchase.order.line.allocation'].create({
                    'purchase_line_id': po_line.id,
                    'sale_line_id': data['sale_line'].id,
                    'quantity': data['qty_pending'],
                    'state': 'pending',
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