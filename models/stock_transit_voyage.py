# -*- coding: utf-8 -*-
import logging
from markupsafe import Markup
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

    # =========================================================================
    # CAMBIO #3: ETA original (inmutable) y cálculo de días de retraso
    # =========================================================================
    eta_original = fields.Date(
        string='ETA Original',
        readonly=True,
        copy=False,
        tracking=True,
        help="Primer ETA registrado. Nunca se sobreescribe una vez guardado.",
    )

    delay_days = fields.Integer(
        string='Días de Retraso',
        compute='_compute_delay_days',
        store=True,
        help="Días entre ETA original y ETA actual (o fecha real de llegada en bodega).",
    )

    # =========================================================================
    # CAMBIO #4: Nivel de alerta ETA
    # =========================================================================
    eta_alert_level = fields.Selection([
        ('ok',      'En Tiempo'),
        ('warning', 'Próximo a Vencer'),
        ('danger',  'Vencido'),
        ('done',    'Entregado'),
    ], string='Alerta ETA', compute='_compute_eta_alert', store=True)

    arrival_date = fields.Date(string='Llegada Real', tracking=True)

    # =========================================================================
    # CAMBIO #8: Fecha real de entrega en bodega
    # =========================================================================
    arrival_date_bodega = fields.Date(
        string='Entregado en Bodega',
        tracking=True,
        help="Fecha real de ingreso físico a bodega.",
    )

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
    # CÓMPUTOS
    # =========================================================================

    @api.depends('line_ids.container_number')
    def _compute_container_number(self):
        for rec in self:
            containers = set()
            for line in rec.line_ids:
                if line.container_number and line.container_number not in ('', 'PENDIENTE', 'SN', 'False'):
                    containers.add(line.container_number)
            rec.container_number = ', '.join(sorted(containers)) if containers else 'PENDIENTE'

    @api.depends('eta', 'eta_original', 'arrival_date_bodega')
    def _compute_delay_days(self):
        for rec in self:
            if not rec.eta_original:
                rec.delay_days = 0
                continue
            reference_end = rec.arrival_date_bodega or rec.eta
            if reference_end:
                rec.delay_days = (reference_end - rec.eta_original).days
            else:
                rec.delay_days = 0

    @api.depends('eta', 'custom_status')
    def _compute_eta_alert(self):
        today = fields.Date.today()
        try:
            warning_days = int(self.env['ir.config_parameter'].sudo().get_param(
                'stock_transit_allocation.eta_warning_days', '7'))
        except Exception:
            warning_days = 7
        for rec in self:
            if rec.custom_status == 'delivered':
                rec.eta_alert_level = 'done'
            elif not rec.eta:
                rec.eta_alert_level = 'ok'
            elif today > rec.eta:
                rec.eta_alert_level = 'danger'
            elif (rec.eta - today).days <= warning_days:
                rec.eta_alert_level = 'warning'
            else:
                rec.eta_alert_level = 'ok'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
            vals.pop('container_number', None)
            # CAMBIO #3: capturar eta_original en creación
            if vals.get('eta') and not vals.get('eta_original'):
                vals['eta_original'] = vals['eta']
        return super(StockTransitVoyage, self).create(vals_list)

    def write(self, vals):
        # CAMBIO #3: preservar eta_original — se fija la primera vez que hay ETA
        if 'eta' in vals:
            for rec in self:
                if not rec.eta_original and vals.get('eta'):
                    # Solo actualizar eta_original si aún no tiene uno
                    super(StockTransitVoyage, rec).write({'eta_original': vals['eta']})
        
        res = super().write(vals)

        if 'custom_status' in vals or 'eta' in vals:
            transit_lines = self.mapped('line_ids')
            order_ids = transit_lines.mapped('order_id')
            if order_ids:
                sol = self.env['sale.order.line'].search([
                    ('order_id', 'in', order_ids.ids),
                    ('auto_transit_assign', '=', True),
                ])
                sol._compute_transit_info()

        # CAMBIO #4: disparar notificaciones si entra en alerta
        if 'eta' in vals or 'custom_status' in vals:
            self._check_eta_alerts()

        return res

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

    # =========================================================================
    # CAMBIO #4: Notificaciones de alerta ETA
    # =========================================================================

    def _check_eta_alerts(self):
        """Envía notificación al responsable del viaje cuando entra en alerta."""
        for rec in self:
            if rec.eta_alert_level not in ('warning', 'danger'):
                continue
            if rec.custom_status == 'delivered':
                continue
            # Buscar usuario responsable (el que creó el viaje o la PO)
            responsible = False
            if rec.purchase_id and rec.purchase_id.user_id:
                responsible = rec.purchase_id.user_id
            if not responsible:
                # Notificar a todos los seguidores del viaje
                followers = rec.message_partner_ids
                if not followers:
                    continue
                level_label = 'VENCIDO' if rec.eta_alert_level == 'danger' else 'PRÓXIMO A VENCER'
                eta_str = rec.eta.strftime('%d/%m/%Y') if rec.eta else '—'
                body = Markup(
                    "⚠️ <b>Alerta ETA %s</b><br/>"
                    "El embarque <b>%s</b> tiene ETA %s y está en estado <b>%s</b>."
                ) % (level_label, rec.name, eta_str, rec.custom_status)
                rec.message_post(
                    body=body,
                    partner_ids=followers.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
                continue

            level_label = 'VENCIDO' if rec.eta_alert_level == 'danger' else 'PRÓXIMO A VENCER'
            eta_str = rec.eta.strftime('%d/%m/%Y') if rec.eta else '—'
            body = Markup(
                "⚠️ <b>Alerta ETA %s</b><br/>"
                "El embarque <b>%s</b> tiene ETA %s y está en estado <b>%s</b>."
            ) % (level_label, rec.name, eta_str, rec.custom_status)
            rec.message_post(
                body=body,
                partner_ids=responsible.partner_id.ids,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

    # =========================================================================
    # AVANCE SECUENCIAL DE ESTADOS
    # =========================================================================
    
    STATUS_SEQUENCE = [
        'solicitud', 'production', 'booking', 'puerto_origen',
        'on_sea', 'puerto_destino', 'arrived_port', 'reception_pending', 'delivered',
    ]

    STATUS_LABELS = {
        'solicitud':         'Solicitud Enviada',
        'production':        'Producción',
        'booking':           'Booking',
        'puerto_origen':     'Puerto Origen',
        'on_sea':            'En Altamar',
        'puerto_destino':    'Puerto Destino',
        'arrived_port':      'Arribo a Puerto',
        'reception_pending': 'En Recepción',
        'delivered':         'Entregado en Almacén',
        'cancel':            'Cancelado',
    }

    # -------------------------------------------------------------------------
    # CAMBIO #2: Botones públicos ahora abren el wizard de nota
    # -------------------------------------------------------------------------

    def action_advance_status(self):
        """Abre wizard para confirmar avance con nota opcional."""
        self.ensure_one()
        if self.custom_status in ('delivered', 'cancel'):
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'transit.status.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_direction': 'advance',
            },
        }

    def action_retreat_status(self):
        """Abre wizard para confirmar retroceso con nota opcional."""
        self.ensure_one()
        if self.custom_status in ('solicitud', 'delivered', 'cancel'):
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'transit.status.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_direction': 'retreat',
            },
        }

    # -------------------------------------------------------------------------
    # Métodos internos usados por el wizard
    # -------------------------------------------------------------------------

    def _do_advance_status(self, notes=None):
        """Ejecuta el avance real de estado. Llamado desde el wizard."""
        self.ensure_one()
        current = self.custom_status
        if current in ('cancel', 'delivered'):
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        next_idx = idx + 1
        if next_idx >= len(self.STATUS_SEQUENCE):
            return

        next_status = self.STATUS_SEQUENCE[next_idx]

        if next_status == 'delivered':
            if self.reception_picking_id and self.reception_picking_id.state != 'done':
                raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))
            write_vals = {
                'arrival_date': fields.Date.today(),
                'custom_status': 'delivered',
            }
            if not self.arrival_date_bodega:
                write_vals['arrival_date_bodega'] = fields.Date.today()
            self.write(write_vals)
            for line in self.line_ids:
                if line.allocation_id and line.allocation_id.state != 'done':
                    line.allocation_id.action_mark_received(line.product_uom_qty)
        else:
            if next_status == 'on_sea':
                if self.picking_id and self.picking_id.purchase_id:
                    allocations = self.env['purchase.order.line.allocation'].search([
                        ('purchase_order_id', '=', self.picking_id.purchase_id.id),
                        ('state', '=', 'pending')
                    ])
                    allocations.action_mark_in_transit()
            self.write({'custom_status': next_status})

        # Registrar en chatter
        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(self.custom_status, self.custom_status)
        msg_parts = [
            Markup("⏩ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]
        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)
        self.message_post(body=Markup('').join(msg_parts))

    def _do_retreat_status(self, notes=None):
        """Ejecuta el retroceso real de estado. Llamado desde el wizard."""
        self.ensure_one()
        current = self.custom_status
        if current == 'cancel':
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        if idx <= 0:
            return

        prev_status = self.STATUS_SEQUENCE[idx - 1]
        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(prev_status, prev_status)
        self.write({'custom_status': prev_status})

        msg_parts = [
            Markup("⏪ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]
        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)
        self.message_post(body=Markup('').join(msg_parts))

    # -------------------------------------------------------------------------
    # Acciones legacy (mantienen compatibilidad)
    # -------------------------------------------------------------------------

    def action_confirm_transit(self):
        return self.action_advance_status()

    def action_arrive(self):
        self.ensure_one()
        if self.reception_picking_id and self.reception_picking_id.state != 'done':
            raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))
        write_vals = {
            'arrival_date': fields.Date.today(),
            'custom_status': 'delivered'
        }
        if not self.arrival_date_bodega:
            write_vals['arrival_date_bodega'] = fields.Date.today()
        self.write(write_vals)
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
            if line.product_uom_qty <= 0:
                continue
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

    def action_sync_reception_from_voyage(self):
        """
        Sincroniza los lotes del Voyage al reception_picking_id.
        """
        self.ensure_one()
        if not self.reception_picking_id:
            raise UserError(_("Primero debe generar la Recepción Física con el botón '🏭 Generar Recepción Física'."))

        picking = self.reception_picking_id

        if picking.state == 'done':
            raise UserError(_("La recepción ya fue validada."))

        if picking.state == 'draft':
            picking.action_confirm()

        picking.move_line_ids.unlink()

        lines_created = 0
        for line in self.line_ids:
            if not line.lot_id or line.product_uom_qty <= 0:
                continue

            move = picking.move_ids.filtered(
                lambda m: m.product_id.id == line.product_id.id and m.state not in ['done', 'cancel']
            )

            if not move:
                move = self.env['stock.move'].create({
                    'picking_id': picking.id,
                    'product_id': line.product_id.id,
                    'product_uom': line.product_id.uom_id.id,
                    'product_uom_qty': line.product_uom_qty,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'company_id': self.company_id.id,
                })
                move._action_confirm()
            else:
                move = move[0]

            try:
                self.env['stock.move.line'].create({
                    'picking_id': picking.id,
                    'move_id': move.id,
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_id.uom_id.id,
                    'lot_id': line.lot_id.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'quantity': line.product_uom_qty,
                })
                lines_created += 1
            except Exception as e:
                _logger.error(f"[TC_ERROR] Error creando move.line para lote {line.lot_id.name}: {e}")

        if lines_created == 0:
            raise UserError(_("No hay líneas con lote asignado en el viaje para sincronizar."))

        picking.message_post(body=f"🔄 {lines_created} lotes sincronizados desde Viaje {self.name}.")
        self.message_post(body=f"📦 Recepción física sincronizada: {lines_created} lotes cargados en {picking.name}.")

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
        
        created_count = 0
        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)
            created_count = len(transit_lines)
        
        _logger.info(f"[TC_SYNC] action_load_from_purchase: {created_count} líneas nuevas creadas para {self.name}")

    def action_load_from_picking(self):
        self.ensure_one()
        if not self.picking_id:
            return
        
        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)
        if placeholder_lines:
            placeholder_lines.unlink()

        existing_by_lot = {}
        for line in self.line_ids:
            if line.lot_id:
                existing_by_lot[line.lot_id.id] = line

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

        lines_to_create = []
        lines_updated = 0
        lines_created_count = 0
        hold_orders_map = {}

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue
            
            lot_id = move_line.lot_id.id
            product_id = move_line.product_id.id
            qty_done = move_line.quantity

            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False
            
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

            lot_container = ''
            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref

            if lot_id in existing_by_lot:
                existing_line = existing_by_lot[lot_id]
                update_vals = {}
                
                if existing_line.product_uom_qty != qty_done:
                    update_vals['product_uom_qty'] = qty_done
                if found_quant and existing_line.quant_id.id != found_quant.id:
                    update_vals['quant_id'] = found_quant.id
                if lot_container and existing_line.container_number != lot_container:
                    update_vals['container_number'] = lot_container
                if allocation_to_use and not existing_line.allocation_id:
                    update_vals['allocation_id'] = allocation_to_use.id
                if not existing_line.partner_id and partner_to_assign:
                    update_vals['partner_id'] = partner_to_assign.id
                    update_vals['order_id'] = order_to_assign.id if order_to_assign else False
                    update_vals['allocation_status'] = 'reserved'
                
                if update_vals:
                    self.env['stock.transit.line'].browse(existing_line.id).with_context(
                        skip_reservation_logic=True
                    ).write(update_vals)
                    lines_updated += 1
                continue

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container,
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }
            lines_to_create.append(line_vals)
            
            if partner_to_assign and order_to_assign:
                key = (partner_to_assign.id, order_to_assign.id)
                if key not in hold_orders_map:
                    hold_orders_map[key] = {
                        'partner': partner_to_assign,
                        'order': order_to_assign,
                        'line_vals_indices': []
                    }
                hold_orders_map[key]['line_vals_indices'].append(len(lines_to_create) - 1)

        created_lines = self.env['stock.transit.line']
        if lines_to_create:
            created_lines = self.env['stock.transit.line'].create(lines_to_create)
            lines_created_count = len(created_lines)
        
        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)
                new_received = alloc.qty_received + qty_consumed
                alloc.write({'qty_received': min(new_received, alloc.quantity), 'state': 'in_transit'})

        for key, data in hold_orders_map.items():
            partner = data['partner']
            order = data['order']
            indices = data['line_vals_indices']
            
            relevant_lines = [created_lines[i] for i in indices if i < len(created_lines)]
            if not relevant_lines:
                continue
            
            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            })
            
            for line in relevant_lines:
                TransitManager.reassign_lot(self.env, line, partner, order, notes=False, hold_order_obj=hold_order)
            
            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()

        summary = f"Sincronización completada: {lines_created_count} nuevos, {lines_updated} actualizados"
        _logger.info(f"[TC_SYNC] {self.name}: {summary}")
        
        if lines_created_count > 0 or lines_updated > 0:
            self.message_post(body=Markup("🔄 %s") % summary)