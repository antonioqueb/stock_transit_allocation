# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)


class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    _rec_name = 'lot_id'

    voyage_id = fields.Many2one(
        'stock.transit.voyage',
        string='Viaje',
        required=True,
        ondelete='cascade',
    )
    company_id = fields.Many2one(
        related='voyage_id.company_id',
        store=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Descripción / Producto',
        required=True,
    )

    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote / Placa',
        required=False,
    )
    container_number = fields.Char(
        string='Contenedor',
    )
    quant_id = fields.Many2one(
        'stock.quant',
        string='Quant Físico',
    )

    x_grosor = fields.Char(
        related='lot_id.x_grosor',
        string='Grosor',
        readonly=True,
    )
    x_alto = fields.Float(
        related='lot_id.x_alto',
        string='Alto',
        readonly=True,
    )
    x_ancho = fields.Float(
        related='lot_id.x_ancho',
        string='Largo',
        readonly=True,
    )

    product_uom_qty = fields.Float(
        string='M2 Embarcados',
        digits='Product Unit of Measure',
    )

    eligible_partner_ids = fields.Many2many(
        'res.partner',
        compute='_compute_eligible_partners',
        string='Clientes Elegibles',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente / Proyecto',
        index=True,
        domain="[('id', 'in', eligible_partner_ids)]",
    )

    eligible_order_ids = fields.Many2many(
        'sale.order',
        compute='_compute_eligible_orders',
        string='Órdenes Elegibles',
    )
    order_id = fields.Many2one(
        'sale.order',
        string='Sales Order',
        domain="[('id', 'in', eligible_order_ids)]",
    )

    allocation_id = fields.Many2one(
        'purchase.order.line.allocation',
        string='Asignación Origen',
    )

    allocation_status = fields.Selection(
        [
            ('available', 'Disponible (Stock)'),
            ('reserved', 'Reservado / Vendido'),
        ],
        string='Estado Asignación',
        default='available',
        required=True,
    )

    purchase_id = fields.Many2one(
        'purchase.order',
        compute='_compute_purchase_id',
        string='OC Sistema',
        store=True,
    )

    @api.depends('voyage_id.purchase_id', 'voyage_id.picking_id.purchase_id')
    def _compute_purchase_id(self):
        for line in self:
            line.purchase_id = line.voyage_id.purchase_id or line.voyage_id.picking_id.purchase_id

    date_order = fields.Datetime(
        related='purchase_id.date_order',
        string='Fecha OC',
        store=True,
    )
    vendor_id = fields.Many2one(
        'res.partner',
        related='purchase_id.partner_id',
        string='Proveedor',
        store=True,
    )
    proforma_ref = fields.Char(
        related='purchase_id.partner_ref',
        string='Proforma / Ref Prov',
        store=True,
    )
    salesperson_id = fields.Many2one(
        'res.users',
        related='order_id.user_id',
        string='Vendedor',
        store=True,
    )

    qty_proforma = fields.Float(
        string='Metraje Proforma',
        compute='_compute_po_so_qty',
        store=True,
    )
    qty_original_demand = fields.Float(
        string='Metraje Pedido Original',
        compute='_compute_po_so_qty',
        store=True,
    )

    voyage_status = fields.Selection(
        related='voyage_id.custom_status',
        string='Status',
        store=True,
    )
    shipping_line = fields.Char(
        related='voyage_id.shipping_line',
        string='Naviera',
        store=True,
    )
    bl_number = fields.Char(
        related='voyage_id.bl_number',
        string='Factura de Carga / BL',
        store=True,
    )
    etd = fields.Date(
        related='voyage_id.etd',
        string='ETD',
        store=True,
    )
    eta = fields.Date(
        related='voyage_id.eta',
        string='ETA',
        store=True,
    )
    arrival_date = fields.Date(
        related='voyage_id.arrival_date',
        string='Llegada Real',
        store=True,
    )
    notes = fields.Text(
        string='Comentarios',
    )

    # -------------------------------------------------------------------------
    # DOMINIOS ELEGIBLES
    # -------------------------------------------------------------------------

    @api.depends('product_id')
    def _compute_eligible_partners(self):
        for line in self:
            if not line.product_id:
                line.eligible_partner_ids = [(5, 0, 0)]
                continue

            sale_lines = self.env['sale.order.line'].search([
                ('product_id', '=', line.product_id.id),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])

            partner_ids = sale_lines.mapped('order_id.partner_id').ids
            line.eligible_partner_ids = [(6, 0, partner_ids)]

    @api.depends('product_id', 'partner_id')
    def _compute_eligible_orders(self):
        for line in self:
            if not line.product_id or not line.partner_id:
                line.eligible_order_ids = [(5, 0, 0)]
                continue

            sale_lines = self.env['sale.order.line'].search([
                ('product_id', '=', line.product_id.id),
                ('order_id.partner_id', '=', line.partner_id.id),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])

            order_ids = sale_lines.mapped('order_id').ids
            line.eligible_order_ids = [(6, 0, order_ids)]

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if not self.partner_id:
            self.order_id = False
            return

        if not self.product_id:
            self.order_id = False
            return

        sale_lines = self.env['sale.order.line'].search([
            ('product_id', '=', self.product_id.id),
            ('order_id.partner_id', '=', self.partner_id.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
        ])

        eligible_orders = sale_lines.mapped('order_id')

        if len(eligible_orders) == 1:
            self.order_id = eligible_orders[0]
        elif self.order_id and self.order_id not in eligible_orders:
            self.order_id = False

    # -------------------------------------------------------------------------
    # HELPERS: ASIGNACIÓN COMERCIAL DESDE EMBARQUE
    # -------------------------------------------------------------------------

    def _tc_get_sale_line_for_assignment(self, order=False, product=False):
        """
        Devuelve la línea de venta objetivo para este producto dentro del pedido.

        Regla:
        - La asignación desde embarque solo puede apuntar a una SO confirmada.
        - Se trabaja por producto/material.
        - No se tocan otros materiales del pedido.
        """
        self.ensure_one()

        SaleLine = self.env['sale.order.line']
        order = order or self.order_id
        product = product or self.product_id

        if not order or not product:
            return SaleLine

        lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.id == product.id
        )

        if not lines:
            return SaleLine

        pending_lines = lines.filtered(
            lambda l: (l.product_uom_qty or 0.0) > (l.qty_delivered or 0.0)
        )

        return (pending_lines or lines)[:1]

    def _tc_validate_assignment_target(self, partner=False, order=False):
        """
        Valida la regla de negocio antes de aceptar la asignación a pedido.

        Reglas:
        - El pedido debe estar confirmado.
        - El cliente asignado debe corresponder al cliente del pedido.
        - El pedido debe tener una línea del producto/material asignado.
        """
        self.ensure_one()

        if not order:
            return True

        if order.state not in ('sale', 'done'):
            raise UserError(_(
                "No puede asignar el lote %(lot)s al pedido %(order)s porque el pedido "
                "no está confirmado.\n\n"
                "Confirme primero la orden de venta y después asigne el material desde el embarque."
            ) % {
                'lot': self.lot_id.display_name if self.lot_id else self.product_id.display_name,
                'order': order.name,
            })

        if partner and order.partner_id.id != partner.id:
            raise UserError(_(
                "El cliente seleccionado (%(partner)s) no corresponde al cliente del pedido "
                "%(order)s (%(order_partner)s)."
            ) % {
                'partner': partner.display_name,
                'order': order.name,
                'order_partner': order.partner_id.display_name,
            })

        sale_line = self._tc_get_sale_line_for_assignment(
            order=order,
            product=self.product_id,
        )

        if not sale_line:
            raise UserError(_(
                "El pedido %(order)s está confirmado, pero no contiene una línea para el producto:\n\n"
                "%(product)s\n\n"
                "Agregue el producto al pedido antes de asignar lotes desde el embarque."
            ) % {
                'order': order.name,
                'product': self.product_id.display_name,
            })

        return True

    def _tc_get_purchase_for_manual_assignment(self):
        """
        Resuelve la OC de referencia para vincular una asignación manual de lote.

        La asignación de lotes NO debe hacerse automáticamente al cargar el
        picking; sin embargo, cuando Compras selecciona manualmente un lote para
        un pedido, sí conviene vincular la allocation de la OC si existe. Esto
        mantiene trazabilidad PO/SO y permite cerrar la allocation al recibir.
        """
        self.ensure_one()

        purchase = self.purchase_id

        if not purchase and self.voyage_id:
            purchase = self.voyage_id.purchase_id or self.voyage_id.picking_id.purchase_id

        return purchase

    def _tc_get_matching_allocation_for_manual_assignment(self, order=False):
        """
        Busca una allocation compatible con la selección manual del comprador.

        No decide qué lote asignar; eso ya lo hizo el usuario al seleccionar la
        línea de tránsito. Este helper solo enlaza, de forma contable/operativa,
        la línea seleccionada con la allocation activa de la misma OC/SO/producto.
        """
        self.ensure_one()

        Allocation = self.env['purchase.order.line.allocation'].sudo()

        order = order or self.order_id
        purchase = self._tc_get_purchase_for_manual_assignment()

        if not order or not purchase or not self.product_id:
            return Allocation

        sale_line = self._tc_get_sale_line_for_assignment(
            order=order,
            product=self.product_id,
        )

        if not sale_line:
            return Allocation

        allocations = Allocation.search([
            ('purchase_order_id', '=', purchase.id),
            ('sale_line_id', '=', sale_line.id),
            ('product_id', '=', self.product_id.id),
            ('state', 'not in', ['done', 'cancelled']),
        ], order='id asc')

        if not allocations:
            return Allocation

        TransitLine = self.env['stock.transit.line'].sudo()

        for allocation in allocations:
            linked_lines = TransitLine.search([
                ('allocation_id', '=', allocation.id),
                ('id', '!=', self.id),
                ('voyage_id.custom_status', '!=', 'cancel'),
            ])
            linked_qty = sum(linked_lines.mapped('product_uom_qty'))
            remaining_qty = (allocation.quantity or 0.0) - linked_qty

            if remaining_qty > 0:
                return allocation

        return Allocation

    def _tc_link_allocation_after_manual_assignment(self, order=False):
        """
        Vincula allocation_id después de una asignación manual de Compras.

        Se ejecuta solo cuando ya existe order_id/partner_id en la línea de
        tránsito. No crea ni cambia la asignación comercial; únicamente conserva
        trazabilidad contra la OC que originó la necesidad.
        """
        self.ensure_one()

        if self.allocation_id or not self.order_id or not self.product_id:
            return False

        allocation = self._tc_get_matching_allocation_for_manual_assignment(
            order=order or self.order_id,
        )

        if not allocation:
            return False

        self.with_context(
            skip_reservation_logic=True,
            skip_transit_publication_sync=False,
        ).write({
            'allocation_id': allocation.id,
        })

        _logger.info(
            '[TC_MANUAL_ASSIGN] Línea de tránsito %s vinculada a allocation %s por selección manual. Lote=%s Pedido=%s',
            self.id,
            allocation.id,
            self.lot_id.name if self.lot_id else 'N/A',
            self.order_id.name if self.order_id else 'N/A',
        )

        return allocation

    def _tc_get_assigned_transit_lines_for_order_product(self, order, product):
        """
        Devuelve las líneas reservadas de ESTE viaje para un pedido/producto.

        Se usa para saber qué lotes deben quedar preseleccionados en la venta.
        """
        self.ensure_one()

        if not self.voyage_id or not order or not product:
            return self.env['stock.transit.line']

        return self.env['stock.transit.line'].search([
            ('voyage_id', '=', self.voyage_id.id),
            ('order_id', '=', order.id),
            ('product_id', '=', product.id),
            ('allocation_status', '=', 'reserved'),
            ('lot_id', '!=', False),
        ], order='id asc')

    def _tc_sync_sale_line_lots_from_transit_assignment(self, order=False, product=False):
        """
        Preselección comercial.

        Esto vincula los lotes del embarque con la línea de venta,
        pero NO sincroniza la entrega todavía y NO crea reserva física.

        La reserva física/move lines de entrega se crean hasta validar
        la recepción física.
        """
        self.ensure_one()

        order = order or self.order_id
        product = product or self.product_id

        if not order or not product:
            return False

        sale_line = self._tc_get_sale_line_for_assignment(order=order, product=product)

        if not sale_line or 'lot_ids' not in sale_line._fields:
            return False

        transit_lines = self._tc_get_assigned_transit_lines_for_order_product(order, product)
        lot_ids = transit_lines.mapped('lot_id').ids

        vals = {
            'lot_ids': [(6, 0, lot_ids)],
        }

        # Evita la restricción: Mandar Pedir + placas asignadas.
        # En este punto ya no es "mandar pedir", ya existe lote concreto en tránsito.
        if lot_ids and 'auto_transit_assign' in sale_line._fields:
            vals['auto_transit_assign'] = False

        if 'x_lot_breakdown_json' in sale_line._fields:
            vals['x_lot_breakdown_json'] = False

        sale_line.with_context(
            skip_stone_sync_picking=True,
            skip_stone_sync_so=True,
            skip_hold_validation=True,
            skip_picking_clean=True,
            skip_transit_sale_sync=True,
        ).write(vals)

        _logger.info(
            "[TC_ASSIGN_PRE] SO %s | Producto %s | lot_ids preseleccionados desde viaje %s: %s",
            order.name,
            product.display_name,
            self.voyage_id.name if self.voyage_id else 'N/A',
            lot_ids,
        )

        return True

    # -------------------------------------------------------------------------
    # WRITE: ASIGNACIÓN VISUAL/COMERCIAL EN EL EMBARQUE
    # -------------------------------------------------------------------------

    def write(self, vals):
        if self.env.context.get('skip_reservation_logic'):
            return super(StockTransitLine, self).write(vals)

        vals = dict(vals or {})
        assignment_changed = 'partner_id' in vals or 'order_id' in vals

        old_assignments = {}

        if assignment_changed:
            if vals.get('partner_id') is False and 'order_id' not in vals:
                vals['order_id'] = False

            if 'order_id' in vals and 'allocation_id' not in vals:
                vals['allocation_id'] = False

            if vals.get('order_id') and 'partner_id' not in vals:
                order = self.env['sale.order'].browse(vals['order_id'])
                if order.exists():
                    vals['partner_id'] = order.partner_id.id

            for line in self:
                old_assignments[line.id] = {
                    'partner_id': line.partner_id.id if line.partner_id else False,
                    'order_id': line.order_id.id if line.order_id else False,
                    'product_id': line.product_id.id if line.product_id else False,
                }

                new_partner = line.partner_id
                new_order = line.order_id

                if 'partner_id' in vals:
                    new_partner = (
                        self.env['res.partner'].browse(vals['partner_id'])
                        if vals.get('partner_id') else False
                    )

                if 'order_id' in vals:
                    new_order = (
                        self.env['sale.order'].browse(vals['order_id'])
                        if vals.get('order_id') else False
                    )

                if new_order:
                    line._tc_validate_assignment_target(new_partner, new_order)

        res = super(StockTransitLine, self).write(vals)

        if assignment_changed:
            sync_targets = set()

            for line in self:
                old = old_assignments.get(line.id, {})
                old_partner_id = old.get('partner_id')
                old_order_id = old.get('order_id')
                old_product_id = old.get('product_id')

                new_partner = line.partner_id
                new_order = line.order_id

                changed = (
                    old_partner_id != (new_partner.id if new_partner else False)
                    or old_order_id != (new_order.id if new_order else False)
                )

                if not changed:
                    continue

                new_status = 'reserved' if (new_partner and new_order) else 'available'

                if line.allocation_status != new_status:
                    super(StockTransitLine, line).write({
                        'allocation_status': new_status,
                    })

                if new_partner and new_order:
                    # En ubicación de tránsito, stock_transit_publication intercepta
                    # esta llamada y evita crear hold físico.
                    line._execute_reservation_logic(new_partner, new_order)
                    line._tc_link_allocation_after_manual_assignment(order=new_order)
                    sync_targets.add((line.id, new_order.id, line.product_id.id))
                else:
                    if line.allocation_id:
                        super(StockTransitLine, line).write({
                            'allocation_id': False,
                        })
                    line._execute_release_logic()
                    if old_order_id and old_product_id:
                        sync_targets.add((line.id, old_order_id, old_product_id))

                if line.voyage_id:
                    if new_partner and new_order:
                        msg = Markup("🔄 <b>Asignación:</b> %s<br/>→ %s / %s") % (
                            line.lot_id.name or line.product_id.name,
                            new_partner.name,
                            new_order.name,
                        )
                    elif new_partner and not new_order:
                        msg = Markup("👤 <b>Cliente asignado pendiente de pedido:</b> %s → %s") % (
                            line.lot_id.name or line.product_id.name,
                            new_partner.name,
                        )
                    else:
                        msg = Markup("🔓 <b>Liberado a Stock:</b> %s") % (
                            line.lot_id.name or line.product_id.name,
                        )

                    line.voyage_id.message_post(body=msg)

            for line_id, order_id, product_id in sync_targets:
                line = self.browse(line_id)
                order = self.env['sale.order'].browse(order_id)
                product = self.env['product.product'].browse(product_id)

                if line.exists() and order.exists() and product.exists():
                    line._tc_sync_sale_line_lots_from_transit_assignment(
                        order=order,
                        product=product,
                    )

        return res

    # -------------------------------------------------------------------------
    # RESERVA / LIBERACIÓN
    # -------------------------------------------------------------------------

    def _execute_reservation_logic(self, partner, order):
        self.ensure_one()

        if not self.lot_id or not self.quant_id:
            _logger.info(
                "TransitLine %s: Sin lote físico, solo asignación visual",
                self.id,
            )
            return True

        existing_hold = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo'),
        ], limit=1)

        if existing_hold:
            _logger.info("TransitLine %s: Ya existe hold activo, verificando...", self.id)
            hold_partner = existing_hold.partner_id if hasattr(existing_hold, 'partner_id') else False

            if hold_partner and hold_partner == partner:
                return True

            try:
                existing_hold.action_cancelar_hold()
            except Exception as e:
                _logger.warning("No se pudo cancelar hold existente: %s", e)

        try:
            from .utils.transit_manager import TransitManager

            TransitManager.reassign_lot(
                self.env,
                self,
                partner,
                order,
                notes="Asignación directa desde Torre de Control",
            )
        except Exception as e:
            _logger.error("Error creando reserva: %s", e, exc_info=True)

        return True

    def _execute_release_logic(self):
        self.ensure_one()

        if not self.quant_id:
            return True

        existing_holds = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo'),
        ])

        for hold in existing_holds:
            try:
                hold.action_cancelar_hold()
                _logger.info("TransitLine %s: Hold cancelado", self.id)
            except Exception as e:
                _logger.error("Error cancelando hold: %s", e, exc_info=True)

        return True

    # -------------------------------------------------------------------------
    # CANTIDADES REFERENCIA
    # -------------------------------------------------------------------------

    @api.depends('purchase_id', 'order_id', 'product_id', 'allocation_id')
    def _compute_po_so_qty(self):
        for line in self:
            po_qty = line.allocation_id.quantity if line.allocation_id else 0.0
            so_qty = line.allocation_id.quantity if line.allocation_id else 0.0

            if not line.allocation_id:
                if line.purchase_id:
                    po_qty = sum(
                        line.purchase_id.order_line.filtered(
                            lambda l: l.product_id == line.product_id
                        ).mapped('product_qty')
                    )

                if line.order_id:
                    so_qty = sum(
                        line.order_id.order_line.filtered(
                            lambda l: l.product_id == line.product_id
                        ).mapped('product_uom_qty')
                    )

            line.qty_proforma = po_qty
            line.qty_original_demand = so_qty