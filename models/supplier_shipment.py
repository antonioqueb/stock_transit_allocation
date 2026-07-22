# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class SupplierShipment(models.Model):
    _name = 'supplier.shipment'
    _description = 'Embarque del Proveedor'
    _order = 'proforma_id, sequence, id'
    _inherit = ['mail.thread']

    proforma_id = fields.Many2one(
        'supplier.proforma.header', string='Proforma',
        required=True, ondelete='cascade', index=True,
    )
    purchase_id = fields.Many2one(
        'purchase.order', string='Orden de Compra',
        related='proforma_id.purchase_id', store=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        related='proforma_id.company_id', store=True,
    )

    sequence = fields.Integer(string='Secuencia', default=10)
    name = fields.Char(
        string='Referencia', required=True, copy=False,
        default=lambda self: _('Nuevo'),
    )

    # --- Datos logísticos ---
    shipment_type = fields.Selection([
        ('maritime', 'Marítimo'),
        ('air', 'Aéreo'),
        ('land', 'Terrestre'),
    ], string='Tipo de Embarque', default='maritime')

    shipping_line = fields.Char(string='Naviera', tracking=True)
    # DUPLICADO INTENCIONAL (la clase con _name reemplaza a la del módulo
    # base — ver supplier_shipment_packing.py): catálogo del tarifario.
    naviera_id = fields.Many2one('res.partner', string='Naviera (catálogo)')
    forwarder_id = fields.Many2one('res.partner', string='Forwarder (catálogo)')
    pol_id = fields.Many2one('res.partner', string='POL (catálogo)')
    pod_id = fields.Many2one('res.partner', string='POD (catálogo)')
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    etd = fields.Date(string='ETD (Salida Estimada)')
    eta = fields.Date(string='ETA (Llegada Estimada)', tracking=True)
    port_origin = fields.Char(string='Puerto Salida')
    port_destination = fields.Char(string='Puerto Destino')
    notes = fields.Text(string='Observaciones Logísticas')

    # --- BL (pertenece al embarque, NO global) ---
    bl_number = fields.Char(string='Número de B/L', tracking=True)
    bl_date = fields.Date(string='Fecha de B/L', tracking=True)
    bl_file = fields.Binary(string='Archivo B/L', attachment=True)
    bl_filename = fields.Char(string='Nombre archivo B/L')

    # --- Conteo de contenedores ---
    container_count = fields.Integer(
        string='Nº Contenedores', compute='_compute_container_count', store=True,
    )

    # --- Estado ---
    status = fields.Selection([
        ('draft', 'Borrador'),
        ('in_production', 'En Producción'),
        ('booked', 'Booking Confirmado'),
        ('departed', 'Despachado'),
        ('in_transit', 'En Tránsito'),
        ('arrived', 'Arribado'),
        ('delivered', 'Entregado'),
    ], string='Estado', default='draft', tracking=True)

    # --- Relaciones hijas ---
    invoice_ids = fields.One2many(
        'supplier.shipment.invoice', 'shipment_id', string='Invoices',
    )
    packing_ids = fields.One2many(
        'supplier.shipment.packing', 'shipment_id', string='Packing Lists',
    )
    container_ids = fields.One2many(
        'supplier.shipment.container', 'shipment_id', string='Contenedores',
    )

    # --- Vínculo con Torre de Control ---
    voyage_id = fields.Many2one(
        'stock.transit.voyage', string='Viaje Torre de Control',
        ondelete='set null', index=True, tracking=True,
    )

    # --- Cómputos ---
    invoice_count = fields.Integer(compute='_compute_counts', store=True)
    packing_count = fields.Integer(compute='_compute_counts', store=True)

    block_image_ids = fields.One2many(
        'supplier.shipment.block.image', 'shipment_id', string='Fotos de Bloques',
    )

    @api.depends('container_ids')
    def _compute_container_count(self):
        for rec in self:
            rec.container_count = len(rec.container_ids)

    @api.depends('invoice_ids', 'packing_ids')
    def _compute_counts(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)
            rec.packing_count = len(rec.packing_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                proforma = self.env['supplier.proforma.header'].browse(vals.get('proforma_id'))
                existing = self.search_count([('proforma_id', '=', vals.get('proforma_id'))])
                po_name = proforma.purchase_id.name if proforma.purchase_id else 'X'
                vals['name'] = f"EMB-{po_name}-{existing + 1:03d}"
        
        records = super(SupplierShipment, self).create(vals_list)
        
        # SINCRONIZACIÓN AUTOMÁTICA AL CREAR
        if not self.env.context.get('skip_date_sync'):
            for record in records:
                sync_fields = {'bl_number', 'bl_date', 'eta', 'etd'}
                if any(field in self.env.context.get('default_vals', {}) or field in record for field in sync_fields):
                    record._sync_dates_to_others({
                        'bl_number': record.bl_number, 
                        'bl_date': record.bl_date, 
                        'eta': record.eta, 
                        'etd': record.etd
                    })

        return records

    def write(self, vals):
        res = super().write(vals)

        # ---------------------------------------------------------
        # SINCRONIZACIÓN BIDIRECCIONAL A OC Y TORRE DE CONTROL
        # ---------------------------------------------------------
        if not self.env.context.get('skip_date_sync'):
            sync_fields = {'bl_number', 'bl_date', 'eta', 'etd'}
            if sync_fields.intersection(vals.keys()):
                for shipment in self:
                    shipment._sync_dates_to_others(vals)

        return res

    def _sync_dates_to_others(self, vals):
        """Helper para sincronizar fechas logísticas con Orden de Compra y Viaje en Tránsito"""
        for shipment in self:
            # Sincronizar hacia Orden de Compra
            if shipment.purchase_id:
                po_vals = {}
                if 'bl_number' in vals: po_vals['bl_number'] = vals['bl_number']
                if 'bl_date' in vals: po_vals['bl_date'] = vals['bl_date']
                if 'eta' in vals: po_vals['eta_date'] = vals['eta']
                if po_vals:
                    shipment.purchase_id.with_context(skip_date_sync=True).write(po_vals)

            # Sincronizar hacia Torre de Control (Viaje)
            if shipment.voyage_id:
                v_vals = {}
                if 'bl_number' in vals: v_vals['bl_number'] = vals['bl_number']
                if 'eta' in vals: v_vals['eta'] = vals['eta']
                if 'etd' in vals: v_vals['etd'] = vals['etd']
                if v_vals:
                    shipment.voyage_id.with_context(skip_date_sync=True).write(v_vals)


    def name_get(self):
        return [(r.id, r.name or f"EMB-{r.id}") for r in self]

    # --- Sincronización con Torre de Control ---
    def action_sync_to_voyage(self):
        """Crea o actualiza el voyage vinculado en la Torre de Control y da de alta tracking por contenedor."""
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']

        vals = {}
        if self.bl_number:
            vals['bl_number'] = self.bl_number
        if self.shipping_line:
            vals['shipping_line'] = self.shipping_line
        if self.vessel_name:
            vals['vessel_name'] = self.vessel_name
        if self.etd:
            vals['etd'] = self.etd
        if self.eta:
            vals['eta'] = self.eta

        if self.voyage_id:
            # Importante: al llamar a write pasamos el contexto skip_date_sync 
            # para no generar bucles infinitos, ya que esta acción manual ya es un sync en sí mismo.
            self.voyage_id.with_context(skip_date_sync=True).write(vals)
            voyage = self.voyage_id
            _logger.info(f"[SHIPMENT] Voyage {voyage.name} actualizado desde embarque {self.name}")
        else:
            vals.update({
                'purchase_id': self.purchase_id.id,
                'custom_status': 'solicitud',
            })
            voyage = Voyage.with_context(skip_date_sync=True).create(vals)
            self.write({'voyage_id': voyage.id})
            _logger.info(f"[SHIPMENT] Voyage {voyage.name} creado desde embarque {self.name}")

        # ------------------------------------------------------------
        # NUEVO: crear / resolver tracking ShipsGo para cada contenedor
        # ------------------------------------------------------------
        for container in self.container_ids.filtered(lambda c: c.container_number):
            try:
                container.action_create_shipsgo_tracking()
            except Exception as e:
                _logger.warning(
                    "[SHIPMENT] No se pudo crear tracking ShipsGo para %s en %s: %s",
                    container.container_number, self.name, e
                )
                self.message_post(
                    body=_("⚠️ No se pudo crear tracking ShipsGo para el contenedor %s: %s")
                    % (container.container_number, str(e))
                )

        return True