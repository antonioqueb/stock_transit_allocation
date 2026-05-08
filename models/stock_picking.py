# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many(
        'stock.transit.voyage',
        'picking_id',
        string='Viajes de Tránsito',
    )

    transit_count = fields.Integer(
        compute='_compute_transit_count',
    )

    supplier_shipment_id = fields.Many2one(
        'supplier.shipment',
        string='Embarque proveedor',
        copy=False,
        index=True,
        ondelete='set null',
        help='Relaciona esta recepción con un embarque específico capturado en el portal del proveedor.',
    )

    transit_sale_order_ids = fields.Many2many(
        'sale.order',
        string='Pedidos Consolidados',
        compute='_compute_transit_sale_orders',
        store=True,
    )

    @api.depends('move_ids.sale_line_id')
    def _compute_transit_sale_orders(self):
        for picking in self:
            picking.transit_sale_order_ids = picking.move_ids.sale_line_id.order_id

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    # -------------------------------------------------------------------------
    # HELPERS GENERALES
    # -------------------------------------------------------------------------

    def _tc_move_line_qty(self, move_line):
        """
        Compatibilidad Odoo 17/18/19:
        - En Odoo 18/19 suele usarse quantity.
        - En versiones previas o algunos flujos legacy puede existir qty_done.
        """
        if 'quantity' in move_line._fields:
            return move_line.quantity or 0.0

        return move_line.qty_done or 0.0

    def _tc_assignment_context(self):
        ctx = dict(self.env.context or {})
        ctx.update({
            'skip_stone_sync_so': True,
            'skip_stone_sync_picking': True,
            'skip_hold_validation': True,
            'skip_picking_clean': True,
            'skip_transit_sale_sync': True,
            'skip_procurement': True,
        })
        return ctx

    def _tc_prepare_stock_move_vals(
        self,
        picking,
        product,
        qty,
        sale_line=False,
        description=False,
    ):
        """
        Crea valores seguros para stock.move en Odoo 18/19.

        En tu Odoo 19 ya se detectó que stock.move puede no aceptar 'name',
        por eso todos los campos se validan contra _fields antes de crear.
        """
        self.ensure_one()

        Move = self.env['stock.move']
        move_fields = Move._fields

        vals = {
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': qty or 0.0,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'company_id': picking.company_id.id or self.company_id.id,
        }

        label = description or product.display_name

        if 'name' in move_fields:
            vals['name'] = label

        if 'description_picking' in move_fields:
            vals['description_picking'] = label

        if 'product_uom' in move_fields:
            vals['product_uom'] = product.uom_id.id
        elif 'product_uom_id' in move_fields:
            vals['product_uom_id'] = product.uom_id.id

        if 'picking_type_id' in move_fields:
            vals['picking_type_id'] = picking.picking_type_id.id

        if 'partner_id' in move_fields:
            vals['partner_id'] = picking.partner_id.id if picking.partner_id else False

        if 'group_id' in move_fields and picking.group_id:
            vals['group_id'] = picking.group_id.id

        if sale_line and 'sale_line_id' in move_fields:
            vals['sale_line_id'] = sale_line.id

        if 'date' in move_fields:
            vals['date'] = fields.Datetime.now()

        if 'procure_method' in move_fields:
            vals['procure_method'] = 'make_to_stock'

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_fields
        }

        return vals

    def _tc_prepare_stock_move_line_vals(
        self,
        picking,
        move,
        product,
        lot,
        qty,
        location_id=False,
        location_dest_id=False,
    ):
        """
        Crea valores seguros para stock.move.line en Odoo 18/19.
        """
        self.ensure_one()

        MoveLine = self.env['stock.move.line']
        move_line_fields = MoveLine._fields

        vals = {
            'picking_id': picking.id,
            'move_id': move.id,
            'company_id': picking.company_id.id or self.company_id.id,
            'product_id': product.id,
            'lot_id': lot.id if lot else False,
            'location_id': location_id or move.location_id.id,
            'location_dest_id': location_dest_id or move.location_dest_id.id,
        }

        if 'product_uom_id' in move_line_fields:
            vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in move_line_fields:
            vals['product_uom'] = product.uom_id.id

        if 'quantity' in move_line_fields:
            vals['quantity'] = qty or 0.0
        elif 'qty_done' in move_line_fields:
            vals['qty_done'] = qty or 0.0

        # Evita que Odoo 19 trate la línea como físicamente "picked"
        # antes de que el usuario valide la operación de salida.
        if 'picked' in move_line_fields:
            vals['picked'] = False

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_line_fields
        }

        return vals

    def _get_linked_reception_voyage(self):
        self.ensure_one()

        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id),
        ], limit=1)

        if not voyage and self.origin:
            origin_ref = self.origin.split(' ')[0]
            voyage = self.env['stock.transit.voyage'].search([
                ('name', 'ilike', origin_ref),
            ], limit=1)

        return voyage

    def _auto_close_linked_transit_voyage(self):
        self.ensure_one()

        voyage = self._get_linked_reception_voyage()
        if not voyage:
            _logger.info(
                "[TC_DEBUG] Picking %s no tiene viaje de recepción vinculado para cierre automático.",
                self.name,
            )
            return

        try:
            voyage._auto_finalize_after_reception()
            _logger.info(
                "[TC_DEBUG] Viaje %s auto-cerrado tras validar %s.",
                voyage.name,
                self.name,
            )
        except Exception as e:
            _logger.error(
                "[TC_ERROR] Falló el cierre automático del viaje %s desde %s: %s",
                voyage.name,
                self.name,
                e,
                exc_info=True,
            )
            raise

    # -------------------------------------------------------------------------
    # ACCIONES
    # -------------------------------------------------------------------------

    def action_sync_from_voyage(self):
        self.ensure_one()
        _logger.info("[TC_DEBUG] Sincronizando Picking %s con Viaje...", self.name)

        voyage = self._get_linked_reception_voyage()

        if not voyage:
            raise UserError(_(
                "No se encontró un Viaje de Tránsito vinculado a esta recepción para sincronizar."
            ))

        if self.state == 'done':
            raise UserError(_("No puede sincronizar una recepción ya validada."))

        if self.state == 'cancel':
            raise UserError(_("No puede sincronizar una recepción cancelada."))

        ctx = self._tc_assignment_context()

        if self.move_line_ids:
            self.move_line_ids.with_context(ctx).unlink()

        lines_created = 0

        for line in voyage.line_ids:
            if not line.lot_id or line.product_uom_qty <= 0:
                continue

            move = self.move_ids.filtered(
                lambda m: m.product_id.id == line.product_id.id
                and m.state not in ['done', 'cancel']
            )

            target_move = False

            if move:
                target_move = move[0]
            else:
                _logger.info(
                    "[TC_FIX] Creando demanda faltante para %s en %s",
                    line.product_id.name,
                    self.name,
                )

                try:
                    move_vals = self._tc_prepare_stock_move_vals(
                        picking=self,
                        product=line.product_id,
                        qty=line.product_uom_qty,
                        description=line.product_id.display_name,
                    )
                    target_move = self.env['stock.move'].with_context(ctx).create(move_vals)

                    if target_move.state == 'draft' and hasattr(target_move, '_action_confirm'):
                        target_move.with_context(ctx)._action_confirm()

                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] No se pudo crear demanda para %s: %s",
                        line.product_id.name,
                        e,
                        exc_info=True,
                    )
                    continue

            try:
                move_line_vals = self._tc_prepare_stock_move_line_vals(
                    picking=self,
                    move=target_move,
                    product=line.product_id,
                    lot=line.lot_id,
                    qty=line.product_uom_qty,
                    location_id=target_move.location_id.id,
                    location_dest_id=target_move.location_dest_id.id,
                )

                self.env['stock.move.line'].with_context(ctx).create(move_line_vals)
                lines_created += 1

            except Exception as e:
                _logger.error(
                    "[TC_ERROR] Error creando línea de sincronización para lote %s: %s",
                    line.lot_id.name,
                    e,
                    exc_info=True,
                )

        if lines_created > 0:
            msg = _(
                "Sincronización completada. %s líneas de lotes cargadas desde el Viaje %s."
            ) % (lines_created, voyage.name)

            self.message_post(body=msg)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sincronización Exitosa'),
                    'message': _(
                        'Los lotes han sido cargados. Verifique las cantidades y presione Validar.'
                    ),
                    'type': 'success',
                    'sticky': False,
                },
            }

        raise UserError(_(
            "No se encontraron líneas válidas con lote y cantidad mayor a cero en el viaje para sincronizar."
        ))

    def button_validate(self):
        """
        Validación protegida para recepción física.

        Regla:
        Si el picking interno está vinculado como recepción física de un viaje,
        NO se puede validar hasta que el Worksheet físico haya sido procesado.

        Además, cuando la recepción física se valida, se pasan los lotes
        preasignados desde Torre de Control hacia la entrega del pedido.
        """
        for pick in self:
            voyage = pick._get_linked_reception_voyage()

            if (
                voyage
                and pick.picking_type_code == 'internal'
                and pick.state not in ('done', 'cancel')
                and 'worksheet_imported' in pick._fields
                and not pick.worksheet_imported
            ):
                raise UserError(_(
                    "Debe procesar el Worksheet físico antes de validar la recepción.\n\n"
                    "Flujo requerido:\n"
                    "1. Corregir o confirmar el Packing List físico.\n"
                    "2. Reprocesar el Packing List físico.\n"
                    "3. Generar o abrir el Worksheet.\n"
                    "4. Capturar/procesar medidas reales.\n"
                    "5. Validar la recepción física."
                ))

        _logger.info(
            "=== [TC_DEBUG] VALIDATE BUTTON CLICKED - Picking IDs: %s ===",
            self.ids,
        )

        res = super(StockPicking, self).button_validate()

        for pick in self:
            is_transit_loc = False
            dest_loc = pick.location_dest_id

            if dest_loc and (
                dest_loc.id == 128
                or any(x in (dest_loc.name or '') for x in ['Transit', 'Tránsito', 'Trancit'])
            ):
                is_transit_loc = True

            if is_transit_loc and pick.picking_type_code == 'incoming':
                _logger.info(
                    "[TC_DEBUG] Picking %s detectado como Entrada a Tránsito. Creando Viaje independiente...",
                    pick.name,
                )
                pick._create_automatic_transit_voyage()

            if pick.picking_type_code == 'internal' and pick.state == 'done':
                _logger.info(
                    "[TC_DEBUG] Picking %s validado Internal/Done. Iniciando lógica de asignación a Ventas...",
                    pick.name,
                )

                try:
                    pick._assign_lots_to_delivery_orders()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] Falló la asignación automática en %s: %s",
                        pick.name,
                        str(e),
                        exc_info=True,
                    )
                    raise

                try:
                    pick._auto_close_linked_transit_voyage()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] Falló el cierre automático del viaje en %s: %s",
                        pick.name,
                        str(e),
                        exc_info=True,
                    )
                    raise

        _logger.info(
            "=== [TC_DEBUG] VALIDATION FINISHED - Picking IDs: %s ===",
            self.ids,
        )
        return res

    # -------------------------------------------------------------------------
    # HELPERS: PASAR ASIGNACIÓN DE TRÁNSITO A ENTREGA
    # -------------------------------------------------------------------------

    def _tc_get_sale_line_for_assignment(self, order, product):
        SaleLine = self.env['sale.order.line']

        if not order or not product:
            return SaleLine

        lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.id == product.id
        )

        if not lines:
            return SaleLine

        pending_lines = lines.filtered(
            lambda l: (l.product_uom_qty or 0.0) > (l.qty_delivered or 0.0)
        )

        return (pending_lines or lines)[:1]

    def _tc_build_lot_breakdown_from_transit_lines(self, transit_lines):
        breakdown = {}

        for transit_line in transit_lines:
            lot = transit_line.lot_id
            if not lot:
                continue

            lot_type = ''
            if 'x_tipo' in lot._fields and lot.x_tipo:
                lot_type = str(lot.x_tipo).lower()

            if lot_type in ('formato', 'pieza'):
                breakdown[str(lot.id)] = transit_line.product_uom_qty or 0.0

        return breakdown

    def _tc_prepare_breakdown_value_for_sale_line(self, sale_line, breakdown):
        if not breakdown:
            return False

        field = sale_line._fields.get('x_lot_breakdown_json')
        if field and field.type in ('char', 'text'):
            return json.dumps(breakdown)

        return breakdown

    def _tc_sync_sale_line_lots_after_reception(self, sale_line, transit_lines):
        """
        Fusiona en la SO los lotes recibidos/asignados desde tránsito.

        Flujo mixto:
        - Conserva placas ya seleccionadas manualmente.
        - Agrega lotes comprados/recibidos desde tránsito.
        - Solo limpia Mandar a pedir cuando ya no queda pendiente real.
        """
        if not sale_line or 'lot_ids' not in sale_line._fields:
            return False

        transit_lot_ids = transit_lines.mapped('lot_id').ids
        current_lot_ids = sale_line.lot_ids.ids if sale_line.lot_ids else []

        merged_lot_ids = list(current_lot_ids)
        for lot_id in transit_lot_ids:
            if lot_id not in merged_lot_ids:
                merged_lot_ids.append(lot_id)

        vals = {
            'lot_ids': [(6, 0, merged_lot_ids)],
        }

        if 'x_lot_breakdown_json' in sale_line._fields:
            breakdown = sale_line._tc_read_lot_breakdown() if hasattr(sale_line, '_tc_read_lot_breakdown') else {}
            transit_breakdown = self._tc_build_lot_breakdown_from_transit_lines(transit_lines)
            breakdown.update(transit_breakdown)

            if hasattr(sale_line, '_tc_prepare_breakdown_value_for_line'):
                vals['x_lot_breakdown_json'] = sale_line._tc_prepare_breakdown_value_for_line(breakdown)
            else:
                vals['x_lot_breakdown_json'] = self._tc_prepare_breakdown_value_for_sale_line(
                    sale_line,
                    breakdown,
                )

        sale_line.with_context(
            skip_stone_sync_picking=True,
            skip_stone_sync_so=True,
            skip_hold_validation=True,
            skip_picking_clean=True,
            skip_transit_sale_sync=True,
        ).write(vals)

        if hasattr(sale_line, '_tc_get_pending_allocation_qty'):
            pending_qty = sale_line._tc_get_pending_allocation_qty()
            if sale_line._tc_float_le_zero(pending_qty):
                clear_vals = {}
                if 'auto_transit_assign' in sale_line._fields:
                    clear_vals['auto_transit_assign'] = False
                if 'tc_stock_rejected' in sale_line._fields:
                    clear_vals.update({
                        'tc_stock_rejected': False,
                        'tc_stock_rejected_reason': False,
                        'tc_stock_rejected_by': False,
                        'tc_stock_rejected_at': False,
                    })
                if clear_vals:
                    sale_line.with_context(skip_tc_allocation_recovery=True).write(clear_vals)

        _logger.info(
            "[TC_ASSIGN] SO line %s lotes fusionados: actuales=%s tránsito=%s final=%s",
            sale_line.id,
            current_lot_ids,
            transit_lot_ids,
            merged_lot_ids,
        )

        return True

    def _tc_find_delivery_for_order(self, order, delivery_cache, product=False, sale_line=False):
        """
        Busca la operación de venta pendiente asociada a una orden.

        Odoo 19 / rutas multi-step:
        - En almacenes con Pick/Pack/Ship, la primera operación vinculada a la SO
          puede ser internal, por ejemplo SOM/PICK/00039.
        - No se debe limitar la búsqueda a picking_type_code = outgoing.
        - La clave real observada es stock.move.sale_line_id.
        """
        self.ensure_one()

        if not order or not order.exists():
            return False

        cache_key = (
            order.id,
            sale_line.id if sale_line else 0,
            product.id if product else 0,
        )

        if cache_key in delivery_cache:
            return delivery_cache[cache_key]

        Picking = self.env['stock.picking']
        Move = self.env['stock.move']

        pending_states = [
            'draft',
            'waiting',
            'confirmed',
            'assigned',
            'partially_available',
        ]

        # CRÍTICO:
        # outgoing = entrega directa / salida final
        # internal = picking/preparación en rutas multi-step, ej. SOM/PICK/00039
        sale_operation_codes = ['outgoing', 'internal']

        base_domain = [
            ('id', '!=', self.id),
            ('picking_type_code', 'in', sale_operation_codes),
            ('state', 'in', pending_states),
            ('company_id', 'in', [False, self.company_id.id]),
        ]

        delivery = Picking
        has_sale_line_id = 'sale_line_id' in Move._fields

        def _search(domain, order_by='id asc'):
            return Picking.search(domain, order=order_by, limit=1)

        # 1) Búsqueda más precisa: operación con move de la sale_line exacta.
        if not delivery and sale_line and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id', '=', sale_line.id),
                ]
            )

        # 2) Operación con movimientos de la orden y producto.
        if not delivery and product and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id.order_id', '=', order.id),
                    ('move_ids.product_id', '=', product.id),
                ]
            )

        # 3) Cualquier operación pendiente ligada a la orden.
        if not delivery and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id.order_id', '=', order.id),
                ]
            )

        # 4) Procurement group defensivo.
        procurement_group = False

        if 'procurement_group_id' in order._fields:
            procurement_group = order.procurement_group_id
        elif 'group_id' in order._fields:
            procurement_group = order.group_id

        if (
            not delivery
            and procurement_group
            and procurement_group.exists()
            and 'group_id' in Picking._fields
        ):
            delivery = _search(
                base_domain + [
                    ('group_id', '=', procurement_group.id),
                ]
            )

        # 5) Búsqueda legacy por sale_id si existe en stock.picking.
        if not delivery and 'sale_id' in Picking._fields:
            delivery = _search(
                base_domain + [
                    ('sale_id', '=', order.id),
                ]
            )

        # 6) Origin exacto.
        if not delivery:
            delivery = _search(
                base_domain + [
                    ('origin', '=', order.name),
                ]
            )

        # 7) Origin flexible.
        if not delivery:
            delivery = _search(
                base_domain + [
                    ('origin', 'ilike', order.name),
                ]
            )

        if delivery:
            _logger.info(
                "[TC_ASSIGN] Operación de venta encontrada para pedido %s | picking=%s | type=%s | sale_line=%s | product=%s",
                order.name,
                delivery.name,
                delivery.picking_type_code,
                sale_line.id if sale_line else False,
                product.display_name if product else False,
            )
        else:
            _logger.warning(
                "[TC_ASSIGN] No se encontró operación de venta pendiente para pedido %s | sale_line=%s | product=%s",
                order.name,
                sale_line.id if sale_line else False,
                product.display_name if product else False,
            )

        delivery_cache[cache_key] = delivery or False
        return delivery_cache[cache_key]

    def _tc_get_or_create_delivery_move(self, delivery, sale_line, product, qty):
        self.ensure_one()

        ctx = self._tc_assignment_context()
        Move = self.env['stock.move']
        has_sale_line_id = 'sale_line_id' in Move._fields

        if has_sale_line_id:
            move = delivery.move_ids.filtered(
                lambda m: m.sale_line_id.id == sale_line.id
                and m.product_id.id == product.id
                and m.state not in ['done', 'cancel']
            )[:1]
        else:
            move = delivery.move_ids.filtered(
                lambda m: m.product_id.id == product.id
                and m.state not in ['done', 'cancel']
            )[:1]

        if move:
            if move.product_uom_qty < qty:
                move.with_context(ctx).write({'product_uom_qty': qty})
            return move

        move_vals = self._tc_prepare_stock_move_vals(
            picking=delivery,
            product=product,
            qty=qty,
            sale_line=sale_line,
            description=sale_line.name or product.display_name,
        )

        move = self.env['stock.move'].with_context(ctx).create(move_vals)

        if move.state == 'draft' and hasattr(move, '_action_confirm'):
            move.with_context(ctx)._action_confirm()

        return move

    def _assign_lots_to_delivery_orders(self):
        """
        Al validar la recepción física Transit → Stock:

        1. Lee las preasignaciones del viaje.
        2. Solo procesa líneas allocation_status = reserved.
        3. Solo procesa lotes realmente recibidos en este picking.
        4. Reemplaza en la SO únicamente los lotes del mismo producto.
        5. Crea/actualiza la entrega con sale_line_id y lotes exactos.
        """
        self.ensure_one()
        _logger.info("[TC_ASSIGN] START recepción física %s", self.name)

        voyage = self._get_linked_reception_voyage()

        if not voyage:
            _logger.info(
                "[TC_ASSIGN] Picking %s sin viaje vinculado. Se omite asignación a ventas.",
                self.name,
            )
            return

        Quant = self.env['stock.quant'].sudo()

        received_by_lot = {}

        for move_line in self.move_line_ids:
            if not move_line.lot_id:
                continue

            qty = self._tc_move_line_qty(move_line)
            if qty <= 0:
                continue

            lot_id = move_line.lot_id.id

            if lot_id not in received_by_lot:
                received_by_lot[lot_id] = {
                    'qty': 0.0,
                    'move_line': move_line,
                    'location_dest_id': move_line.location_dest_id.id,
                }

            received_by_lot[lot_id]['qty'] += qty

        if not received_by_lot:
            _logger.info("[TC_ASSIGN] Picking %s no tiene lotes recibidos.", self.name)
            return

        assignments_by_key = {}

        reserved_lines = voyage.line_ids.filtered(
            lambda l: l.lot_id
            and l.order_id
            and l.product_id
            and l.allocation_status == 'reserved'
            and l.lot_id.id in received_by_lot
        )

        for line in reserved_lines:
            key = (line.order_id.id, line.product_id.id)
            assignments_by_key.setdefault(key, self.env['stock.transit.line'])
            assignments_by_key[key] |= line

        if not assignments_by_key:
            _logger.info(
                "[TC_ASSIGN] Viaje %s no tiene lotes reservados recibidos en %s. Todo queda libre.",
                voyage.name,
                self.name,
            )
            return

        delivery_cache = {}
        total_assigned_lots = 0

        ctx = self._tc_assignment_context()

        for (order_id, product_id), transit_lines in assignments_by_key.items():
            order = self.env['sale.order'].browse(order_id)
            product = self.env['product.product'].browse(product_id)

            if not order.exists() or not product.exists():
                continue

            if order.state not in ('sale', 'done'):
                raise UserError(_(
                    "El pedido %s tiene lotes preasignados desde tránsito, pero no está confirmado."
                ) % order.name)

            sale_line = self._tc_get_sale_line_for_assignment(order, product)

            if not sale_line:
                raise UserError(_(
                    "El pedido %(order)s no tiene una línea vigente para el producto %(product)s."
                ) % {
                    'order': order.name,
                    'product': product.display_name,
                })

            delivery = self._tc_find_delivery_for_order(
                order=order,
                delivery_cache=delivery_cache,
                product=product,
                sale_line=sale_line,
            )

            if not delivery:
                raise UserError(_(
                    "No se encontró una operación de venta pendiente para el pedido %s.\n\n"
                    "Confirme que el pedido tenga un picking activo vinculado a la línea de venta."
                ) % order.name)

            total_qty = 0.0

            for transit_line in transit_lines:
                received_data = received_by_lot.get(transit_line.lot_id.id)
                if not received_data:
                    continue
                total_qty += received_data['qty']

            if total_qty <= 0:
                continue

            # 1) Fusionar selección oficial en la línea de venta.
            #    No reemplaza placas manuales; agrega las compradas/recibidas.
            self._tc_sync_sale_line_lots_after_reception(sale_line, transit_lines)

            assigned_qty = total_qty
            if hasattr(sale_line, '_tc_get_assigned_lot_qty'):
                assigned_qty = sale_line._tc_get_assigned_lot_qty()

            requested_qty = sale_line.product_uom_qty or assigned_qty or total_qty
            target_qty = min(requested_qty, assigned_qty) if assigned_qty else total_qty

            # 2) Obtener/crear movimiento de entrega vinculado a la sale_line.
            target_move = self._tc_get_or_create_delivery_move(
                delivery=delivery,
                sale_line=sale_line,
                product=product,
                qty=target_qty,
            )

            if target_move.product_uom_qty != target_qty:
                target_move.with_context(ctx).write({'product_uom_qty': target_qty})

            # 3) No se llama _do_unreserve() sobre todo el move porque eso puede
            #    soltar placas manuales ya asignadas. Solo reconstruimos los lotes
            #    que llegaron desde tránsito para evitar duplicados.
            transit_lot_ids = transit_lines.mapped('lot_id').ids
            existing_transit_move_lines = target_move.move_line_ids.filtered(
                lambda ml: ml.lot_id and ml.lot_id.id in transit_lot_ids
            )
            if existing_transit_move_lines:
                existing_transit_move_lines.with_context(ctx).unlink()

            # 4) Crear move lines exactas desde ubicación interna real para los lotes recibidos.
            for transit_line in transit_lines:
                lot = transit_line.lot_id
                received_data = received_by_lot.get(lot.id)

                if not received_data:
                    continue

                qty_to_assign = received_data['qty']
                source_location_id = received_data['location_dest_id']

                quant = Quant.search([
                    ('company_id', '=', self.company_id.id),
                    ('product_id', '=', product.id),
                    ('lot_id', '=', lot.id),
                    ('quantity', '>', 0),
                    ('location_id.usage', '=', 'internal'),
                ], order='id desc', limit=1)

                if quant:
                    source_location_id = quant.location_id.id
                    qty_to_assign = min(qty_to_assign, quant.quantity)

                if qty_to_assign <= 0:
                    continue

                move_line_vals = self._tc_prepare_stock_move_line_vals(
                    picking=delivery,
                    move=target_move,
                    product=product,
                    lot=lot,
                    qty=qty_to_assign,
                    location_id=source_location_id,
                    location_dest_id=target_move.location_dest_id.id,
                )

                self.env['stock.move.line'].with_context(ctx).create(move_line_vals)

                total_assigned_lots += 1

                _logger.info(
                    "[TC_ASSIGN] Pedido %s | Operación %s | Producto %s | Lote %s | Qty %.4f",
                    order.name,
                    delivery.name,
                    product.display_name,
                    lot.name,
                    qty_to_assign,
                )

            delivery.message_post(body=_(
                "🚚 Material recibido desde tránsito asignado automáticamente.<br/>"
                "<b>Viaje:</b> %(voyage)s<br/>"
                "<b>Pedido:</b> %(order)s<br/>"
                "<b>Producto:</b> %(product)s<br/>"
                "<b>Lotes:</b> %(lots)s"
            ) % {
                'voyage': voyage.name,
                'order': order.name,
                'product': product.display_name,
                'lots': ', '.join(transit_lines.mapped('lot_id.name')),
            })

        _logger.info(
            "[TC_ASSIGN] FIN recepción %s. Lotes asignados a entregas: %s",
            self.name,
            total_assigned_lots,
        )

    def _create_automatic_transit_voyage(self):
        """
        Crea SIEMPRE un nuevo voyage por picking.
        No reutiliza voyages existentes de la misma OC.
        """
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']

        existing = Voyage.search([
            ('picking_id', '=', self.id),
            ('custom_status', '!=', 'cancel'),
        ], limit=1)

        if existing:
            _logger.info(
                "[TC] Picking %s ya tiene voyage %s, actualizando lotes.",
                self.name,
                existing.name,
            )
            existing.action_load_from_picking()
            return

        bl = (
            getattr(self, 'supplier_bl_number', None)
            or self.origin
            or self.name
        )

        voyage = Voyage.create({
            'picking_id': self.id,
            'purchase_id': self.purchase_id.id if self.purchase_id else False,
            'bl_number': bl,
            'etd': fields.Date.today(),
            'custom_status': 'on_sea',
        })

        _logger.info(
            "[TC] Voyage %s creado para picking %s OC: %s",
            voyage.name,
            self.name,
            self.purchase_id.name if self.purchase_id else 'N/A',
        )

        voyage.action_load_from_picking()

    def action_view_transit_voyage(self):
        self.ensure_one()
        return {
            'name': 'Gestión de Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.transit.voyage',
            'view_mode': 'list,form',
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id},
        }