# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.model
    def _som_transit_source_location(self):
        """SOM/TRANSIT, la ubicación donde LLEGA toda recepción de compra.
        Se resuelve por ruta completa (sobrevive renombres de hijos) con
        respaldo por id histórico (1019)."""
        Location = self.env['stock.location']
        loc = Location.search(
            [('complete_name', '=ilike', 'SOM/TRANSIT')], limit=1)
        if not loc:
            loc = Location.browse(1019).exists()
        return loc

    def _prepare_picking(self):
        vals = super()._prepare_picking()
        loc = self._som_transit_source_location()
        # DESTINO, no origen: el origen de una recepción es Proveedores.
        # Con origen = destino = SOM/TRANSIT la entrada y la salida se
        # cancelan y el lote queda con quant en cero (bug SOM/IN/00009).
        if loc:
            vals['location_dest_id'] = loc.id
        return vals

    sale_order_ids = fields.Many2many(
        'sale.order',
        string='Órdenes de Venta Vinculadas',
        compute='_compute_sale_order_links',
        compute_sudo=True,
        help='Campo de compatibilidad para vistas heredadas que esperan las SO vinculadas a la OC.',
    )

    sale_order_count = fields.Integer(
        string='Cantidad de Órdenes de Venta',
        compute='_compute_sale_order_links',
        compute_sudo=True,
    )

    @api.depends('order_line.allocation_ids.sale_order_id')
    def _compute_sale_order_links(self):
        for po in self:
            sale_orders = po.order_line.mapped('allocation_ids.sale_order_id')
            po.sale_order_ids = sale_orders
            po.sale_order_count = len(sale_orders)

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        for po in self:
            # sudo(): plomería interna al confirmar la OC — el comprador
            # no necesita ACL de Torre de Control.
            allocations = self.env['purchase.order.line.allocation'].sudo().search([
                ('purchase_order_id', '=', po.id)
            ])
            if allocations:
                existing_voyage = self.env['stock.transit.voyage'].sudo().search([
                    ('purchase_id', '=', po.id),
                    ('custom_status', '!=', 'cancel'),
                ], limit=1)

                if not existing_voyage:
                    voyage = self.env['stock.transit.voyage'].sudo().create({
                        'purchase_id': po.id,
                        'custom_status': 'solicitud',
                        'vessel_name': 'Por Definir',
                        'bl_number': po.partner_ref or po.name,
                    })
                    voyage.action_load_from_purchase()

                # Solo activar allocations NUEVAS (draft/False). Escribir
                # 'pending' sin filtrar RESUCITABA las canceladas (incluso las
                # que el guard acababa de cancelar en la línea anterior) y las
                # done, inflando la cobertura → doble compra.
                allocations.filtered(
                    lambda a: a.state not in ('cancelled', 'done', 'pending')
                ).write({'state': 'pending'})
        return res