# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from collections import defaultdict

class SaleOrderConsolidatePurchase(models.TransientModel):
    _name = 'sale.order.consolidate.purchase'
    _description = 'Consolidar Ventas en una Compra Global'

    @api.model
    def default_get(self, fields_list):
        res = super(SaleOrderConsolidatePurchase, self).default_get(fields_list)
        if self.env.context.get('active_model') == 'sale.order' and self.env.context.get('active_ids'):
            res['sale_order_ids'] = [(6, 0, self.env.context.get('active_ids'))]
        return res

    vendor_id = fields.Many2one('res.partner', string='Proveedor', required=True, 
        domain=[('supplier_rank', '>', 0)])
    
    target_type = fields.Selection([
        ('new', 'Crear Nueva Orden de Compra'),
        ('exist', 'Agregar a Orden Existente')
    ], string="Acción", default='new', required=True)

    purchase_order_id = fields.Many2one('purchase.order', string="Orden de Compra Existente",
        domain="[('partner_id', '=', vendor_id), ('state', 'in', ['draft', 'sent'])]")

    sale_order_ids = fields.Many2many('sale.order', string='Pedidos a Consolidar')
    
    only_mto_lines = fields.Boolean(string='Solo productos "Mandar Pedir"', default=True)

    def action_create_consolidated_po(self):
        self.ensure_one()
        if not self.sale_order_ids:
            raise UserError(_("No hay pedidos seleccionados para consolidar."))

        if self.target_type == 'new':
            origin_names = ', '.join(self.sale_order_ids.mapped('name'))
            purchase_order = self.env['purchase.order'].create({
                'partner_id': self.vendor_id.id,
                'origin': origin_names,
                'date_order': fields.Datetime.now(),
                'company_id': self.env.company.id,
            })
        else:
            if not self.purchase_order_id:
                raise UserError(_("Debe seleccionar una Orden de Compra existente."))
            
            purchase_order = self.purchase_order_id
            new_origins = self.sale_order_ids.mapped('name')
            current_origin = purchase_order.origin or ''
            
            for name in new_origins:
                if name not in current_origin:
                    current_origin += f", {name}" if current_origin else name
            
            purchase_order.write({'origin': current_origin})

        # CONSOLIDACIÓN POR PRODUCTO
        lines_by_product = defaultdict(list)
        
        for so in self.sale_order_ids:
            for line in so.order_line:
                if line.display_type or line.product_id.type == 'service':
                    continue
                if self.only_mto_lines and not line.auto_transit_assign:
                    continue
                if line.product_uom_qty <= 0:
                    continue

                lines_by_product[line.product_id.id].append({
                    'sale_line': line,
                    'qty': line.product_uom_qty,
                })
        
        if not lines_by_product:
            raise UserError(_("No se encontraron líneas válidas para generar la compra."))

        for product_id, sale_line_data in lines_by_product.items():
            product = self.env['product.product'].browse(product_id)
            total_qty = sum(d['qty'] for d in sale_line_data)
            
            existing_po_line = purchase_order.order_line.filtered(lambda l: l.product_id.id == product_id)
            
            if existing_po_line:
                po_line = existing_po_line[0]
                new_qty = po_line.product_qty + total_qty
                po_line.write({'product_qty': new_qty})
            else:
                uom_id = product.uom_id.id
                so_refs = list(set([d['sale_line'].order_id.name for d in sale_line_data]))
                
                po_line = self.env['purchase.order.line'].create({
                    'order_id': purchase_order.id,
                    'product_id': product_id,
                    'name': f"[{', '.join(so_refs)}] {product.name}",
                    'product_qty': total_qty,
                    'product_uom_id': uom_id,
                    'price_unit': product.standard_price, 
                    'date_planned': fields.Datetime.now(),
                })
            
            # Crear ALLOCATIONS
            for data in sale_line_data:
                self.env['purchase.order.line.allocation'].create({
                    'purchase_line_id': po_line.id,
                    'sale_line_id': data['sale_line'].id,
                    'quantity': data['qty'],
                    'state': 'pending',
                })

        return {
            'name': 'Orden de Compra Global',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': purchase_order.id,
            'view_mode': 'form',
        }