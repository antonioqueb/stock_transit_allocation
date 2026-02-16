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

    @api.depends('lot_ids')
    def _compute_has_stone_lots(self):
        for line in self:
            line.has_stone_lots = bool(line.lot_ids)

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