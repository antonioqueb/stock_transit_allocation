# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    _rec_name = 'lot_id'
    
    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    
    # --- INFO DE CARGA ---
    product_id = fields.Many2one('product.product', string='Descripción / Producto', required=True)
    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=True)
    container_number = fields.Char(string='Contenedor', help="Contenedor específico")
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    x_grosor = fields.Float(related='lot_id.x_grosor', string='Grosor')
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto')
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho')
    
    # M2 Embarcados (Real)
    product_uom_qty = fields.Float(string='M2 Embarcados', digits='Product Unit of Measure')
    
    # --- ASIGNACIÓN (VENTAS) ---
    partner_id = fields.Many2one('res.partner', string='Cliente / Proyecto', tracking=True, index=True)
    order_id = fields.Many2one('sale.order', string='Sales Order', 
        domain="[('partner_id', '=', partner_id), ('state', 'in', ['sale', 'done'])]",
        tracking=True, help="Pedido específico del cliente.")

    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido')
    ], string='Estado Asignación', default='available', required=True)

    # --- CAMPOS PLANOS PARA VISTA "SÁBANA DE SEGUIMIENTO" (EXCEL) ---
    
    # 1. Datos de Compra (OC Sistema)
    # Se obtienen navegando Voyage -> Picking -> Purchase Order
    purchase_id = fields.Many2one('purchase.order', related='voyage_id.picking_id.purchase_id', string='OC Sistema', store=True)
    date_order = fields.Datetime(related='purchase_id.date_order', string='Fecha OC', store=True)
    vendor_id = fields.Many2one('res.partner', related='purchase_id.partner_id', string='Proveedor', store=True)
    vendor_country_id = fields.Many2one('res.country', related='vendor_id.country_id', string='País', store=True)
    proforma_ref = fields.Char(related='purchase_id.partner_ref', string='Proforma / Ref Prov', store=True)
    currency_id = fields.Many2one('res.currency', related='purchase_id.currency_id', string='Moneda', store=True)
    
    # Precio (Aprox. tomamos el costo del producto o de la línea de compra si pudiéramos vincularla exactamente)
    # Usaremos el costo estándar del producto como referencia rápida o si es posible vincular a la línea de compra
    price_unit = fields.Float(related='product_id.standard_price', string='Precio / M2 (Est.)')

    # 2. Datos de Venta Extra
    salesperson_id = fields.Many2one('res.users', related='order_id.user_id', string='Vendedor', store=True)
    
    # 3. Metrajes Teóricos
    # Metraje Proforma (Tomado de lo que se pidió en la OC globalmente para este producto - Aprox)
    qty_proforma = fields.Float(string='Metraje Proforma', help="Cantidad total en la OC original", compute='_compute_po_so_qty')
    # Metraje Pedido Original (Tomado de la SO)
    qty_original_demand = fields.Float(string='Metraje Pedido Original', help="Cantidad original demandada en la SO", compute='_compute_po_so_qty')

    # 4. Datos de Logística (Viaje)
    voyage_status = fields.Selection(related='voyage_id.custom_status', string='Status', store=True)
    shipping_line = fields.Char(related='voyage_id.shipping_line', string='Naviera', store=True)
    bl_number = fields.Char(related='voyage_id.bl_number', string='Factura de Carga / BL', store=True)
    transit_days = fields.Integer(related='voyage_id.transit_days_expected', string='Tiempo Tránsito', store=True)
    etd = fields.Date(related='voyage_id.etd', string='ETD', store=True)
    eta = fields.Date(related='voyage_id.eta', string='ETA', store=True)
    arrival_date = fields.Date(related='voyage_id.arrival_date', string='Llegada Real', store=True)
    
    # 5. Comentarios
    notes = fields.Text(string='Comentarios', help="Notas libres de seguimiento")

    @api.depends('purchase_id', 'order_id', 'product_id')
    def _compute_po_so_qty(self):
        for line in self:
            # Lógica simple: Sumar líneas de la OC para este producto
            po_qty = 0.0
            if line.purchase_id:
                po_lines = line.purchase_id.order_line.filtered(lambda l: l.product_id == line.product_id)
                po_qty = sum(po_lines.mapped('product_qty'))
            
            # Lógica simple: Sumar líneas de la SO para este producto
            so_qty = 0.0
            if line.order_id:
                so_lines = line.order_id.order_line.filtered(lambda l: l.product_id == line.product_id)
                so_qty = sum(so_lines.mapped('product_uom_qty'))
            
            line.qty_proforma = po_qty
            line.qty_original_demand = so_qty

    @api.constrains('partner_id', 'order_id')
    def _check_order_assignment(self):
        for record in self:
            if record.partner_id and not record.order_id:
                raise ValidationError(_(
                    "Error de Integridad: El lote %s está asignado al cliente %s "
                    "pero falta vincular la Orden de Venta." 
                    % (record.lot_id.name, record.partner_id.name)
                ))

    def action_reassign_wizard(self):
        return {
            'name': 'Reasignar en Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'transit.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_ids': self.ids,
                'default_current_partner_id': self.partner_id.id,
                'default_current_order_id': self.order_id.id,
            }
        }