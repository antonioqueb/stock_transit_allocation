# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from datetime import timedelta

class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    
    # Datos Naviera/Logística
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields.Char(string='No. Viaje', tracking=True)
    container_number = fields.Char(string='Contenedor', required=True, tracking=True)
    bl_number = fields.Char(string='BL Number', tracking=True)
    
    # Fechas
    etd = fields.Date(string='ETD (Salida Estimada)', help='Estimated Time of Departure')
    eta = fields.Date(string='ETA (Llegada Estimada)', required=True, tracking=True, help='Estimated Time of Arrival')
    arrival_date = fields.Date(string='Llegada Real', tracking=True)
    
    # Estado y Progreso
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('in_transit', 'En Tránsito (Altamar)'),
        ('at_port', 'En Puerto'),
        ('arrived', 'Recibido en Almacén'),
        ('cancel', 'Cancelado')
    ], string='Estado', default='draft', tracking=True, group_expand='_expand_states')

    # Integración con Odoo Stock
    picking_id = fields.Many2one('stock.picking', string='Recepción Vinculada', 
        domain=[('picking_type_code', '=', 'incoming')],
        help="El picking donde se cargó el Packing List previamente.")
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    
    # Líneas de Contenido (Lotes)
    line_ids = fields.One2many('stock.transit.line', 'voyage_id', string='Contenido del Contenedor')
    
    # Computed fields for Dashboard
    total_m2 = fields.Float(string='Total m²', compute='_compute_totals', store=True)
    allocated_m2 = fields.Float(string='Asignado m²', compute='_compute_totals', store=True)
    allocation_percent = fields.Float(string='% Asignación', compute='_compute_totals')
    
    # Campo para el Widget JS de Progreso
    transit_progress = fields.Integer(string='Progreso Viaje', compute='_compute_transit_progress')

    @api.model
    def create(self, vals):
        if vals.get('name', _('Nuevo')) == _('Nuevo'):
            vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
        return super(StockTransitVoyage, self).create(vals)

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status')
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('product_uom_qty'))
            allocated = sum(rec.line_ids.filtered(lambda l: l.allocation_status == 'reserved').mapped('product_uom_qty'))
            rec.total_m2 = total
            rec.allocated_m2 = allocated
            rec.allocation_percent = (allocated / total) * 100 if total > 0 else 0

    @api.depends('etd', 'eta')
    def _compute_transit_progress(self):
        """Calcula un porcentaje estimado basado en fechas para la barra visual"""
        today = fields.Date.today()
        for rec in self:
            if rec.state == 'arrived':
                rec.transit_progress = 100
            elif not rec.etd or not rec.eta:
                rec.transit_progress = 0
            elif today < rec.etd:
                rec.transit_progress = 0
            elif today > rec.eta:
                rec.transit_progress = 95 # Casi llegando, aunque retrasado
            else:
                total_days = (rec.eta - rec.etd).days
                elapsed = (today - rec.etd).days
                rec.transit_progress = int((elapsed / total_days) * 100) if total_days > 0 else 0

    def action_confirm_transit(self):
        self.write({'state': 'in_transit'})

    def action_arrive(self):
        self.write({'state': 'arrived', 'arrival_date': fields.Date.today()})

    def action_load_from_picking(self):
        """
        Carga mágica: Lee las líneas del Picking vinculado (que ya tiene lotes gracias al BL)
        y crea las líneas de tránsito.
        """
        self.ensure_one()
        if not self.picking_id:
            return
        
        # Limpiar líneas anteriores si está en borrador
        if self.state == 'draft':
            self.line_ids.unlink()

        transit_lines = []
        for move_line in self.picking_id.move_line_ids:
            # Solo nos interesan líneas con lotes (placas identificadas)
            if not move_line.lot_id:
                continue
                
            transit_lines.append({
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': self.env['stock.quant'].search([('lot_id', '=', move_line.lot_id.id), ('location_id', '=', move_line.picking_id.location_dest_id.id)], limit=1).id,
                'product_uom_qty': move_line.qty_done or move_line.reserved_uom_qty,
                'allocation_status': 'available' # Por defecto disponible
            })
        
        self.env['stock.transit.line'].create(transit_lines)
    
    # CORRECCIÓN: order=None hace el argumento opcional
    def _expand_states(self, states, domain, order=None):
        return [key for key, val in type(self).state.selection]