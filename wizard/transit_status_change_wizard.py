# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class TransitStatusChangeWizard(models.TransientModel):
    _name = 'transit.status.change.wizard'
    _description = 'Cambio de Estado con Nota - Torre de Control'

    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True)
    direction = fields.Selection([
        ('advance', 'Avanzar'),
        ('retreat', 'Retroceder'),
    ], string='Dirección', required=True)

    current_status = fields.Selection(
        related='voyage_id.custom_status',
        string='Estado Actual',
        readonly=True,
    )

    next_status_label = fields.Char(
        string='Nuevo Estado',
        compute='_compute_next_status_label',
    )

    notes = fields.Text(
        string='Comentario / Motivo',
        placeholder='Ej. Cambio de barco, demora en puerto, problema aduanal...',
    )

    @api.depends('voyage_id', 'direction')
    def _compute_next_status_label(self):
        STATUS_LABELS = {
            'solicitud':         'Solicitud Enviada',
            'production':        'Producción',
            'booking':           'Booking',
            'puerto_origen':     'Puerto Origen',
            'on_sea':            'En Altamar',
            'puerto_destino':    'Puerto Destino',
            'arrived_port':      'Arribo a Puerto',
            'reception_pending': 'En Recepción',
            'delivered':         'Entregado en Almacén',
        }
        STATUS_SEQUENCE = [
            'solicitud', 'production', 'booking', 'puerto_origen',
            'on_sea', 'puerto_destino', 'arrived_port', 'reception_pending', 'delivered',
        ]
        for rec in self:
            if not rec.voyage_id or not rec.direction:
                rec.next_status_label = ''
                continue
            current = rec.voyage_id.custom_status
            try:
                idx = STATUS_SEQUENCE.index(current)
            except ValueError:
                rec.next_status_label = ''
                continue
            if rec.direction == 'advance':
                next_idx = idx + 1
                if next_idx >= len(STATUS_SEQUENCE):
                    rec.next_status_label = ''
                else:
                    rec.next_status_label = STATUS_LABELS.get(STATUS_SEQUENCE[next_idx], '')
            else:
                prev_idx = idx - 1
                if prev_idx < 0:
                    rec.next_status_label = ''
                else:
                    rec.next_status_label = STATUS_LABELS.get(STATUS_SEQUENCE[prev_idx], '')

    def action_confirm(self):
        self.ensure_one()
        voyage = self.voyage_id
        if self.direction == 'advance':
            voyage._do_advance_status(notes=self.notes)
        else:
            voyage._do_retreat_status(notes=self.notes)
        return {'type': 'ir.actions.act_window_close'}