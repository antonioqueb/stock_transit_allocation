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
        Sobreescritura para gatillar lógica de Torre de Control:
        1. Incoming: Crea el Viaje automáticamente.
        2. Internal (Recepción Física): Reserva automáticamente la salida al cliente.
        """
        res = super(StockPicking, self).button_validate()
        
        for pick in self:
            # 1. LÓGICA DE ENTRADA (Crear Viaje)
            is_transit = False
            dest_loc = pick.location_dest_id
            if dest_loc and (dest_loc.id == 128 or any(x in dest_loc.name for x in ['Transit', 'Tránsito', 'Trancit'])):
                is_transit = True
            
            if is_transit and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()

            # 2. LÓGICA DE RECEPCIÓN FÍSICA (Auto-Reservar Salida al Cliente)
            # Si validamos una interna y esta es la "Recepción Física" de un viaje activo
            if pick.picking_type_code == 'internal' and pick.state == 'done':
                pick._auto_reserve_deliveries_from_transit()

        return res

    def _auto_reserve_deliveries_from_transit(self):
        """
        Busca si este picking es la Recepción Física de un Viaje.
        Si lo es, busca los pedidos de venta asignados en el viaje y fuerza 
        la reserva de los lotes recibidos en sus respectivas Entregas (Delivery Orders).
        """
        self.ensure_one()
        # Buscar si este picking es la recepción final de algún viaje
        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id)
        ], limit=1)

        if not voyage:
            return

        _logger.info(f"[TRANSIT_AUTO_RESERVE] Iniciando asignación automática para Picking {self.name} desde Viaje {voyage.name}")

        # Mapa de Lote -> Orden de Venta (Según la Torre de Control)
        # Esto es la "Fuente de la Verdad"
        lot_assignment_map = {}
        for line in voyage.line_ids:
            if line.lot_id and line.order_id and line.allocation_status == 'reserved':
                lot_assignment_map[line.lot_id.id] = {
                    'so': line.order_id,
                    'partner': line.partner_id
                }

        # Iteramos sobre lo que ACABAMOS de recibir en este picking
        # Usamos move_line_ids porque ahí están los lotes específicos
        for move_line in self.move_line_ids:
            if not move_line.lot_id or not move_line.qty_done:
                continue
            
            # Verificamos si este lote tiene dueño según la Torre de Control
            assignment = lot_assignment_map.get(move_line.lot_id.id)
            if not assignment:
                continue

            sale_order = assignment['so']
            
            # Buscamos la Entrega (Delivery Order) pendiente de este pedido
            # Debe ser 'outgoing', estar confirmada (esperando reserva) y salir de la ubicación donde recibimos
            # o de una ubicación padre/hija (normalmente stock).
            delivery_picking = self.env['stock.picking'].search([
                ('origin', '=', sale_order.name),
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ['confirmed', 'assigned', 'partially_available']),
                ('company_id', '=', self.company_id.id)
            ], limit=1)

            if not delivery_picking:
                _logger.warning(f"[TRANSIT_AUTO_RESERVE] No se encontró entrega pendiente para SO {sale_order.name} (Lote {move_line.lot_id.name})")
                continue

            # Buscar el movimiento de stock (stock.move) dentro de la entrega que corresponda al producto
            target_move = delivery_picking.move_ids.filtered(
                lambda m: m.product_id == move_line.product_id and m.state not in ['done', 'cancel']
            )

            if not target_move:
                continue
            
            # En caso de múltiples líneas para el mismo producto, tomamos la primera disponible
            target_move = target_move[0]

            # --- FORZAR LA RESERVA ---
            try:
                # Verificamos si ya está reservado para evitar duplicados
                already_reserved = target_move.move_line_ids.filtered(lambda ml: ml.lot_id.id == move_line.lot_id.id)
                
                if not already_reserved:
                    # Creamos la línea de movimiento (reserva) explícita
                    # Al crearla con lot_id y quantity, Odoo la considera "Reservada"
                    self.env['stock.move.line'].create({
                        'move_id': target_move.id,
                        'picking_id': delivery_picking.id,
                        'product_id': move_line.product_id.id,
                        'lot_id': move_line.lot_id.id,
                        'product_uom_id': move_line.product_id.uom_id.id,
                        'location_id': move_line.location_dest_id.id, # Ubicación donde quedó el stock (Stock principal)
                        'location_dest_id': delivery_picking.location_dest_id.id,
                        'quantity': move_line.qty_done, # Reservamos lo que se recibió
                        'qty_done': 0, 
                    })
                    
                    _logger.info(f"[TRANSIT_AUTO_RESERVE] Lote {move_line.lot_id.name} reservado exitosamente en {delivery_picking.name}")
                
            except Exception as e:
                _logger.error(f"[TRANSIT_AUTO_RESERVE] Error al reservar lote {move_line.lot_id.name} en {delivery_picking.name}: {str(e)}")

        # Opcional: Recalcular el estado del picking de salida para que refleje "Reservado"
        # delivery_pickings_to_update = ... (agrupar y ejecutar action_assign si es necesario, 
        # aunque crear los move_line manualmente suele ser suficiente).

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']
        
        # CORRECCIÓN: Búsqueda por custom_status
        voyage = Voyage.search([
            ('purchase_id', '=', self.purchase_id.id),
            ('custom_status', '!=', 'cancel')
        ], limit=1)

        if voyage:
            # CORRECCIÓN: Se eliminó 'state': 'in_transit'
            voyage.write({
                'picking_id': self.id,
                'container_number': self.transit_container_number or voyage.container_number,
                'bl_number': self.transit_bl_number or voyage.bl_number,
                'custom_status': 'on_sea'
            })
            voyage.action_load_from_picking()
        else:
            # CORRECCIÓN: Se eliminó 'state': 'in_transit'
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