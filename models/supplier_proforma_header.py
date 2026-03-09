# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SupplierProformaHeader(models.Model):
    _name = 'supplier.proforma.header'
    _description = 'Cabecera de Proforma (Datos Globales del Proveedor)'
    _order = 'create_date desc'
    _inherit = ['mail.thread']

    purchase_id = fields.Many2one(
        'purchase.order', string='Orden de Compra',
        required=True, ondelete='cascade', index=True,
    )
    access_id = fields.Many2one(
        'stock.picking.supplier.access', string='Acceso Portal',
        ondelete='set null', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        related='purchase_id.company_id', store=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Proveedor',
        related='purchase_id.partner_id', store=True,
    )

    # --- Datos globales de la proforma ---
    proforma_number = fields.Char(string='Número de Proforma', tracking=True)
    invoice_global_number = fields.Char(string='Factura Global', tracking=True)
    payment_terms = fields.Char(string='Condiciones de Pago')
    country_origin = fields.Char(string='País Origen')
    port_origin = fields.Char(string='Puerto Origen')
    port_destination = fields.Char(string='Puerto Destino')
    incoterm = fields.Char(string='Incoterm')
    general_notes = fields.Text(string='Observaciones Generales')

    # --- Embarques ---
    shipment_ids = fields.One2many(
        'supplier.shipment', 'proforma_id', string='Embarques',
    )
    shipment_count = fields.Integer(
        string='Nº Embarques', compute='_compute_shipment_count', store=True,
    )

    # --- Estado ---
    status = fields.Selection([
        ('draft', 'Borrador'),
        ('partial', 'Parcialmente Capturado'),
        ('complete', 'Completo'),
    ], string='Estado', default='draft', tracking=True)

    _sql_constraints = [
        ('purchase_unique', 'unique(purchase_id)',
         'Ya existe una proforma para esta Orden de Compra.'),
    ]

    @api.depends('shipment_ids')
    def _compute_shipment_count(self):
        for rec in self:
            rec.shipment_count = len(rec.shipment_ids)

    def name_get(self):
        return [(r.id, f"PRF-{r.purchase_id.name}" if r.purchase_id else f"PRF-{r.id}") for r in self]

    def action_view_shipments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Embarques'),
            'res_model': 'supplier.shipment',
            'view_mode': 'list,form',
            'domain': [('proforma_id', '=', self.id)],
            'context': {'default_proforma_id': self.id},
        }