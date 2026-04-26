# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many(
        'stock.transit.voyage',
        'picking_id',
        string='Viajes de Tránsito'
    )

    transit_count = fields.Integer(compute='_compute_transit_count')

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
    # HELPERS
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

    def _get_linked_reception_voyage(self):
        self.ensure_one()

        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage and self.origin:
            origin_ref = self.origin.split(' ')[0]
            voyage = self.env['stock.transit.voyage'].search([
                ('name', 'ilike', origin_ref)
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

        if self.move_line_ids:
            self.move_line_ids.unlink()

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
                    target_move = self.env['stock.move'].create({
                        'picking_id': self.id,
                        'product_id': line.product_id.id,
                        'product_uom': line.product_id.uom_id.id,
                        'product_uom_qty': line.product_uom_qty,
                        'location_id': self.location_id.id,
                        'location_dest_id': self.location_dest_id.id,
                        'company_id': self.company_id.id,
                    })
                    target_move._action_confirm()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] No se pudo crear demanda para %s: %s",
                        line.product_id.name,
                        e,
                        exc_info=True,
                    )
                    continue

            try:
                self.env['stock.move.line'].create({
                    'picking_id': self.id,
                    'move_id': target_move.id,
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_id.uom_id.id,
                    'lot_id': line.lot_id.id,
                    'location_id': target_move.location_id.id,
                    'location_dest_id': target_move.location_dest_id.id,
                    'quantity': line.product_uom_qty,
                })
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
                }
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

        Esto evita cerrar inventario físico con datos declarados o parcialmente
        conciliados.
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

                try:
                    pick._auto_close_linked_transit_voyage()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] Falló el cierre automático del viaje en %s: %s",
                        pick.name,
                        str(e),
                        exc_info=True,
                    )

        _logger.info(
            "=== [TC_DEBUG] VALIDATION FINISHED - Picking IDs: %s ===",
            self.ids,
        )
        return res

    def _assign_lots_to_delivery_orders(self):
        """
        Cuando se valida el picking interno Transit → Stock, asigna los lotes
        a las entregas pendientes según la pre-asignación del Viaje.

        Fuente de verdad:
        - Solo se asignan lotes con allocation_status = reserved.
        - Solo se asignan lotes realmente movidos en esta recepción física.
        """
        self.ensure_one()
        _logger.info("[TC_DEBUG] _assign_lots_to_delivery_orders START for %s", self.name)

        voyage = self._get_linked_reception_voyage()

        if not voyage:
            _logger.info(
                "[TC_DEBUG] El picking %s NO está vinculado como recepción de ningún Viaje de Tránsito. Saltando.",
                self.name,
            )
            return

        _logger.info(
            "[TC_DEBUG] Voyage vinculado: %s ID %s.",
            voyage.name,
            voyage.id,
        )

        lot_to_assignment = {}

        for line in voyage.line_ids:
            if line.lot_id and line.order_id and line.allocation_status == 'reserved':
                lot_to_assignment[line.lot_id.id] = {
                    'order': line.order_id,
                    'product': line.product_id,
                }

        _logger.info(
            "[TC_DEBUG] Mapa de Asignación Lote -> SO: %s reglas encontradas en el viaje.",
            len(lot_to_assignment),
        )

        delivery_cache = {}

        def _find_delivery(target_so):
            if target_so.id in delivery_cache:
                return delivery_cache[target_so.id]

            domain_delivery = [
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ['confirmed', 'assigned', 'partially_available']),
                ('company_id', '=', self.company_id.id),
            ]

            delivery = self.env['stock.picking'].search(
                domain_delivery + [('sale_id', '=', target_so.id)],
                limit=1
            )

            if not delivery:
                delivery = self.env['stock.picking'].search(
                    domain_delivery + [('origin', '=', target_so.name)],
                    limit=1
                )

            delivery_cache[target_so.id] = delivery or False
            return delivery_cache[target_so.id]

        unreserved_moves = set()
        count_success = 0

        for move_line in self.move_line_ids:
            if not move_line.lot_id:
                continue

            qty_just_moved = self._tc_move_line_qty(move_line)

            if qty_just_moved <= 0:
                continue

            assignment = lot_to_assignment.get(move_line.lot_id.id)

            if not assignment:
                _logger.info(
                    "[TC_DEBUG] Lote %s recibido, pero NO tenía asignación reservada en el Viaje. Queda libre.",
                    move_line.lot_id.name,
                )
                continue

            target_so = assignment['order']
            lot_product = move_line.product_id

            _logger.info("--- [TC_DEBUG] Procesando Lote: %s ---", move_line.lot_id.name)
            _logger.info("    > Destino Comercial: %s", target_so.name)
            _logger.info("    > Producto del Lote: %s", lot_product.name)
            _logger.info("    > Ubicación Física Actual: %s", move_line.location_dest_id.display_name)
            _logger.info("    > Cantidad: %s", qty_just_moved)

            delivery_picking = _find_delivery(target_so)

            if not delivery_picking:
                _logger.warning(
                    "    [!] No se encontró Entrega pendiente para %s.",
                    target_so.name,
                )
                continue

            target_move = delivery_picking.move_ids.filtered(
                lambda m: m.product_id.id == lot_product.id
                and m.state not in ['done', 'cancel']
            )

            if not target_move:
                _logger.info(
                    "    [+] Producto %s no está en la entrega %s. Creando move...",
                    lot_product.name,
                    delivery_picking.name,
                )
                try:
                    new_move = self.env['stock.move'].create({
                        'picking_id': delivery_picking.id,
                        'product_id': lot_product.id,
                        'product_uom': lot_product.uom_id.id,
                        'product_uom_qty': qty_just_moved,
                        'location_id': delivery_picking.location_id.id,
                        'location_dest_id': delivery_picking.location_dest_id.id,
                        'company_id': delivery_picking.company_id.id,
                    })

                    if new_move.state == 'draft':
                        new_move._action_confirm()

                    target_move = new_move

                    _logger.info(
                        "    [+] Move creado: ID %s para %s en %s",
                        new_move.id,
                        lot_product.name,
                        delivery_picking.name,
                    )
                except Exception as e:
                    _logger.error(
                        "    [TC_ERROR] No se pudo crear move para %s en %s: %s",
                        lot_product.name,
                        delivery_picking.name,
                        e,
                        exc_info=True,
                    )
                    continue
            else:
                target_move = target_move[0]

                if target_move.product_uom_qty < qty_just_moved:
                    try:
                        target_move.write({'product_uom_qty': qty_just_moved})
                    except Exception as e:
                        _logger.warning(
                            "    [!] No se pudo ajustar demanda del move %s: %s",
                            target_move.id,
                            e,
                        )

            move_key = (delivery_picking.id, target_move.id)

            if move_key not in unreserved_moves:
                if target_move.state in ('partially_available', 'assigned'):
                    try:
                        target_move._do_unreserve()
                        _logger.info(
                            "    [~] Des-reservado move %s en %s",
                            target_move.id,
                            delivery_picking.name,
                        )
                    except Exception as e:
                        _logger.warning("    [!] Error al des-reservar: %s", e)

                unreserved_moves.add(move_key)

            try:
                existing_reserved = self.env['stock.move.line'].search([
                    ('move_id', '=', target_move.id),
                    ('lot_id', '=', move_line.lot_id.id),
                    ('picking_id', '=', delivery_picking.id),
                ], limit=1)

                if existing_reserved:
                    existing_qty = self._tc_move_line_qty(existing_reserved)
                    new_qty = existing_qty + qty_just_moved

                    existing_reserved.write({
                        'quantity': new_qty,
                        'location_id': move_line.location_dest_id.id,
                    })

                    _logger.info(
                        "    [✓] Actualizado move line existente para lote %s: %s",
                        move_line.lot_id.name,
                        new_qty,
                    )
                else:
                    self.env['stock.move.line'].create({
                        'picking_id': delivery_picking.id,
                        'move_id': target_move.id,
                        'product_id': lot_product.id,
                        'lot_id': move_line.lot_id.id,
                        'product_uom_id': move_line.product_uom_id.id,
                        'location_id': move_line.location_dest_id.id,
                        'location_dest_id': target_move.location_dest_id.id,
                        'quantity': qty_just_moved,
                    })

                    _logger.info(
                        "    [✓] Creado move line para lote %s cantidad %s",
                        move_line.lot_id.name,
                        qty_just_moved,
                    )

                count_success += 1

            except Exception as e:
                _logger.error(
                    "    [TC_ERROR] Error crítico asignando lote %s: %s",
                    move_line.lot_id.name,
                    e,
                    exc_info=True,
                )

        _logger.info(
            "[TC_DEBUG] Proceso finalizado. %s lotes asignados exitosamente.",
            count_success,
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
            'context': {'default_picking_id': self.id}
        }