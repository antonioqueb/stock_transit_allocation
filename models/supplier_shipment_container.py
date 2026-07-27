# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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

    # ============================================================
    # SHIPSGO TRACKING FIELDS (POR CONTENEDOR)
    # ============================================================
    shipsgo_shipment_id = fields.Integer(
        string='ShipsGo Shipment ID',
        copy=False,
        readonly=True,
        help='Identificador devuelto por ShipsGo al crear o resolver el tracking del contenedor.',
    )
    shipsgo_reference = fields.Char(
        string='ShipsGo Reference',
        copy=False,
        readonly=True,
    )
    shipsgo_last_create = fields.Datetime(
        string='Última Alta ShipsGo',
        copy=False,
        readonly=True,
    )
    shipsgo_last_sync = fields.Datetime(
        string='Última Sync ShipsGo',
        copy=False,
        readonly=True,
    )
    shipsgo_last_error = fields.Text(
        string='Último Error ShipsGo',
        copy=False,
        readonly=True,
    )

    @api.depends('container_number')
    def _compute_display_name(self):
        # Odoo 19: reemplaza name_get (ya no lo invoca el ORM).
        for record in self:
            record.display_name = record.container_number or f"CNT-{record.id}"

    def _normalize_container_number(self, value):
        return (value or '').strip().upper()

    def action_create_shipsgo_tracking(self):
        """
        Crea o resuelve el tracking del contenedor en ShipsGo.
        El shipment_id queda guardado en ESTE contenedor.
        """
        for rec in self:
            container_ref = rec._normalize_container_number(rec.container_number)
            if not container_ref:
                continue

            # Asegurar que el embarque tenga viaje vinculado
            if not rec.shipment_id.voyage_id:
                rec.shipment_id.action_sync_to_voyage()

            if not rec.shipment_id.voyage_id:
                raise UserError(_("No se pudo vincular el embarque a un Viaje antes de crear el tracking."))

            rec.shipment_id.voyage_id._create_or_link_shipsgo_tracking_for_container(
                container_ref=container_ref,
                shipment_container=rec,
            )
        return True

    def _auto_create_shipsgo_tracking(self):
        """
        Hook silencioso: intenta crear tracking al guardar contenedor.
        Si falla, deja trazabilidad en el shipment pero no rompe la captura.
        """
        for rec in self:
            container_ref = rec._normalize_container_number(rec.container_number)
            if not container_ref:
                continue

            try:
                rec.action_create_shipsgo_tracking()
            except Exception as e:
                _logger.exception(
                    "[SHIPSGO] No se pudo crear tracking automático para contenedor %s",
                    container_ref
                )
                rec.write({'shipsgo_last_error': str(e)})
                if rec.shipment_id:
                    rec.shipment_id.message_post(
                        body=_("⚠️ No se pudo crear tracking ShipsGo para el contenedor %s: %s")
                        % (container_ref, str(e))
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Crear tracking al capturar un contenedor nuevo
        records._auto_create_shipsgo_tracking()
        return records

    def write(self, vals):
        track_after = 'container_number' in vals and not self.env.context.get('skip_auto_shipsgo')
        old_numbers = {}
        if track_after:
            old_numbers = {rec.id: rec._normalize_container_number(rec.container_number) for rec in self}

        res = super().write(vals)

        if track_after:
            changed = self.filtered(
                lambda rec: rec._normalize_container_number(rec.container_number)
                and old_numbers.get(rec.id) != rec._normalize_container_number(rec.container_number)
            )
            if changed:
                changed._auto_create_shipsgo_tracking()

        return res