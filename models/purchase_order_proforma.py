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

    tc_voyage_count = fields.Integer(
        string='Embarques Torre de Control',
        compute='_compute_tc_voyage_count',
    )

    def _som_cargo_sibling_pos(self):
        """La OC + sus hermanas de FACTURA DE CARGA (una carga = varias OCs
        en un solo embarque). El viaje solo apunta a la OC principal de la
        carga: sin esto, las demás OCs no ven su embarque."""
        self.ensure_one()
        pos = self
        header = self.env['supplier.proforma.header'].sudo().search(
            [('purchase_id', '=', self.id)], limit=1)
        access = header.access_id if header else False
        cargo = getattr(access, 'cargo_invoice_id', False) if access else False
        if cargo and getattr(cargo, 'purchase_ids', False):
            pos |= cargo.purchase_ids
        return pos

    def _compute_tc_voyage_count(self):
        # sudo(): el grupo Tránsito es solo UI — el conteo debe calcularse
        # para cualquiera que abra la OC.
        Voyage = self.env['stock.transit.voyage'].sudo()
        for po in self:
            po.tc_voyage_count = Voyage.search_count([
                ('purchase_id', 'in', po._som_cargo_sibling_pos().ids),
                ('custom_status', '!=', 'cancel'),
            ])

    def action_open_tc_voyage(self):
        """Acceso DIRECTO al embarque de Torre de Control de esta OC.
        Con uno solo abre su formulario; con varios, la lista filtrada.
        El botón solo es visible cuando existe al menos un embarque —
        pedido explícito: nada de formularios intermedios de proforma.
        Con factura de carga, el embarque de la OC principal ampara a
        todas sus hermanas."""
        self.ensure_one()
        voyages = self.env['stock.transit.voyage'].sudo().search([
            ('purchase_id', 'in', self._som_cargo_sibling_pos().ids),
            ('custom_status', '!=', 'cancel'),
        ])
        if len(voyages) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Embarque',
                'res_model': 'stock.transit.voyage',
                'res_id': voyages.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': 'Embarques',
            'res_model': 'stock.transit.voyage',
            'view_mode': 'list,form',
            'domain': [('id', 'in', voyages.ids)],
            'target': 'current',
        }

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