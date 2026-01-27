# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import drop_view_if_exists
import logging

_logger = logging.getLogger(__name__)

class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    _rec_name = 'lot_id'
    
    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    product_id = fields.Many2one('product.product', string='Descripción / Producto', required=True)
    
    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=False)
    container_number = fields.Char(string='Contenedor')
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    x_grosor = fields.Float(related='lot_id.x_grosor', string='Grosor', readonly=True)
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto', readonly=True)
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho', readonly=True)
    
    product_uom_qty = fields.Float(string='M2 Embarcados', digits='Product Unit of Measure')
    
    # CAMPO CLIENTE: Dominio dinámico calculado
    eligible_partner_ids = fields.Many2many(
        'res.partner', 
        compute='_compute_eligible_partners',
        string='Clientes Elegibles'
    )
    partner_id = fields.Many2one(
        'res.partner', 
        string='Cliente / Proyecto', 
        tracking=True, 
        index=True,
        domain="[('id', 'in', eligible_partner_ids)]"
    )
    
    # CAMPO ORDEN: Dominio dinámico basado en cliente Y producto
    eligible_order_ids = fields.Many2many(
        'sale.order',
        compute='_compute_eligible_orders',
        string='Órdenes Elegibles'
    )
    order_id = fields.Many2one(
        'sale.order', 
        string='Sales Order',
        tracking=True,
        domain="[('id', 'in', eligible_order_ids)]"
    )

    allocation_id = fields.Many2one('purchase.order.line.allocation', string='Asignación Origen')

    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido')
    ], string='Estado Asignación', default='available', required=True)

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

    # =========================================================================
    # CÓMPUTOS PARA DOMINIOS DINÁMICOS
    # =========================================================================
    
    @api.depends('product_id')
    def _compute_eligible_partners(self):
        """
        Calcula los clientes elegibles: aquellos que tienen órdenes confirmadas
        con líneas del producto de esta línea de tránsito.
        """
        for line in self:
            if not line.product_id:
                line.eligible_partner_ids = [(5, 0, 0)]
                continue
            
            # Buscar órdenes de venta confirmadas que tengan este producto
            sale_lines = self.env['sale.order.line'].search([
                ('product_id', '=', line.product_id.id),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])
            
            partner_ids = sale_lines.mapped('order_id.partner_id').ids
            line.eligible_partner_ids = [(6, 0, partner_ids)]

    @api.depends('product_id', 'partner_id')
    def _compute_eligible_orders(self):
        """
        Calcula las órdenes elegibles: órdenes del cliente seleccionado
        que contengan el producto de esta línea.
        """
        for line in self:
            if not line.product_id or not line.partner_id:
                line.eligible_order_ids = [(5, 0, 0)]
                continue
            
            # Buscar órdenes del cliente que tengan este producto
            sale_lines = self.env['sale.order.line'].search([
                ('product_id', '=', line.product_id.id),
                ('order_id.partner_id', '=', line.partner_id.id),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])
            
            order_ids = sale_lines.mapped('order_id').ids
            line.eligible_order_ids = [(6, 0, order_ids)]

    # =========================================================================
    # ONCHANGE PARA LIMPIAR Y ASIGNAR AUTOMÁTICAMENTE
    # =========================================================================
    
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """
        Al cambiar el cliente:
        - Si se limpia el cliente, limpiar la orden
        - Si el cliente tiene solo una orden elegible, seleccionarla automáticamente
        """
        if not self.partner_id:
            self.order_id = False
            return
        
        # Recalcular órdenes elegibles
        if not self.product_id:
            self.order_id = False
            return
            
        sale_lines = self.env['sale.order.line'].search([
            ('product_id', '=', self.product_id.id),
            ('order_id.partner_id', '=', self.partner_id.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
        ])
        
        eligible_orders = sale_lines.mapped('order_id')
        
        if len(eligible_orders) == 1:
            # Auto-seleccionar si solo hay una orden
            self.order_id = eligible_orders[0]
        elif self.order_id and self.order_id not in eligible_orders:
            # Limpiar si la orden actual no es válida para el nuevo cliente
            self.order_id = False

    # =========================================================================
    # WRITE OVERRIDE PARA EJECUTAR LÓGICA DE RESERVA
    # =========================================================================
    
    def write(self, vals):
        """
        Override write para detectar cambios en partner_id/order_id
        y ejecutar la lógica de reserva automáticamente.
        """
        # Detectar si hay cambio de asignación
        assignment_changed = 'partner_id' in vals or 'order_id' in vals
        
        # Guardar estado previo para comparación
        old_assignments = {}
        if assignment_changed:
            for line in self:
                old_assignments[line.id] = {
                    'partner_id': line.partner_id.id if line.partner_id else False,
                    'order_id': line.order_id.id if line.order_id else False,
                }
        
        # Ejecutar write estándar
        res = super(StockTransitLine, self).write(vals)
        
        # Procesar cambios de asignación
        if assignment_changed:
            for line in self:
                old = old_assignments.get(line.id, {})
                new_partner = line.partner_id
                new_order = line.order_id
                
                # Verificar si realmente cambió
                if old.get('partner_id') != (new_partner.id if new_partner else False) or \
                   old.get('order_id') != (new_order.id if new_order else False):
                    
                    # Actualizar estado de asignación
                    new_status = 'reserved' if (new_partner and new_order) else 'available'
                    if line.allocation_status != new_status:
                        super(StockTransitLine, line).write({'allocation_status': new_status})
                    
                    # Ejecutar lógica de reserva/liberación
                    if new_partner and new_order:
                        line._execute_reservation_logic(new_partner, new_order)
                    elif not new_partner:
                        line._execute_release_logic()
                    
                    # Log en el viaje
                    if line.voyage_id:
                        if new_partner and new_order:
                            msg = f"🔄 <b>Asignación:</b> {line.lot_id.name or line.product_id.name}<br/>"
                            msg += f"→ {new_partner.name} / {new_order.name}"
                        else:
                            msg = f"🔓 <b>Liberado a Stock:</b> {line.lot_id.name or line.product_id.name}"
                        line.voyage_id.message_post(body=msg)
        
        return res

    def _execute_reservation_logic(self, partner, order):
        """
        Ejecuta la lógica de reserva cuando se asigna a un cliente/orden.
        Crea Hold Order si hay lote físico.
        """
        self.ensure_one()
        
        if not self.lot_id or not self.quant_id:
            _logger.info(f"TransitLine {self.id}: Sin lote físico, solo asignación visual")
            return
        
        # Verificar si ya existe hold activo para este quant
        existing_hold = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo')
        ], limit=1)
        
        if existing_hold:
            _logger.info(f"TransitLine {self.id}: Ya existe hold activo, verificando...")
            # Si ya está reservado para el mismo cliente, no hacer nada
            if existing_hold.order_id and existing_hold.order_id.partner_id == partner:
                return
            # Cancelar el hold anterior si es de otro cliente
            try:
                existing_hold.action_cancelar_hold()
            except Exception as e:
                _logger.warning(f"No se pudo cancelar hold existente: {e}")
        
        # Crear nuevo Hold Order usando TransitManager
        try:
            from .utils.transit_manager import TransitManager
            TransitManager.reassign_lot(
                self.env,
                self,
                partner,
                order,
                notes="Asignación directa desde Torre de Control"
            )
        except Exception as e:
            _logger.error(f"Error creando reserva: {e}")

    def _execute_release_logic(self):
        """
        Ejecuta la lógica de liberación cuando se quita el cliente.
        Cancela Hold Orders existentes.
        """
        self.ensure_one()
        
        if not self.quant_id:
            return
        
        existing_holds = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo')
        ])
        
        for hold in existing_holds:
            try:
                hold.action_cancelar_hold()
                _logger.info(f"TransitLine {self.id}: Hold cancelado")
            except Exception as e:
                _logger.error(f"Error cancelando hold: {e}")

    # =========================================================================
    # MÉTODOS LEGACY (mantener compatibilidad)
    # =========================================================================

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
                raise ValidationError(_("Debe seleccionar una Orden de Venta para el cliente %s." % record.partner_id.name))


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