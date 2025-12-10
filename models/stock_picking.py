# -*- coding: utf-8 -*-
from odoo import models, fields, _
from odoo.exceptions import UserError

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many('stock.transit.voyage', 'picking_id', string='Viajes de Tránsito')
    transit_count = fields.Integer(compute='_compute_transit_count')
    
    # Campos para capturar datos ANTES de validar la recepción
    transit_container_number = fields.Char(string='No. Contenedor (Tránsito)', 
        help="Indique el número de contenedor si esta mercancía va a una ubicación de tránsito.")
    transit_bl_number = fields.Char(string='BL Number (Tránsito)')

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    def button_validate(self):
        """
        Sobrescríbimos la validación para automatizar la Torre de Control.
        """
        # 1. Ejecutar validación estándar de Odoo
        res = super(StockPicking, self).button_validate()

        for pick in self:
            # 2. Detectar si va a la ubicación SOM/Tránsito (ID 128)
            # Usamos el ID 128 como pediste, pero agregamos chequeo de nombre por seguridad.
            is_transit_location = pick.location_dest_id.id == 128 or 'Tránsito' in pick.location_dest_id.name

            if is_transit_location and pick.picking_type_code == 'incoming':
                if not pick.transit_container_number:
                    # Opcional: Obligar a poner contenedor si va a tránsito
                    # raise UserError(_("Por favor ingrese el 'No. Contenedor' antes de validar una entrada a Tránsito."))
                    pass
                
                # Crear el Viaje Automáticamente
                pick._create_automatic_transit_voyage()

        return res

    def _create_automatic_transit_voyage(self):
        """Crea el registro en la Torre de Control y asigna mercancía"""
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']
        
        # Verificar si ya existe para no duplicar
        if self.transit_voyage_ids:
            return

        # Crear cabecera
        voyage = Voyage.create({
            'picking_id': self.id,
            'container_number': self.transit_container_number or 'PENDIENTE-' + self.name,
            'bl_number': self.transit_bl_number,
            'vessel_name': 'Por Definir', # Se puede actualizar después
            'eta': fields.Date.add(fields.Date.today(), days=21), # Default 21 días
            'state': 'in_transit', # Ya nace "En Tránsito"
        })

        # Cargar líneas y realizar asignación automática (SO -> PO)
        voyage.action_load_from_picking()
        
        # Log en el chatter
        self.message_post(body=f"🚢 <b>Viaje creado automáticamente:</b> {voyage.name} (Contenedor: {voyage.container_number})")

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