# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

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
    x_tipo = fields.Selection(
        related='lot_id.x_tipo',
        string='Tipo',
        readonly=True,
        help='Tipo de lote (placa/formato/pieza). Formatos y piezas admiten '
             'consumo parcial; las placas van enteras.',
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

        # Evita la restricción: Pedir + placas asignadas.
        # En este punto ya no es "Pedir", ya existe lote concreto en tránsito.
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
        # TRAMPA VACÍA-LÍNEAS (caso EMBARQUE/2026/0038): las líneas de los
        # lotes no recibidos amanecieron con product_uom_qty = 0 sin que
        # ningún código conocido las vacíe. Cualquier escritura que ponga
        # en 0 (o reduzca) la cantidad de una línea CON LOTE de un viaje
        # operativo queda registrada con el stack completo del autor.
        if 'product_uom_qty' in (vals or {}):
            try:
                new_qty = float(vals.get('product_uom_qty') or 0.0)
                suspects = self.filtered(
                    lambda l: l.lot_id
                    and l.voyage_id
                    and l.voyage_id.custom_status not in ('cancel',)
                    and (l.product_uom_qty or 0.0) > 0.01
                    and new_qty < (l.product_uom_qty or 0.0) - 0.01)
                if suspects:
                    _logger.warning(
                        "[TC_LINEZERO] Reducción/vaciado de %s línea(s) de "
                        "viaje con lote (%s) a qty=%s. Stack del autor:",
                        len(suspects),
                        ', '.join((suspects.mapped('lot_id.name') or [])[:8]),
                        new_qty,
                        stack_info=True,
                    )
            except Exception:
                pass

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
            released_lines = self.env['stock.transit.line']

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
                    released_lines |= line
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

            # El sync es idempotente por (orden, producto): correrlo una
            # vez por LOTE repetía todo el trabajo N veces (asignar 30
            # placas = 30 pasadas completas → congelamiento del hub).
            seen_sync = set()
            for line_id, order_id, product_id in sync_targets:
                key = (order_id, product_id)
                if key in seen_sync:
                    continue
                seen_sync.add(key)
                line = self.browse(line_id)
                order = self.env['sale.order'].browse(order_id)
                product = self.env['product.product'].browse(product_id)

                if line.exists() and order.exists() and product.exists():
                    line._tc_sync_sale_line_lots_from_transit_assignment(
                        order=order,
                        product=product,
                    )

            # Al liberar, las gemelas por parcialidad vuelven a ser UNA sola
            # línea: nunca debe verse el mismo lote duplicado sin razón.
            if released_lines:
                released_lines.exists()._tc_merge_available_twins()

        return res

    def _tc_merge_available_twins(self):
        """Fusiona líneas DISPONIBLES duplicadas del mismo lote en el viaje.

        El split de parcialidad crea una gemela con el saldo; al liberar la
        parte asignada ambas quedan disponibles y deben fusionarse. La cantidad
        resultante se ACOTA a la cantidad física del lote menos lo aún
        reservado en otras líneas: eso autocorrige lotes inflados por syncs
        previos a este fix.
        """
        processed_lots = set()

        for line in self:
            if not line.exists() or not line.lot_id or not line.voyage_id:
                continue

            key = (line.voyage_id.id, line.lot_id.id, line.product_id.id)
            if key in processed_lots:
                continue
            processed_lots.add(key)

            twins = self.search([
                ('voyage_id', '=', line.voyage_id.id),
                ('lot_id', '=', line.lot_id.id),
                ('product_id', '=', line.product_id.id),
                ('allocation_status', '=', 'available'),
                ('order_id', '=', False),
            ], order='id asc')

            if len(twins) <= 1:
                continue

            keeper = twins[0]
            total = sum(twins.mapped('product_uom_qty'))

            # Cota física del lote (quants con existencia; respaldo: área).
            phys = sum(self.env['stock.quant'].sudo().search([
                ('lot_id', '=', line.lot_id.id),
                ('quantity', '>', 0),
            ]).mapped('quantity'))
            if phys <= 0:
                lot = line.lot_id
                alto = getattr(lot, 'x_alto', 0.0) or 0.0
                ancho = getattr(lot, 'x_ancho', 0.0) or 0.0
                phys = alto * ancho

            reserved_elsewhere = sum(self.search([
                ('voyage_id', '=', line.voyage_id.id),
                ('lot_id', '=', line.lot_id.id),
                ('id', 'not in', twins.ids),
            ]).mapped('product_uom_qty'))

            if phys > 0:
                total = min(total, max(phys - reserved_elsewhere, 0.0))

            (twins - keeper).with_context(skip_reservation_logic=True).unlink()
            keeper.with_context(skip_reservation_logic=True).write({
                'product_uom_qty': total,
            })

            _logger.info(
                "[TC_TWINS] merge lote=%s gemelas=%s -> line=%s qty=%.3f "
                "(fisico=%.3f reservado_otras=%.3f)",
                line.lot_id.name, len(twins), keeper.id, total,
                phys, reserved_elsewhere,
            )

            if keeper.voyage_id:
                keeper.voyage_id.message_post(body=Markup(
                    "🧩 <b>Lote reunificado:</b> %s — el saldo disponible "
                    "volvió a ser una sola línea (%s)."
                ) % (line.lot_id.name, '%.3f' % total))

    # -------------------------------------------------------------------------
    # ASIGNACIÓN DESDE EL FORMULARIO DEL VIAJE (con control de sobre-asignación)
    # -------------------------------------------------------------------------

    @api.model
    def tc_normalize_voyage_twins(self, voyage_id):
        """Normaliza líneas duplicadas del mismo lote en un viaje (autocura).

        Se invoca al ABRIR el formulario del viaje. Para cada lote con más de
        una línea:
        - La(s) reservada(s) recuperan su PARCIALIDAD real desde el desglose
          de la línea de venta (x_lot_breakdown_json), si existe.
        - La disponible absorbe el resto contra la cantidad FÍSICA del lote
          (quants; respaldo: área). Si no queda resto, se elimina.
        - Varias disponibles se fusionan en una.

        Repara los lotes inflados por el sync de recepción previo al fix
        [TC_TWINS] sin intervención manual.
        """
        voyage = self.env['stock.transit.voyage'].browse(int(voyage_id or 0)).exists()
        if not voyage:
            return False

        by_key = {}
        for line in voyage.line_ids:
            if line.lot_id:
                by_key.setdefault(
                    (line.lot_id.id, line.product_id.id),
                    self.env['stock.transit.line'],
                )
                by_key[(line.lot_id.id, line.product_id.id)] |= line

        fixed = 0
        for (lot_id, _product_id), lines in by_key.items():
            if len(lines) <= 1:
                continue

            lot = lines[0].lot_id
            rounding = lines[0]._tc_qty_rounding()

            phys = sum(self.env['stock.quant'].sudo().search([
                ('lot_id', '=', lot_id),
                ('quantity', '>', 0),
            ]).mapped('quantity'))
            if phys <= 0:
                alto = getattr(lot, 'x_alto', 0.0) or 0.0
                ancho = getattr(lot, 'x_ancho', 0.0) or 0.0
                phys = alto * ancho
            if phys <= 0:
                continue

            reserved = lines.filtered(lambda l: l.order_id)
            free = (lines - reserved).sorted('id')

            # Parcialidad real desde el desglose de la venta (fuente de verdad).
            for r in reserved:
                sale_line = r._tc_get_sale_line_for_assignment(
                    order=r.order_id, product=r.product_id)
                if not sale_line or not hasattr(sale_line, '_tc_read_lot_breakdown'):
                    continue
                bd = sale_line._tc_read_lot_breakdown() or {}
                raw = bd.get(str(lot_id))
                if raw is None:
                    continue
                try:
                    real_qty = float(raw or 0.0)
                except (TypeError, ValueError):
                    continue
                if real_qty > 0 and float_compare(
                    real_qty, r.product_uom_qty or 0.0,
                    precision_rounding=rounding,
                ) != 0:
                    r.with_context(skip_reservation_logic=True).write({
                        'product_uom_qty': real_qty,
                    })

            reserved_qty = min(sum(reserved.mapped('product_uom_qty')), phys)
            rest = max(phys - reserved_qty, 0.0)

            if free:
                keeper = free[0]
                extras = free - keeper
                if extras:
                    extras.with_context(skip_reservation_logic=True).unlink()
                if rest <= rounding:
                    keeper.with_context(skip_reservation_logic=True).unlink()
                elif float_compare(
                    keeper.product_uom_qty or 0.0, rest,
                    precision_rounding=rounding,
                ) != 0:
                    keeper.with_context(skip_reservation_logic=True).write({
                        'product_uom_qty': rest,
                    })

            fixed += 1
            _logger.info(
                "[TC_TWINS] normalize voyage=%s lote=%s fisico=%.3f "
                "reservado=%.3f saldo=%.3f lineas_antes=%s",
                voyage.name, lot.name, phys, reserved_qty, rest, len(lines),
            )
            voyage.message_post(body=Markup(
                "🧩 <b>Lote normalizado:</b> %s — reservado %s · saldo libre %s "
                "(cantidad física %s)."
            ) % (lot.name, '%.3f' % reserved_qty, '%.3f' % rest, '%.3f' % phys))

        return fixed

    def tc_voyage_propagate_smart(self, order_id=False):
        """Propagación INTELIGENTE desde el Viaje (botón ↓↓).

        Asigna los lotes EN ORDEN hasta llenar lo solicitado de la línea de
        venta y se DETIENE antes del lote que generaría excedente — sin popup
        de decisión y sin obligar a ir uno por uno. En formatos/piezas, el
        último hueco se llena con una PARCIALIDAD exacta.

        Devuelve: assigned_count, skipped_count, remaining_qty y un mensaje
        listo para notificar.
        """
        TOL = 0.0001

        def _as_id(val):
            try:
                return int(val) or False
            except (TypeError, ValueError):
                return False

        order = self.env['sale.order'].browse(_as_id(order_id) or 0).exists()
        if not order:
            return {'success': False, 'message': _('Orden de venta no encontrada.')}

        # OJO call_kw: el primer argumento del RPC son los IDS del recordset
        # (self); order_id llega como segundo. No hay parámetro de ids aquí.
        lines = self.exists()
        if not lines:
            return {'success': False, 'message': _('Sin líneas para propagar.')}

        def _is_fractionable(line):
            lot = line.lot_id
            tipo = ''
            if lot and 'x_tipo' in lot._fields and lot.x_tipo:
                tipo = str(lot.x_tipo).strip().lower()
            return tipo in ('formato', 'pieza')

        # Pendiente por línea de venta destino (por producto).
        remaining_by_sl = {}
        sl_by_product = {}

        include_ids = []
        partial_by_line = {}
        skipped = 0
        stopped_products = set()

        for line in lines:
            product = line.product_id

            # Ya reservada a OTRA orden: no es candidata (se salta sin romper).
            if line.order_id and line.order_id.id != order.id:
                skipped += 1
                continue

            # Tras el primer lote que no cabe, ese producto ya no recibe más.
            if product.id in stopped_products:
                skipped += 1
                continue

            if product.id not in sl_by_product:
                sale_line = line._tc_get_sale_line_for_assignment(
                    order=order, product=product)
                sl_by_product[product.id] = sale_line
                if sale_line:
                    requested = sale_line.product_uom_qty or 0.0
                    assigned_before = sale_line._tc_get_assigned_lot_qty()
                    remaining_by_sl[sale_line.id] = max(
                        0.0, requested - assigned_before)

            sale_line = sl_by_product[product.id]
            if not sale_line:
                skipped += 1
                continue

            already = (
                sale_line.lot_ids.ids
                if 'lot_ids' in sale_line._fields else []
            )
            if line.lot_id and line.lot_id.id in already:
                # Idempotente: ya está en la orden, no consume pendiente.
                include_ids.append(line.id)
                continue

            qty = line.product_uom_qty or 0.0
            remaining = remaining_by_sl.get(sale_line.id, 0.0)

            if qty <= remaining + TOL:
                include_ids.append(line.id)
                remaining_by_sl[sale_line.id] = remaining - qty
            elif remaining > TOL and _is_fractionable(line):
                # Último hueco: parcialidad exacta en formato/pieza.
                include_ids.append(line.id)
                partial_by_line[line.id] = remaining
                remaining_by_sl[sale_line.id] = 0.0
                stopped_products.add(product.id)
            else:
                skipped += 1
                stopped_products.add(product.id)

        remaining_total = sum(remaining_by_sl.values())

        if not include_ids:
            return {
                'success': True,
                'assigned_count': 0,
                'skipped_count': skipped,
                'remaining_qty': remaining_total,
                'message': _(
                    'No se propagó ningún lote: el siguiente excedería lo '
                    'solicitado (pendiente: %(rem).2f). Asígnalo manualmente '
                    'si deseas manejar el excedente.'
                ) % {'rem': remaining_total},
            }

        result = self.tc_voyage_assign(
            include_ids, False, order.id,
            over_action=False, over_reason=False,
            partial_qty_by_line=partial_by_line or False,
        )

        if not result or result.get('success') is False or result.get('need_over_assignment_decision'):
            # No debería ocurrir (se llenó por debajo de lo solicitado), pero
            # si el estado cambió entre lecturas, se reporta tal cual.
            return result

        if skipped:
            message = _(
                'Propagado a %(n)s lote(s). %(m)s quedaron SIN asignar para '
                'no exceder lo solicitado (pendiente restante: %(rem).2f).'
            ) % {'n': len(include_ids), 'm': skipped, 'rem': remaining_total}
        else:
            message = _('Propagado a %(n)s lote(s).') % {'n': len(include_ids)}

        result.update({
            'assigned_count': len(include_ids),
            'skipped_count': skipped,
            'remaining_qty': remaining_total,
            'message': message,
        })
        return result

    def tc_voyage_assign(self, transit_line_ids, partner_id, order_id,
                         over_action=False, over_reason=False,
                         partial_qty_by_line=False):
        """Asignación desde el formulario del Viaje con control de excedente.

        Detecta si asignar estos lotes a la orden supera lo SOLICITADO en la
        línea de venta. Si hay excedente y aún no se eligió qué hacer (free/bill),
        devuelve ``need_over_assignment_decision`` para que el front muestre el
        popup. Con la decisión tomada, escribe la asignación pasando la decisión
        por contexto: el ratchet ajusta 'Solicitado' (y aplica descuento si es
        'free') correctamente. Es decir, 'Solicitado' NO cambia hasta decidir."""
        transit_lines = self.browse(transit_line_ids or self.ids).exists()
        if not transit_lines:
            return {'success': False, 'message': _('Sin líneas para asignar.')}

        # SANEO DE FK COLGANTE: si una línea apunta a un cliente borrado/fusionado
        # (p.ej. contactos deduplicados, que no repuntan modelos custom), cualquier
        # lectura posterior de ese partner lanza MissingError y rompe la asignación.
        # Se limpia a bajo nivel ANTES de operar; el cliente correcto se fija luego
        # desde la propia orden. .exists() no lanza (solo consulta existencia).
        dangling = transit_lines.filtered(
            lambda l: l.partner_id and not l.partner_id.exists()
        )
        if dangling:
            self.env.cr.execute(
                "UPDATE stock_transit_line SET partner_id = NULL WHERE id IN %s",
                (tuple(dangling.ids),),
            )
            dangling.invalidate_recordset(['partner_id'])

        # SANEO DE TIPOS (fronts con assets viejos en caché):
        # order_id DEBE ser entero. Si llega 'free'/'bill', el caller viejo mandó
        # la DECISIÓN de excedente en el slot de la orden:
        #   (lines, orden_en_slot_partner, action, reason) → se recorren los args
        # y el compat de abajo rescata la orden desde el slot del partner.
        if isinstance(order_id, str) and order_id.strip().lower() in ('free', 'bill'):
            if not over_reason and isinstance(over_action, str):
                over_reason = over_action
            over_action = order_id.strip().lower()
            order_id = False
            _logger.warning(
                "[TC_VOYAGE_ASSIGN] compat args recorridos: la decisión de "
                "excedente llegó en el slot de order_id (front desactualizado).",
            )

        def _as_id(val):
            try:
                return int(val) or False
            except (TypeError, ValueError):
                return False

        order_id = _as_id(order_id)
        partner_id = _as_id(partner_id)

        _logger.info(
            "[TC_VOYAGE_ASSIGN] lines=%s partner_id=%s order_id=%s over_action=%s",
            transit_lines.ids, partner_id, order_id, over_action,
        )

        # COMPAT DEFENSIVO (args invertidos):
        # En el flujo correcto, tc_voyage_assign SIEMPRE recibe partner_id=False
        # (el cliente se deriva de la orden). Si llega un partner_id truthy y
        # order_id=False, es un front desincronizado que mandó el id de la ORDEN
        # en el slot del partner. Si ese id resuelve a una sale.order, se
        # reinterpreta como order_id para que la asignación NO se pierda.
        if not order_id and partner_id:
            try:
                possible_order = self.env['sale.order'].sudo().browse(int(partner_id)).exists()
            except (TypeError, ValueError):
                possible_order = False
            if possible_order:
                _logger.warning(
                    "[TC_VOYAGE_ASSIGN] compat args invertidos: partner_id=%s "
                    "reinterpretado como order_id=%s (%s)",
                    partner_id, possible_order.id, possible_order.name,
                )
                order_id = possible_order.id
                partner_id = False

        # Sin orden: solo se limpia/asigna cliente (acción explícita). El partner,
        # si llega, debe existir para no caer en MissingError. NO se pierde una
        # asignación de orden por aquí porque el compat de arriba ya la rescató.
        if not order_id:
            vals = {'order_id': False}
            if partner_id:
                valid_partner = self.env['res.partner'].browse(partner_id).exists()
                if valid_partner:
                    vals['partner_id'] = valid_partner.id
            transit_lines.write(vals)
            _logger.info("[TC_VOYAGE_ASSIGN] rama sin-orden, vals=%s", vals)
            return {'success': True, 'over_assigned_qty': 0.0}

        order = self.env['sale.order'].browse(order_id).exists()
        if not order:
            return {'success': False, 'message': _('Orden de venta no encontrada.')}

        # El cliente SIEMPRE se deriva de la orden (no del valor que mande el
        # front, que puede estar obsoleto). La validación de asignación exige
        # que coincidan, y así evitamos escribir un partner inexistente.
        partner = order.partner_id
        if not partner.exists():
            return {
                'success': False,
                'message': _(
                    'El cliente del pedido %s no existe (fue borrado/fusionado). '
                    'Corrige el cliente del pedido antes de asignar.'
                ) % order.name,
            }
        partner_id = partner.id

        # No mover en silencio líneas ya reservadas a OTRA orden: eso debe pasar
        # por desasignar/reasignar explícitamente. Reasignar a la MISMA orden es
        # idempotente y se permite.
        locked = transit_lines.filtered(
            lambda l: l.order_id and l.order_id.id != order.id
        )
        if locked:
            return {
                'success': False,
                'message': _(
                    'Estos lotes ya están reservados a otra orden: %s. '
                    'Desasígnalos primero (botón "Desasignar") antes de reasignar.'
                ) % ', '.join(
                    '%s→%s' % (l.lot_id.name or l.id, l.order_id.name)
                    for l in locked
                ),
            }

        # Parcialidades (FORMATOS/PIEZAS): antes de medir excedente, partir las
        # líneas seleccionadas para asignar solo lo elegido y dejar el saldo
        # disponible. Tras el split, product_uom_qty ya refleja la parcialidad,
        # por lo que el cálculo de excedente y el breakdown la respetan sin
        # lógica adicional.
        transit_lines._tc_apply_partial_assignment_splits(partial_qty_by_line)

        # Excedente agregado por producto contra su línea de venta destino.
        total_over = 0.0
        for product in transit_lines.mapped('product_id'):
            product_lines = transit_lines.filtered(lambda l: l.product_id == product)
            sale_line = product_lines[:1]._tc_get_sale_line_for_assignment(
                order=order, product=product,
            )
            if not sale_line:
                return {
                    'success': False,
                    'message': _(
                        'La orden %(order)s no tiene una línea para %(prod)s. '
                        'Agrega el producto al pedido antes de asignar.'
                    ) % {'order': order.name, 'prod': product.display_name},
                }

            requested = sale_line.product_uom_qty or 0.0
            assigned_before = sale_line._tc_get_assigned_lot_qty()
            already_assigned = set(sale_line.lot_ids.ids) if 'lot_ids' in sale_line._fields else set()
            new_qty = sum(
                tl.product_uom_qty or 0.0
                for tl in product_lines
                if tl.lot_id and tl.lot_id.id not in already_assigned
            )
            projected = assigned_before + new_qty
            over = max(projected - requested, 0.0) if requested > 0 else projected
            _logger.info(
                "[TC_VOYAGE_ASSIGN] sale_line=%s prod=%s requested=%.3f "
                "assigned_before=%.3f new_qty=%.3f projected=%.3f over=%.3f",
                sale_line.id, product.display_name, requested,
                assigned_before, new_qty, projected, over,
            )
            if over > 0:
                total_over += over

        if total_over > 0 and over_action not in ('free', 'bill'):
            return {
                'success': False,
                'need_over_assignment_decision': True,
                'over_assigned_qty': total_over,
                'message': _(
                    'La asignación excede lo solicitado. Indica si el excedente '
                    'se entrega sin cobrar o si se cobrará al cliente.'
                ),
            }

        # La decisión viaja por contexto: _tc_sync_requested_qty_from_lots la
        # aplica (cantidad + descuento) en vez del ratchet automático.
        transit_lines.with_context(
            tc_over_assignment_action=over_action or False,
            tc_over_assignment_reason=over_reason or False,
        ).write({
            'partner_id': partner_id,
            'order_id': order_id,
        })

        _logger.info(
            "[TC_VOYAGE_ASSIGN] ESCRITO partner_id=%s order_id=%s total_over=%.3f "
            "(lines ahora: %s)",
            partner_id, order_id, total_over,
            [(l.id, l.partner_id.id, l.order_id.id, l.allocation_status) for l in transit_lines],
        )

        return {
            'success': True,
            'over_assigned_qty': total_over,
            'over_assignment_action': over_action if total_over > 0 else False,
        }

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
    # PARCIALIDADES (FORMATOS / PIEZAS): SPLIT DE LÍNEA EN TRÁNSITO
    # -------------------------------------------------------------------------

    def _tc_qty_rounding(self):
        self.ensure_one()
        product = self.product_id
        if product and product.uom_id and product.uom_id.rounding:
            return product.uom_id.rounding
        return 0.0001

    def _tc_is_fractionable(self):
        """Solo FORMATOS y PIEZAS admiten consumo parcial; las PLACAS van enteras.

        Espeja la misma regla de tipo (x_tipo) que ya usan
        ``_tal_build_breakdown_from_transit_lines`` y
        ``stock.picking._tc_build_lot_breakdown_from_transit_lines``.
        """
        self.ensure_one()
        lot = self.lot_id
        if lot and 'x_tipo' in lot._fields and lot.x_tipo:
            return str(lot.x_tipo).lower() in ('formato', 'pieza')
        return False

    def _tc_split_for_partial_assignment(self, assign_qty):
        """Parte la línea para asignar solo ``assign_qty`` y dejar el saldo disponible.

        Reduce esta línea a la cantidad asignada y crea una línea hermana
        ``available`` con el saldo, conservando viaje, lote, quant y allocation.
        ``product_uom_qty`` sigue siendo la única fuente de verdad para el
        breakdown, la recepción y la entrega, por lo que NO se duplica lógica:
        el desglose de parcialidad ya existente lee automáticamente la cantidad
        reducida.

        Devuelve la línea de saldo creada (recordset vacío si no hubo split).
        Solo aplica a fraccionables (formato/pieza); las placas se devuelven sin
        cambios.
        """
        self.ensure_one()

        rounding = self._tc_qty_rounding()
        full_qty = self.product_uom_qty or 0.0
        assign_qty = float_round(assign_qty or 0.0, precision_rounding=rounding)

        if float_compare(assign_qty, 0.0, precision_rounding=rounding) <= 0:
            raise UserError(_(
                'La cantidad parcial a asignar del lote %s debe ser mayor a cero.'
            ) % (self.lot_id.display_name or self.product_id.display_name))

        # Igual o más que la línea: no hay saldo que partir.
        if float_compare(assign_qty, full_qty, precision_rounding=rounding) >= 0:
            return self.env['stock.transit.line']

        # Las placas no se fraccionan: se conserva la línea completa.
        if not self._tc_is_fractionable():
            return self.env['stock.transit.line']

        saldo_qty = float_round(full_qty - assign_qty, precision_rounding=rounding)

        saldo = self.with_context(skip_reservation_logic=True).copy({
            'product_uom_qty': saldo_qty,
            'allocation_status': 'available',
            'partner_id': False,
            'order_id': False,
        })

        self.with_context(skip_reservation_logic=True).write({
            'product_uom_qty': assign_qty,
        })

        if self.voyage_id:
            self.voyage_id.message_post(body=Markup(
                "✂️ <b>Parcialidad:</b> %s<br/>"
                "Asignado al pedido: %s · Saldo disponible en tránsito: %s"
            ) % (
                self.lot_id.name or self.product_id.name,
                ('%.3f' % assign_qty),
                ('%.3f' % saldo_qty),
            ))

        _logger.info(
            "[TC_SPLIT] transit_line=%s lote=%s full=%.3f asignado=%.3f saldo_line=%s saldo=%.3f",
            self.id,
            self.lot_id.name if self.lot_id else 'N/A',
            full_qty,
            assign_qty,
            saldo.id,
            saldo_qty,
        )

        return saldo

    def _tc_operational_qty(self):
        """Cantidad OPERATIVA de la línea de tránsito.

        Es el mismo número que el hub muestra al asignar (min entre la
        cantidad capturada en la línea y su quant físico de tránsito).
        Toda métrica de "asignado" sobre lotes en tránsito debe usar ESTA
        fuente: el quant crudo o las medidas del lote (x_alto × x_ancho)
        pueden quedar desfasados de la captura y desalinean Solicitado,
        Asignado y Pendiente entre sí.
        """
        self.ensure_one()

        qty = self.product_uom_qty or 0.0

        quant = self.quant_id
        if not quant or not quant.exists() or not self._tc_is_transit_quant(quant):
            quant = self._tc_resolve_transit_quant() if hasattr(self, '_tc_resolve_transit_quant') else False

        if quant and quant.quantity > 0:
            qty = min(quant.quantity, qty)

        return qty

    def _tc_apply_partial_assignment_splits(self, partial_qty_by_line):
        """Aplica parcialidades (split) a las líneas fraccionables indicadas.

        ``partial_qty_by_line``: dict ``{transit_line_id: qty}``. Solo afecta a
        formatos/piezas; las placas se ignoran (van enteras). Cada línea de
        ``self`` queda reducida a su parcialidad y se crea el saldo disponible.
        Devuelve el recordset de líneas de saldo creadas.
        """
        if not partial_qty_by_line:
            return self.env['stock.transit.line']

        norm = {}
        for key, value in partial_qty_by_line.items():
            try:
                norm[int(key)] = float(value or 0.0)
            except (TypeError, ValueError):
                continue

        if not norm:
            return self.env['stock.transit.line']

        saldo_lines = self.env['stock.transit.line']

        for line in self:
            if line.id not in norm:
                continue

            # Las placas no se fraccionan: se ignora cualquier parcialidad.
            if not line._tc_is_fractionable():
                continue

            qty = norm[line.id]
            rounding = line._tc_qty_rounding()
            available = line.product_uom_qty or 0.0

            if float_compare(qty, available, precision_rounding=rounding) > 0:
                raise UserError(_(
                    'La parcialidad solicitada (%(qty)s) del lote %(lot)s supera la '
                    'cantidad embarcada disponible (%(avail)s).'
                ) % {
                    'qty': ('%.3f' % qty),
                    'lot': line.lot_id.display_name or line.product_id.display_name,
                    'avail': ('%.3f' % available),
                })

            saldo_lines |= line._tc_split_for_partial_assignment(qty)

        return saldo_lines

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