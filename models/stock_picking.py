# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.tools.float_utils import float_compare
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
        Al validar la recepción, si es una recepción de Torre de Control:
        1. Identificamos los lotes recibidos.
        2. Buscamos a qué cliente (SO) pertenecen según el Viaje.
        3. Insertamos el lote directamente en la Orden de Entrega (Delivery) de ese cliente.
        """
        res = super(StockPicking, self).button_validate()
        
        for pick in self:
            # A) Lógica de Entrada (Creación del Viaje al recibir PO -> Tránsito)
            is_transit_loc = False
            dest_loc = pick.location_dest_id
            if dest_loc and (dest_loc.id == 128 or any(x in dest_loc.name for x in ['Transit', 'Tránsito', 'Trancit'])):
                is_transit_loc = True
            
            if is_transit_loc and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()

            # B) Lógica de Recepción Física (Tránsito -> Stock)
            # Aquí es donde ocurre la asignación a la Orden de Entrega
            if pick.picking_type_code == 'internal' and pick.state == 'done':
                pick._assign_lots_to_delivery_orders()

        return res

    def _assign_lots_to_delivery_orders(self):
        """
        Reserva forzosamente los lotes en la Orden de Entrega del cliente.
        """
        self.ensure_one()
        
        # 1. Identificar si este picking pertenece a un Viaje
        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage:
            return

        _logger.info(f"[CONTROL_TOWER] Sincronizando entregas para Recepción {self.name} (Viaje: {voyage.name})")

        # 2. Crear mapa de asignación: Lote ID -> Orden de Venta ID
        # Solo nos interesan las líneas que tienen un cliente asignado (allocation_status='reserved')
        lot_to_so_map = {}
        for line in voyage.line_ids:
            if line.lot_id and line.order_id and line.allocation_status == 'reserved':
                lot_to_so_map[line.lot_id.id] = line.order_id

        # 3. Iterar sobre lo que acabamos de recibir físicamente
        # self.move_line_ids contiene el detalle de Lote, Producto y Cantidad Real recibida
        for move_line in self.move_line_ids:
            # Validaciones básicas
            if not move_line.lot_id or not move_line.qty_done:
                continue

            # Ver si este lote tiene dueño en la Torre de Control
            target_so = lot_to_so_map.get(move_line.lot_id.id)
            if not target_so:
                continue

            # 4. Buscar la Orden de Entrega (Picking de Salida) de esa SO
            # Debe ser 'outgoing', estar confirmada (esperando reserva) y salir de la ubicación donde recibimos
            # o de una ubicación padre/hija (normalmente stock).
            delivery_picking = self.env['stock.picking'].search([
                ('origin', '=', target_so.name),
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ['confirmed', 'assigned', 'partially_available']),
                ('company_id', '=', self.company_id.id)
            ], limit=1)

            if not delivery_picking:
                _logger.warning(f"[CONTROL_TOWER] No se encontró entrega pendiente para {target_so.name}. Lote {move_line.lot_id.name} queda libre en stock.")
                continue

            # 5. Buscar el Movimiento (Stock Move) del producto en la Entrega
            target_move = delivery_picking.move_ids.filtered(
                lambda m: m.product_id.id == move_line.product_id.id and m.state not in ['done', 'cancel']
            )

            if not target_move:
                # Caso borde: El producto no está en la entrega (¿pedido modificado?)
                continue
            
            # Tomamos el primer movimiento válido (por si hay líneas divididas, tomamos la primera pendiente)
            target_move = target_move[0]

            # 6. INYECCIÓN DE LA RESERVA
            # Creamos un stock.move.line en la entrega vinculado al lote específico
            try:
                # Verificamos si ya existe para no duplicar (idempotencia)
                existing_reserved = self.env['stock.move.line'].search([
                    ('move_id', '=', target_move.id),
                    ('lot_id', '=', move_line.lot_id.id),
                    ('picking_id', '=', delivery_picking.id)
                ], limit=1)

                if existing_reserved:
                    # Si ya existe, actualizamos la cantidad reservada si es necesario
                    # Odoo usa 'quantity' (o reserved_uom_qty) para la reserva, NO qty_done
                    existing_reserved.write({
                        'quantity': existing_reserved.quantity + move_line.qty_done,
                        'location_id': move_line.location_dest_id.id, # Actualizamos ubicación por si acaso
                    })
                else:
                    # Crear nueva línea de reserva
                    # IMPORTANTE: 
                    # - location_id: Dónde está el lote AHORA (destino de la recepción)
                    # - quantity: Cuánto reservamos (lo que recibimos)
                    # - qty_done: 0 (porque aún no sale del almacén)
                    self.env['stock.move.line'].create({
                        'picking_id': delivery_picking.id,
                        'move_id': target_move.id,
                        'product_id': move_line.product_id.id,
                        'lot_id': move_line.lot_id.id,
                        'product_uom_id': move_line.product_uom_id.id,
                        'location_id': move_line.location_dest_id.id, # El stock físico actual
                        'location_dest_id': target_move.location_dest_id.id, # Cliente
                        'quantity': move_line.qty_done, # ESTO ES LA RESERVA
                        'qty_done': 0.0,
                    })

                _logger.info(f"[CONTROL_TOWER] Lote {move_line.lot_id.name} asignado a Entrega {delivery_picking.name}")

            except Exception as e:
                _logger.error(f"[CONTROL_TOWER] Error asignando lote {move_line.lot_id.name} a {delivery_picking.name}: {e}")

        # Opcional: Forzar recomputo de estado del picking de salida
        # Esto ayuda a que pase de "Esperando" a "Preparado" visualmente
        # delivery_pickings_found = ... (podrías recolectarlos en el loop y llamar check_availability)

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