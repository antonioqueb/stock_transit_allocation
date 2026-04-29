# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def unlink(self):
        for order in self:
            transit_lines = self.env['stock.transit.line'].search([
                ('order_id', '=', order.id),
                ('lot_id', '!=', False)
            ])
            if transit_lines:
                raise UserError(_(
                    "No puede eliminar el pedido %s porque ya tiene mercancía recibida "
                    "en tránsito (Torre de Control)."
                ) % order.name)
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
                line.auto_transit_assign
                for line in order.order_line
                if not line.display_type
            )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    auto_transit_assign = fields.Boolean(
        string='Mandar Pedir',
        default=False,
        help=(
            "Si está marcado, se considerará para la asignación automática en la Torre de Control "
            "cuando se genere la compra."
        ),
    )

    has_stone_lots = fields.Boolean(
        string='Tiene Placas Asignadas',
        compute='_compute_has_stone_lots',
        store=True,
    )

    tc_stock_rejected = fields.Boolean(
        string='Stock rechazado por vendedor',
        default=False,
        copy=False,
        index=True,
        help=(
            "Indica que el vendedor revisó el stock disponible y decidió mandar a pedir "
            "el requerimiento pendiente aunque exista inventario."
        ),
    )

    tc_stock_rejected_reason = fields.Text(
        string='Motivo rechazo stock',
        copy=False,
    )

    tc_stock_rejected_by = fields.Many2one(
        'res.users',
        string='Stock rechazado por',
        copy=False,
        readonly=True,
    )

    tc_stock_rejected_at = fields.Datetime(
        string='Fecha rechazo stock',
        copy=False,
        readonly=True,
    )

    tc_qty_assigned_lots = fields.Float(
        string='Cantidad asignada por placas',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
    )

    tc_qty_pending_allocation = fields.Float(
        string='Cantidad pendiente de asignar/comprar',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
    )

    tc_available_internal_qty = fields.Float(
        string='Stock libre disponible',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
    )

    tc_allocation_hub_state = fields.Selection(
        selection=[
            ('allocated', 'Asignado'),
            ('to_be_allocated', 'To Be Allocated'),
            ('to_be_purchased', 'To Be Purchased'),
            ('nothing', 'Sin acción'),
        ],
        string='Hub de asignación',
        compute='_compute_tc_allocation_qtys',
    )

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

    # -------------------------------------------------------------------------
    # CANTIDAD COMERCIAL ASIGNADA / PENDIENTE
    # -------------------------------------------------------------------------

    def _tc_get_line_uom(self):
        """
        Compatibilidad Odoo 17/18/19.

        En algunas versiones/customizaciones la línea de venta usa product_uom.
        En Odoo 19 en este entorno el campo observado es product_uom_id.
        Nunca se debe acceder directo a self.product_uom sin validar _fields,
        porque rompe los tableros To Be Allocated / To Be Purchased.
        """
        self.ensure_one()

        for field_name in ('product_uom', 'product_uom_id'):
            if field_name in self._fields:
                uom = self[field_name]
                if uom:
                    return uom

        if self.product_id and self.product_id.uom_id:
            return self.product_id.uom_id

        return self.env['uom.uom']

    def _tc_get_qty_rounding(self):
        self.ensure_one()

        uom = self._tc_get_line_uom()
        if uom and uom.rounding:
            return uom.rounding

        if self.product_id and self.product_id.uom_id and self.product_id.uom_id.rounding:
            return self.product_id.uom_id.rounding

        return 0.0001

    def _tc_float_gt_zero(self, qty):
        self.ensure_one()
        return float_compare(
            qty or 0.0,
            0.0,
            precision_rounding=self._tc_get_qty_rounding(),
        ) > 0

    def _tc_float_le_zero(self, qty):
        self.ensure_one()
        return float_compare(
            qty or 0.0,
            0.0,
            precision_rounding=self._tc_get_qty_rounding(),
        ) <= 0

    def _tc_read_lot_breakdown(self):
        self.ensure_one()

        if 'x_lot_breakdown_json' not in self._fields:
            return {}

        raw = self.x_lot_breakdown_json

        if not raw:
            return {}

        if isinstance(raw, dict):
            return raw

        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}

        return {}

    def _tc_get_lot_qty(self, lot, breakdown=None):
        self.ensure_one()

        if not lot:
            return 0.0

        breakdown = breakdown or {}
        lot_type = ''

        if 'x_tipo' in lot._fields and lot.x_tipo:
            lot_type = str(lot.x_tipo).lower()

        if lot_type in ('formato', 'pieza') and str(lot.id) in breakdown:
            try:
                return float(breakdown.get(str(lot.id)) or 0.0)
            except Exception:
                return 0.0

        Quant = self.env['stock.quant'].sudo()
        quant = Quant.search([
            ('product_id', '=', self.product_id.id),
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ], order='id desc', limit=1)

        return quant.quantity if quant else 0.0

    def _tc_get_assigned_lot_qty(self):
        self.ensure_one()

        if 'lot_ids' not in self._fields or not self.lot_ids:
            return 0.0

        breakdown = self._tc_read_lot_breakdown()

        total = 0.0
        for lot in self.lot_ids:
            total += self._tc_get_lot_qty(lot, breakdown=breakdown)

        return total

    def _tc_get_pending_allocation_qty(self):
        self.ensure_one()

        assigned_qty = self._tc_get_assigned_lot_qty()
        delivered_qty = self.qty_delivered or 0.0

        # Lo entregado también cubre necesidad; lo asignado comercialmente cubre necesidad.
        covered_qty = max(assigned_qty, delivered_qty)
        pending_qty = (self.product_uom_qty or 0.0) - covered_qty

        if self._tc_float_le_zero(pending_qty):
            return 0.0

        return pending_qty

    def _tc_get_free_internal_qty(self):
        self.ensure_one()

        if not self.product_id:
            return 0.0

        Quant = self.env['stock.quant'].sudo()

        domain = [
            ('product_id', '=', self.product_id.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('reserved_quantity', '=', 0),
        ]

        if 'x_tiene_hold' in Quant._fields:
            domain.append(('x_tiene_hold', '=', False))

        if hasattr(Quant, '_get_committed_lot_ids'):
            committed_lot_ids = Quant._get_committed_lot_ids(self.product_id.id)
            safe_current_ids = self.lot_ids.ids if 'lot_ids' in self._fields and self.lot_ids else []

            excluded_lot_ids = [
                lot_id for lot_id in committed_lot_ids
                if lot_id not in safe_current_ids
            ]

            if excluded_lot_ids:
                domain.append(('lot_id', 'not in', excluded_lot_ids))

        quants = Quant.search(domain)
        return sum(quants.mapped('quantity'))

    @api.depends(
        'product_uom_qty',
        'qty_delivered',
        'lot_ids',
        'x_lot_breakdown_json',
        'product_id',
        'tc_stock_rejected',
        'auto_transit_assign',
        'state',
    )
    def _compute_tc_allocation_qtys(self):
        for line in self:
            if (
                line.display_type
                or line.state not in ('sale', 'done')
                or not line.product_id
            ):
                line.tc_qty_assigned_lots = 0.0
                line.tc_qty_pending_allocation = 0.0
                line.tc_available_internal_qty = 0.0
                line.tc_allocation_hub_state = 'nothing'
                continue

            assigned_qty = line._tc_get_assigned_lot_qty()
            pending_qty = line._tc_get_pending_allocation_qty()
            available_qty = line._tc_get_free_internal_qty()

            line.tc_qty_assigned_lots = assigned_qty
            line.tc_qty_pending_allocation = pending_qty
            line.tc_available_internal_qty = available_qty

            if line._tc_float_le_zero(pending_qty):
                line.tc_allocation_hub_state = 'allocated'
            elif line.tc_stock_rejected or line.auto_transit_assign:
                line.tc_allocation_hub_state = 'to_be_purchased'
            elif line._tc_float_gt_zero(available_qty):
                line.tc_allocation_hub_state = 'to_be_allocated'
            else:
                line.tc_allocation_hub_state = 'to_be_purchased'

    # -------------------------------------------------------------------------
    # ACCIONES HUB
    # -------------------------------------------------------------------------

    def action_tc_send_to_purchase(self, reason=False):
        for line in self:
            if line.display_type:
                continue

            pending_qty = line._tc_get_pending_allocation_qty()

            if line._tc_float_le_zero(pending_qty):
                raise UserError(_(
                    'La línea "%s" ya no tiene cantidad pendiente para mandar a pedir.'
                ) % (line.product_id.display_name or line.name or line.id))

            line.write({
                'tc_stock_rejected': True,
                'tc_stock_rejected_reason': reason or line.tc_stock_rejected_reason or '',
                'tc_stock_rejected_by': self.env.user.id,
                'tc_stock_rejected_at': fields.Datetime.now(),
                'auto_transit_assign': True,
            })

            line.order_id.message_post(body=_(
                '📌 <b>Mandar pedido desde To Be Allocated</b><br/>'
                'Producto: <b>%(product)s</b><br/>'
                'Cantidad pendiente: <b>%(qty).3f</b><br/>'
                'El inventario disponible fue rechazado por el vendedor; compras debe generar/mantener OC.'
            ) % {
                'product': line.product_id.display_name,
                'qty': pending_qty,
            })

        return True

    def action_tc_clear_stock_rejection(self):
        for line in self:
            line.write({
                'tc_stock_rejected': False,
                'tc_stock_rejected_reason': False,
                'tc_stock_rejected_by': False,
                'tc_stock_rejected_at': False,
            })
        return True

    # -------------------------------------------------------------------------
    # INFO TRÁNSITO / OC
    # -------------------------------------------------------------------------

    @api.depends('auto_transit_assign', 'order_id', 'product_id')
    def _compute_transit_info(self):
        for line in self:
            if not line.auto_transit_assign or not line.product_id or not line.order_id:
                line.transit_status = False
                line.transit_eta = False
                line.transit_voyage_id = False
                continue

            allocation = self.env['purchase.order.line.allocation'].search([
                ('sale_line_id', '=', line.id),
                ('state', 'not in', ['cancelled', 'done']),
            ], order='id desc', limit=1)

            if not allocation:
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

            transit_line = self.env['stock.transit.line'].search([
                ('order_id', '=', line.order_id.id),
                ('product_id', '=', line.product_id.id),
            ], order='id desc', limit=1)

            if transit_line and transit_line.voyage_id:
                line.transit_status = transit_line.voyage_id.custom_status
                line.transit_eta = transit_line.voyage_id.eta
                line.transit_voyage_id = transit_line.voyage_id
            else:
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
            pending_qty = self._tc_get_pending_allocation_qty()

            if self._tc_float_le_zero(pending_qty):
                self.auto_transit_assign = False
                return {
                    'warning': {
                        'title': _('No permitido'),
                        'message': _(
                            'Esta línea ya está completamente cubierta con placas seleccionadas. '
                            'No queda cantidad pendiente para mandar a pedir.'
                        ),
                    }
                }

    @api.constrains('auto_transit_assign', 'lot_ids', 'product_uom_qty')
    def _check_transit_vs_lots(self):
        for line in self:
            if not line.auto_transit_assign or not line.lot_ids:
                continue

            pending_qty = line._tc_get_pending_allocation_qty()

            if line._tc_float_le_zero(pending_qty):
                raise UserError(_(
                    'La línea "%s" ya está completamente cubierta con placas seleccionadas. '
                    'No puede marcarse como "Mandar Pedir" si no queda cantidad pendiente.'
                ) % (line.product_id.display_name or ''))