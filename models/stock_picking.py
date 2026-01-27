# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
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
            # Se ejecuta solo si es interno, está hecho, y viene de un tránsito
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
                _logger.info(f"[TC_DEBUG] MoveLine {move_line.id} sin lote. Saltando.")
                continue
            
            qty_just_moved = move_line.qty_done or move_line.quantity
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
            
            # Intento por Grupo (Procurement Group) - Método más seguro
            if target_so.procurement_group_id:
                delivery_picking = self.env['stock.picking'].search(
                    domain_delivery + [('group_id', '=', target_so.procurement_group_id.id)], 
                    limit=1
                )
                if delivery_picking:
                    _logger.info(f"    > Delivery encontrado por Grupo: {delivery_picking.name}")

            # Intento por Origen (Nombre string) si falló el grupo
            if not delivery_picking:
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
                        'qty_done': 0.0,
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