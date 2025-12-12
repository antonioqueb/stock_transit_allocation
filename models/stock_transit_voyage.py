# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    
    custom_status = fields.Selection([
        ('production', 'Producción'),
        ('booked', 'Booking Solicitado'),
        ('loaded', 'Cargado'),
        ('on_sea', 'En Altamar / Mar'),
        ('at_port', 'En Puerto'),
        ('delivered', 'Entregado'),
    ], string='Status (Manual)', default='production', tracking=True, 
       help="Estatus operativo similar al Excel de seguimiento.")
    
    shipping_line = fields.Char(string='Naviera', tracking=True, help="Ej. MSC, Maersk, CMA CGM")
    transit_days_expected = fields.Integer(string='Tiempo Tránsito (Días)', help="Días estimados desde origen a destino")
    
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields.Char(string='No. Viaje', tracking=True)
    container_number = fields.Char(string='Contenedor(es)', tracking=True)
    bl_number = fields.Char(string='Folio Compra / BL', tracking=True, help="Referencia de la Compra o BL Marítimo")
    
    etd = fields.Date(string='ETD (Salida Estimada)')
    eta = fields.Date(string='ETA (Llegada Estimada)', required=True, tracking=True)
    arrival_date = fields.Date(string='Llegada Real', tracking=True)
    
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('in_transit', 'En Tránsito (Altamar)'),
        ('at_port', 'En Puerto'),
        ('arrived', 'Recibido en Almacén'),
        ('cancel', 'Cancelado')
    ], string='Estado Sistema', default='draft', tracking=True, group_expand='_expand_states')

    picking_id = fields.Many2one('stock.picking', string='Recepción Vinculada', 
        domain=[('picking_type_code', '=', 'incoming')])
    
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

    @api.depends('etd', 'eta', 'state', 'create_date')
    def _compute_transit_progress(self):
        today = fields.Date.today()
        for rec in self:
            if rec.state == 'arrived':
                rec.transit_progress = 100
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
        self.write({'state': 'in_transit', 'custom_status': 'on_sea'})

    def action_arrive(self):
        self.write({
            'state': 'arrived', 
            'arrival_date': fields.Date.today(),
            'custom_status': 'delivered'
        })

    def action_load_from_picking(self):
        """
        LÓGICA ACTUALIZADA: Búsqueda robusta de Quants.
        """
        self.ensure_one()
        if not self.picking_id:
            return
        
        if self.state == 'draft':
            self.line_ids.unlink()

        transit_lines = []
        from .utils.transit_manager import TransitManager

        containers_found = set()

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue
            
            partner_to_assign = False
            order_to_assign = False
            
            move = move_line.move_id
            
            # --- INICIO DETECCIÓN INTELIGENTE ---
            sale_line = False
            if getattr(move, 'sale_line_id', False):
                sale_line = move.sale_line_id
            elif move.purchase_line_id and getattr(move.purchase_line_id, 'sale_line_id', False):
                sale_line = move.purchase_line_id.sale_line_id

            if sale_line:
                auto_assign = getattr(sale_line, 'auto_transit_assign', True)
                if auto_assign and sale_line.order_id:
                    order_to_assign = sale_line.order_id
                    partner_to_assign = sale_line.order_id.partner_id
            elif move.group_id and move.group_id.sale_id:
                order_to_assign = move.group_id.sale_id
                partner_to_assign = order_to_assign.partner_id
            # --- FIN DETECCIÓN ---

            # === CORRECCIÓN CRÍTICA: Búsqueda del Quant ===
            # Buscamos EXACTAMENTE en la ubicación destino del movimiento.
            # Esto evita fallos si la ubicación es de tipo 'Transit' o 'Internal'.
            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id), 
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id) # <--- CLAVE
            ], limit=1)

            if move_line.lot_id.ref:
                containers_found.add(move_line.lot_id.ref)

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': move_line.qty_done or move_line.reserved_uom_qty,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': move_line.lot_id.ref,
            }
            transit_lines.append(line_vals)
        
        created_lines = self.env['stock.transit.line'].create(transit_lines)
        
        if containers_found:
            current_conts = self.container_number or ''
            new_conts = ', '.join(list(containers_found))
            if new_conts not in current_conts:
                self.write({'container_number': new_conts[:50]})

        # Generar Reservas (Holds) Automáticas
        for line in created_lines:
            if line.partner_id and line.order_id:
                TransitManager.reassign_lot(
                    self.env, 
                    line, 
                    line.partner_id, 
                    line.order_id, 
                    notes=f"Asignación Automática - Pedido {line.order_id.name}"
                )

    def _expand_states(self, states, domain, order=None):
        return [key for key, val in type(self).state.selection]