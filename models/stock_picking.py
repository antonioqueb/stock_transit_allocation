# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many('stock.transit.voyage', 'picking_id', string='Viajes de Tránsito')
    transit_count = fields.Integer(compute='_compute_transit_count')
    transit_container_number = fields.Char(string='No. Contenedor (Ref)')
    transit_bl_number = fields.Char(string='BL Number (Tránsito)')
    transit_sale_order_ids = fields.Many2many('sale.order', string='Pedidos Consolidados', compute='_compute_transit_sale_orders', store=True)

    @api.depends('move_ids.sale_line_id')
    def _compute_transit_sale_orders(self):
        for picking in self:
            picking.transit_sale_order_ids = picking.move_ids.sale_line_id.order_id

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    # -------------------------------------------------------------------------
    # NUEVO MÉTODO: Paso 2 de la Recepción Manual
    # -------------------------------------------------------------------------
    def action_sync_from_voyage(self):
        """
        Busca el Viaje de Tránsito origen y carga los lotes específicos en las líneas detalladas.
        Este método se llama manualmente desde el botón en la vista del picking.
        NO valida el picking, solo prepara los datos para que el usuario valide.
        """
        self.ensure_one()
        _logger.info(f"[TC_DEBUG] Sincronizando Picking {self.name} con Viaje...")

        # 1. Encontrar el viaje que generó este picking
        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage:
            # Fallback: intentar por el nombre en el origen si se perdió el enlace directo
            if self.origin:
                origin_ref = self.origin.split(' ')[0] # Ej: "VOY/2026/005" de "VOY/2026/005 (Recepción...)"
                voyage = self.env['stock.transit.voyage'].search([
                    ('name', 'ilike', origin_ref)
                ], limit=1)
        
        if not voyage:
            raise UserError(_("No se encontró un Viaje de Tránsito vinculado a esta recepción para sincronizar."))

        # 2. Limpiar líneas de detalle existentes (stock.move.line)
        # Esto permite re-sincronizar si hubo cambios en el viaje antes de validar
        self.move_line_ids.unlink()

        # 3. Inyectar Lotes desde el Viaje
        lines_created = 0
        for line in voyage.line_ids:
            if not line.lot_id or line.product_uom_qty <= 0:
                continue

            # Buscar el movimiento de demanda (stock.move) correspondiente a este producto
            move = self.move_ids.filtered(lambda m: m.product_id.id == line.product_id.id and m.state not in ['done', 'cancel'])
            
            if not move:
                _logger.warning(f"[TC_WARN] No se encontró demanda para producto {line.product_id.name} en picking {self.name}")
                continue
            
            # Tomamos el primer move disponible
            target_move = move[0]

            try:
                # Crear la línea con la cantidad YA establecida.
                # Al hacerlo en un picking ya existente y confirmado, Odoo no valida automáticamente.
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
                _logger.error(f"[TC_ERROR] Error creando linea de sincronización para lote {line.lot_id.name}: {e}")

        # 4. Resultado
        if lines_created > 0:
            msg = f"Sincronización completada. {lines_created} líneas de lotes cargadas desde el Viaje {voyage.name}."
            self.message_post(body=msg)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sincronización Exitosa'),
                    'message': _('Los lotes han sido cargados. Verifique las cantidades y presione Validar.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            raise UserError(_("No se encontraron líneas válidas con lotes en el viaje para sincronizar."))

    # -------------------------------------------------------------------------
    # SOBREESCRITURAS EXISTENTES
    # -------------------------------------------------------------------------

    def button_validate(self):
        """
        Sobreescritura: Al validar la Recepción Física (Internal: Transit->Stock), 
        buscamos el Delivery Order correspondiente y forzamos la reserva del lote recibido.
        """
        _logger.info(f"=== [TC_DEBUG] VALIDATE BUTTON CLICKED - Picking {self.name} (ID: {self.id}) ===")
        
        # 1. Ejecutar validación estándar de Odoo (mueve el stock a físico)
        res = super(StockPicking, self).button_validate()
        
        for pick in self:
            # A) Lógica de Entrada (Crear Viaje al recibir PO -> Tránsito)
            is_transit_loc = False
            dest_loc = pick.location_dest_id
            if dest_loc and (dest_loc.id == 128 or any(x in dest_loc.name for x in ['Transit', 'Tránsito', 'Trancit'])):
                is_transit_loc = True
            
            if is_transit_loc and pick.picking_type_code == 'incoming':
                _logger.info(f"[TC_DEBUG] Picking {pick.name} detectado como Entrada a Tránsito. Creando/Actualizando Viaje...")
                pick._create_automatic_transit_voyage()

            # B) Lógica de Recepción Física (Tránsito -> Stock) -> Asignar a Entrega
            # Esta lógica se ejecutará AHORA que tú valides manualmente después de sincronizar.
            if pick.picking_type_code == 'internal' and pick.state == 'done':
                _logger.info(f"[TC_DEBUG] Picking {pick.name} validado (Internal/Done). Iniciando lógica de asignación a Ventas...")
                try:
                    pick._assign_lots_to_delivery_orders()
                except Exception as e:
                    _logger.error(f"[TC_ERROR] Falló la asignación automática en {pick.name}: {str(e)}", exc_info=True)

        _logger.info(f"=== [TC_DEBUG] VALIDATION FINISHED - Picking {self.name} ===")
        return res

    def _assign_lots_to_delivery_orders(self):
        """
        Reserva forzosamente los lotes en la Orden de Entrega del cliente.
        Limpiamos reservas previas genéricas para asegurar que entre EL lote del viaje.
        """
        self.ensure_one()
        _logger.info(f"[TC_DEBUG] _assign_lots_to_delivery_orders START for {self.name}")
        
        # 1. Buscar si esta recepción pertenece a un Voyage (Torre de Control)
        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage:
            _logger.info(f"[TC_DEBUG] El picking {self.name} NO está vinculado como recepción de ningún Viaje de Tránsito. Saltando.")
            return

        _logger.info(f"[TC_DEBUG] Voyage vinculado: {voyage.name} (ID: {voyage.id}).")

        # 2. Mapa de Verdad: Qué lote va a qué Orden de Venta
        lot_to_so_map = {}
        for line in voyage.line_ids:
            if line.lot_id and line.order_id and line.allocation_status == 'reserved':
                lot_to_so_map[line.lot_id.id] = line.order_id

        _logger.info(f"[TC_DEBUG] Mapa de Asignación (Lote -> SO): {len(lot_to_so_map)} reglas encontradas en el viaje.")

        # 3. Recorrer lo que ACABAMOS de recibir (Move Lines del Picking actual)
        count_success = 0
        for move_line in self.move_line_ids:
            if not move_line.lot_id:
                continue
            
            # ODOO 19 FIX: usar 'quantity' en lugar de 'qty_done'
            qty_just_moved = move_line.quantity if move_line.quantity > 0 else move_line.qty_done
            
            if qty_just_moved <= 0:
                _logger.info(f"[TC_DEBUG] MoveLine {move_line.id} con cantidad 0. Saltando.")
                continue

            # ¿Este lote tiene dueño en el viaje?
            target_so = lot_to_so_map.get(move_line.lot_id.id)
            if not target_so:
                _logger.info(f"[TC_DEBUG] Lote {move_line.lot_id.name} recibido, pero NO tenía asignación reservada en el Viaje. Queda Libre.")
                continue

            _logger.info(f"--- [TC_DEBUG] Procesando Lote: {move_line.lot_id.name} ---")
            _logger.info(f"    > Destino Comercial: {target_so.name}")
            _logger.info(f"    > Ubicación Física Actual: {move_line.location_dest_id.display_name}")
            _logger.info(f"    > Cantidad: {qty_just_moved}")

            # 4. Buscar la Entrega (Delivery) pendiente de esa SO
            domain_delivery = [
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ['confirmed', 'assigned', 'partially_available']),
                ('company_id', '=', self.company_id.id)
            ]
            
            delivery_picking = False
            
            # ESTRATEGIA 1: Buscar por sale_id (El campo existe en tu modelo)
            delivery_picking = self.env['stock.picking'].search(
                domain_delivery + [('sale_id', '=', target_so.id)], 
                limit=1
            )
            
            if delivery_picking:
                _logger.info(f"    > Delivery encontrado por Sale ID: {delivery_picking.name}")
            else:
                # ESTRATEGIA 2: Fallback por Origin (Nombre string)
                delivery_picking = self.env['stock.picking'].search(
                    domain_delivery + [('origin', '=', target_so.name)], 
                    limit=1
                )
                if delivery_picking:
                    _logger.info(f"    > Delivery encontrado por Origin: {delivery_picking.name}")

            if not delivery_picking:
                _logger.warning(f"    [!] No se encontró Entrega (Delivery) pendiente para {target_so.name}.")
                continue

            # 5. Buscar el Movimiento (Stock Move) del producto en la Entrega
            target_move = delivery_picking.move_ids.filtered(
                lambda m: m.product_id.id == move_line.product_id.id and m.state not in ['done', 'cancel']
            )

            if not target_move:
                _logger.warning(f"    [!] El producto {move_line.product_id.name} no está en la entrega {delivery_picking.name}.")
                continue
            
            target_move = target_move[0]
            _logger.info(f"    > Move Objetivo ID: {target_move.id} (Pide: {target_move.product_uom_qty})")

            # =========================================================
            # PASO CRÍTICO: LIMPIEZA DE RESERVAS PREVIAS
            # =========================================================
            if target_move.state in ['partially_available', 'assigned']:
                try:
                    _logger.info(f"    > Liberando reservas previas en {target_move.id}...")
                    target_move._do_unreserve()
                except Exception as e:
                    _logger.warning(f"    [!] Error al des-reservar: {e}")

            # =========================================================
            # PASO CRÍTICO: INYECCIÓN DE LA RESERVA
            # =========================================================
            try:
                # Verificamos si ya existe la línea exacta
                existing_reserved = self.env['stock.move.line'].search([
                    ('move_id', '=', target_move.id),
                    ('lot_id', '=', move_line.lot_id.id),
                    ('picking_id', '=', delivery_picking.id)
                ], limit=1)

                if existing_reserved:
                    new_qty = existing_reserved.quantity + qty_just_moved
                    existing_reserved.write({
                        'quantity': new_qty,
                        'location_id': move_line.location_dest_id.id, 
                    })
                    _logger.info(f"    [OK] Reserva ACTUALIZADA en {delivery_picking.name}")
                else:
                    vals = {
                        'picking_id': delivery_picking.id,
                        'move_id': target_move.id,
                        'product_id': move_line.product_id.id,
                        'lot_id': move_line.lot_id.id,
                        'product_uom_id': move_line.product_uom_id.id,
                        'location_id': move_line.location_dest_id.id, # CRUCIAL: Donde está ahora
                        'location_dest_id': target_move.location_dest_id.id,
                        'quantity': qty_just_moved, 
                    }
                    self.env['stock.move.line'].create(vals)
                    _logger.info(f"    [OK] Reserva CREADA en {delivery_picking.name}")
                
                count_success += 1

            except Exception as e:
                _logger.error(f"    [TC_ERROR] Error crítico asignando lote {move_line.lot_id.name}: {e}")

        _logger.info(f"[TC_DEBUG] Proceso finalizado. {count_success} lotes asignados exitosamente.")

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']
        
        voyage = Voyage.search([
            ('purchase_id', '=', self.purchase_id.id),
            ('custom_status', '!=', 'cancel')
        ], limit=1)

        if voyage:
            voyage.write({
                'picking_id': self.id,
                'container_number': self.transit_container_number or voyage.container_number,
                'bl_number': self.transit_bl_number or voyage.bl_number,
                'custom_status': 'on_sea'
            })
            voyage.action_load_from_picking()
        else:
            voyage = Voyage.create({
                'picking_id': self.id,
                'purchase_id': self.purchase_id.id,
                'container_number': self.transit_container_number or 'TBD',
                'bl_number': self.transit_bl_number or self.origin,
                'etd': fields.Date.today(),
                'custom_status': 'on_sea'
            })
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