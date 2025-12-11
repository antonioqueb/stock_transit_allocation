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
        pickings = super(StockPicking, self).create(vals_list)
        for pick in pickings:
            try:
                pick._ensure_sale_id_link()
            except Exception:
                continue
        return pickings

    def write(self, vals):
        res = super(StockPicking, self).write(vals)
        if 'origin' in vals or 'group_id' in vals:
            for pick in self:
                try:
                    pick._ensure_sale_id_link()
                except Exception:
                    continue
        return res

    def button_validate(self):
        # 1. Limpieza preventiva
        self._clean_unwanted_so_links()

        # 2. Validación estándar
        res = super(StockPicking, self).button_validate()
        
        # 3. Lógica de Tránsito
        for pick in self:
            is_transit = False
            dest_loc = pick.location_dest_id
            
            if dest_loc:
                if dest_loc.id == 128:
                    is_transit = True
                elif 'Trancit' in dest_loc.name or 'Transit' in dest_loc.name or 'Tránsito' in dest_loc.name:
                    is_transit = True
            
            if is_transit and pick.picking_type_code == 'incoming':
                pick._create_automatic_transit_voyage()
        return res

    def _clean_unwanted_so_links(self):
        for pick in self:
            if not pick.sale_id:
                continue
            valid_product_ids = pick.sale_id.order_line.mapped('product_id.id')
            for move in pick.move_ids:
                if move.product_id.id not in valid_product_ids:
                    move.write({'sale_line_id': False})

    def _ensure_sale_id_link(self):
        if getattr(self, 'sale_id', False):
            return

        found_sale_id = False
        group = getattr(self, 'group_id', False)
        if group and getattr(group, 'sale_id', False):
            found_sale_id = group.sale_id
        
        if not found_sale_id:
            purchase = getattr(self, 'purchase_id', False)
            if purchase:
                origin_ref = purchase.origin
                if origin_ref:
                    sale = self.env['sale.order'].search([('name', '=', origin_ref)], limit=1)
                    if sale:
                        found_sale_id = sale

        if not found_sale_id and getattr(self, 'move_ids', False):
            for move in self.move_ids:
                p_line = getattr(move, 'purchase_line_id', False)
                if p_line:
                    s_line = getattr(p_line, 'sale_line_id', False)
                    if s_line and s_line.order_id:
                        found_sale_id = s_line.order_id
                        break

        if found_sale_id:
            self.sudo().write({'sale_id': found_sale_id.id})

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        try:
            self._ensure_sale_id_link()
        except:
            pass
        
        Voyage = self.env['stock.transit.voyage']
        if self.transit_voyage_ids:
            return

        container_ref = self.transit_container_number or 'TBD'
        bl_ref = self.transit_bl_number
        purchase = getattr(self, 'purchase_id', False)
        
        if not bl_ref and purchase:
            bl_ref = purchase.partner_ref
        if not bl_ref:
            bl_ref = self.origin

        # CORRECCIÓN: Se agrega 'etd' con la fecha de hoy para que la barra arranque en 0-1%
        # en lugar de quedarse muerta por falta de fecha de inicio.
        voyage = Voyage.create({
            'picking_id': self.id,
            'container_number': container_ref,
            'bl_number': bl_ref, 
            'vessel_name': 'Por Definir',
            'etd': fields.Date.today(), 
            'eta': fields.Date.add(fields.Date.today(), days=21),
            'state': 'in_transit',
        })

        voyage.action_load_from_picking()
        
        try:
            self.message_post(body=f"🚢 Registro de Tránsito creado automáticamente: {voyage.name}")
        except:
            pass

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