# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SaleOrderConsolidatePurchase(models.TransientModel):
    _name = 'sale.order.consolidate.purchase'
    _description = 'Consolidar Ventas en una Compra Global'

    vendor_id = fields.Many2one('res.partner', string='Proveedor', required=True, 
        domain=[('supplier_rank', '>', 0)],
        help="Seleccione al proveedor al que se le enviará la orden global.")
    
    # OPCIÓN NUEVA: Elegir si crear nueva o agregar a existente
    target_type = fields.Selection([
        ('new', 'Crear Nueva Orden de Compra'),
        ('exist', 'Agregar a Orden Existente')
    ], string="Acción", default='new', required=True)

    # Campo para seleccionar la compra existente (solo borradores)
    purchase_order_id = fields.Many2one('purchase.order', string="Orden de Compra Existente",
        domain="[('partner_id', '=', vendor_id), ('state', 'in', ['draft', 'sent'])]")

    sale_order_ids = fields.Many2many('sale.order', string='Pedidos a Consolidar')
    
    only_mto_lines = fields.Boolean(string='Solo productos "Mandar Pedir"', default=True,
        help="Si se marca, solo se agregarán a la compra los productos que tengan el check 'Mandar Pedir'.")

    def action_create_consolidated_po(self):
        self.ensure_one()
        if not self.sale_order_ids:
            raise UserError(_("No hay pedidos seleccionados para consolidar."))

        purchase_order = False

        # CASO 1: Crear Nueva Orden de Compra
        if self.target_type == 'new':
            origin_names = ', '.join(self.sale_order_ids.mapped('name'))
            po_vals = {
                'partner_id': self.vendor_id.id,
                'origin': origin_names,
                'date_order': fields.Datetime.now(),
                'company_id': self.env.company.id,
            }
            purchase_order = self.env['purchase.order'].create(po_vals)
        
        # CASO 2: Agregar a Existente
        elif self.target_type == 'exist':
            if not self.purchase_order_id:
                raise UserError(_("Debe seleccionar una Orden de Compra existente."))
            
            purchase_order = self.purchase_order_id
            
            # Actualizar el campo origen para incluir los nuevos pedidos (sin duplicar)
            new_origins = self.sale_order_ids.mapped('name')
            current_origin = purchase_order.origin or ''
            
            combined_origin = current_origin
            for name in new_origins:
                if name not in current_origin:
                    combined_origin += f", {name}" if combined_origin else name
            
            purchase_order.write({'origin': combined_origin})

        # --- CREACIÓN DE LÍNEAS ---
        lines_created = 0
        
        for so in self.sale_order_ids:
            for line in so.order_line:
                # Ignorar secciones, notas o servicios
                if line.display_type or line.product_id.type == 'service':
                    continue
                
                # Filtro: Solo lo que está marcado para pedir
                if self.only_mto_lines and not line.auto_transit_assign:
                    continue

                # Evitar cantidades cero o negativas
                if line.product_uom_qty <= 0:
                    continue

                # Lógica de "Agregar a línea existente" vs "Crear nueva línea"
                # Para la Torre de Control, es MEJOR crear líneas nuevas separadas para mantener
                # la trazabilidad 1 a 1 entre Venta y Compra (sale_line_id).
                # Si agrupamos, perdemos la referencia exacta de qué cantidad es para quién.
                
                pol_vals = {
                    'order_id': purchase_order.id,
                    'product_id': line.product_id.id,
                    'name': line.name or line.product_id.name,
                    'product_qty': line.product_uom_qty, # Cantidad completa de la venta
                    'product_uom': line.product_uom.id,
                    'price_unit': line.product_id.standard_price, 
                    'date_planned': fields.Datetime.now(),
                    
                    # VINCULACIÓN CLAVE:
                    'sale_line_id': line.id, 
                }
                self.env['purchase.order.line'].create(pol_vals)
                lines_created += 1

        if lines_created == 0:
            raise UserError(_("No se encontraron líneas válidas para generar la compra (Revise el check 'Mandar Pedir')."))

        return {
            'name': 'Orden de Compra Global',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': purchase_order.id,
            'view_mode': 'form',
        }