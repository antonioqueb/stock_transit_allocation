# -*- coding: utf-8 -*-
from odoo import models, fields, _

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many('stock.transit.voyage', 'picking_id', string='Viajes de Tránsito')
    transit_count = fields.Integer(compute='_compute_transit_count')
    
    transit_container_number = fields.Char(string='No. Contenedor (Ref)', 
        help="Referencia opcional. Si se deja vacío, el sistema intentará leerlo de los lotes.")
    transit_bl_number = fields.Char(string='BL Number (Tránsito)')

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        for pick in self:
            is_transit_location = pick.location_dest_id.id == 128 or 'Tránsito' in pick.location_dest_id.name
            if is_transit_location and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()
        return res

    def _ensure_sale_id_link(self):
        """
        Lógica ROBUSTA para recuperar la Orden de Venta.
        Si la PO fue editada manualmente, el 'origin' puede fallar.
        Aquí escaneamos los movimientos: Si UN movimiento viene de una SO, 
        esa es la SO del Picking.
        """
        if self.sale_id:
            return

        found_sale_id = False
        
        # Estrategia 1: Buscar en los movimientos del picking
        for move in self.move_ids:
            # Caso A: Vínculo directo sale_line_id (Sale Stock)
            if getattr(move, 'sale_line_id', False):
                found_sale_id = move.sale_line_id.order_id
                break
            
            # Caso B: Vínculo indirecto vía Compra (Purchase Line -> Sale Line)
            if move.purchase_line_id and getattr(move.purchase_line_id, 'sale_line_id', False):
                found_sale_id = move.purchase_line_id.sale_line_id.order_id
                break
        
        # Estrategia 2: Si falló lo anterior, intentar por el Grupo de Abastecimiento
        if not found_sale_id and self.group_id and getattr(self.group_id, 'sale_id', False):
             found_sale_id = self.group_id.sale_id

        # Si encontramos la venta, la forzamos en la cabecera
        if found_sale_id:
            self.write({'sale_id': found_sale_id.id})

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        
        # 1. Reparar vínculo SO (Ahora es capaz de detectar ventas en POs mixtas)
        self._ensure_sale_id_link()
        
        Voyage = self.env['stock.transit.voyage']
        if self.transit_voyage_ids:
            return

        container_ref = self.transit_container_number or self.origin or 'TBD'

        bl_ref = self.transit_bl_number
        if not bl_ref and self.purchase_id:
            bl_ref = self.purchase_id.partner_ref
        if not bl_ref:
            bl_ref = self.origin

        voyage = Voyage.create({
            'picking_id': self.id,
            'container_number': container_ref,
            'bl_number': bl_ref, 
            'vessel_name': 'Por Definir',
            'eta': fields.Date.add(fields.Date.today(), days=21),
            'state': 'in_transit',
        })

        voyage.action_load_from_picking()
        
        self.message_post(body=f"🚢 Registro de Tránsito creado: {voyage.name}")

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