# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def unlink(self):
        for order in self:
            transit_lines = self.env['stock.transit.line'].search([
                ('order_id', '=', order.id),
                ('lot_id', '!=', False),
            ])
            if transit_lines:
                raise UserError(_(
                    "No puede eliminar el pedido %s porque ya tiene mercancía recibida "
                    "en tránsito (Torre de Control)."
                ) % order.name)
        return super(SaleOrder, self).unlink()

    has_mandar_pedir = fields.Boolean(
        string='Tiene Mandar a pedir',
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

    # -------------------------------------------------------------------------
    # CAMPOS OPERATIVOS DE ASIGNACIÓN
    # -------------------------------------------------------------------------

    auto_transit_assign = fields.Boolean(
        string='Mandar a pedir',
        default=False,
        copy=False,
        help=(
            "Acción operativa: el pendiente de esta línea se envía a To Be Purchased. "
            "No modifica la cantidad solicitada ni la asignación de placas."
        ),
    )

    has_stone_lots = fields.Boolean(
        string='Tiene placas asignadas',
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

    # Campo legado. Se conserva para no romper BD/vistas históricas,
    # pero ya NO es fuente de verdad.
    tc_qty_origin_requested = fields.Float(
        string='[Deprecado] Cantidad origen',
        digits='Product Unit of Measure',
        copy=False,
        readonly=True,
        help=(
            'Campo legado. Ya no es fuente operativa de demanda. '
            'La demanda viva es product_uom_qty.'
        ),
    )

    tc_qty_assigned_lots = fields.Float(
        string='Asignado',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
        help='Cantidad asignada por placas/lotes seleccionados.',
    )

    tc_qty_pending_allocation = fields.Float(
        string='Pendiente por asignar',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
        help=(
            'Cantidad solicitada menos cantidad cubierta. '
            'Si la línea está cerrada corta, queda en 0 para los hubs.'
        ),
    )

    tc_qty_assigned_percent = fields.Float(
        string='% Asignado',
        compute='_compute_tc_allocation_qtys',
        digits=(16, 2),
    )

    tc_qty_over_assigned = fields.Float(
        string='Sobreasignado',
        compute='_compute_tc_allocation_qtys',
        digits='Product Unit of Measure',
    )

    tc_assignment_state = fields.Selection(
        selection=[
            ('no_demand', 'Sin demanda'),
            ('open', 'Pendiente'),
            ('partial', 'Parcial'),
            ('complete', 'Completo'),
            ('over_assigned', 'Sobreasignado'),
            ('to_purchase', 'Mandado a pedir'),
            ('closed_short', 'Cerrado corto'),
        ],
        string='Estado asignación',
        compute='_compute_tc_allocation_qtys',
    )

    tc_assignment_closed = fields.Boolean(
        string='Asignación cerrada',
        default=False,
        copy=False,
        readonly=True,
        help='Cierra el pendiente operativo sin modificar la cantidad solicitada.',
    )

    tc_closed_short_qty = fields.Float(
        string='Diferencia cerrada',
        digits='Product Unit of Measure',
        copy=False,
        readonly=True,
    )

    tc_closure_reason = fields.Text(
        string='Motivo cierre',
        copy=False,
        readonly=True,
    )

    tc_closure_by = fields.Many2one(
        'res.users',
        string='Cerrado por',
        copy=False,
        readonly=True,
    )

    tc_closure_at = fields.Datetime(
        string='Fecha cierre',
        copy=False,
        readonly=True,
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

    # -------------------------------------------------------------------------
    # CAMPOS DE TRÁNSITO
    # -------------------------------------------------------------------------

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
    # COMPATIBILIDAD / PRECISIÓN
    # -------------------------------------------------------------------------

    def _tc_get_line_uom(self):
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

    # -------------------------------------------------------------------------
    # LECTURA / NORMALIZACIÓN DE LOTES
    # -------------------------------------------------------------------------

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

    def _tc_get_lot_type(self, lot):
        self.ensure_one()

        if lot and 'x_tipo' in lot._fields and lot.x_tipo:
            return str(lot.x_tipo).lower()

        return 'placa'

    def _tc_get_lot_internal_qty(self, lot):
        self.ensure_one()

        if not lot or not self.product_id:
            return 0.0

        Quant = self.env['stock.quant'].sudo()

        domain = [
            ('product_id', '=', self.product_id.id),
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if 'company_id' in Quant._fields and self.order_id and self.order_id.company_id:
            domain.append(('company_id', 'in', [False, self.order_id.company_id.id]))

        quants = Quant.search(domain)
        return sum(quants.mapped('quantity'))

    def _tc_get_lot_fallback_qty(self, lot):
        """
        Fallback defensivo para placas cuando el quant ya no está interno
        porque el material fue entregado/movido.
        """
        self.ensure_one()

        if not lot:
            return 0.0

        if 'x_alto' in lot._fields and 'x_ancho' in lot._fields and lot.x_alto and lot.x_ancho:
            try:
                return float(lot.x_alto or 0.0) * float(lot.x_ancho or 0.0)
            except Exception:
                return 0.0

        return 0.0

    def _tc_get_lot_qty(self, lot, breakdown=None):
        self.ensure_one()

        if not lot:
            return 0.0

        breakdown = breakdown or {}
        lot_type = self._tc_get_lot_type(lot)
        lot_key = str(lot.id)

        if lot_type in ('formato', 'pieza') and lot_key in breakdown:
            try:
                return float(breakdown.get(lot_key) or 0.0)
            except Exception:
                return 0.0

        qty = self._tc_get_lot_internal_qty(lot)

        if self._tc_float_le_zero(qty):
            qty = self._tc_get_lot_fallback_qty(lot)

        return qty

    def _tc_normalize_hub_breakdown(self, lot_ids, breakdown=None):
        self.ensure_one()

        selected_ids = set()
        for lot_id in lot_ids or []:
            try:
                selected_ids.add(int(lot_id))
            except Exception:
                continue

        if not selected_ids:
            return {}

        raw = breakdown or {}

        if isinstance(raw, str):
            try:
                raw = json.loads(raw) or {}
            except Exception:
                raw = {}

        if not isinstance(raw, dict):
            raw = {}

        lots = self.env['stock.lot'].browse(list(selected_ids)).exists()
        partial_lot_ids = set(
            lots.filtered(
                lambda lot: self._tc_get_lot_type(lot) in ('formato', 'pieza')
            ).ids
        )

        clean = {}

        for key, value in raw.items():
            try:
                lot_id = int(key)
            except Exception:
                continue

            if lot_id not in selected_ids or lot_id not in partial_lot_ids:
                continue

            try:
                qty = float(value or 0.0)
            except Exception:
                qty = 0.0

            if qty < 0:
                qty = 0.0

            clean[str(lot_id)] = qty

        return clean

    def _tc_prepare_breakdown_value_for_line(self, breakdown):
        self.ensure_one()

        if not breakdown:
            return False

        field = self._fields.get('x_lot_breakdown_json')
        if field and field.type in ('char', 'text'):
            return json.dumps(breakdown)

        return breakdown

    def _tc_compute_assigned_qty_from_lots(self, lot_ids, breakdown=None):
        self.ensure_one()

        safe_lot_ids = []
        for lot_id in lot_ids or []:
            try:
                lot_id = int(lot_id)
            except Exception:
                continue
            if lot_id not in safe_lot_ids:
                safe_lot_ids.append(lot_id)

        if not safe_lot_ids:
            raise UserError(_(
                'Debe seleccionar al menos un lote para guardar una asignación desde To Be Allocated.'
            ))

        lots = self.env['stock.lot'].browse(safe_lot_ids).exists()

        if len(lots) != len(safe_lot_ids):
            raise UserError(_(
                'Uno o más lotes seleccionados ya no existen. Actualice el tablero y vuelva a intentar.'
            ))

        invalid_lots = lots.filtered(
            lambda lot: lot.product_id and lot.product_id.id != self.product_id.id
        )
        if invalid_lots:
            raise UserError(_(
                'No puede asignar lotes de otro producto a la línea %(line)s.\n\nLotes inválidos: %(lots)s'
            ) % {
                'line': self.display_name,
                'lots': ', '.join(invalid_lots.mapped('display_name')),
            })

        clean_breakdown = self._tc_normalize_hub_breakdown(
            safe_lot_ids,
            breakdown=breakdown,
        )

        total_qty = 0.0
        missing_lots = []
        rounded_breakdown = {}
        rounding = self._tc_get_qty_rounding()

        for lot in lots:
            lot_type = self._tc_get_lot_type(lot)
            lot_key = str(lot.id)
            physical_qty = self._tc_get_lot_internal_qty(lot)

            if lot_type in ('formato', 'pieza') and lot_key in clean_breakdown:
                qty = clean_breakdown.get(lot_key) or 0.0
                if physical_qty > 0 and float_compare(qty, physical_qty, precision_rounding=rounding) > 0:
                    qty = physical_qty
                qty = float_round(qty, precision_rounding=rounding)
                rounded_breakdown[lot_key] = qty
            else:
                qty = float_round(physical_qty, precision_rounding=rounding)

                if float_compare(qty, 0.0, precision_rounding=rounding) <= 0:
                    qty = float_round(
                        self._tc_get_lot_fallback_qty(lot),
                        precision_rounding=rounding,
                    )

            if float_compare(qty, 0.0, precision_rounding=rounding) <= 0:
                missing_lots.append(lot.display_name)
                continue

            total_qty += qty

        total_qty = float_round(total_qty, precision_rounding=rounding)

        if float_compare(total_qty, 0.0, precision_rounding=rounding) <= 0:
            raise UserError(_(
                'La asignación no tiene cantidad positiva. Revise los lotes y las cantidades capturadas.'
            ))

        if missing_lots:
            raise UserError(_(
                'No se puede guardar la asignación porque estos lotes no tienen cantidad positiva:\n\n%s'
            ) % '\n'.join(missing_lots[:50]))

        return total_qty, rounded_breakdown, safe_lot_ids

    # Compatibilidad con código anterior.
    def _tc_compute_final_qty_from_lots(self, lot_ids, breakdown=None):
        return self._tc_compute_assigned_qty_from_lots(lot_ids, breakdown=breakdown)

    def _tc_get_assigned_lot_qty(self):
        self.ensure_one()

        if 'lot_ids' not in self._fields or not self.lot_ids:
            return 0.0

        breakdown = self._tc_read_lot_breakdown()

        total = 0.0
        for lot in self.lot_ids:
            total += self._tc_get_lot_qty(lot, breakdown=breakdown)

        return total

    def _tc_get_covered_qty_for_allocation(self):
        """
        Cubierto para efectos del hub:
        - Asignado por placas, o
        - Entregado, si ya salió físicamente.
        No modifica demanda; solo evita que líneas ya entregadas vuelvan al hub.
        """
        self.ensure_one()
        assigned_qty = self._tc_get_assigned_lot_qty()
        delivered_qty = self.qty_delivered or 0.0
        return max(assigned_qty, delivered_qty)

    def _tc_get_raw_pending_allocation_qty(self):
        self.ensure_one()

        covered_qty = self._tc_get_covered_qty_for_allocation()
        pending_qty = (self.product_uom_qty or 0.0) - covered_qty

        if self._tc_float_le_zero(pending_qty):
            return 0.0

        return pending_qty

    def _tc_get_pending_allocation_qty(self):
        self.ensure_one()

        if self.tc_assignment_closed:
            return 0.0

        return self._tc_get_raw_pending_allocation_qty()

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
        'tc_assignment_closed',
        'tc_closed_short_qty',
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
                line.tc_qty_assigned_percent = 0.0
                line.tc_qty_over_assigned = 0.0
                line.tc_available_internal_qty = 0.0
                line.tc_assignment_state = 'no_demand'
                line.tc_allocation_hub_state = 'nothing'
                continue

            requested_qty = line.product_uom_qty or 0.0
            assigned_qty = line._tc_get_assigned_lot_qty()
            covered_qty = line._tc_get_covered_qty_for_allocation()
            raw_pending_qty = line._tc_get_raw_pending_allocation_qty()
            pending_qty = line._tc_get_pending_allocation_qty()
            available_qty = line._tc_get_free_internal_qty()
            over_assigned_qty = max(assigned_qty - requested_qty, 0.0) if requested_qty > 0 else assigned_qty

            line.tc_qty_assigned_lots = assigned_qty
            line.tc_qty_pending_allocation = pending_qty
            line.tc_qty_assigned_percent = (assigned_qty / requested_qty) * 100.0 if requested_qty > 0 else 0.0
            line.tc_qty_over_assigned = over_assigned_qty
            line.tc_available_internal_qty = available_qty

            if line._tc_float_le_zero(requested_qty):
                line.tc_assignment_state = 'no_demand'
                line.tc_allocation_hub_state = 'nothing'
                continue

            if line.tc_assignment_closed:
                line.tc_assignment_state = 'closed_short' if line._tc_float_gt_zero(line.tc_closed_short_qty or raw_pending_qty) else 'complete'
                line.tc_allocation_hub_state = 'nothing'
                continue

            if line._tc_float_gt_zero(over_assigned_qty):
                line.tc_assignment_state = 'over_assigned'
            elif line._tc_float_le_zero(raw_pending_qty):
                line.tc_assignment_state = 'complete'
            elif line.tc_stock_rejected or line.auto_transit_assign:
                line.tc_assignment_state = 'to_purchase'
            elif line._tc_float_gt_zero(covered_qty):
                line.tc_assignment_state = 'partial'
            else:
                line.tc_assignment_state = 'open'

            if line._tc_float_le_zero(pending_qty):
                line.tc_allocation_hub_state = 'allocated'
            elif line.tc_stock_rejected or line.auto_transit_assign:
                line.tc_allocation_hub_state = 'to_be_purchased'
            elif line._tc_float_gt_zero(available_qty):
                line.tc_allocation_hub_state = 'to_be_allocated'
            else:
                line.tc_allocation_hub_state = 'to_be_purchased'

    # -------------------------------------------------------------------------
    # ASIGNACIÓN DESDE TO BE ALLOCATED
    # -------------------------------------------------------------------------

    def action_tc_apply_allocation_from_hub(self, lot_ids, breakdown=None):
        """
        Punto único para guardar asignaciones desde To Be Allocated.

        Regla:
        - product_uom_qty queda como Solicitado.
        - La selección solo actualiza lot_ids/x_lot_breakdown_json.
        - El pendiente se calcula como Solicitado - Cubierto.
        """
        result = {}

        for line in self:
            if line.display_type or not line.product_id:
                raise UserError(_('La línea seleccionada no es una línea de producto válida.'))

            if line.state not in ('sale', 'done'):
                raise UserError(_(
                    'Solo puede asignar lotes desde To Be Allocated cuando la cotización ya está confirmada como orden de venta.'
                ))

            assigned_qty, clean_breakdown, safe_lot_ids = line._tc_compute_assigned_qty_from_lots(
                lot_ids,
                breakdown=breakdown,
            )

            vals = {
                'lot_ids': [(6, 0, safe_lot_ids)],
            }

            if 'x_lot_breakdown_json' in line._fields:
                vals['x_lot_breakdown_json'] = line._tc_prepare_breakdown_value_for_line(clean_breakdown)

            if 'auto_transit_assign' in line._fields:
                vals['auto_transit_assign'] = False

            if 'tc_stock_rejected' in line._fields:
                vals.update({
                    'tc_stock_rejected': False,
                    'tc_stock_rejected_reason': False,
                    'tc_stock_rejected_by': False,
                    'tc_stock_rejected_at': False,
                })

            if line.tc_assignment_closed:
                vals.update({
                    'tc_assignment_closed': False,
                    'tc_closed_short_qty': 0.0,
                    'tc_closure_reason': False,
                    'tc_closure_by': False,
                    'tc_closure_at': False,
                })

            requested_qty = line.product_uom_qty or 0.0
            old_assigned_qty = line._tc_get_assigned_lot_qty()

            line.write(vals)

            pending_qty = line._tc_get_pending_allocation_qty()

            line.order_id.message_post(body=_(
                '✅ <b>Asignación aplicada desde To Be Allocated</b><br/>'
                'Producto: <b>%(product)s</b><br/>'
                'Solicitado: <b>%(requested).3f</b><br/>'
                'Asignado anterior: <b>%(old_assigned).3f</b><br/>'
                'Asignado actual: <b>%(assigned).3f</b><br/>'
                'Pendiente por asignar: <b>%(pending).3f</b><br/>'
                'Lotes asignados: <b>%(lots)s</b><br/>'
                '<small>La cantidad solicitada no fue modificada por la asignación.</small>'
            ) % {
                'product': line.product_id.display_name,
                'requested': requested_qty,
                'old_assigned': old_assigned_qty,
                'assigned': assigned_qty,
                'pending': pending_qty,
                'lots': len(safe_lot_ids),
            })

            line_uom = line._tc_get_line_uom()
            result = {
                'success': True,
                'sale_line_id': line.id,
                'requested_qty': requested_qty,
                'previous_assigned_qty': old_assigned_qty,
                'assigned_qty': assigned_qty,
                'pending_qty': pending_qty,
                'lot_ids': safe_lot_ids,
                'lot_count': len(safe_lot_ids),
                'uom_name': line_uom.display_name if line_uom else '',
            }

        return result

    # -------------------------------------------------------------------------
    # RECUPERACIÓN AUTOMÁTICA AL DESASIGNAR PLACAS
    # -------------------------------------------------------------------------

    def _tc_has_active_purchase_flow(self):
        self.ensure_one()

        Allocation = self.env['purchase.order.line.allocation'].sudo()
        allocation = Allocation.search([
            ('sale_line_id', '=', self.id),
            ('state', 'not in', ['cancelled', 'done']),
        ], order='id desc', limit=1)

        if not allocation:
            return False

        po = allocation.purchase_order_id
        if not po or po.state == 'cancel':
            return False

        return True

    def _tc_cancel_removed_lot_holds(self, removed_lot_ids):
        self.ensure_one()

        if not removed_lot_ids:
            return

        if 'stock.lot.hold' not in self.env.registry.models:
            return

        Quant = self.env['stock.quant'].sudo()
        Hold = self.env['stock.lot.hold'].sudo()

        quants = Quant.search([
            ('product_id', '=', self.product_id.id),
            ('lot_id', 'in', list(removed_lot_ids)),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ])

        if not quants:
            return

        domain = [
            ('quant_id', 'in', quants.ids),
            ('estado', '=', 'activo'),
        ]

        if 'partner_id' in Hold._fields and self.order_id and self.order_id.partner_id:
            domain.append(('partner_id', '=', self.order_id.partner_id.id))

        holds = Hold.search(domain)

        for hold in holds:
            try:
                if hasattr(hold, 'action_cancelar_hold'):
                    hold.action_cancelar_hold()
                elif 'estado' in hold._fields:
                    hold.write({'estado': 'cancelado'})
            except Exception as e:
                _logger.warning(
                    "[TC_ALLOCATION_RECOVERY] No se pudo cancelar hold de lote removido. "
                    "sale_line=%s hold=%s error=%s",
                    self.id,
                    hold.id,
                    e,
                    exc_info=True,
                )

    def _tc_release_removed_lots_from_pending_pickings(self, removed_lot_ids):
        self.ensure_one()

        if not removed_lot_ids:
            return

        Move = self.env['stock.move'].sudo()
        MoveLine = self.env['stock.move.line'].sudo()

        domain = [
            ('lot_id', 'in', list(removed_lot_ids)),
            ('product_id', '=', self.product_id.id),
            ('picking_id.state', 'not in', ['done', 'cancel']),
        ]

        if 'sale_line_id' in Move._fields:
            domain.append(('move_id.sale_line_id', '=', self.id))
        elif self.order_id and self.order_id.name:
            domain.append(('picking_id.origin', 'ilike', self.order_id.name))

        move_lines = MoveLine.search(domain)
        if not move_lines:
            return

        moves = move_lines.mapped('move_id')
        ctx = {
            'skip_stone_sync_so': True,
            'skip_stone_sync_picking': True,
            'skip_hold_validation': True,
            'skip_picking_clean': True,
            'skip_transit_sale_sync': True,
            'skip_procurement': True,
        }

        for move in moves:
            try:
                if move.state in ('assigned', 'partially_available') and hasattr(move, '_do_unreserve'):
                    move.with_context(ctx)._do_unreserve()
            except Exception as e:
                _logger.warning(
                    "[TC_ALLOCATION_RECOVERY] No se pudo desreservar move %s: %s",
                    move.id,
                    e,
                    exc_info=True,
                )

        remaining_move_lines = MoveLine.search(domain)
        if remaining_move_lines:
            try:
                remaining_move_lines.with_context(ctx).unlink()
            except Exception as e:
                _logger.warning(
                    "[TC_ALLOCATION_RECOVERY] No se pudieron eliminar move lines de lotes removidos. "
                    "sale_line=%s error=%s",
                    self.id,
                    e,
                    exc_info=True,
                )

    def _tc_release_removed_lots(self, removed_lot_ids):
        self.ensure_one()

        if not removed_lot_ids or not self.product_id:
            return

        self._tc_release_removed_lots_from_pending_pickings(removed_lot_ids)
        self._tc_cancel_removed_lot_holds(removed_lot_ids)

    def _tc_prepare_hub_state_for_read(self):
        for line in self:
            if (
                line.display_type
                or line.state not in ('sale', 'done')
                or not line.product_id
                or line.tc_assignment_closed
            ):
                continue

            pending_qty = line._tc_get_pending_allocation_qty()
            if line._tc_float_le_zero(pending_qty):
                continue

            if line._tc_has_active_purchase_flow():
                continue

            available_qty = line._tc_get_free_internal_qty()
            if line._tc_float_le_zero(available_qty):
                continue

            vals = {}

            if 'auto_transit_assign' in line._fields and line.auto_transit_assign:
                vals['auto_transit_assign'] = False

            if line.tc_stock_rejected:
                vals.update({
                    'tc_stock_rejected': False,
                    'tc_stock_rejected_reason': False,
                    'tc_stock_rejected_by': False,
                    'tc_stock_rejected_at': False,
                })

            if vals:
                line.with_context(skip_tc_allocation_recovery=True).write(vals)

    def _tc_after_lot_assignment_change(self, old_lots_by_line):
        for line in self:
            if (
                line.display_type
                or line.state not in ('sale', 'done')
                or not line.product_id
            ):
                continue

            old_lot_ids = old_lots_by_line.get(line.id, set())
            new_lot_ids = set(line.lot_ids.ids) if 'lot_ids' in line._fields else set()
            removed_lot_ids = old_lot_ids - new_lot_ids

            if removed_lot_ids:
                line._tc_release_removed_lots(removed_lot_ids)

            if line.tc_assignment_closed:
                continue

            pending_qty = line._tc_get_pending_allocation_qty()
            if line._tc_float_le_zero(pending_qty):
                continue

            if line._tc_has_active_purchase_flow():
                continue

            vals = {}

            if 'auto_transit_assign' in line._fields and line.auto_transit_assign:
                vals['auto_transit_assign'] = False

            if line.tc_stock_rejected:
                vals.update({
                    'tc_stock_rejected': False,
                    'tc_stock_rejected_reason': False,
                    'tc_stock_rejected_by': False,
                    'tc_stock_rejected_at': False,
                })

            if vals:
                line.with_context(skip_tc_allocation_recovery=True).write(vals)

    def write(self, vals):
        vals = dict(vals or {})

        allocation_sensitive_fields = {
            'lot_ids',
            'x_lot_breakdown_json',
            'product_uom_qty',
            'qty_delivered',
            'tc_assignment_closed',
            'tc_closed_short_qty',
        }

        must_recover = (
            not self.env.context.get('skip_tc_allocation_recovery')
            and bool(allocation_sensitive_fields.intersection(vals.keys()))
        )

        old_lots_by_line = {}

        if must_recover:
            for line in self:
                old_lots_by_line[line.id] = (
                    set(line.lot_ids.ids)
                    if 'lot_ids' in line._fields and line.lot_ids
                    else set()
                )

        if 'product_uom_qty' in vals and not self.env.context.get('skip_tc_qty_manual_reset'):
            vals.update({
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
            })

        res = super(SaleOrderLine, self).write(vals)

        if must_recover:
            self._tc_after_lot_assignment_change(old_lots_by_line)

        return res

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
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
            })

            line.order_id.message_post(body=_(
                '📌 <b>Mandar a pedir desde To Be Allocated</b><br/>'
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
                'auto_transit_assign': False,
            })
        return True

    def action_tc_close_allocation_short(self, reason=False):
        for line in self:
            if line.display_type:
                continue

            raw_pending_qty = line._tc_get_raw_pending_allocation_qty()

            if line._tc_float_le_zero(raw_pending_qty):
                raise UserError(_(
                    'La línea "%s" no tiene pendiente por cerrar.'
                ) % (line.product_id.display_name or line.name or line.id))

            close_reason = reason or _('Cierre manual de pendiente')

            line.with_context(
                skip_tc_allocation_recovery=True,
                skip_tc_qty_manual_reset=True,
            ).write({
                'tc_assignment_closed': True,
                'tc_closed_short_qty': raw_pending_qty,
                'tc_closure_reason': close_reason,
                'tc_closure_by': self.env.user.id,
                'tc_closure_at': fields.Datetime.now(),
                'tc_stock_rejected': False,
                'tc_stock_rejected_reason': False,
                'tc_stock_rejected_by': False,
                'tc_stock_rejected_at': False,
                'auto_transit_assign': False,
            })

            line.order_id.message_post(body=_(
                '🔒 <b>Pendiente de asignación cerrado</b><br/>'
                'Producto: <b>%(product)s</b><br/>'
                'Solicitado: <b>%(requested).3f</b><br/>'
                'Asignado: <b>%(assigned).3f</b><br/>'
                'Diferencia cerrada: <b>%(closed).3f</b><br/>'
                'Motivo: %(reason)s<br/>'
                '<small>La cantidad solicitada se mantiene intacta.</small>'
            ) % {
                'product': line.product_id.display_name,
                'requested': line.product_uom_qty or 0.0,
                'assigned': line._tc_get_assigned_lot_qty(),
                'closed': raw_pending_qty,
                'reason': close_reason,
            })

        return True

    def action_tc_reopen_allocation(self):
        for line in self:
            line.with_context(
                skip_tc_allocation_recovery=True,
                skip_tc_qty_manual_reset=True,
            ).write({
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
            })

            line.order_id.message_post(body=_(
                '🔓 <b>Asignación reabierta</b><br/>'
                'Producto: <b>%(product)s</b><br/>'
                'Solicitado: <b>%(requested).3f</b><br/>'
                'Asignado: <b>%(assigned).3f</b><br/>'
                'Pendiente actual: <b>%(pending).3f</b>'
            ) % {
                'product': line.product_id.display_name,
                'requested': line.product_uom_qty or 0.0,
                'assigned': line._tc_get_assigned_lot_qty(),
                'pending': line._tc_get_raw_pending_allocation_qty(),
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

            transit_line = self.env['stock.transit.line'].search([
                ('order_id', '=', line.order_id.id),
                ('product_id', '=', line.product_id.id),
            ], order='id desc', limit=1)

            if transit_line and transit_line.voyage_id:
                line.transit_status = transit_line.voyage_id.custom_status
                line.transit_eta = transit_line.voyage_id.eta
                line.transit_voyage_id = transit_line.voyage_id
                continue

            if allocation:
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
                    'No puede marcarse como "Mandar a pedir" si no queda cantidad pendiente.'
                ) % (line.product_id.display_name or ''))