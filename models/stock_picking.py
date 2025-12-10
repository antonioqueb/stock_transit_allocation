# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many('stock.transit.voyage', 'picking_id', string='Viajes de Tránsito')
    transit_count = fields.Integer(compute='_compute_transit_count')
    
    transit_container_number = fields.Char(string='No. Contenedor (Ref)', 
        help="Referencia opcional manual.")
    transit_bl_number = fields.Char(string='BL Number (Tránsito)')

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    @api.model_create_multi
    def create(self, vals_list):
        # Intentar propagar sale_id desde la creación
        pickings = super(StockPicking, self).create(vals_list)
        for pick in pickings:
            pick._ensure_sale_id_link()
        return pickings

    def write(self, vals):
        res = super(StockPicking, self).write(vals)
        # Si cambia el origen o movimientos, re-verificar link
        if 'origin' in vals or 'group_id' in vals:
            for pick in self:
                pick._ensure_sale_id_link()
        return res

    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        for pick in self:
            # Corrección de ubicación: SOM/Transit
            # Se busca por ID fijo (128) o por coincidencia de nombre exacto solicitado
            is_transit = False
            if pick.location_dest_id.id == 128:
                is_transit = True
            elif 'Trancit' in pick.location_dest_id.name or 'Tránsito' in pick.location_dest_id.name:
                is_transit = True
            
            if is_transit and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()
        return res

    def _ensure_sale_id_link(self):
        """
        Propaga automáticamente la Orden de Venta al Picking si viene de una Compra relacionada.
        Esto evita que el usuario tenga que ponerlo manualmente.
        """
        if self.sale_id:
            return

        found_sale_id = False
        
        # 1. Buscar vínculo directo en el Grupo de Abastecimiento (Lo más común en MTO)
        if self.group_id and getattr(self.group_id, 'sale_id', False):
            found_sale_id = self.group_id.sale_id
        
        # 2. Si falla, buscar a través de la Orden de Compra origen
        if not found_sale_id and self.purchase_id:
            # A veces el origen de la compra es la venta (Ej: SO001)
            origin_ref = self.purchase_id.origin
            if origin_ref:
                sale = self.env['sale.order'].search([('name', '=', origin_ref)], limit=1)
                if sale:
                    found_sale_id = sale

        # 3. Barrido profundo en líneas (para casos mixtos)
        if not found_sale_id:
            for move in self.move_ids:
                if move.purchase_line_id and getattr(move.purchase_line_id, 'sale_line_id', False):
                    found_sale_id = move.purchase_line_id.sale_line_id.order_id
                    break

        if found_sale_id:
            self.write({'sale_id': found_sale_id.id})

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        self._ensure_sale_id_link()
        
        Voyage = self.env['stock.transit.voyage']
        if self.transit_voyage_ids:
            return

        container_ref = self.transit_container_number or 'TBD'
        
        # Usamos el partner_ref de la compra como BL si no hay otro
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
        
        self.message_post(body=f"🚢 Registro de Tránsito creado automáticamente: {voyage.name}")

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