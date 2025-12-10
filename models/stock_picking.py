# -*- coding: utf-8 -*-
from odoo import models, fields, _

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many('stock.transit.voyage', 'picking_id', string='Viajes de Tránsito')
    transit_count = fields.Integer(compute='_compute_transit_count')
    
    # Estos campos quedan como informativos
    transit_container_number = fields.Char(string='No. Contenedor (Ref)', 
        help="Referencia opcional. Si se deja vacío, el sistema intentará leerlo de los lotes.")
    transit_bl_number = fields.Char(string='BL Number (Tránsito)')

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    def button_validate(self):
        # 1. Validación estándar
        res = super(StockPicking, self).button_validate()

        for pick in self:
            # 2. Detectar ubicación de tránsito (ID 128 o nombre)
            is_transit_location = pick.location_dest_id.id == 128 or 'Tránsito' in pick.location_dest_id.name

            if is_transit_location and pick.picking_type_code == 'incoming':
                # Crear el Viaje Automáticamente SIN preguntar nada
                pick._create_automatic_transit_voyage()

        return res

    def _ensure_sale_id_link(self):
        """
        Lógica para FORZAR la vinculación de la Orden de Venta en la Recepción.
        Si Odoo nativo no llenó el campo 'sale_id', lo buscamos a través de la Compra.
        """
        if not self.sale_id and self.purchase_id and self.purchase_id.origin:
            # Buscamos la SO cuyo nombre coincida con el origen de la PO (Ej. S00016)
            # Usamos 'ilike' por si el origen tiene texto extra, aunque '=' es más seguro para 1 a 1.
            sale_order = self.env['sale.order'].search([
                ('name', '=', self.purchase_id.origin)
            ], limit=1)
            
            if sale_order:
                # Escribimos explícitamente el ID de la venta en el picking
                self.write({'sale_id': sale_order.id})

    def _create_automatic_transit_voyage(self):
        self.ensure_one()
        
        # --- PASO CRÍTICO: Reparar el vínculo con la Venta si falta ---
        self._ensure_sale_id_link()
        
        Voyage = self.env['stock.transit.voyage']
        
        if self.transit_voyage_ids:
            return

        # DEFINICIÓN DEL NOMBRE DEL CONTENEDOR
        container_ref = self.transit_container_number or self.origin or 'TBD'

        # DEFINICIÓN DEL BL / REFERENCIA
        bl_ref = self.transit_bl_number
        if not bl_ref and self.purchase_id:
            bl_ref = self.purchase_id.partner_ref
        if not bl_ref:
            bl_ref = self.origin

        # Creamos la cabecera del viaje
        voyage = Voyage.create({
            'picking_id': self.id,
            'container_number': container_ref,
            'bl_number': bl_ref, 
            'vessel_name': 'Por Definir',
            'eta': fields.Date.add(fields.Date.today(), days=21),
            'state': 'in_transit',
        })

        # Cargar líneas y ejecutar lógica de asignación
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