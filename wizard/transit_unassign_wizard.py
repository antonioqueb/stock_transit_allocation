# -*- coding: utf-8 -*-
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..models.utils.transit_manager import TransitManager


class TransitUnassignWizard(models.TransientModel):
    _name = 'transit.unassign.wizard'
    _description = 'Wizard de Desasignación Masiva en Tránsito'

    voyage_id = fields.Many2one(
        'stock.transit.voyage',
        string='Viaje',
        readonly=True,
    )

    line_wizard_ids = fields.One2many(
        'transit.unassign.wizard.line',
        'wizard_id',
        string='Materiales asignados',
    )

    # Filtros de conveniencia: marcan/desmarcan en bloque las líneas que
    # coincidan, para acelerar "desasignar todo lo de este pedido/cliente".
    filter_partner_id = fields.Many2one(
        'res.partner',
        string='Filtrar por cliente',
        help='Marca solo las líneas de este cliente. Vacío = todas.',
    )
    filter_order_id = fields.Many2one(
        'sale.order',
        string='Filtrar por orden',
        help='Marca solo las líneas de esta orden. Vacío = todas.',
    )

    reason = fields.Text(
        string='Motivo / Notas',
        help='Opcional. Queda registrado en el chatter del viaje.',
    )

    selected_count = fields.Integer(
        string='Seleccionadas',
        compute='_compute_selected_count',
    )

    @api.depends('line_wizard_ids.selected')
    def _compute_selected_count(self):
        for wizard in self:
            wizard.selected_count = len(
                wizard.line_wizard_ids.filtered('selected')
            )

    # =========================================================================
    # CARGA INICIAL
    # =========================================================================

    @api.model
    def _candidate_transit_lines(self, lines):
        """Líneas realmente asignadas que pueden desasignarse.

        'Asignado' = tiene cliente. Se excluyen viajes ya entregados o
        cancelados: ahí el material ya no está en tránsito asignable."""
        return lines.filtered(
            lambda l: l.partner_id
            and (
                not l.voyage_id
                or l.voyage_id.custom_status not in ('delivered', 'cancel')
            )
        )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        ctx = self.env.context
        active_model = ctx.get('active_model')

        lines = self.env['stock.transit.line']
        voyage = self.env['stock.transit.voyage']

        if active_model == 'stock.transit.voyage':
            voyage = self.env['stock.transit.voyage'].browse(
                ctx.get('active_id') or ctx.get('active_ids', [])
            ).exists()
            lines = voyage.line_ids
        elif active_model == 'stock.transit.line':
            lines = self.env['stock.transit.line'].browse(
                ctx.get('active_ids', [])
            ).exists()

        candidates = self._candidate_transit_lines(lines)

        if not voyage:
            voyage_set = candidates.mapped('voyage_id')
            if len(voyage_set) == 1:
                voyage = voyage_set

        res.update({
            'voyage_id': voyage.id if len(voyage) == 1 else False,
            'line_wizard_ids': [
                (0, 0, {'transit_line_id': line.id, 'selected': True})
                for line in candidates
            ],
        })

        return res

    # =========================================================================
    # FILTROS (marcado en bloque)
    # =========================================================================

    @api.onchange('filter_partner_id', 'filter_order_id')
    def _onchange_filters(self):
        for line in self.line_wizard_ids:
            match = True
            if self.filter_partner_id:
                match = match and line.partner_id == self.filter_partner_id
            if self.filter_order_id:
                match = match and line.order_id == self.filter_order_id
            line.selected = match

    # =========================================================================
    # ACCIÓN PRINCIPAL
    # =========================================================================

    def action_apply(self):
        self.ensure_one()

        transit_lines = self.line_wizard_ids.filtered('selected').mapped(
            'transit_line_id'
        ).exists()

        if not transit_lines:
            raise UserError(_(
                'Selecciona al menos un material para desasignar.'
            ))

        # Liberación a Stock: sin nuevo cliente/orden. reassign_lot limpia
        # partner_id/order_id, marca la línea como disponible, cancela los hold
        # físicos del quant y re-sincroniza la línea de venta. Cada write ya
        # publica su propio "🔓 Liberado a Stock" en el chatter del viaje.
        for line in transit_lines:
            TransitManager.reassign_lot(
                self.env,
                line,
                False,
                False,
                self.reason,
            )

        # Mensaje de auditoría consolidado por viaje.
        voyages = transit_lines.mapped('voyage_id')
        for voyage in voyages:
            count = len(transit_lines.filtered(lambda l: l.voyage_id == voyage))
            body = Markup(
                "🔓 <b>Desasignación masiva:</b> %s material(es) liberado(s) a Stock."
            ) % count
            if self.reason:
                body += Markup("<br/><b>Motivo:</b> %s") % self.reason
            voyage.message_post(body=body)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Desasignación completada'),
                'message': _(
                    '%s material(es) liberado(s) a Stock.'
                ) % len(transit_lines),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class TransitUnassignWizardLine(models.TransientModel):
    _name = 'transit.unassign.wizard.line'
    _description = 'Línea de Desasignación Masiva en Tránsito'

    wizard_id = fields.Many2one(
        'transit.unassign.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )

    transit_line_id = fields.Many2one(
        'stock.transit.line',
        string='Línea de tránsito',
        required=True,
        readonly=True,
        ondelete='cascade',
    )

    selected = fields.Boolean(string='Desasignar', default=True)

    lot_id = fields.Many2one(
        related='transit_line_id.lot_id',
        string='Lote',
        readonly=True,
    )
    product_id = fields.Many2one(
        related='transit_line_id.product_id',
        string='Producto',
        readonly=True,
    )
    product_uom_qty = fields.Float(
        related='transit_line_id.product_uom_qty',
        string='Cantidad',
        readonly=True,
    )
    partner_id = fields.Many2one(
        related='transit_line_id.partner_id',
        string='Cliente',
        readonly=True,
    )
    order_id = fields.Many2one(
        related='transit_line_id.order_id',
        string='Orden',
        readonly=True,
    )
    container_number = fields.Char(
        related='transit_line_id.container_number',
        string='Contenedor',
        readonly=True,
    )
