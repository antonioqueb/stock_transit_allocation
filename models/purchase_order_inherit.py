# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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

    def write(self, vals):
        res = super().write(vals)
        # LA OC MANDA: cambios de ruta/embarque en la OC (forwarder,
        # naviera, POL/POD, ETD, tipo, BL, ETA) se PROPAGAN a sus
        # embarques del portal, pisando lo que el proveedor haya
        # capturado. Los vacíos no viajan (no se borra nada del portal).
        if (not self.env.context.get('som_carrier_sync')
                and not self.env.context.get('skip_date_sync')
                and 'supplier.shipment' in self.env):
            watch = {
                'som_route_forwarder_id': 'forwarder_id',
                'som_route_naviera_id': 'naviera_id',
                'som_route_pol_id': 'pol_id',
                'som_route_pod_id': 'pod_id',
                'som_route_etd': 'etd',
                'som_transport_type': 'shipment_type',
                'bl_number': 'bl_number',
                'eta_date': 'eta',
            }
            touched = {k: v for k, v in watch.items() if k in vals}
            if touched:
                Ship = self.env['supplier.shipment'].sudo()
                for po in self:
                    ships = Ship.search([('purchase_id', '=', po.id)])
                    if not ships:
                        continue
                    s_vals = {}
                    for po_f, ship_f in touched.items():
                        if po_f not in po._fields or \
                                ship_f not in Ship._fields:
                            continue
                        value = po[po_f]
                        if not value:
                            continue
                        if po._fields[po_f].type == 'many2one':
                            value = value.id
                        s_vals[ship_f] = value
                    if s_vals:
                        ships.with_context(
                            skip_date_sync=True, som_carrier_sync=True,
                        ).write(s_vals)
                        _logger.info(
                            "[PO→PORTAL] OC %s propagó %s a %s embarque(s).",
                            po.name, list(s_vals), len(ships))
        return res

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        # La recepción a tránsito nace UNIFICADA: un move por producto aunque
        # la OC repita el producto en varias líneas (el core crea un move por
        # línea de compra y nunca los fusiona).
        for po in self:
            po.picking_ids.sudo()._som_unify_transit_demand()
        for po in self:
            # sudo(): plomería interna al confirmar la OC — el comprador
            # no necesita ACL de Torre de Control.
            allocations = self.env['purchase.order.line.allocation'].sudo().search([
                ('purchase_order_id', '=', po.id)
            ])

            # EL EMBARQUE NACE SIEMPRE que la OC tenga producto almacenable.
            # Antes solo nacía con allocations: una OC sin apartados llegaba
            # a la recepción sin viaje — el material se validaba a tránsito
            # pero la Torre no tenía embarque que mostrar ni recepción
            # física que generar (caso C51).
            has_storable = any(
                line.product_id and line.product_id.type != 'service'
                for line in po.order_line if not line.display_type
            )
            if has_storable:
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
                    try:
                        voyage.action_load_from_purchase()
                    except Exception:
                        _logger.exception(
                            '[TC] No se pudieron cargar las líneas del viaje '
                            'de %s al confirmar; el viaje queda creado.',
                            po.name)

            if allocations:
                # Solo activar allocations NUEVAS (draft/False). Escribir
                # 'pending' sin filtrar RESUCITABA las canceladas (incluso las
                # que el guard acababa de cancelar en la línea anterior) y las
                # done, inflando la cobertura → doble compra.
                allocations.filtered(
                    lambda a: a.state not in ('cancelled', 'done', 'pending')
                ).write({'state': 'pending'})
        return res