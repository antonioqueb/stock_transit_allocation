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
    container_number = fields.Char(string='Contenedor(es)', tracking=True)
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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
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
            Genera una Transferencia Interna (Transit -> Stock).
            FIX: Estrategia de dos pasos para evitar el Auto-Validate.
            1. Creamos la línea con Quantity = 0 (Reserva el lote).
            2. Actualizamos la Quantity (Indica que está listo para validar).
            """
            self.ensure_one()
            _logger.info(f"[TC_DEBUG] >>> GENERATE RECEPTION START for Voyage: {self.name}")

            if self.reception_picking_id:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'stock.picking',
                    'res_id': self.reception_picking_id.id,
                    'view_mode': 'form',
                    'target': 'current',
                }

            # 1. Configuración
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

            # 2. Crear Header Picking
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
            _logger.info(f"[TC_DEBUG] Picking Header Creado: {picking.name} (ID: {picking.id})")

            # 3. Crear STOCK.MOVES (Demanda)
            products_map = {}
            for line in valid_lines:
                if line.product_uom_qty <= 0: continue
                if line.product_id not in products_map:
                    products_map[line.product_id] = 0.0
                products_map[line.product_id] += line.product_uom_qty

            move_objs = {} 
            for product, qty in products_map.items():
                move = self.env['stock.move'].create({
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'product_uom': product.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': source_location.id,
                    'location_dest_id': picking_type.default_location_dest_id.id,
                    'company_id': self.company_id.id,
                })
                move_objs[product.id] = move.id

            # 4. Confirmar Picking (Pasa a estado 'Confirmado' / 'Waiting')
            picking.action_confirm()

            # Limpiar reservas automáticas si Odoo las creó
            if picking.move_line_ids:
                picking.move_line_ids.unlink()

            # 5. Crear STOCK.MOVE.LINES (Estrategia Anti-Auto-Done)
            for line in valid_lines:
                if line.product_uom_qty <= 0: continue
                
                move_id = move_objs.get(line.product_id.id)
                if not move_id: continue

                try:
                    # PASO A: Crear la línea con Cantidad 0.
                    # Esto vincula el lote y el movimiento, pero no dispara el cierre.
                    sml = self.env['stock.move.line'].create({
                        'move_id': move_id,
                        'picking_id': picking.id,
                        'product_id': line.product_id.id,
                        'lot_id': line.lot_id.id,
                        'product_uom_id': line.product_id.uom_id.id,
                        'location_id': source_location.id,
                        'location_dest_id': picking_type.default_location_dest_id.id,
                        'quantity': 0, # <--- CLAVE: Empezamos en cero
                    })
                    
                    # PASO B: Actualizar la cantidad.
                    # Al editar una línea existente, Odoo es menos agresivo con la validación automática.
                    sml.write({'quantity': line.product_uom_qty})
                    
                    _logger.info(f"[TC_DEBUG] MoveLine configurada: {sml.id} | Lote: {line.lot_id.name}")
                except Exception as e:
                    _logger.error(f"[TC_DEBUG] Error en linea para lote {line.lot_id.name}: {e}")

            # Opcional: Si el estado sigue siendo 'confirmed' (porque borramos las reservas auto),
            # podemos intentar verificar disponibilidad, pero sin forzar.
            # Generalmente con quantity > 0, ya debería verse como 'assigned'.
            if picking.state == 'confirmed':
                picking.action_assign()

            _logger.info(f"[TC_DEBUG] Picking Finalizado. Estado actual: {picking.state}")

            self.write({
                'reception_picking_id': picking.id,
                'custom_status': 'reception_pending'
            })
            
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': picking.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def action_load_from_purchase(self):
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
        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)

    def action_load_from_picking(self):
        self.ensure_one()
        if not self.picking_id:
            return
        
        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)
        placeholder_lines.unlink()

        transit_lines = []
        from .utils.transit_manager import TransitManager
        containers_found = set()
        
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
            qty_done = move_line.quantity # ODOO 19 FIX: Use quantity
            
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

            if move_line.lot_id.ref:
                containers_found.add(move_line.lot_id.ref)

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': move_line.lot_id.ref,
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }
            transit_lines.append(line_vals)
        
        created_lines = self.env['stock.transit.line'].create(transit_lines)
        
        if containers_found:
            new_conts = ', '.join(list(containers_found))
            self.write({'container_number': new_conts[:50]})

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