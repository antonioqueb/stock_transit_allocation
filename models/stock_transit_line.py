# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import drop_view_if_exists

class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    _rec_name = 'lot_id'
    
    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    product_id = fields.Many2one('product.product', string='Descripción / Producto', required=True)
    
    # IMPORTANTE: required=False para permitir la fase de Solicitud/OC
    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=False)
    container_number = fields.Char(string='Contenedor')
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    x_grosor = fields.Float(related='lot_id.x_grosor', string='Grosor', readonly=True)
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto', readonly=True)
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho', readonly=True)
    
    product_uom_qty = fields.Float(string='M2 Embarcados', digits='Product Unit of Measure')
    partner_id = fields.Many2one('res.partner', string='Cliente / Proyecto', tracking=True, index=True)
    order_id = fields.Many2one('sale.order', string='Sales Order', 
        domain="[('partner_id', '=', partner_id), ('state', 'in', ['sale', 'done'])]",
        tracking=True)

    allocation_id = fields.Many2one('purchase.order.line.allocation', string='Asignación Origen')

    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido')
    ], string='Estado Asignación', default='available', required=True)

    # El purchase_id debe venir de la línea si no hay picking aún
    purchase_id = fields.Many2one('purchase.order', compute='_compute_purchase_id', string='OC Sistema', store=True)
    
    @api.depends('voyage_id.purchase_id', 'voyage_id.picking_id.purchase_id')
    def _compute_purchase_id(self):
        for line in self:
            line.purchase_id = line.voyage_id.purchase_id or line.voyage_id.picking_id.purchase_id

    date_order = fields.Datetime(related='purchase_id.date_order', string='Fecha OC', store=True)
    vendor_id = fields.Many2one('res.partner', related='purchase_id.partner_id', string='Proveedor', store=True)
    proforma_ref = fields.Char(related='purchase_id.partner_ref', string='Proforma / Ref Prov', store=True)
    salesperson_id = fields.Many2one('res.users', related='order_id.user_id', string='Vendedor', store=True)
    
    qty_proforma = fields.Float(string='Metraje Proforma', compute='_compute_po_so_qty', store=True)
    qty_original_demand = fields.Float(string='Metraje Pedido Original', compute='_compute_po_so_qty', store=True)

    voyage_status = fields.Selection(related='voyage_id.custom_status', string='Status', store=True)
    shipping_line = fields.Char(related='voyage_id.shipping_line', string='Naviera', store=True)
    bl_number = fields.Char(related='voyage_id.bl_number', string='Factura de Carga / BL', store=True)
    etd = fields.Date(related='voyage_id.etd', string='ETD', store=True)
    eta = fields.Date(related='voyage_id.eta', string='ETA', store=True)
    arrival_date = fields.Date(related='voyage_id.arrival_date', string='Llegada Real', store=True)
    notes = fields.Text(string='Comentarios')

    @api.depends('purchase_id', 'order_id', 'product_id', 'allocation_id')
    def _compute_po_so_qty(self):
        for line in self:
            po_qty = line.allocation_id.quantity if line.allocation_id else 0.0
            so_qty = line.allocation_id.quantity if line.allocation_id else 0.0
            if not line.allocation_id:
                if line.purchase_id:
                    po_qty = sum(line.purchase_id.order_line.filtered(lambda l: l.product_id == line.product_id).mapped('product_qty'))
                if line.order_id:
                    so_qty = sum(line.order_id.order_line.filtered(lambda l: l.product_id == line.product_id).mapped('product_uom_qty'))
            line.qty_proforma = po_qty
            line.qty_original_demand = so_qty

    @api.constrains('partner_id', 'order_id')
    def _check_order_assignment(self):
        for record in self:
            if record.partner_id and not record.order_id:
                raise ValidationError(_("Falta vincular la Orden de Venta para el cliente %s." % record.partner_id.name))

    def action_reassign_wizard(self):
        return {
            'name': 'Reasignar en Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'transit.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_ids': self.ids}
        }

class StockTransitSheet(models.Model):
    _name = 'stock.transit.sheet'
    _description = 'Sábana de Seguimiento (Resumen)'
    _auto = False
    _order = 'eta asc, voyage_id desc'

    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', readonly=True)
    product_id = fields.Many2one('product.product', string='Descripción / Producto', readonly=True)
    order_id = fields.Many2one('sale.order', string='Sales Order', readonly=True)
    purchase_id = fields.Many2one('purchase.order', string='OC Sistema', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Cliente / Proyecto', readonly=True)
    container_number = fields.Char(string='Contenedor', readonly=True)
    date_order = fields.Datetime(string='Fecha OC', readonly=True)
    proforma_ref = fields.Char(string='Proforma / Ref Prov', readonly=True)
    vendor_id = fields.Many2one('res.partner', string='Proveedor', readonly=True)
    
    # LOS KEYS DEBEN COINCIDIR EXACTAMENTE CON EL MODELO VOYAGE
    voyage_status = fields.Selection([
        ('solicitud', 'Solicitud Enviada'),
        ('production', 'Producción'),
        ('booking', 'Booking'),
        ('puerto_origen', 'Puerto Origen'),
        ('on_sea', 'En Altamar / Mar'),
        ('puerto_destino', 'Puerto Destino'),
        ('delivered', 'Entregado en Almacén'),
        ('cancel', 'Cancelado'),
    ], string='Status', readonly=True)
    
    shipping_line = fields.Char(string='Naviera', readonly=True)
    bl_number = fields.Char(string='Factura de Carga / BL', readonly=True)
    etd = fields.Date(string='ETD', readonly=True)
    eta = fields.Date(string='ETA', readonly=True)
    arrival_date = fields.Date(string='Llegada Real', readonly=True)
    product_uom_qty = fields.Float(string='M2 Embarcados', readonly=True)
    qty_proforma = fields.Float(string='Metraje Proforma', readonly=True)
    qty_original_demand = fields.Float(string='Metraje Pedido Original', readonly=True)
    salesperson_id = fields.Many2one('res.users', string='Vendedor', readonly=True)

    def init(self):
        drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW stock_transit_sheet AS (
                SELECT
                    MIN(l.id) as id,
                    l.voyage_id,
                    l.product_id,
                    l.order_id,
                    l.purchase_id,
                    l.partner_id,
                    l.container_number,
                    MAX(l.date_order) as date_order,
                    MAX(l.proforma_ref) as proforma_ref,
                    MAX(l.vendor_id) as vendor_id,
                    MAX(l.voyage_status) as voyage_status,
                    MAX(l.shipping_line) as shipping_line,
                    MAX(l.bl_number) as bl_number,
                    MAX(l.etd) as etd,
                    MAX(l.eta) as eta,
                    MAX(l.arrival_date) as arrival_date,
                    MAX(l.salesperson_id) as salesperson_id,
                    SUM(l.product_uom_qty) as product_uom_qty,
                    MAX(l.qty_proforma) as qty_proforma,
                    MAX(l.qty_original_demand) as qty_original_demand
                FROM
                    stock_transit_line l
                GROUP BY
                    l.voyage_id, l.product_id, l.order_id, l.purchase_id, l.partner_id, l.container_number
            )
        """)