# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


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
    # Multiempresa. DUPLICADO INTENCIONAL con stock_lot_packing_import
    # (patrón _name duplicado: la definición debe ser IDÉNTICA en ambas).
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        related='purchase_id.company_id', store=True, readonly=True, index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Proveedor',
        related='purchase_id.partner_id', store=True,
    )

    proforma_number = fields.Char(string='Número de Proforma', tracking=True)
    # OJO (patrón Odoo 19 documentado): esta clase RE-DECLARA el modelo con
    # _name y REEMPLAZA los campos de stock_lot_packing_import — todo campo
    # nuevo del portal debe existir en AMBAS clases o desaparece del registry
    # (así se perdió portal_overall_pct: "Invalid field ... in write").
    portal_overall_pct = fields.Integer(
        string='Avance del portal (%)', copy=False,
        help='Porcentaje de avance reportado por el PROPIO portal del '
             'proveedor (el mismo número que él ve). Fuente de verdad del '
             'avance de captura.',
    )
    invoice_global_number = fields.Char(string='Factura Global', tracking=True)
    payment_terms = fields.Char(string='Condiciones de Pago')
    country_origin = fields.Char(string='País Origen')
    port_origin = fields.Char(string='Puerto Origen')
    port_destination = fields.Char(string='Puerto Destino')
    incoterm = fields.Char(string='Incoterm')
    general_notes = fields.Text(string='Observaciones Generales')

    shipment_ids = fields.One2many(
        'supplier.shipment', 'proforma_id', string='Embarques',
    )
    shipment_count = fields.Integer(
        string='Nº Embarques', compute='_compute_shipment_count', store=True,
    )

    status = fields.Selection([
        ('draft', 'Borrador'),
        ('partial', 'Parcialmente Capturado'),
        ('complete', 'Completo'),
    ], string='Estado', default='draft', tracking=True)

    @api.constrains('purchase_id')
    def _check_unique_purchase_id(self):
        for rec in self:
            if not rec.purchase_id:
                continue
            dup = self.search([
                ('purchase_id', '=', rec.purchase_id.id),
                ('id', '!=', rec.id),
            ], limit=1)
            if dup:
                raise ValidationError(_('Ya existe una proforma para esta Orden de Compra.'))

    @api.depends('shipment_ids')
    def _compute_shipment_count(self):
        for rec in self:
            rec.shipment_count = len(rec.shipment_ids)

    # ------------------------------------------------------------------
    # FACTURA GLOBAL → invoice del embarque (sin recapturar)
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        pending = records.filtered('invoice_global_number')
        if pending:
            pending._som_sync_global_invoice()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'invoice_global_number' in vals \
                and not self.env.context.get('skip_global_invoice_sync'):
            self._som_sync_global_invoice()
        return res

    def _som_sync_global_invoice(self):
        """La factura global se captura UNA vez (aquí o en el portal) y se
        refleja sola como invoice del embarque: mismo folio y el TOTAL de la
        OC como monto, en la divisa de la OC. Si la OC aún no tiene embarque,
        se crea uno por default. El invoice sincronizado queda marcado con
        is_global y se actualiza en lugar de duplicarse."""
        Invoice = self.env['supplier.shipment.invoice'].sudo()
        Shipment = self.env['supplier.shipment'].sudo()
        for header in self:
            number = (header.invoice_global_number or '').strip()
            if not number:
                continue

            shipment = header.shipment_ids[:1]
            if not shipment:
                shipment = Shipment.with_context(
                    skip_date_sync=True,
                ).create({'proforma_id': header.id})

            po = header.purchase_id
            vals = {
                'invoice_number': number,
                'amount': po.amount_total if po else 0.0,
                'currency_id': po.currency_id.id if po else False,
                'is_global': True,
            }

            existing_global = Invoice.search([
                ('shipment_id', 'in', header.shipment_ids.ids),
                ('is_global', '=', True),
            ], limit=1)
            if not existing_global:
                # Adoptar un invoice ya capturado con ese mismo folio (evita
                # chocar con la restricción de folio único por embarque).
                existing_global = Invoice.search([
                    ('shipment_id', 'in', header.shipment_ids.ids),
                    ('invoice_number', '=ilike', number),
                ], limit=1)

            if existing_global:
                existing_global.write(vals)
            else:
                Invoice.create({
                    **vals,
                    'shipment_id': shipment.id,
                    'invoice_date': fields.Date.context_today(header),
                    'scope': 'full_shipment',
                })

    @api.depends('purchase_id.name')
    def _compute_display_name(self):
        # Odoo 19: reemplaza name_get (ya no lo invoca el ORM).
        for record in self:
            record.display_name = (
                f"PRF-{record.purchase_id.name}" if record.purchase_id
                else f"PRF-{record.id}"
            )

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