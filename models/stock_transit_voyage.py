# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    
    custom_status = fields.Selection([
        ('solicitud', 'Solicitud Enviada'),
        ('production', 'Producción'),
        ('booking', 'Booking'),
        ('puerto_origen', 'Puerto Origen'),
        ('on_sea', 'En Altamar / Mar'),
        ('puerto_destino', 'Puerto Destino'),
        ('arrived_port', 'Arribo a Puerto (Trámite)'), 
        ('reception_pending', 'En Recepción Física'),   
        ('delivered', 'Entregado en Almacén'),
        ('cancel', 'Cancelado'),
    ], string='Estado', default='solicitud', tracking=True)
    
    shipping_line = fields.Char(string='Naviera', tracking=True)
    transit_days_expected = fields.Integer(string='Tiempo Tránsito (Días)')
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields.Char(string='No. Viaje', tracking=True)
    
    # =========================================================================
    # CAMBIO: container_number ahora es COMPUTADO desde las líneas del viaje
    # Ya no es un campo editable manual en el header
    # =========================================================================
    container_number = fields.Char(
        string='Contenedores', 
        compute='_compute_container_number',
        store=True,
        tracking=True,
        help="Resumen automático de contenedores presentes en las líneas del viaje"
    )
    
    bl_number = fields.Char(string='Folio Compra / BL', tracking=True)
    
    etd = fields.Date(string='ETD (Salida Estimada)')
    eta = fields.Date(string='ETA (Llegada Estimada)', required=False, tracking=True)
    arrival_date = fields.Date(string='Llegada Real', tracking=True)

    picking_id = fields.Many2one('stock.picking', string='Recepción (Tránsito)', 
        domain=[('picking_type_code', '=', 'incoming')], help="Recepción administrativa en ubicación de tránsito")
    
    reception_picking_id = fields.Many2one('stock.picking', string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')], readonly=True,
        help="Transferencia interna para ingreso físico y validación de medidas (Worksheet)")

    purchase_id = fields.Many2one('purchase.order', string='Orden de Compra Origen', readonly=True)
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    line_ids = fields.One2many('stock.transit.line', 'voyage_id', string='Contenido (Lotes)')
    
    total_m2 = fields.Float(string='Total m²', compute='_compute_totals', store=True)
    allocated_m2 = fields.Float(string='Asignado m²', compute='_compute_totals', store=True)
    allocation_percent = fields.Float(string='% Asignación', compute='_compute_totals')
    transit_progress = fields.Integer(string='Progreso Viaje', compute='_compute_transit_progress', store=False)

    # =========================================================================
    # COMPUTE: container_number desde líneas
    # =========================================================================
    @api.depends('line_ids.container_number')
    def _compute_container_number(self):
        for rec in self:
            containers = set()
            for line in rec.line_ids:
                if line.container_number and line.container_number not in ('', 'PENDIENTE', 'SN', 'False'):
                    containers.add(line.container_number)
            rec.container_number = ', '.join(sorted(containers)) if containers else 'PENDIENTE'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
            # Limpiar container_number de vals ya que ahora es computado
            vals.pop('container_number', None)
        return super(StockTransitVoyage, self).create(vals_list)

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status')
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('product_uom_qty'))
            allocated = sum(rec.line_ids.filtered(lambda l: l.allocation_status == 'reserved').mapped('product_uom_qty'))
            rec.total_m2 = total
            rec.allocated_m2 = allocated
            rec.allocation_percent = (allocated / total) * 100 if total > 0 else 0

    @api.depends('etd', 'eta', 'custom_status', 'create_date')
    def _compute_transit_progress(self):
        today = fields.Date.today()
        for rec in self:
            if rec.custom_status == 'delivered':
                rec.transit_progress = 100
                continue
            if rec.custom_status == 'cancel':
                rec.transit_progress = 0
                continue

            start_date = rec.etd
            if not start_date and rec.create_date:
                start_date = rec.create_date.date()
            if not start_date or not rec.eta:
                rec.transit_progress = 0
                continue
            
            if today < start_date:
                rec.transit_progress = 0
            elif today > rec.eta:
                rec.transit_progress = 95 
            else:
                total_days = (rec.eta - start_date).days
                elapsed = (today - start_date).days
                if total_days > 0:
                    progress = int((elapsed / total_days) * 100)
                    rec.transit_progress = max(0, min(95, progress))
                else:
                    rec.transit_progress = 0

    def action_confirm_transit(self):
        self.write({'custom_status': 'on_sea'})
        if self.picking_id and self.picking_id.purchase_id:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', self.picking_id.purchase_id.id),
                ('state', '=', 'pending')
            ])
            allocations.action_mark_in_transit()

    def action_arrive(self):
        if self.reception_picking_id and self.reception_picking_id.state != 'done':
            raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física (Worksheet) haya sido validada."))

        self.write({
            'arrival_date': fields.Date.today(),
            'custom_status': 'delivered'
        })
        for line in self.line_ids:
            if line.allocation_id and line.allocation_id.state != 'done':
                line.allocation_id.action_mark_received(line.product_uom_qty)

    def action_cancel(self):
        self.write({'custom_status': 'cancel'})

    
    def action_generate_reception(self):
        """
        PASO 1: Genera el Picking y los Movimientos (Demanda) en estado BORRADOR.
        """
        self.ensure_one()
        _logger.info(f"[TC_DEBUG] >>> PASO 1: Creando Picking para Viaje: {self.name}")

        if self.reception_picking_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.reception_picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not picking_type:
            raise UserError(_("No se encontró un tipo de operación 'Internal Transfer'."))

        valid_lines = self.line_ids.filtered(lambda l: l.lot_id and l.quant_id)
        if not valid_lines:
            raise UserError(_("No hay líneas válidas para mover."))
            
        source_location = valid_lines[0].quant_id.location_id
        if not source_location:
             raise UserError(_("No se pudo determinar la ubicación de origen."))

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': f"{self.name} (Recepción Física)",
            'company_id': self.company_id.id,
            'move_type': 'direct',
            'supplier_bl_number': self.bl_number if hasattr(self.env['stock.picking'], 'supplier_bl_number') else False,
            'supplier_container_no': self.container_number if hasattr(self.env['stock.picking'], 'supplier_container_no') else False,
            'supplier_origin': 'TRÁNSITO' if hasattr(self.env['stock.picking'], 'supplier_origin') else False,
        })

        products_map = {}
        for line in valid_lines:
            if line.product_uom_qty <= 0: continue
            if line.product_id not in products_map:
                products_map[line.product_id] = 0.0
            products_map[line.product_id] += line.product_uom_qty

        for product, qty in products_map.items():
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty,
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': source_location.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'company_id': self.company_id.id,
                'state': 'draft',
            })
        
        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending'
        })
        
        _logger.info(f"[TC_DEBUG] Picking {picking.name} creado en BORRADOR. ID: {picking.id}")

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def action_load_from_purchase(self):
        """
        Carga líneas en el Voyage desde la Orden de Compra.
        """
        self.ensure_one()
        if not self.purchase_id:
            return
        
        existing_alloc_ids = self.line_ids.mapped('allocation_id.id')
        allocations = self.env['purchase.order.line.allocation'].search([
            ('purchase_order_id', '=', self.purchase_id.id),
            ('id', 'not in', existing_alloc_ids)
        ])
        
        transit_lines = []
        for alloc in allocations:
            transit_lines.append({
                'voyage_id': self.id,
                'product_id': alloc.product_id.id,
                'product_uom_qty': alloc.quantity,
                'partner_id': alloc.partner_id.id,
                'order_id': alloc.sale_order_id.id,
                'allocation_id': alloc.id,
                'allocation_status': 'reserved',
                'container_number': 'PENDIENTE',
            })
        
        existing_stock_lines = self.line_ids.filtered(
            lambda l: not l.allocation_id and not l.partner_id and not l.order_id
        )
        existing_stock_by_product = {l.product_id.id: l for l in existing_stock_lines}
        
        for po_line in self.purchase_id.order_line:
            total_po_qty = po_line.product_qty
            total_allocated = sum(po_line.allocation_ids.mapped('quantity'))
            extra_for_stock = total_po_qty - total_allocated
            
            product_id = po_line.product_id.id
            
            if product_id in existing_stock_by_product:
                existing_line = existing_stock_by_product[product_id]
                if extra_for_stock > 0:
                    if existing_line.product_uom_qty != extra_for_stock:
                        existing_line.write({'product_uom_qty': extra_for_stock})
                else:
                    existing_line.unlink()
            elif extra_for_stock > 0:
                transit_lines.append({
                    'voyage_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': extra_for_stock,
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'container_number': 'PENDIENTE',
                    'notes': 'Para Stock (cantidad extra en OC)',
                })
        
        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)

    def action_load_from_picking(self):
        """
        FIX: Ahora extrae container_number desde lot.x_contenedor (campo del PL)
        en lugar de lot.ref (que siempre está vacío).
        """
        self.ensure_one()
        if not self.picking_id:
            return
        
        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)
        placeholder_lines.unlink()

        transit_lines = []
        from .utils.transit_manager import TransitManager
        
        purchase = self.picking_id.purchase_id
        allocations_map = {}
        allocation_consumed = {}
        
        if purchase:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', purchase.id),
                ('state', 'not in', ['done', 'cancelled'])
            ], order='id asc')
            
            for alloc in allocations:
                if alloc.product_id.id not in allocations_map:
                    allocations_map[alloc.product_id.id] = []
                allocations_map[alloc.product_id.id].append(alloc)
                allocation_consumed[alloc.id] = 0.0

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue
            
            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False
            product_id = move_line.product_id.id
            qty_done = move_line.quantity  # ODOO 19 FIX
            
            if product_id in allocations_map:
                for alloc in allocations_map[product_id]:
                    already_received = alloc.qty_received
                    consumed_this_load = allocation_consumed.get(alloc.id, 0.0)
                    total_consumed = already_received + consumed_this_load
                    remaining = alloc.quantity - total_consumed
                    
                    if remaining > 0:
                        allocation_to_use = alloc
                        partner_to_assign = alloc.partner_id
                        order_to_assign = alloc.sale_order_id
                        
                        if alloc.sale_line_id:
                            auto_assign = getattr(alloc.sale_line_id, 'auto_transit_assign', True)
                            if not auto_assign:
                                partner_to_assign = False
                                order_to_assign = False
                                allocation_to_use = False
                                continue
                        
                        allocation_consumed[alloc.id] = consumed_this_load + qty_done
                        break

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id), 
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id)
            ], limit=1)

            # =====================================================================
            # FIX PRINCIPAL: Extraer contenedor desde x_contenedor del lote
            # El campo x_contenedor se llena desde el Packing List (portal/wizard)
            # Fallback a lot.ref si x_contenedor está vacío
            # =====================================================================
            lot_container = ''
            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref
            # =====================================================================

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container,  # FIX: Ahora usa x_contenedor
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }
            transit_lines.append(line_vals)
        
        created_lines = self.env['stock.transit.line'].create(transit_lines)
        
        # =====================================================================
        # ELIMINADO: Ya no sobreescribimos self.container_number manualmente
        # Ahora es un campo computado que se calcula desde las líneas
        # =====================================================================

        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)
                new_received = alloc.qty_received + qty_consumed
                alloc.write({'qty_received': min(new_received, alloc.quantity), 'state': 'in_transit'})

        lines_by_order = {}
        for line in created_lines:
            if line.partner_id and line.order_id:
                key = (line.partner_id, line.order_id)
                if key not in lines_by_order:
                    lines_by_order[key] = []
                lines_by_order[key].append(line)
        
        for (partner, order), lines in lines_by_order.items():
            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            })
            for line in lines:
                TransitManager.reassign_lot(self.env, line, partner, order, notes=False, hold_order_obj=hold_order)
            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()