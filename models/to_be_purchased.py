# -*- coding: utf-8 -*-
from odoo import models, fields, api

class ToBePurchasedLogic(models.AbstractModel):
    _name = 'purchase.manager.logic'
    _description = 'Lógica para el Tablero To Be Purchased'

    @api.model
    def get_data(self):
        # 1. Buscar líneas de venta con el check "Mandar Pedir"
        all_sale_lines = self.env['sale.order.line'].search([
            ('auto_transit_assign', '=', True),
            ('state', '=', 'sale'),
            ('display_type', '=', False)
        ])
        
        # Filtramos las pendientes de entrega
        sale_lines = all_sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
        
        product_ids = sale_lines.mapped('product_id.id')
        products = self.env['product.product'].browse(product_ids)
        
        result = []
        for product in products:
            # Cálculos de stock (A, I, P)
            quants = self.env['stock.quant'].search([('product_id', '=', product.id)])
            
            # A: Disponible (Ubicaciones Internas)
            qty_a = sum(quants.filtered(lambda q: q.location_id.usage == 'internal').mapped('quantity'))
            
            # I: En tránsito
            qty_i = sum(quants.filtered(lambda q: q.location_id.usage == 'transit' or 
                                               'transit' in q.location_id.name.lower() or 
                                               'tránsito' in q.location_id.name.lower()).mapped('quantity'))
            
            # P: En órdenes de compra abiertas (no canceladas)
            all_po_lines = self.env['purchase.order.line'].search([
                ('product_id', '=', product.id),
                ('state', 'in', ['draft', 'sent', 'purchase'])
            ])
            po_lines_open = all_po_lines.filtered(lambda pol: pol.product_qty > pol.qty_received)
            qty_p = sum(po_lines_open.mapped('product_qty')) - sum(po_lines_open.mapped('qty_received'))

            # Detalle de Pedidos (SO)
            product_sale_lines = sale_lines.filtered(lambda l: l.product_id.id == product.id)
            so_details = []
            total_demanded = 0
            
            for sol in product_sale_lines:
                pending = sol.product_uom_qty - sol.qty_delivered
                total_demanded += pending
                
                # Buscar línea de compra vinculada
                linked_po_line = self.env['purchase.order.line'].search([('sale_line_id', '=', sol.id)], limit=1)
                
                # Verificar si la OC está cancelada -> desvincular
                po_name = ''
                po_qty = 0
                po_id = False
                po_state = ''
                
                if linked_po_line:
                    if linked_po_line.order_id.state == 'cancel':
                        # Desvincular la línea de venta de la OC cancelada
                        linked_po_line.write({'sale_line_id': False})
                    else:
                        po_name = linked_po_line.order_id.name
                        po_qty = linked_po_line.product_qty
                        po_id = linked_po_line.order_id.id
                        po_state = linked_po_line.order_id.state
                
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

            # Obtener proveedores del producto
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
                'vendors': vendors,  # Lista de todos los proveedores
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
        """Obtener órdenes de compra abiertas (borrador/enviado) de un proveedor"""
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
        """Obtener todos los proveedores activos"""
        partners = self.env['res.partner'].search([
            ('supplier_rank', '>', 0),
            ('active', '=', True)
        ], order='name')
        
        return [{
            'id': p.id,
            'name': p.name,
        } for p in partners]

    @api.model
    def create_purchase_orders(self, selected_line_ids, vendor_id=False, existing_po_id=False):
        """
        Crear o agregar a orden de compra.
        - vendor_id: Proveedor seleccionado manualmente
        - existing_po_id: Si se seleccionó una OC existente
        """
        sale_lines = self.env['sale.order.line'].browse(selected_line_ids)
        
        if not sale_lines:
            return {'error': 'No hay líneas seleccionadas'}

        # Si se especificó un proveedor, usamos ese para todas las líneas
        if vendor_id:
            vendor = self.env['res.partner'].browse(vendor_id)
            if not vendor.exists():
                return {'error': 'Proveedor no encontrado'}
            
            # Caso: Agregar a OC existente
            if existing_po_id:
                po = self.env['purchase.order'].browse(existing_po_id)
                if not po.exists() or po.state not in ['draft', 'sent']:
                    return {'error': 'La orden de compra no existe o ya fue confirmada'}
                
                # Actualizar origen
                new_origins = sale_lines.mapped('order_id.name')
                current_origin = po.origin or ''
                for name in new_origins:
                    if name not in current_origin:
                        current_origin += f", {name}" if current_origin else name
                po.write({'origin': current_origin})
                
            else:
                # Crear nueva OC
                po = self.env['purchase.order'].create({
                    'partner_id': vendor.id,
                    'origin': ', '.join(list(set(sale_lines.mapped('order_id.name')))),
                    'company_id': self.env.company.id,
                })
            
            # Crear líneas manteniendo la relación con sale_line_id
            for line in sale_lines:
                qty_pending = line.product_uom_qty - line.qty_delivered
                if qty_pending <= 0:
                    continue
                    
                self.env['purchase.order.line'].create({
                    'order_id': po.id,
                    'product_id': line.product_id.id,
                    'product_qty': qty_pending,
                    'product_uom_id': line.product_uom_id.id if hasattr(line, 'product_uom_id') else line.product_id.uom_id.id,
                    'price_unit': line.product_id.standard_price,
                    'sale_line_id': line.id,  # CLAVE: Mantener relación
                    'name': f"[{line.order_id.name}] {line.name or line.product_id.name}",
                    'date_planned': fields.Datetime.now(),
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
        
        # Caso legacy: Agrupar por proveedor preferido (comportamiento anterior)
        vendor_map = {}
        for line in sale_lines:
            seller = line.product_id._select_seller(quantity=line.product_uom_qty)
            v = seller.partner_id if seller else False
            
            if not v:
                continue
                
            if v.id not in vendor_map:
                vendor_map[v.id] = self.env['sale.order.line']
            vendor_map[v.id] |= line
            
        po_ids = []
        for vid, lines in vendor_map.items():
            po = self.env['purchase.order'].create({
                'partner_id': vid,
                'origin': ', '.join(list(set(lines.mapped('order_id.name')))),
                'company_id': self.env.company.id,
            })
            for l in lines:
                qty_pending = l.product_uom_qty - l.qty_delivered
                if qty_pending <= 0:
                    continue
                self.env['purchase.order.line'].create({
                    'order_id': po.id,
                    'product_id': l.product_id.id,
                    'product_qty': qty_pending,
                    'product_uom_id': l.product_uom_id.id if hasattr(l, 'product_uom_id') else l.product_id.uom_id.id,
                    'price_unit': l.product_id.standard_price,
                    'sale_line_id': l.id,
                    'name': f"[{l.order_id.name}] {l.name or l.product_id.name}",
                    'date_planned': fields.Datetime.now(),
                })
            po_ids.append(po.id)
        
        if len(po_ids) == 1:
            return {
                'name': 'Orden de Compra',
                'type': 'ir.actions.act_window',
                'res_model': 'purchase.order',
                'res_id': po_ids[0],
                'view_mode': 'form',
                'views': [[False, 'form']],
                'target': 'current',
            }
            
        return {
            'name': 'Órdenes de Compra Generadas',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('id', 'in', po_ids)],
            'target': 'current',
        }