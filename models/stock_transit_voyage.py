# -*- coding: utf-8 -*-
import logging
import traceback

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields.Char(
        string='Referencia Viaje',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )

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

    picking_id = fields.Many2one(
        'stock.picking',
        string='Recepción (Tránsito)',
        domain=[('picking_type_code', '=', 'incoming')],
        help="Recepción administrativa en ubicación de tránsito",
    )

    reception_picking_id = fields.Many2one(
        'stock.picking',
        string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')],
        readonly=True,
        help="Transferencia interna para ingreso físico y validación de medidas (Worksheet)",
    )

    purchase_id = fields.Many2one('purchase.order', string='Orden de Compra Origen', readonly=True)

    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    line_ids = fields.One2many('stock.transit.line', 'voyage_id', string='Contenido (Lotes)')

    total_m2 = fields.Float(string='Total m²', compute='_compute_totals', store=True)
    allocated_m2 = fields.Float(string='Asignado m²', compute='_compute_totals', store=True)
    allocation_percent = fields.Float(string='% Asignación', compute='_compute_totals')
    transit_progress = fields.Integer(string='Progreso Viaje', compute='_compute_transit_progress', store=False)

    # -------------------------
    # Helpers / Debug
    # -------------------------
    def _tc_ctx(self):
        """Helper para imprimir contexto relevante sin inundar logs."""
        ctx = dict(self.env.context or {})
        keys = [
            'planned_picking', 'ws_ok', 'disable_auto_validate',
            'skip_immediate', 'no_immediate_transfer', 'force_draft',
        ]
        return {k: ctx.get(k) for k in keys if k in ctx}

    def _tc_log_picking_state(self, picking, label):
        if not picking:
            _logger.info("[TC_DEBUG] %s | picking=None", label)
            return

        try:
            ml_count = len(picking.move_line_ids)
        except Exception:
            ml_count = -1
        try:
            mv_count = len(picking.move_ids)
        except Exception:
            mv_count = -1

        _logger.info(
            "[TC_DEBUG] %s | Picking=%s(ID=%s) state=%s | move_lines=%s | moves=%s | ctx=%s",
            label, picking.name, picking.id, picking.state, ml_count, mv_count, self._tc_ctx()
        )

        try:
            sml = self.env['stock.move.line']
            has_qty_done = 'qty_done' in sml._fields
            has_quantity = 'quantity' in sml._fields
            has_reserved = 'reserved_uom_qty' in sml._fields

            done_total = sum(picking.move_line_ids.mapped('qty_done')) if has_qty_done else 0.0
            qty_total = sum(picking.move_line_ids.mapped('quantity')) if has_quantity else 0.0
            res_total = sum(picking.move_line_ids.mapped('reserved_uom_qty')) if has_reserved else 0.0
            _logger.info("[TC_DEBUG] %s | Sum(qty_done)=%s | Sum(quantity)=%s | Sum(reserved_uom_qty)=%s",
                         label, done_total, qty_total, res_total)
        except Exception as e:
            _logger.warning("[TC_DEBUG] %s | No se pudo calcular sumas: %s", label, e)

    # -------------------------
    # Core
    # -------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
        return super().create(vals_list)

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
        Genera una Transferencia Interna (Transit -> Stock) con los lotes exactos.

        OBJETIVO:
        - Quedar en draft/confirmed/assigned
        - NUNCA en done automáticamente
        - Permitir capturar medidas (Worksheet) antes de validar

        NOTA CRÍTICA (Odoo 19):
        - Para "planear/reservar" en move lines usar reserved_uom_qty.
        - qty_done DEBE quedar en 0.0.
        - Evitar usar "quantity" como cantidad hecha / trigger de quants.
        """
        self.ensure_one()

        _logger.info("[TC_DEBUG] >>> GENERATE RECEPTION START | Voyage=%s(ID=%s) | ctx=%s",
                     self.name, self.id, self._tc_ctx())

        # Si ya existe, solo abrirla
        if self.reception_picking_id:
            _logger.info("[TC_DEBUG] Reception picking ya existe: %s(ID=%s) state=%s",
                         self.reception_picking_id.name, self.reception_picking_id.id, self.reception_picking_id.state)
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.reception_picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

        # 1) Tipo de operación (internal)
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        if not picking_type:
            raise UserError(_("No se encontró un tipo de operación 'Internal Transfer'."))

        # 2) Líneas válidas
        valid_lines = self.line_ids.filtered(lambda l: l.lot_id and l.quant_id and l.product_id and l.product_uom_qty > 0)
        if not valid_lines:
            raise UserError(_("No hay líneas válidas (con Lote + Quant + Producto + Cantidad>0) para mover."))

        # Ubicaciones
        source_location = valid_lines[0].quant_id.location_id
        if not source_location:
            raise UserError(_("No se pudo determinar la ubicación de origen."))

        dest_location = picking_type.default_location_dest_id
        if not dest_location:
            raise UserError(_("El tipo de operación interno no tiene ubicación destino por defecto."))

        _logger.info("[TC_DEBUG] Tipo operación=%s(ID=%s) | Origen=%s(ID=%s) | Destino=%s(ID=%s)",
                     picking_type.display_name, picking_type.id,
                     source_location.display_name, source_location.id,
                     dest_location.display_name, dest_location.id)

        # 3) Crear Picking (NUNCA validar)
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': f"{self.name} (Recepción Física)",
            'company_id': self.company_id.id,
            'move_type': 'direct',
        }

        if hasattr(self.env['stock.picking'], 'supplier_bl_number'):
            picking_vals.update({
                'supplier_bl_number': self.bl_number,
                'supplier_vessel': self.vessel_name,
                'supplier_container_no': self.container_number,
                'supplier_origin': 'TRÁNSITO',
            })

        # Contextos que suelen frenar flows inmediatos + nuestro flag del guard
        ctx_create = dict(self.env.context or {})
        ctx_create.update({
            'planned_picking': True,
            'disable_auto_validate': True,
            'skip_immediate': True,
            'no_immediate_transfer': True,
            'ws_ok': False,  # MUY IMPORTANTE: el guard bloqueará done si no viene ws_ok=True
        })

        picking = self.env['stock.picking'].with_context(ctx_create).create(picking_vals)
        self._tc_log_picking_state(picking, "POST-CREATE")

        # 4) Crear moves por producto (demanda)
        products_map = {}
        for line in valid_lines:
            products_map.setdefault(line.product_id, 0.0)
            products_map[line.product_id] += line.product_uom_qty

        move_objs = {}
        try:
            # Savepoint para que si algún módulo intenta "done" y nuestro guard bloquee,
            # se haga rollback limpio del paso actual con logs claros.
            with self.env.cr.savepoint():
                for product, qty in products_map.items():
                    mv_vals = {
                        'product_id': product.id,
                        'product_uom_qty': qty,
                        'product_uom': product.uom_id.id,
                        'picking_id': picking.id,
                        'location_id': source_location.id,
                        'location_dest_id': dest_location.id,
                        'company_id': self.company_id.id,
                    }
                    move = self.env['stock.move'].create(mv_vals)
                    move_objs[product.id] = move.id
                    _logger.info("[TC_DEBUG] Move creado | product=%s(ID=%s) qty=%s | move_id=%s",
                                 product.display_name, product.id, qty, move.id)
        except UserError as e:
            _logger.error(
                "[TC_DEBUG] BLOQUEO (UserError) creando moves. Probable auto-done bloqueado por guard. err=%s\nSTACK:\n%s",
                e, ''.join(traceback.format_stack(limit=35))
            )
            raise
        except Exception as e:
            _logger.exception("[TC_DEBUG] Error inesperado creando moves: %s", e)
            raise

        self._tc_log_picking_state(picking, "POST-MOVES")

        # 5) Crear move lines (detalle por lote) -> reservar con reserved_uom_qty, qty_done=0
        sml_model = self.env['stock.move.line']
        has_qty_done = 'qty_done' in sml_model._fields
        has_reserved = 'reserved_uom_qty' in sml_model._fields
        has_quant_id = 'quant_id' in sml_model._fields

        _logger.info("[TC_DEBUG] MoveLine fields | has_qty_done=%s | has_reserved_uom_qty=%s | has_quant_id=%s",
                     has_qty_done, has_reserved, has_quant_id)

        if not has_reserved:
            # En Odoo 19 debe existir. Si no, tu build está muy alterado.
            raise UserError(_("Tu modelo stock.move.line no tiene reserved_uom_qty. Revisa versión/overrides."))

        lines_created = 0
        for line in valid_lines:
            move_id = move_objs.get(line.product_id.id)
            if not move_id:
                _logger.warning("[TC_DEBUG] Sin move_id para producto=%s(ID=%s). Saltando.",
                                line.product_id.display_name, line.product_id.id)
                continue

            sml_vals = {
                'move_id': move_id,
                'picking_id': picking.id,
                'product_id': line.product_id.id,
                'lot_id': line.lot_id.id,
                'product_uom_id': line.product_id.uom_id.id,
                'location_id': source_location.id,
                'location_dest_id': dest_location.id,
                'reserved_uom_qty': line.product_uom_qty,  # ✅ esto evita el error “cantidad o reservada”
            }

            # ✅ Blindaje: jamás crear con hecho
            if has_qty_done:
                sml_vals['qty_done'] = 0.0

            # ✅ Anclar quant si existe
            if has_quant_id and line.quant_id:
                sml_vals['quant_id'] = line.quant_id.id

            try:
                sml = sml_model.create(sml_vals)
                lines_created += 1
                _logger.info(
                    "[TC_DEBUG] MoveLine OK | sml_id=%s | product=%s | lot=%s | reserved=%s | qty_done=%s",
                    sml.id, line.product_id.display_name, line.lot_id.name,
                    getattr(sml, 'reserved_uom_qty', None), getattr(sml, 'qty_done', None)
                )
            except UserError as e:
                _logger.error(
                    "[TC_DEBUG] BLOQUEO (UserError) creando move line | lot=%s | product=%s | err=%s\nSTACK:\n%s",
                    line.lot_id.name, line.product_id.display_name, e,
                    ''.join(traceback.format_stack(limit=35))
                )
                raise
            except Exception as e:
                _logger.exception("[TC_DEBUG] Error creando move line | lot=%s | product=%s | err=%s",
                                  line.lot_id.name, line.product_id.display_name, e)
                raise

        _logger.info("[TC_DEBUG] Total move lines creadas=%s", lines_created)
        self._tc_log_picking_state(picking, "POST-MOVELINES")

        # 6) Confirmar (NO validar)
        _logger.info("[TC_DEBUG] Confirmando picking %s(ID=%s)...", picking.name, picking.id)
        picking.with_context(ctx_create).action_confirm()
        self._tc_log_picking_state(picking, "POST-CONFIRM")

        # 7) Reservar/Asignar (NO validar)
        if picking.state not in ['assigned', 'done', 'cancel']:
            _logger.info("[TC_DEBUG] Asignando (reservando) picking %s(ID=%s)...", picking.name, picking.id)
            picking.with_context(ctx_create).action_assign()
            self._tc_log_picking_state(picking, "POST-ASSIGN")

        # 8) Verificación final: qty_done debe ser 0
        try:
            if has_qty_done:
                done_lines = picking.move_line_ids.filtered(lambda ml: (ml.qty_done or 0.0) > 0.0)
                if done_lines:
                    _logger.warning("[TC_DEBUG] DETECTADO qty_done>0 en %s líneas. Reseteando a 0.0.", len(done_lines))
                    done_lines.write({'qty_done': 0.0})
                    self._tc_log_picking_state(picking, "POST-RESET-QTYDONE")
        except Exception as e:
            _logger.exception("[TC_DEBUG] Error verificando/reset qty_done: %s", e)

        # 9) Persistir en viaje
        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending',
        })

        _logger.info("[TC_DEBUG] >>> GENERATE RECEPTION END | Voyage=%s | Picking=%s(ID=%s) state=%s",
                     self.name, picking.name, picking.id, picking.state)

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
                allocations_map.setdefault(alloc.product_id.id, [])
                allocations_map[alloc.product_id.id].append(alloc)
                allocation_consumed[alloc.id] = 0.0

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue

            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False
            product_id = move_line.product_id.id

            qty_done = move_line.qty_done if 'qty_done' in move_line._fields else move_line.quantity

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

            transit_lines.append({
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
            })

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
                lines_by_order.setdefault(key, [])
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

    def action_view_transit_voyage(self):
        self.ensure_one()
        return {
            'name': 'Gestión de Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.transit.voyage',
            'view_mode': 'list,form',
            'domain': [('id', '=', self.id)],
            'context': {'default_picking_id': self.id}
        }


# ---------------------------------------------------------
# BLINDAJE DURO: NO permitir que “Recibir en almacén”
# o "(Recepción Física)" pase a done sin ws_ok=True,
# aunque lo intenten por write() o _action_done().
# ---------------------------------------------------------
class StockPickingGuard(models.Model):
    _inherit = 'stock.picking'

    def _tc_is_physical_reception(self):
        self.ensure_one()
        origin = (self.origin or '').lower()
        pt_name = (self.picking_type_id.name or '').strip().lower()
        is_internal = (self.picking_type_id.code == 'internal')
        is_recepcion_fisica = '(recepción física)' in origin
        is_recibir_en_almacen = (pt_name == 'recibir en almacén')
        return is_internal and (is_recepcion_fisica or is_recibir_en_almacen)

    def _tc_ws_ok(self):
        return bool(self.env.context.get('ws_ok'))

    def write(self, vals):
        if vals.get('state') == 'done':
            for p in self:
                if p._tc_is_physical_reception() and not p._tc_ws_ok():
                    _logger.error(
                        "[TC_GUARD] BLOQUEADO write(state=done) | picking=%s(ID=%s) origin=%s pt=%s ctx=%s\nSTACK:\n%s",
                        p.name, p.id, p.origin, p.picking_type_id.name, dict(self.env.context),
                        ''.join(traceback.format_stack(limit=35))
                    )
                    raise UserError(_(
                        "Bloqueado: esta recepción física NO puede pasar a HECHO (done) automáticamente.\n"
                        "Primero captura Worksheet y luego valida con contexto ws_ok=True."
                    ))
                _logger.warning(
                    "[TC_GUARD] write(state=done) PERMITIDO | picking=%s(ID=%s) ws_ok=%s",
                    p.name, p.id, p._tc_ws_ok()
                )
        return super().write(vals)

    def _action_done(self):
        for p in self:
            if p._tc_is_physical_reception() and not p._tc_ws_ok():
                _logger.error(
                    "[TC_GUARD] BLOQUEADO _action_done() | picking=%s(ID=%s) origin=%s pt=%s ctx=%s\nSTACK:\n%s",
                    p.name, p.id, p.origin, p.picking_type_id.name, dict(self.env.context),
                    ''.join(traceback.format_stack(limit=35))
                )
                raise UserError(_(
                    "Bloqueado: no puedes completar esta recepción física hasta terminar Worksheet."
                ))
        return super()._action_done()

    def button_validate(self):
        for p in self:
            if p._tc_is_physical_reception():
                _logger.info(
                    "[TC_GUARD] button_validate() | picking=%s(ID=%s) state=%s ws_ok=%s ctx=%s",
                    p.name, p.id, p.state, p._tc_ws_ok(), dict(self.env.context)
                )
                if not p._tc_ws_ok():
                    raise UserError(_(
                        "Esta transferencia NO puede validarse todavía.\n"
                        "Primero captura Worksheet (medidas) y valida desde tu botón de Worksheet."
                    ))
        return super().button_validate()
