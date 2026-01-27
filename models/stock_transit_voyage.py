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
    def _tc_ctx_of(self, ctx):
        keys = [
            'planned_picking', 'ws_ok', 'disable_auto_validate',
            'skip_immediate', 'no_immediate_transfer', 'force_draft',
            'disable_auto_done', 'skip_auto_done', 'skip_validate',
        ]
        ctx = dict(ctx or {})
        return {k: ctx.get(k) for k in keys if k in ctx}

    def _tc_log_picking_state(self, picking, label, ctx=None):
        if not picking:
            _logger.info("[TC_DEBUG] %s | picking=None", label)
            return
        ctx_print = self._tc_ctx_of(ctx if ctx is not None else self.env.context)
        _logger.info(
            "[TC_DEBUG] %s | Picking=%s(ID=%s) state=%s | move_lines=%s | moves=%s | ctx=%s",
            label, picking.name, picking.id, picking.state,
            len(picking.move_line_ids), len(picking.move_ids),
            ctx_print
        )

        try:
            sml = picking.move_line_ids
            has_qty_done = 'qty_done' in self.env['stock.move.line']._fields
            has_quantity = 'quantity' in self.env['stock.move.line']._fields

            done_total = sum(sml.mapped('qty_done')) if has_qty_done else 0.0
            qty_total = sum(sml.mapped('quantity')) if has_quantity else 0.0
            _logger.info("[TC_DEBUG] %s | Sum(qty_done)=%s | Sum(quantity)=%s", label, done_total, qty_total)
        except Exception as e:
            _logger.warning("[TC_DEBUG] %s | No se pudo calcular sumas: %s", label, e)

    def _tc_reserved_field_name(self):
        """
        Odoo 19 en tu build NO tiene reserved_uom_qty.
        Detectamos el nombre real del campo de 'reservado' en stock.move.line.
        """
        sml = self.env['stock.move.line']
        fields_set = set(sml._fields.keys())

        # Orden de preferencia
        candidates = [
            'reserved_uom_qty',
            'reserved_quantity',
            'reserved_qty',
            'reserved_qty_uom',
            'reserved_uom_quantity',
        ]
        found = [c for c in candidates if c in fields_set]

        _logger.info("[TC_DEBUG] Reserved field candidates found=%s | all_candidates=%s",
                     found, candidates)

        return found[0] if found else False

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
        - Reservar por lote sin tocar cantidad hecha (qty_done/quantity)
        """
        self.ensure_one()

        # Contexto "seguro": debe acompañar TODAS las operaciones (picking/move/moveline/confirm/assign)
        ctx_safe = dict(self.env.context or {})
        ctx_safe.update({
            'planned_picking': True,
            'ws_ok': False,  # el guard bloqueará cualquier done sin ws_ok
            'disable_auto_validate': True,
            'skip_immediate': True,
            'no_immediate_transfer': True,

            # claves típicas que muchos custom leen:
            'disable_auto_done': True,
            'skip_auto_done': True,
            'skip_validate': True,
            'force_draft': True,
        })

        _logger.info("[TC_DEBUG] >>> GENERATE RECEPTION START | Voyage=%s(ID=%s) | ctx_safe=%s",
                     self.name, self.id, self._tc_ctx_of(ctx_safe))

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

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        if not picking_type:
            raise UserError(_("No se encontró un tipo de operación 'Internal Transfer'."))

        valid_lines = self.line_ids.filtered(lambda l: l.lot_id and l.quant_id and l.product_id and l.product_uom_qty > 0)
        if not valid_lines:
            raise UserError(_("No hay líneas válidas (con Lote + Quant + Producto + Cantidad>0) para mover."))

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

        Picking = self.env['stock.picking'].with_context(ctx_safe)
        Move = self.env['stock.move'].with_context(ctx_safe)
        SML = self.env['stock.move.line'].with_context(ctx_safe)

        picking = Picking.create(picking_vals)
        self._tc_log_picking_state(picking, "POST-CREATE", ctx=ctx_safe)

        # Crear moves por producto
        products_map = {}
        for line in valid_lines:
            products_map.setdefault(line.product_id, 0.0)
            products_map[line.product_id] += line.product_uom_qty

        move_objs = {}
        for product, qty in products_map.items():
            try:
                move = Move.create({
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'product_uom': product.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': source_location.id,
                    'location_dest_id': dest_location.id,
                    'company_id': self.company_id.id,
                })
                move_objs[product.id] = move.id
                _logger.info("[TC_DEBUG] Move creado | product=%s(ID=%s) qty=%s | move_id=%s",
                             product.display_name, product.id, qty, move.id)
            except Exception as e:
                _logger.exception("[TC_DEBUG] Error creando move | product=%s err=%s", product.display_name, e)
                raise

        # IMPORTANTÍSIMO: aquí es donde te estaba quedando done. Lo volvemos a loggear
        self._tc_log_picking_state(picking, "POST-MOVES", ctx=ctx_safe)

        # Detectar campo real de reservado
        reserved_field = self._tc_reserved_field_name()

        # Flags de campos
        has_qty_done = 'qty_done' in SML._fields
        has_quantity = 'quantity' in SML._fields
        has_quant_id = 'quant_id' in SML._fields

        _logger.info("[TC_DEBUG] MoveLine fields | has_qty_done=%s | has_quantity=%s | has_quant_id=%s | reserved_field=%s",
                     has_qty_done, has_quantity, has_quant_id, reserved_field)

        if not reserved_field:
            # En tu build, el error te lo confirma: existe "Reserved Quantity", pero el campo se llama distinto.
            # Este raise te obliga a ver el log de candidates y ajustar lista si tu nombre es raro.
            raise UserError(_("No se encontró campo de cantidad reservada en stock.move.line (reserved_*). Revisa overrides."))

        # Crear move lines (reservas por lote) sin marcar hecho
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
                reserved_field: line.product_uom_qty,  # ✅ aquí va la reserva real
            }

            # ✅ blindaje: jamás done
            if has_qty_done:
                sml_vals['qty_done'] = 0.0

            # ⚠️ NO tocar quantity aquí (en tu stack, "quantity" dispara _update_available_quantity)
            # Si por cualquier razón tu reserved_field no hace reserva real, se hace en action_assign.

            if has_quant_id and line.quant_id:
                sml_vals['quant_id'] = line.quant_id.id

            try:
                ml = SML.create(sml_vals)
                lines_created += 1
                _logger.info(
                    "[TC_DEBUG] MoveLine OK | ml_id=%s | lot=%s | reserved=%s | qty_done=%s | quantity=%s",
                    ml.id,
                    line.lot_id.name,
                    getattr(ml, reserved_field, None),
                    getattr(ml, 'qty_done', None) if has_qty_done else None,
                    getattr(ml, 'quantity', None) if has_quantity else None,
                )
            except Exception as e:
                _logger.exception("[TC_DEBUG] Error creando move line | lot=%s | product=%s | err=%s",
                                  line.lot_id.name, line.product_id.display_name, e)
                raise

        _logger.info("[TC_DEBUG] Total move lines creadas=%s", lines_created)
        self._tc_log_picking_state(picking, "POST-MOVELINES", ctx=ctx_safe)

        # Confirmar + asignar con contexto seguro
        _logger.info("[TC_DEBUG] Confirmando picking %s(ID=%s)...", picking.name, picking.id)
        picking.with_context(ctx_safe).action_confirm()
        self._tc_log_picking_state(picking, "POST-CONFIRM", ctx=ctx_safe)

        if picking.state not in ['assigned', 'done', 'cancel']:
            _logger.info("[TC_DEBUG] Asignando picking %s(ID=%s)...", picking.name, picking.id)
            picking.with_context(ctx_safe).action_assign()
            self._tc_log_picking_state(picking, "POST-ASSIGN", ctx=ctx_safe)

        # Verificación final: si alguien metió done_qty, resetea
        if has_qty_done:
            done_lines = picking.move_line_ids.filtered(lambda ml: (ml.qty_done or 0.0) > 0.0)
            if done_lines:
                _logger.warning("[TC_DEBUG] DETECTADO qty_done>0 (%s líneas). Reseteando a 0.0.", len(done_lines))
                done_lines.with_context(ctx_safe).write({'qty_done': 0.0})
                self._tc_log_picking_state(picking, "POST-RESET-QTYDONE", ctx=ctx_safe)

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
            'context': {'default_picking_id': self.id},
        }


# ---------------------------------------------------------
# BLINDAJE DURO: bloquear DONE sin ws_ok=True
# - nivel picking
# - nivel move (porque tu "done" está entrando por moves)
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
                        ''.join(traceback.format_stack(limit=40)),
                    )
                    raise UserError(_(
                        "Bloqueado: esta recepción física NO puede pasar a HECHO (done) automáticamente.\n"
                        "Primero captura Worksheet y luego valida con ws_ok=True."
                    ))
        return super().write(vals)

    def _action_done(self):
        for p in self:
            if p._tc_is_physical_reception() and not p._tc_ws_ok():
                _logger.error(
                    "[TC_GUARD] BLOQUEADO picking._action_done() | picking=%s(ID=%s) origin=%s pt=%s ctx=%s\nSTACK:\n%s",
                    p.name, p.id, p.origin, p.picking_type_id.name, dict(self.env.context),
                    ''.join(traceback.format_stack(limit=40)),
                )
                raise UserError(_(
                    "Bloqueado: no puedes completar esta recepción física hasta terminar Worksheet."
                ))
        return super()._action_done()

    def button_validate(self):
        for p in self:
            if p._tc_is_physical_reception() and not p._tc_ws_ok():
                _logger.info(
                    "[TC_GUARD] picking.button_validate() bloqueado | picking=%s(ID=%s) state=%s ws_ok=%s ctx=%s",
                    p.name, p.id, p.state, p._tc_ws_ok(), dict(self.env.context)
                )
                raise UserError(_(
                    "Esta transferencia NO puede validarse todavía.\n"
                    "Primero captura Worksheet y valida desde tu botón de Worksheet."
                ))
        return super().button_validate()


class StockMoveGuard(models.Model):
    _inherit = 'stock.move'

    def _tc_is_physical_reception_move(self):
        self.ensure_one()
        picking = self.picking_id
        if not picking:
            return False
        # Reutilizamos criterio del picking
        origin = (picking.origin or '').lower()
        pt_name = (picking.picking_type_id.name or '').strip().lower()
        is_internal = (picking.picking_type_id.code == 'internal')
        is_recepcion_fisica = '(recepción física)' in origin
        is_recibir_en_almacen = (pt_name == 'recibir en almacén')
        return is_internal and (is_recepcion_fisica or is_recibir_en_almacen)

    def _tc_ws_ok(self):
        return bool(self.env.context.get('ws_ok'))

    def write(self, vals):
        if vals.get('state') == 'done':
            for m in self:
                if m._tc_is_physical_reception_move() and not m._tc_ws_ok():
                    _logger.error(
                        "[TC_GUARD] BLOQUEADO move.write(state=done) | move=%s(ID=%s) picking=%s(ID=%s) ctx=%s\nSTACK:\n%s",
                        m.display_name, m.id,
                        m.picking_id.name if m.picking_id else None,
                        m.picking_id.id if m.picking_id else None,
                        dict(self.env.context),
                        ''.join(traceback.format_stack(limit=40)),
                    )
                    raise UserError(_(
                        "Bloqueado: un módulo intentó marcar movimientos en DONE durante Recepción Física.\n"
                        "Debe completarse Worksheet primero (ws_ok=True para permitir done)."
                    ))
        return super().write(vals)

    def _action_done(self, cancel_backorder=False):
        for m in self:
            if m._tc_is_physical_reception_move() and not m._tc_ws_ok():
                _logger.error(
                    "[TC_GUARD] BLOQUEADO move._action_done() | move=%s(ID=%s) picking=%s(ID=%s) ctx=%s\nSTACK:\n%s",
                    m.display_name, m.id,
                    m.picking_id.name if m.picking_id else None,
                    m.picking_id.id if m.picking_id else None,
                    dict(self.env.context),
                    ''.join(traceback.format_stack(limit=40)),
                )
                raise UserError(_(
                    "Bloqueado: intento de completar movimientos (DONE) en Recepción Física sin Worksheet."
                ))
        return super()._action_done(cancel_backorder=cancel_backorder)
