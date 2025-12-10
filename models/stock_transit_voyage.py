# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    
    # Cabecera: Representa el BL o el Viaje General
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields.Char(string='No. Viaje', tracking=True)
    
    # Ya no es obligatorio aquí porque puede haber múltiples, se vuelve informativo o principal
    container_number = fields.Char(string='Contenedor (Principal)', tracking=True, 
        help="Si hay múltiples contenedores, ver detalle en líneas.")
        
    bl_number = fields.Char(string='BL Number', tracking=True)
    
    etd = fields.Date(string='ETD (Salida Estimada)')
    eta = fields.Date(string='ETA (Llegada Estimada)', required=True, tracking=True)
    arrival_date = fields.Date(string='Llegada Real', tracking=True)
    
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('in_transit', 'En Tránsito (Altamar)'),
        ('at_port', 'En Puerto'),
        ('arrived', 'Recibido en Almacén'),
        ('cancel', 'Cancelado')
    ], string='Estado', default='draft', tracking=True, group_expand='_expand_states')

    picking_id = fields.Many2one('stock.picking', string='Recepción Vinculada', 
        domain=[('picking_type_code', '=', 'incoming')])
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    line_ids = fields.One2many('stock.transit.line', 'voyage_id', string='Contenido (Lotes)')
    
    # Computados
    total_m2 = fields.Float(string='Total m²', compute='_compute_totals', store=True)
    allocated_m2 = fields.Float(string='Asignado m²', compute='_compute_totals', store=True)
    allocation_percent = fields.Float(string='% Asignación', compute='_compute_totals')
    transit_progress = fields.Integer(string='Progreso Viaje', compute='_compute_transit_progress')

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

    @api.depends('etd', 'eta')
    def _compute_transit_progress(self):
        today = fields.Date.today()
        for rec in self:
            if rec.state == 'arrived':
                rec.transit_progress = 100
            elif not rec.etd or not rec.eta:
                rec.transit_progress = 0
            elif today < rec.etd:
                rec.transit_progress = 0
            elif today > rec.eta:
                rec.transit_progress = 95
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
        Carga INTELIGENTE V2:
        1. Soporte Multi-Contenedor (Extrae info del lote).
        2. Rastreo Robusto de Venta (Usa Procurement Group).
        """
        self.ensure_one()
        if not self.picking_id:
            return
        
        if self.state == 'draft':
            self.line_ids.unlink()

        transit_lines = []
        from .utils.transit_manager import TransitManager

        # Sets para detectar si hay múltiples contenedores
        containers_found = set()

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue
            
            # --- 1. LÓGICA DE ASIGNACIÓN (CORREGIDA) ---
            partner_to_assign = False
            move = move_line.move_id
            
            # Intentar encontrar la venta a través del Grupo de Abastecimiento (Método más seguro)
            # El Picking tiene un group_id, y el Move también.
            procurement_group = move.group_id or move.picking_id.group_id
            
            if procurement_group and procurement_group.sale_id:
                # ¡Bingo! Encontramos la venta origen
                sale_order = procurement_group.sale_id
                
                # Buscamos la línea específica si es posible, o usamos la cabecera
                # Para simplificar y asegurar, usamos el partner de la orden
                if sale_order.partner_id:
                    # Opcional: Validar el booleano en líneas si es crítico, 
                    # pero generalmente si viene de una SO específica, es para ese cliente.
                    partner_to_assign = sale_order.partner_id

            # --- 2. LÓGICA DE CONTENEDOR (NUEVA) ---
            # Asumimos que el contenedor viene en la 'ref' del lote (común en importaciones)
            # o si usas un campo específico, cámbialo aquí.
            lot_container = move_line.lot_id.ref or False
            if lot_container:
                containers_found.add(lot_container)

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': self.env['stock.quant'].search([
                    ('lot_id', '=', move_line.lot_id.id), 
                    ('location_id', '=', move_line.picking_id.location_dest_id.id)
                ], limit=1).id,
                'product_uom_qty': move_line.qty_done or move_line.reserved_uom_qty,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container, # Guardamos el contenedor en la línea
            }
            transit_lines.append(line_vals)
        
        # Crear líneas
        created_lines = self.env['stock.transit.line'].create(transit_lines)
        
        # Actualizar cabecera con información resumen
        updates = {}
        if containers_found:
            # Si encontramos contenedores en los lotes, ponemos una referencia en la cabecera
            updates['container_number'] = ', '.join(list(containers_found))[:50] # Cortar si es muy largo
        
        if updates:
            self.write(updates)

        # Crear Holds para lo asignado
        for line in created_lines:
            if line.partner_id:
                TransitManager.reassign_lot(self.env, line, line.partner_id, notes="Asignación Automática (Origen Venta)")