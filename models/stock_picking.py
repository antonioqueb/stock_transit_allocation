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
        Sobreescritura: Al validar la Recepción Física (Internal), inyectamos los lotes en la Entrega (Delivery).
        """
        # 1. Ejecutar validación estándar de Odoo (mueve el stock a físico)
        res = super(StockPicking, self).button_validate()
        
        for pick in self:
            # A) Lógica de Entrada (Crear Viaje al recibir PO -> Tránsito)
            is_transit_loc = False
            dest_loc = pick.location_dest_id
            if dest_loc and (dest_loc.id == 128 or any(x in dest_loc.name for x in ['Transit', 'Tránsito', 'Trancit'])):
                is_transit_loc = True
            
            if is_transit_loc and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()

            # B) Lógica de Recepción Física (Tránsito -> Stock) -> Asignar a Entrega
            # Se ejecuta solo si la transferencia se completó ('done') y es interna
            if pick.picking_type_code == 'internal' and pick.state == 'done':
                _logger.info(f"[FIX_DEBUG] Validado Picking {pick.name}. Iniciando asignación automática...")
                pick._assign_lots_to_delivery_orders()

        return res

    def _assign_lots_to_delivery_orders(self):
        """
        Reserva forzosamente los lotes en la Orden de Entrega del cliente.
        Limpiamos reservas previas genéricas para asegurar que entre EL lote del viaje.
        """
        self.ensure_one()
        
        # 1. Buscar si esta recepción pertenece a un Voyage (Torre de Control)
        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage:
            _logger.info("[FIX_DEBUG] Este picking no es una recepción de Voyage. Saltando.")
            return

        _logger.info(f"[FIX_DEBUG] Sincronizando entregas desde Viaje: {voyage.name}")

        # 2. Mapa de Verdad: Qué lote va a qué Orden de Venta
        lot_to_so_map = {}
        for line in voyage.line_ids:
            if line.lot_id and line.order_id and line.allocation_status == 'reserved':
                lot_to_so_map[line.lot_id.id] = line.order_id

        # 3. Recorrer lo que ACABAMOS de recibir (Move Lines del Picking actual)
        for move_line in self.move_line_ids:
            if not move_line.lot_id or not move_line.qty_done:
                continue

            # ¿Este lote tiene dueño?
            target_so = lot_to_so_map.get(move_line.lot_id.id)
            if not target_so:
                continue

            _logger.info(f"[FIX_DEBUG] Procesando Lote {move_line.lot_id.name} -> Para SO {target_so.name}")

            # 4. Buscar la Entrega (Delivery) pendiente de esa SO
            delivery_picking = self.env['stock.picking'].search([
                ('origin', '=', target_so.name),
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ['confirmed', 'assigned', 'partially_available']),
                ('company_id', '=', self.company_id.id)
            ], limit=1)

            if not delivery_picking:
                _logger.warning(f"[FIX_DEBUG] No se encontró Entrega pendiente para {target_so.name}.")
                continue

            # 5. Buscar el Movimiento (Stock Move) del producto en la Entrega
            target_move = delivery_picking.move_ids.filtered(
                lambda m: m.product_id.id == move_line.product_id.id and m.state not in ['done', 'cancel']
            )

            if not target_move:
                _logger.warning(f"[FIX_DEBUG] El producto {move_line.product_id.name} no está en la entrega {delivery_picking.name}.")
                continue
            
            # Tomamos el primer movimiento válido
            target_move = target_move[0]

            # =========================================================
            # PASO CRÍTICO: LIMPIEZA DE RESERVAS PREVIAS
            # =========================================================
            # Si Odoo ya reservó algo automáticamente (sin lote o con otro lote),
            # necesitamos liberarlo para meter nuestro lote específico.
            try:
                if target_move.state in ['partially_available', 'assigned']:
                    _logger.info(f"[FIX_DEBUG] Liberando reservas previas en {target_move.id} para inyectar lote correcto.")
                    target_move._do_unreserve()
            except Exception as e:
                _logger.warning(f"[FIX_DEBUG] No se pudo des-reservar (puede ser normal): {e}")

            # =========================================================
            # PASO CRÍTICO: INYECCIÓN DE LA RESERVA (STOCK.MOVE.LINE)
            # =========================================================
            try:
                # Verificamos si ya existe la línea exacta para no duplicar
                existing_reserved = self.env['stock.move.line'].search([
                    ('move_id', '=', target_move.id),
                    ('lot_id', '=', move_line.lot_id.id),
                    ('picking_id', '=', delivery_picking.id)
                ], limit=1)

                if existing_reserved:
                    # Actualizar
                    existing_reserved.write({
                        'quantity': existing_reserved.quantity + move_line.qty_done,
                        'location_id': move_line.location_dest_id.id, 
                    })
                    _logger.info(f"[FIX_DEBUG] Reserva actualizada en {delivery_picking.name} para lote {move_line.lot_id.name}")
                else:
                    # Crear
                    # location_id: DEBE SER la ubicación donde acabamos de poner el stock (move_line.location_dest_id)
                    vals = {
                        'picking_id': delivery_picking.id,
                        'move_id': target_move.id,
                        'product_id': move_line.product_id.id,
                        'lot_id': move_line.lot_id.id,
                        'product_uom_id': move_line.product_uom_id.id,
                        'location_id': move_line.location_dest_id.id, 
                        'location_dest_id': target_move.location_dest_id.id,
                        'quantity': move_line.qty_done, # Esto reserva
                        'qty_done': 0.0,
                    }
                    self.env['stock.move.line'].create(vals)
                    _logger.info(f"[FIX_DEBUG] Lote {move_line.lot_id.name} inyectado exitosamente en {delivery_picking.name}")

            except Exception as e:
                _logger.error(f"[FIX_DEBUG] Error asignando lote {move_line.lot_id.name}: {e}")

        _logger.info("[FIX_DEBUG] Proceso de asignación finalizado.")

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