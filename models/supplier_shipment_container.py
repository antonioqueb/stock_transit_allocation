# -*- coding: utf-8 -*-
from odoo import models, fields


class SupplierShipmentContainer(models.Model):
    _name = 'supplier.shipment.container'
    _description = 'Contenedor de Embarque'
    _order = 'shipment_id, id'

    shipment_id = fields.Many2one(
        'supplier.shipment', string='Embarque',
        required=True, ondelete='cascade', index=True,
    )
    proforma_id = fields.Many2one(
        'supplier.proforma.header', string='Proforma',
        related='shipment_id.proforma_id', store=True,
    )

    container_number = fields.Char(string='Número de Contenedor', required=True)
    seal_number = fields.Char(string='Número de Sello')
    container_type = fields.Char(string='Tipo de Contenedor', help='Ej. 40HC, 20GP, 40OT')
    weight = fields.Float(string='Peso Bruto (kg)', digits=(12, 2))
    volume = fields.Float(string='Volumen (m³)', digits=(12, 3))
    packages = fields.Integer(string='Total Paquetes')
    notes = fields.Text(string='Observaciones')

    def name_get(self):
        return [(r.id, r.container_number or f"CNT-{r.id}") for r in self]