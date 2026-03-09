# -*- coding: utf-8 -*-
from odoo import models, fields, api


class SupplierShipmentInvoice(models.Model):
    _name = 'supplier.shipment.invoice'
    _description = 'Invoice de Embarque'
    _order = 'shipment_id, invoice_date, id'

    shipment_id = fields.Many2one(
        'supplier.shipment', string='Embarque',
        required=True, ondelete='cascade', index=True,
    )
    proforma_id = fields.Many2one(
        'supplier.proforma.header', string='Proforma',
        related='shipment_id.proforma_id', store=True,
    )
    purchase_id = fields.Many2one(
        'purchase.order', string='OC',
        related='shipment_id.purchase_id', store=True,
    )

    invoice_number = fields.Char(string='Número de Invoice', required=True)
    invoice_date = fields.Date(string='Fecha del Invoice')
    amount = fields.Float(string='Monto', digits='Product Price')
    currency_id = fields.Many2one(
        'res.currency', string='Moneda',
        default=lambda self: self.env.company.currency_id,
    )
    file = fields.Binary(string='Archivo Invoice', attachment=True)
    filename = fields.Char(string='Nombre archivo')

    scope = fields.Selection([
        ('full_shipment', 'Embarque Completo'),
        ('specific_containers', 'Contenedores Específicos'),
    ], string='Alcance', default='full_shipment')

    container_ids = fields.Many2many(
        'supplier.shipment.container',
        'supplier_invoice_container_rel',
        'invoice_id', 'container_id',
        string='Contenedores que Cubre',
    )

    def name_get(self):
        return [(r.id, r.invoice_number or f"INV-{r.id}") for r in self]