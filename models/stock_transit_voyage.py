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

    picking_id = fields.Many2one(
        'stock.picking',
        string='Recepción (Tránsito)',
        domain=[('picking_type_code', '=', 'incoming')],
        help="Recepción administrativa en ubicación de tránsito"
    )

    reception_picking_id = fields.Many2one(
        'stock.picking',
        string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')],
        readonly=True,
        help="Transferencia interna para ingreso físico y validación de medidas (Worksheet)"
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
        _logger.info(
            "[TC_DEBUG] %s | Picking=%s(ID=%s) state=%s | move_lines=%s | moves=%s | ctx=%s",
            label, picking.name, picking.id, picking.state,
            len(picking.move_line_ids), len(picking.move_ids),
            self._tc_ctx()
        )
        # Conteo de qty_done por seguridad
        try:
            done_total = sum(picking.move_line_ids.mapped('qty_done'))
            qty_total = 0.0
            if 'quantity' in self.env['stock.move.line']._fields:
                qty_total = sum(picking.move_line_ids.mapped('quantity'))
            _logger.info("[TC_DEBUG] %s | Sum(qty_done)=%s | Sum(quantity)=%s", label, done_total, qty_total)
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
        Genera una Transferencia Interna (Transit -> Stock) con los lotes exactos.
        OBJETIVO: que quede en estado draft/confirmed/assigned pero NUNCA en done automáticamente,
        para permitir capturar medidas (Worksheet) antes de validar.
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

        # 2) Validaciones de líneas
        valid_lines = self.line_ids.filtered(lambda l: l.lot_id and l.quant_id and l.product_id and l.product_uom_qty > 0)
        if not valid_lines:
            raise UserError(_("No hay líneas válidas (con Lote + Quant + Producto + Cantidad>0) para mover."))

        # Origen desde quant de la primera línea
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

        # 3) Crear Picking (NO validar)
        picking_vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': f"{self.name} (Recepción Física)",
            'company_id': self.company_id.id,
            'move_type': 'direct',
        }

        # Campos opcionales (si existen)
        if hasattr(self.env['stock.picking'], 'supplier_bl_number'):
            picking_vals.update({
                'supplier_bl_number': self.bl_number,
                'supplier_vessel': self.vessel_name,
                'supplier_container_no': self.container_number,
                'supplier_origin': 'TRÁNSITO',
            })

        # Contextos para evitar flujos "immediates"/auto validate de terceros
        ctx_create = dict(self.env.context or {})
        ctx_create.update({
            'planned_picking': True,
            'disable_auto_validate': True,   # por si tienes código custom que lo respete
            'skip_immediate': True,          # por si algún flujo lo usa
            'no_immediate_transfer': True,   # por si algún módulo lo usa
        })

        picking = self.env['stock.picking'].with_context(ctx_create).create(picking_vals)
        self._tc_log_picking_state(picking, "POST-CREATE")

        # 4) Crear moves por producto (demanda)
        products_map = {}
        for line in valid_lines:
            products_map.setdefault(line.product_id, 0.0)
            products_map[line.product_id] += line.product_uom_qty

        move_objs = {}
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

        self._tc_log_picking_state(picking, "POST-MOVES")

        # 5) Crear move lines (detalle por lote) SIN marcar done.
        # Tu BD tiene qty_done (Cantidad hecha). Debe quedar en 0 para evitar bloqueos/validación.
        sml_model = self.env['stock.move.line']
        has_qty_done = 'qty_done' in sml_model._fields
        has_quantity = 'quantity' in sml_model._fields
        has_quant_id = 'quant_id' in sml_model._fields

        _logger.info("[TC_DEBUG] MoveLine fields | has_qty_done=%s | has_quantity=%s | has_quant_id=%s",
                     has_qty_done, has_quantity, has_quant_id)

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
            }

            # ✅ Blindaje: jamás crear con hecho
            if has_qty_done:
                sml_vals['qty_done'] = 0.0

            # ✅ Opcional: si quieres “pre-cargar” cantidad en operaciones (NO es done).
            # Si esto te detona un auto-validate por un módulo externo, ponlo en 0.0.
            if has_quantity:
                sml_vals['quantity'] = line.product_uom_qty

            # ✅ Mejor anclar quant si existe (reduce problemas de reserva)
            if has_quant_id and line.quant_id:
                sml_vals['quant_id'] = line.quant_id.id

            try:
                sml = sml_model.create(sml_vals)
                lines_created += 1
                _logger.info(
                    "[TC_DEBUG] MoveLine creada | sml_id=%s | product=%s | lot=%s | qty_plan=%s | qty_done=%s",
                    sml.id,
                    line.product_id.display_name,
                    line.lot_id.name,
                    line.product_uom_qty,
                    getattr(sml, 'qty_done', None),
                )
            except Exception as e:
                _logger.exception("[TC_DEBUG] Error creando move line | lot=%s | product=%s | err=%s",
                                  line.lot_id.name, line.product_id.display_name, e)

        _logger.info("[TC_DEBUG] Total move lines creadas=%s", lines_created)
        self._tc_log_picking_state(picking, "POST-MOVELINES")

        # 6) Confirmar picking (NO validar)
        _logger.info("[TC_DEBUG] Confirmando picking %s(ID=%s)...", picking.name, picking.id)
        picking.with_context(ctx_create).action_confirm()
        self._tc_log_picking_state(picking, "POST-CONFIRM")

        # 7) Reservar/Asignar (pero nunca validar)
        if picking.state not in ['assigned', 'done', 'cancel']:
            _logger.info("[TC_DEBUG] Asignando (reservando) picking %s(ID=%s)...", picking.name, picking.id)
            picking.with_context(ctx_create).action_assign()
            self._tc_log_picking_state(picking, "POST-ASSIGN")

        # 8) Verificación final de blindaje
        # Si alguien en el camino marcó qty_done>0, lo reseteamos para asegurar edición de Worksheet.
        try:
            if has_qty_done:
                done_lines = picking.move_line_ids.filtered(lambda ml: (ml.qty_done or 0.0) > 0.0)
                if done_lines:
                    _logger.warning("[TC_DEBUG] DETECTADO qty_done>0 en %s líneas. Reseteando a 0.0 para evitar autovalidación.",
                                    len(done_lines))
                    done_lines.write({'qty_done': 0.0})
        except Exception as e:
            _logger.exception("[TC_DEBUG] Error verificando/reset qty_done: %s", e)

        # 9) Persistir en viaje
        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending'
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

            # Tu lista de campos muestra qty_done y quantity.
            # Para lecturas de "cantidad hecha" usa qty_done si existe; si no, quantity.
            qty_done = 0.0
            if 'qty_done' in move_line._fields:
                qty_done = move_line.qty_done
            else:
                qty_done = move_line.quantity

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
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id}
        }


# ---------------------------------------------------------
# BLINDAJE DURO: NO permitir validar “Recibir en almacén”
# (o las recepciones físicas creadas desde Tránsito)
# sin que venga el contexto ws_ok=True.
# ---------------------------------------------------------
class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        for p in self:
            try:
                is_internal = (p.picking_type_id.code == 'internal')
                pt_name = (p.picking_type_id.name or '').strip().lower()
                is_recibir_en_almacen = (pt_name == 'recibir en almacén')
                is_recepcion_fisica = '(recepción física)' in ((p.origin or '').lower())
                should_block = is_internal and (is_recibir_en_almacen or is_recepcion_fisica)

                _logger.info(
                    "[TC_DEBUG] button_validate() intercept | picking=%s(ID=%s) state=%s | internal=%s | pt=%s | origin=%s | should_block=%s | ctx.ws_ok=%s",
                    p.name, p.id, p.state, is_internal, p.picking_type_id.name, p.origin, should_block,
                    bool(self.env.context.get('ws_ok'))
                )

                if should_block and not self.env.context.get('ws_ok'):
                    raise UserError(_(
                        "Esta transferencia NO puede validarse todavía.\n"
                        "Primero captura/valida la Worksheet (medidas) y luego valida desde el botón de Worksheet."
                    ))
            except UserError:
                raise
            except Exception as e:
                _logger.exception("[TC_DEBUG] Error en blindaje button_validate | picking=%s(ID=%s) err=%s", p.name, p.id, e)

        return super().button_validate()
