# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class SupplierShipmentInvoice(models.Model):
    _name = 'supplier.shipment.invoice'
    _description = 'Invoice de Embarque'
    _order = 'shipment_id, invoice_date, id'

    @api.constrains('shipment_id', 'invoice_number')
    def _check_unique_invoice_number_per_shipment(self):
        """Un mismo número de invoice no puede capturarse dos veces en el
        mismo embarque: el cargo quedaba registrado (y pagable) doble.

        La CAPTURA jamás choca con esto: create() hace UPSERT por folio
        (si el folio ya existe en el embarque, actualiza ese registro).
        La restricción queda como red de seguridad para el único caso que
        sí es un error humano real: RENOMBRAR un invoice existente al folio
        de otro invoice del mismo embarque."""
        for rec in self:
            number = (rec.invoice_number or '').strip().lower()
            if not rec.shipment_id or not number:
                continue
            duplicated = rec.shipment_id.invoice_ids.filtered(
                lambda i: i.id != rec.id
                and (i.invoice_number or '').strip().lower() == number
            )
            if duplicated:
                raise ValidationError(
                    'El invoice "%s" ya está capturado en el embarque %s. '
                    'Un cargo no puede registrarse dos veces.' % (
                        rec.invoice_number,
                        rec.shipment_id.display_name,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        # Moneda por defecto: la de la OC del embarque (proveedor extranjero,
        # normalmente USD). El default de compañía (MXN) etiquetaba mal los
        # montos capturados desde backoffice.
        for vals in vals_list:
            if not vals.get('currency_id') and vals.get('shipment_id'):
                shipment = self.env['supplier.shipment'].browse(vals['shipment_id'])
                if shipment.exists() and shipment.purchase_id.currency_id:
                    vals['currency_id'] = shipment.purchase_id.currency_id.id

        # UPSERT POR FOLIO: la factura global de la proforma se auto-crea
        # como invoice del embarque; capturar después el mismo folio (portal
        # o backend) creaba un duplicado y la restricción tumbaba TODO el
        # guardado ("El invoice ya está capturado en el embarque..."). Si el
        # folio ya existe en el embarque, se ACTUALIZA ese registro con lo
        # capturado — sin error y sin cargo doble.
        records = self.browse()
        remaining = []
        for vals in vals_list:
            number = (vals.get('invoice_number') or '').strip().lower()
            shipment_id = vals.get('shipment_id')
            existing = self.browse()
            if number and shipment_id:
                existing = self.search([
                    ('shipment_id', '=', shipment_id),
                ]).filtered(
                    lambda i: (i.invoice_number or '').strip().lower() == number
                )[:1]
            if existing:
                update_vals = {
                    k: v for k, v in vals.items()
                    if k != 'shipment_id' and v not in (None, False, '')
                }
                # El marcador de factura global no se degrada por una
                # recaptura manual del mismo folio.
                update_vals.pop('is_global', None)
                if update_vals:
                    existing.write(update_vals)
                records |= existing
            else:
                remaining.append(vals)

        if remaining:
            records |= super().create(remaining)
        return records

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
    # Marcador de la FACTURA GLOBAL sincronizada desde la proforma: el folio
    # y el monto se actualizan solos cuando cambia invoice_global_number.
    # (Campo también declarado en la clase duplicada de stock_lot_packing_import
    # — patrón _name duplicado: debe existir en AMBAS.)
    is_global = fields.Boolean(string='Factura global', copy=False)
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

    @api.depends('invoice_number')
    def _compute_display_name(self):
        # Odoo 19: reemplaza name_get (ya no lo invoca el ORM).
        for record in self:
            record.display_name = record.invoice_number or f"INV-{record.id}"
