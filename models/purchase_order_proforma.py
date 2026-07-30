# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PurchaseOrderProformaLink(models.Model):
    _inherit = 'purchase.order'

    proforma_header_id = fields.Many2one(
        'supplier.proforma.header',
        string='Proforma (Portal)',
        compute='_compute_proforma_header',
        store=False,
    )
    proforma_shipment_count = fields.Integer(
        string='Embarques Portal',
        compute='_compute_proforma_shipment_count',
    )

    def _compute_proforma_header(self):
        # sudo(): compute visible para CUALQUIER usuario que abra la OC;
        # sin sudo, quien no tiene grupo Tránsito no podía ni ver la orden.
        ProformaHeader = self.env['supplier.proforma.header'].sudo()
        for po in self:
            header = ProformaHeader.search([('purchase_id', '=', po.id)], limit=1)
            po.proforma_header_id = header.id if header else False

    def _compute_proforma_shipment_count(self):
        ProformaHeader = self.env['supplier.proforma.header'].sudo()
        for po in self:
            header = ProformaHeader.search([('purchase_id', '=', po.id)], limit=1)
            po.proforma_shipment_count = len(header.shipment_ids) if header else 0

    def action_open_proforma_header(self):
        """Abre o crea la proforma.header para esta OC."""
        self.ensure_one()
        ProformaHeader = self.env['supplier.proforma.header']
        header = ProformaHeader.search([('purchase_id', '=', self.id)], limit=1)
        if not header:
            header = ProformaHeader.create({'purchase_id': self.id})
        return {
            'type': 'ir.actions.act_window',
            'name': 'Proforma',
            'res_model': 'supplier.proforma.header',
            'res_id': header.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _get_or_create_proforma_header(self):
        """Helper usado por el portal para obtener/crear la proforma."""
        self.ensure_one()
        ProformaHeader = self.env['supplier.proforma.header']
        header = ProformaHeader.search([('purchase_id', '=', self.id)], limit=1)
        if not header:
            access = self.env['stock.picking.supplier.access'].search(
                [('purchase_id', '=', self.id)], limit=1
            )
            header = ProformaHeader.create({
                'purchase_id': self.id,
                'access_id': access.id if access else False,
            })
        return header