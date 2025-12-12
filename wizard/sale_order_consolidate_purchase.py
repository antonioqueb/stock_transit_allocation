# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SaleOrderConsolidatePurchase(models.TransientModel):
    _name = 'sale.order.consolidate.purchase'
    _description = 'Consolidar Ventas en una Compra Global'

    vendor_id = fields.Many2one('res.partner', string='Proveedor', required=True, 
        domain=[('supplier_rank', '>', 0)],
        help="Seleccione al proveedor al que se le enviará la orden global.")
    
    sale_order_ids = fields.Many2many('sale.order', string='Pedidos a Consolidar')
    
    only_mto_lines = fields.Boolean(string='Solo productos "Mandar Pedir"', default=True,
        help="Si se marca, solo se agregarán a la compra los productos que tengan el check 'Mandar Pedir'.")

    def action_create_consolidated_po(self):
        self.ensure_one()
        if not self.sale_order_ids:
            raise UserError(_("No hay pedidos seleccionados para consolidar."))

        # 1. Crear el Encabezado de la Compra
        # Juntamos los nombres de los pedidos en el campo 'Origen' (Ej: SO001, SO004, SO008)
        origin_names = ', '.join(self.sale_order_ids.mapped('name'))
        
        po_vals = {
            'partner_id': self.vendor_id.id,
            'origin': origin_names,
            'date_order': fields.Datetime.now(),
            'company_id': self.env.company.id,
        }
        purchase_order = self.env['purchase.order'].create(po_vals)

        # 2. Recorrer cada línea de venta y convertirla en línea de compra
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

                # 3. CREACIÓN DE LA LÍNEA Y VINCULACIÓN (La parte más importante)
                # No agrupamos productos iguales. Si hay 2 pedidos con Mármol Blanco,
                # creamos 2 líneas de compra separadas. Esto es vital para la Torre de Control.
                pol_vals = {
                    'order_id': purchase_order.id,
                    'product_id': line.product_id.id,
                    'name': line.name or line.product_id.name,
                    'product_qty': line.product_uom_qty, # Pedimos lo que se vendió
                    'product_uom': line.product_uom.id,
                    'price_unit': line.product_id.standard_price, # Costo estimado
                    'date_planned': fields.Datetime.now(),
                    
                    # AQUÍ ESTÁ EL TRUCO: Guardamos el ID de la línea de venta
                    'sale_line_id': line.id, 
                }
                self.env['purchase.order.line'].create(pol_vals)
                lines_created += 1

        if lines_created == 0:
            raise UserError(_("No se encontraron líneas válidas para generar la compra (Revise el check 'Mandar Pedir')."))

        # 4. Abrir la Compra creada
        return {
            'name': 'Orden de Compra Global',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': purchase_order.id,
            'view_mode': 'form',
        }