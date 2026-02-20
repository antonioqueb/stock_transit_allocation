# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def unlink(self):
        for order in self:
            transit_lines = self.env['stock.transit.line'].search([
                ('order_id', '=', order.id),
                ('lot_id', '!=', False)
            ])
            if transit_lines:
                raise UserError(_("No puede eliminar el pedido %s porque ya tiene mercancía recibida en tránsito (Torre de Control).") % order.name)
        return super(SaleOrder, self).unlink()

    has_mandar_pedir = fields.Boolean(
        string='Tiene Mandar Pedir',
        compute='_compute_transit_status',
        store=True,
    )

    @api.depends('order_line.auto_transit_assign')
    def _compute_transit_status(self):
        for order in self:
            order.has_mandar_pedir = any(
                l.auto_transit_assign for l in order.order_line if not l.display_type
            )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    auto_transit_assign = fields.Boolean(
        string='Mandar Pedir',
        default=False,
        help="Si está marcado, se considerará para la asignación automática en la Torre de Control "
             "cuando se genere la compra."
    )

    has_stone_lots = fields.Boolean(
        string='Tiene Placas Asignadas',
        compute='_compute_has_stone_lots',
        store=True,
    )

    # ── Nuevos campos de tránsito ──────────────────────────────────────────────

    transit_status = fields.Selection(
        selection=[
            ('solicitud', 'Solicitud Enviada'),
            ('production', 'Producción'),
            ('booking', 'Booking'),
            ('puerto_origen', 'Puerto Origen'),
            ('on_sea', 'En Altamar'),
            ('puerto_destino', 'Puerto Destino'),
            ('arrived_port', 'Arribo a Puerto'),
            ('reception_pending', 'En Recepción'),
            ('delivered', 'Entregado'),
            ('cancel', 'Cancelado'),
        ],
        string='Estado Embarque',
        compute='_compute_transit_info',
        store=True,
    )

    transit_eta = fields.Date(
        string='ETA Embarque',
        compute='_compute_transit_info',
        store=True,
    )

    transit_voyage_id = fields.Many2one(
        'stock.transit.voyage',
        string='Viaje',
        compute='_compute_transit_info',
        store=True,
    )

    @api.depends('lot_ids')
    def _compute_has_stone_lots(self):
        for line in self:
            line.has_stone_lots = bool(line.lot_ids)

    @api.depends(
        'auto_transit_assign',
        'order_id',
        'product_id',
    )
    def _compute_transit_info(self):
        for line in self:
            if not line.auto_transit_assign or not line.product_id or not line.order_id:
                line.transit_status = False
                line.transit_eta = False
                line.transit_voyage_id = False
                continue

            # Buscar allocation activa para esta línea de venta
            allocation = self.env['purchase.order.line.allocation'].search([
                ('sale_line_id', '=', line.id),
                ('state', 'not in', ['cancelled', 'done']),
            ], order='id desc', limit=1)

            if not allocation:
                # También buscar directamente en transit lines por order_id
                transit_line = self.env['stock.transit.line'].search([
                    ('order_id', '=', line.order_id.id),
                    ('product_id', '=', line.product_id.id),
                ], order='id desc', limit=1)

                if transit_line and transit_line.voyage_id:
                    line.transit_status = transit_line.voyage_id.custom_status
                    line.transit_eta = transit_line.voyage_id.eta
                    line.transit_voyage_id = transit_line.voyage_id
                else:
                    line.transit_status = False
                    line.transit_eta = False
                    line.transit_voyage_id = False
                continue

            voyage = allocation.purchase_line_id.order_id.transit_voyage if hasattr(allocation.purchase_line_id.order_id, 'transit_voyage') else False

            # Buscar voyage vía transit lines
            transit_line = self.env['stock.transit.line'].search([
                ('order_id', '=', line.order_id.id),
                ('product_id', '=', line.product_id.id),
            ], order='id desc', limit=1)

            if transit_line and transit_line.voyage_id:
                line.transit_status = transit_line.voyage_id.custom_status
                line.transit_eta = transit_line.voyage_id.eta
                line.transit_voyage_id = transit_line.voyage_id
            else:
                # Buscar voyage por la OC del allocation
                po = allocation.purchase_order_id
                if po:
                    voyage = self.env['stock.transit.voyage'].search([
                        ('purchase_id', '=', po.id),
                        ('custom_status', '!=', 'cancel'),
                    ], order='id desc', limit=1)
                    if voyage:
                        line.transit_status = voyage.custom_status
                        line.transit_eta = voyage.eta
                        line.transit_voyage_id = voyage
                        continue

                line.transit_status = False
                line.transit_eta = False
                line.transit_voyage_id = False

    @api.onchange('auto_transit_assign')
    def _onchange_auto_transit_assign(self):
        if self.auto_transit_assign and self.lot_ids:
            self.auto_transit_assign = False
            return {
                'warning': {
                    'title': _('No permitido'),
                    'message': _('Esta línea ya tiene placas asignadas. No puede marcar "Mandar Pedir" cuando hay placas seleccionadas.'),
                }
            }

    @api.constrains('auto_transit_assign', 'lot_ids')
    def _check_transit_vs_lots(self):
        for line in self:
            if line.auto_transit_assign and line.lot_ids:
                raise UserError(_(
                    'La línea "%s" tiene placas asignadas y no puede estar marcada como "Mandar Pedir". '
                    'Quite las placas o desmarque "Mandar Pedir".'
                ) % (line.product_id.display_name or ''))