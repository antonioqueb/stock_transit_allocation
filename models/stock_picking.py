# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

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

    @api.depends('move_ids.sale_line_id')
    def _compute_sale_id(self):
        for picking in self:
            sale_orders = picking.move_ids.sale_line_id.order_id
            if not sale_orders:
                picking.sale_id = False
            else:
                picking.sale_id = sale_orders[0].id

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        for pick in self:
            is_transit = False
            dest_loc = pick.location_dest_id
            if dest_loc and (dest_loc.id == 128 or any(x in dest_loc.name for x in ['Transit', 'Tránsito', 'Trancit'])):
                is_transit = True
            
            if is_transit and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()
        return res

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']
        
        # BUSCAR SI YA EXISTE UN VIAJE CREADO DESDE LA OC
        voyage = Voyage.search([
            ('purchase_id', '=', self.purchase_id.id),
            ('state', '!=', 'cancel')
        ], limit=1)

        if voyage:
            # Si ya existe (etapa solicitud), lo vinculamos y actualizamos con datos reales
            voyage.write({
                'picking_id': self.id,
                'container_number': self.transit_container_number or voyage.container_number,
                'bl_number': self.transit_bl_number or voyage.bl_number,
                'state': 'in_transit',
                'custom_status': 'on_sea'
            })
            # Esta función ahora limpia las preventivas y pone los lotes reales
            voyage.action_load_from_picking()
        else:
            # Fallback: Si no existe, crear uno (Lógica original)
            voyage = Voyage.create({
                'picking_id': self.id,
                'purchase_id': self.purchase_id.id,
                'container_number': self.transit_container_number or 'TBD',
                'bl_number': self.transit_bl_number or self.origin,
                'etd': fields.Date.today(),
                'state': 'in_transit',
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