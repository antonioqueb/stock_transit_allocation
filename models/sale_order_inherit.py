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

    por_asignar = fields.Boolean(
        string='Por Asignar',
        default=False,
        copy=False,
        help=(
            "Modo de cantidad manual, mutuamente excluyente con 'Mandar a pedir': "
            "permite editar la cantidad solicitada y la conserva SIEMPRE "
            "(no se sincroniza desde las placas, ni siquiera con ajuste forzado)."
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

    tc_closure_action = fields.Selection(
        selection=[
            ('settle', 'Liquidar sin ajustar cantidad'),
            ('discount', 'Aplicar descuento'),
            ('credit_note', 'Generar nota de crédito'),
        ],
        string='Acción administrativa cierre',
        copy=False,
        readonly=True,
        help=(
            'Define qué hará administración con el faltante cerrado: cobrarlo igual, '
            'compensarlo con descuento o generar una nota de crédito.'
        ),
    )

    tc_over_assignment_action = fields.Selection(
        selection=[
            ('free', 'Entregar excedente sin cobrar'),
            ('bill', 'Cobrar excedente'),
        ],
        string='Acción sobre exceso asignado',
        copy=False,
        readonly=True,
        help=(
            'Decisión administrativa cuando se asigna más cantidad que la solicitada. '
            'Si se entrega sin cobrar, se ajusta la cantidad y se aplica descuento equivalente al excedente. '
            'Si se cobra, se ajusta la cantidad solicitada a la cantidad asignada.'
        ),
    )

    tc_over_assignment_reason = fields.Text(
        string='Motivo sobreasignación',
        copy=False,
        readonly=True,
    )

    tc_over_assignment_by = fields.Many2one(
        'res.users',
        string='Sobreasignado por',
        copy=False,
        readonly=True,
    )

    tc_over_assignment_at = fields.Datetime(
        string='Fecha sobreasignación',
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


    def _tc_is_service_product(self):
        """Los hubs de asignación/compra no gestionan servicios."""
        self.ensure_one()

        product = self.product_id
        if not product:
            return False

        product_type = False

        if 'detailed_type' in product._fields:
            product_type = product.detailed_type
        elif 'type' in product._fields:
            product_type = product.type

        if product_type == 'service':
            return True

        template = product.product_tmpl_id
        if template:
            template_type = False

            if 'detailed_type' in template._fields:
                template_type = template.detailed_type
            elif 'type' in template._fields:
                template_type = template.type

            if template_type == 'service':
                return True

        return False

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
                or line._tc_is_service_product()
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
    # AJUSTES COMERCIALES POR DIFERENCIAS DE ASIGNACIÓN
    # -------------------------------------------------------------------------

    def _tc_get_discount_percent(self):
        self.ensure_one()
        if 'discount' not in self._fields:
            return 0.0
        try:
            return float(self.discount or 0.0)
        except Exception:
            return 0.0

    def _tc_require_discount_field(self):
        self.ensure_one()
        if 'discount' not in self._fields:
            raise UserError(_(
                'No se puede aplicar descuento automático porque sale.order.line no tiene el campo discount. '
                'Active el descuento en ventas o adapte este método al campo de descuento usado por la empresa.'
            ))

    def _tc_compute_discount_to_charge_qty(self, billing_qty, charged_qty, base_discount=None):
        """
        Calcula el descuento total de la línea para que una cantidad facturable
        mayor conserve el cobro económico de una cantidad menor.

        Ejemplo sin descuento previo:
        - billing_qty = 7.00
        - charged_qty = 6.55
        Resultado: descuento que deja el subtotal equivalente a 6.55 unidades.
        """
        self.ensure_one()

        billing_qty = float(billing_qty or 0.0)
        charged_qty = float(charged_qty or 0.0)

        if billing_qty <= 0:
            return 0.0

        charged_qty = max(0.0, min(charged_qty, billing_qty))

        if base_discount is None:
            base_discount = self._tc_get_discount_percent()

        base_discount = max(0.0, min(float(base_discount or 0.0), 100.0))
        base_factor = 1.0 - (base_discount / 100.0)

        target_factor = (charged_qty / billing_qty) * base_factor
        new_discount = (1.0 - target_factor) * 100.0

        return max(0.0, min(new_discount, 100.0))

    def _tc_prepare_discount_equivalent_result(self, billing_qty, charged_qty, base_discount=None):
        self.ensure_one()
        self._tc_require_discount_field()

        old_discount = self._tc_get_discount_percent() if base_discount is None else float(base_discount or 0.0)
        new_discount = self._tc_compute_discount_to_charge_qty(
            billing_qty=billing_qty,
            charged_qty=charged_qty,
            base_discount=old_discount,
        )

        return {
            'discount_before': old_discount,
            'discount_after': new_discount,
            'discount_applied': True,
        }

    def _tc_apply_over_assignment_admin_action(self, assigned_qty, requested_qty, over_assigned_qty, action, reason=False):
        """
        Ejecuta la consecuencia real de una sobreasignación.

        - free: ajusta product_uom_qty a lo asignado y aplica descuento para que
          el subtotal económico conserve el cobro de la cantidad originalmente solicitada.
        - bill: ajusta product_uom_qty a lo asignado y conserva el descuento actual,
          por lo que el excedente queda cobrado.
        """
        self.ensure_one()

        assigned_qty = float(assigned_qty or 0.0)
        requested_qty = float(requested_qty or 0.0)
        over_assigned_qty = float(over_assigned_qty or 0.0)

        result = {
            'qty_before': requested_qty,
            'qty_after': requested_qty,
            'discount_before': self._tc_get_discount_percent(),
            'discount_after': self._tc_get_discount_percent(),
            'discount_applied': False,
            'qty_updated': False,
            'action_label': 'No aplica',
        }

        if over_assigned_qty <= 0:
            return result

        if action not in ('free', 'bill'):
            raise UserError(_(
                'La asignación excede lo solicitado por %.3f. '
                'Debe indicar si el excedente se entrega sin cobrar o si se cobrará al cliente.'
            ) % over_assigned_qty)

        vals = {
            'product_uom_qty': assigned_qty,
        }

        result.update({
            'qty_after': assigned_qty,
            'qty_updated': True,
            'action_label': dict(self._fields['tc_over_assignment_action'].selection).get(action, action),
        })

        if action == 'free':
            discount_result = self._tc_prepare_discount_equivalent_result(
                billing_qty=assigned_qty,
                charged_qty=requested_qty,
                base_discount=result['discount_before'],
            )
            vals['discount'] = discount_result['discount_after']
            result.update(discount_result)

        self.with_context(
            skip_tc_allocation_recovery=True,
            skip_tc_qty_manual_reset=True,
        ).write(vals)

        return result

    def _tc_apply_short_close_discount(self, short_qty):
        """
        Aplica descuento equivalente al faltante cerrado, conservando product_uom_qty.
        El subtotal resultante equivale a cobrar solo la cantidad cubierta/asignada.
        """
        self.ensure_one()

        requested_qty = float(self.product_uom_qty or 0.0)
        short_qty = float(short_qty or 0.0)
        charged_qty = max(requested_qty - short_qty, 0.0)

        discount_result = self._tc_prepare_discount_equivalent_result(
            billing_qty=requested_qty,
            charged_qty=charged_qty,
            base_discount=self._tc_get_discount_percent(),
        )

        self.with_context(
            skip_tc_allocation_recovery=True,
            skip_tc_qty_manual_reset=True,
        ).write({
            'discount': discount_result['discount_after'],
        })

        return discount_result

    # -------------------------------------------------------------------------
    # ASIGNACIÓN DESDE TO BE ALLOCATED
    # -------------------------------------------------------------------------

    def action_tc_apply_allocation_from_hub(
        self,
        lot_ids,
        breakdown=None,
        send_pending_to_purchase=False,
        reason=False,
        over_assignment_action=False,
        over_assignment_reason=False,
        force_qty_to_selection=False,
    ):
        """
        Punto único para guardar asignaciones desde To Be Allocated.

        Reglas centrales:
        - La selección actualiza lot_ids/x_lot_breakdown_json.
        - Sin 'Mandar a pedir', la cantidad (Solicitado) sigue a las placas con
          regla RATCHET: sube al asignar más, pero NO baja sola al quitar placas
          (se conserva lo más alto, p.ej. al reemplazar una placa rota).
        - force_qty_to_selection=True ('Ajustar cantidad a la selección'):
          iguala la cantidad a la selección actual AUNQUE SEA MENOR (cuando el
          cliente ya no quiso la placa y no hay reemplazo).
        - Si se manda el restante a compra y se asigna de más, se exige decisión
          administrativa (free/bill).
        """
        result = {}

        # El ajuste explícito a la selección es excluyente con mandar a compra:
        # al igualar la cantidad a lo asignado no queda pendiente que comprar.
        if force_qty_to_selection:
            send_pending_to_purchase = False

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

            requested_qty_before = line.product_uom_qty or 0.0
            old_assigned_qty = line._tc_get_assigned_lot_qty()

            purchase_intent_before = bool(
                getattr(line, 'auto_transit_assign', False)
                or getattr(line, 'tc_stock_rejected', False)
            )
            purchase_intent_after = bool(purchase_intent_before or send_pending_to_purchase)

            # La sobreasignación solo tiene sentido en modo compra ('Mandar a pedir'),
            # donde existe una demanda manual independiente de las placas.
            # Sin 'Mandar a pedir' la cantidad SIGUE a las placas (Solicitado = Asignado),
            # por lo que no hay excedente que cobrar/regalar: la derivación de cantidad
            # en write() ajustará product_uom_qty a lo asignado.
            over_assigned_qty = max(assigned_qty - requested_qty_before, 0.0) if requested_qty_before > 0 else assigned_qty
            has_over_assignment = (
                purchase_intent_after
                and not force_qty_to_selection
                and line._tc_float_gt_zero(over_assigned_qty)
            )

            # Sin modo compra el excedente no aplica: la cantidad seguirá a las placas.
            if not has_over_assignment:
                over_assigned_qty = 0.0

            if has_over_assignment and over_assignment_action not in ('free', 'bill'):
                raise UserError(_(
                    'La asignación excede lo solicitado por %(qty).3f. '
                    'Debe indicar si el excedente se entrega sin cobrar o si se cobrará al cliente.'
                ) % {'qty': over_assigned_qty})

            if has_over_assignment and over_assignment_action == 'free':
                line._tc_require_discount_field()

            vals = {
                'lot_ids': [(6, 0, safe_lot_ids)],
                'tc_over_assignment_action': over_assignment_action if has_over_assignment else False,
                'tc_over_assignment_reason': over_assignment_reason if has_over_assignment else False,
                'tc_over_assignment_by': self.env.user.id if has_over_assignment else False,
                'tc_over_assignment_at': fields.Datetime.now() if has_over_assignment else False,
            }

            # En modo compra, fijar la intención JUNTO con las placas para que la
            # derivación de cantidad respete la demanda manual y conserve el pendiente
            # que debe irse a compra. Sin modo compra, la cantidad seguirá a las placas.
            if purchase_intent_after and not force_qty_to_selection:
                vals['auto_transit_assign'] = True

            if 'x_lot_breakdown_json' in line._fields:
                vals['x_lot_breakdown_json'] = line._tc_prepare_breakdown_value_for_line(clean_breakdown)

            if line.tc_assignment_closed:
                vals.update({
                    'tc_assignment_closed': False,
                    'tc_closed_short_qty': 0.0,
                    'tc_closure_reason': False,
                    'tc_closure_action': False,
                    'tc_closure_by': False,
                    'tc_closure_at': False,
                })

            # Si no había intención de compra y tampoco se pidió mandar el restante,
            # se limpia cualquier residuo técnico para que el pendiente siga en TBA.
            if not purchase_intent_after:
                if 'auto_transit_assign' in line._fields:
                    vals['auto_transit_assign'] = False

                if 'tc_stock_rejected' in line._fields:
                    vals.update({
                        'tc_stock_rejected': False,
                        'tc_stock_rejected_reason': False,
                        'tc_stock_rejected_by': False,
                        'tc_stock_rejected_at': False,
                    })

            line.with_context(
                skip_tc_allocation_recovery=True,
                tc_force_qty_to_selection=force_qty_to_selection,
            ).write(vals)

            over_admin_result = {
                'qty_before': requested_qty_before,
                'qty_after': line.product_uom_qty or 0.0,
                'discount_before': line._tc_get_discount_percent(),
                'discount_after': line._tc_get_discount_percent(),
                'discount_applied': False,
                'qty_updated': False,
                'action_label': 'No aplica',
            }

            if has_over_assignment:
                over_admin_result = line._tc_apply_over_assignment_admin_action(
                    assigned_qty=assigned_qty,
                    requested_qty=requested_qty_before,
                    over_assigned_qty=over_assigned_qty,
                    action=over_assignment_action,
                    reason=over_assignment_reason,
                )

            pending_qty = line._tc_get_pending_allocation_qty()
            sent_to_purchase = False
            purchase_qty = 0.0

            if purchase_intent_after and line._tc_float_gt_zero(pending_qty):
                purchase_qty = pending_qty
                sent_to_purchase = True

                purchase_reason = reason or line.tc_stock_rejected_reason or _(
                    'Pendiente restante enviado a compra por decisión del vendedor.'
                )

                line.with_context(skip_tc_allocation_recovery=True).write({
                    'tc_stock_rejected': True,
                    'tc_stock_rejected_reason': purchase_reason,
                    'tc_stock_rejected_by': self.env.user.id,
                    'tc_stock_rejected_at': fields.Datetime.now(),
                    'auto_transit_assign': True,
                    'tc_assignment_closed': False,
                    'tc_closed_short_qty': 0.0,
                    'tc_closure_reason': False,
                    'tc_closure_action': False,
                    'tc_closure_by': False,
                    'tc_closure_at': False,
                })

                pending_qty = line._tc_get_pending_allocation_qty()

            # Nota: si la línea queda totalmente cubierta NO se apaga 'Mandar a
            # pedir' ni el rechazo de stock. Es intención explícita del vendedor y
            # solo él la quita ('Revisar stock'). La cobertura se refleja en
            # hub_state/assignment_state.

            requested_qty_after = line.product_uom_qty or 0.0
            discount_after = line._tc_get_discount_percent()

            if has_over_assignment and over_assignment_action == 'free':
                commercial_note = _(
                    'Se ajustó la cantidad solicitada a %.3f y se aplicó descuento equivalente al excedente para no cobrarlo.'
                ) % requested_qty_after
            elif has_over_assignment and over_assignment_action == 'bill':
                commercial_note = _(
                    'Se ajustó la cantidad solicitada a %.3f para cobrar el excedente asignado.'
                ) % requested_qty_after
            else:
                commercial_note = _('La cantidad solicitada no fue modificada por la asignación.')

            line._tc_post_plain_message(
                _('✅ Asignación aplicada desde To Be Allocated'),
                [
                    _('Producto: %s') % (line.product_id.display_name or ''),
                    _('Solicitado anterior: %.3f') % requested_qty_before,
                    _('Solicitado actual: %.3f') % requested_qty_after,
                    _('Asignado anterior: %.3f') % old_assigned_qty,
                    _('Asignado actual: %.3f') % assigned_qty,
                    _('Pendiente operativo: %.3f') % pending_qty,
                    _('Pendiente enviado a compra: %.3f') % (purchase_qty if sent_to_purchase else 0.0),
                    _('Sobreasignado: %.3f') % over_assigned_qty,
                    _('Acción sobre exceso: %s') % over_admin_result.get('action_label', 'No aplica'),
                    _('Descuento anterior: %.4f%%') % over_admin_result.get('discount_before', 0.0),
                    _('Descuento actual: %.4f%%') % discount_after,
                    _('Nota administrativa: %s') % (over_assignment_reason or 'N/A'),
                    _('Lotes asignados: %s') % len(safe_lot_ids),
                    commercial_note,
                ],
            )

            line_uom = line._tc_get_line_uom()
            result = {
                'success': True,
                'sale_line_id': line.id,
                'requested_qty': requested_qty_after,
                'requested_qty_before': requested_qty_before,
                'previous_assigned_qty': old_assigned_qty,
                'assigned_qty': assigned_qty,
                'pending_qty': pending_qty,
                'sent_to_purchase': sent_to_purchase,
                'purchase_qty': purchase_qty,
                'over_assigned_qty': over_assigned_qty,
                'over_assignment_action': over_assignment_action if has_over_assignment else False,
                'discount_before': over_admin_result.get('discount_before', 0.0),
                'discount_after': discount_after,
                'discount_applied': over_admin_result.get('discount_applied', False),
                'qty_updated': over_admin_result.get('qty_updated', False),
                'lot_ids': safe_lot_ids,
                'lot_count': len(safe_lot_ids),
                'uom_name': line_uom.display_name if line_uom else '',
                'assignment_state': line.tc_assignment_state,
                'hub_state': line.tc_allocation_hub_state,
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
        """
        Normalización antes de leer los hubs.

        Regla de negocio:
        - auto_transit_assign / tc_stock_rejected son intención EXPLÍCITA del
          vendedor y NUNCA se limpian de forma automática, ni siquiera cuando la
          línea queda totalmente cubierta por placas. Solo el usuario las quita
          (botón 'Revisar stock'). El estado de cobertura se refleja en
          hub_state/assignment_state, no apagando el flag.

        Por eso este método ya no modifica nada (se conserva por compatibilidad).
        """
        return

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

            # IMPORTANTE: 'Mandar a pedir' (auto_transit_assign) y el rechazo de
            # stock son intención EXPLÍCITA del vendedor. NO se apagan solos al
            # asignar placas, aunque la línea quede totalmente cubierta. Si el
            # usuario marcó 'Mandar a pedir', se mantiene marcado; solo él lo quita
            # con el botón 'Revisar stock'. El hub ya muestra la línea como cubierta
            # vía hub_state='allocated' cuando no queda pendiente, sin tocar el flag.

    def write(self, vals):
        vals = dict(vals or {})

        # 'Mandar a pedir' y 'Por Asignar' son modos mutuamente excluyentes:
        # activar uno apaga el otro, también a nivel de datos (no solo en UI).
        if vals.get('por_asignar'):
            vals['auto_transit_assign'] = False
        elif vals.get('auto_transit_assign'):
            vals['por_asignar'] = False

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

        # ---------------------------------------------------------------------
        # REGLA DE MODO (Mandar a pedir):
        # Cuando 'Mandar a pedir' está apagado, la cantidad vendible se fija por
        # las placas. Por eso, tras escribir, hay que igualar product_uom_qty a
        # la suma asignada en las líneas SIN intención de compra cuando:
        #   - cambian las placas/desglose (lot_ids / x_lot_breakdown_json), o
        #   - se APAGA 'Mandar a pedir' (se deja de pedir el restante).
        # El propio sync se reentra con tc_qty_sync_from_lots para no recursar.
        # ---------------------------------------------------------------------
        is_qty_sync = self.env.context.get('tc_qty_sync_from_lots')
        lots_in_vals = ('lot_ids' in vals) or ('x_lot_breakdown_json' in vals)
        unchecking_purchase = (
            'auto_transit_assign' in vals and not vals.get('auto_transit_assign')
        )

        qty_derive_line_ids = set()
        if not is_qty_sync and (lots_in_vals or unchecking_purchase):
            for line in self:
                if line.display_type or not line.product_id:
                    continue
                qty_derive_line_ids.add(line.id)

        if 'product_uom_qty' in vals and not self.env.context.get('skip_tc_qty_manual_reset'):
            vals.update({
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_action': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
                'tc_over_assignment_action': False,
                'tc_over_assignment_reason': False,
                'tc_over_assignment_by': False,
                'tc_over_assignment_at': False,
            })

        res = super(SaleOrderLine, self).write(vals)

        if must_recover:
            self._tc_after_lot_assignment_change(old_lots_by_line)

        if qty_derive_line_ids:
            self.browse(qty_derive_line_ids).exists()._tc_sync_requested_qty_from_lots()

        return res

    def _tc_sync_requested_qty_from_lots(self):
        """
        Ajusta product_uom_qty (Solicitado) en función de las placas.

        Tres modos:
        - 'Por Asignar' (por_asignar): la cantidad escrita se conserva SIEMPRE;
          nunca se deriva de las placas, ni siquiera con ajuste forzado.
        - 'Mandar a pedir' (auto_transit_assign): RATCHET. La demanda manual SUBE
          si se asigna de más que lo solicitado (es lo que se va a cobrar), pero
          se MANTIENE si se asigna de menos. Nunca baja por las placas ni por el
          ajuste forzado (su demanda manual se respeta).
        - Sin ninguno de los dos: RATCHET igual (sube al asignar más, no baja al
          quitar placas) y además admite el ajuste forzado a la baja
          (tc_force_qty_to_selection / 'Ajustar a selección') para igualar la
          cantidad a la selección AUNQUE SEA MENOR.
        """
        force = self.env.context.get('tc_force_qty_to_selection')

        for line in self:
            if line.display_type or not line.product_id:
                continue

            # 'Por Asignar': la cantidad escrita se conserva SIEMPRE; nunca se
            # deriva de las placas, ni siquiera con ajuste forzado explícito.
            if line.por_asignar:
                continue

            assigned_qty = line._tc_get_assigned_lot_qty()
            rounding = line._tc_get_qty_rounding()
            current_qty = line.product_uom_qty or 0.0

            # El ajuste forzado a la baja solo aplica a líneas SIN modo manual.
            # 'Mandar a pedir' conserva su demanda manual: solo CRECE al asignar
            # de más; nunca baja (ni por placas ni por ajuste forzado).
            allow_force_down = force and not line.auto_transit_assign

            if allow_force_down:
                # Ajuste explícito: la demanda se iguala a la selección, aun si baja.
                target_qty = assigned_qty
            else:
                # Ratchet: sube al asignar de más; nunca baja por desasignar placas.
                target_qty = (
                    assigned_qty
                    if float_compare(assigned_qty, current_qty, precision_rounding=rounding) > 0
                    else current_qty
                )

            if float_compare(current_qty, target_qty, precision_rounding=rounding) == 0:
                continue

            line.with_context(
                tc_qty_sync_from_lots=True,
                skip_tc_qty_manual_reset=True,
                skip_tc_allocation_recovery=True,
            ).write({'product_uom_qty': target_qty})

    def _tc_post_plain_message(self, title, lines=None):
        """
        Publica mensajes operativos en texto plano.

        En algunos registros/vistas de Odoo 19 el chatter no renderiza HTML
        y muestra etiquetas como <b> o <br/> literalmente. Por eso este helper
        evita HTML en logs operativos.
        """
        self.ensure_one()

        clean_lines = []
        for item in lines or []:
            if item is None or item is False:
                continue
            text = str(item).strip()
            if text:
                clean_lines.append(text)

        body = "\n".join([str(title).strip()] + clean_lines)

        self.order_id.message_post(
            body=body,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    # -------------------------------------------------------------------------
    # ACCIONES HUB
    # -------------------------------------------------------------------------

    def _tc_cancel_active_purchase_flow_from_tbp(self, reason=False):
        """
        Limpia la intención de compra vinculada a una línea cerrada desde
        To Be Purchased.

        Reglas conservadoras:
        - Cancela allocations activas de la línea de venta.
        - Si la OC sigue en borrador/enviada, reduce la línea de compra por la
          cantidad cancelada, sin bajar de las allocations que sigan activas.
        - Si la OC ya está confirmada, NO modifica la OC; solo cancela la
          allocation para que el material llegue como stock libre.
        - Si ya existen líneas de tránsito no entregadas para esa allocation,
          se liberan como disponibles.
        """
        self.ensure_one()

        Allocation = self.env['purchase.order.line.allocation'].sudo()
        allocations = Allocation.search([
            ('sale_line_id', '=', self.id),
            ('state', 'not in', ['cancelled', 'done']),
        ], order='id asc')

        result = {
            'allocation_count': 0,
            'cancelled_qty': 0.0,
            'po_names': [],
            'draft_po_adjusted': 0,
            'confirmed_po_untouched': 0,
            'transit_lines_released': 0,
        }

        if not allocations:
            return result

        po_names = set()

        for allocation in allocations:
            allocation_qty = allocation.quantity or 0.0
            po_line = allocation.purchase_line_id
            po = allocation.purchase_order_id

            result['allocation_count'] += 1
            result['cancelled_qty'] += allocation_qty

            if po:
                po_names.add(po.name)

            allocation.write({'state': 'cancelled'})

            if po_line and po_line.exists() and po and po.exists():
                if po.state in ('draft', 'sent'):
                    active_allocations = po_line.allocation_ids.filtered(
                        lambda alloc: alloc.state not in ('cancelled', 'done')
                    )
                    active_qty = sum(active_allocations.mapped('quantity'))
                    reduced_qty = max((po_line.product_qty or 0.0) - allocation_qty, 0.0)
                    target_qty = max(active_qty, reduced_qty)

                    if target_qty > 0:
                        po_line.write({'product_qty': target_qty})
                    elif (po_line.qty_received or 0.0) <= 0:
                        po_line.unlink()
                    else:
                        po_line.write({'product_qty': po_line.qty_received})

                    result['draft_po_adjusted'] += 1

                    po.message_post(body=(
                        '🧹 <b>Pendiente de compra cancelado desde To Be Purchased</b><br/>'
                        'Pedido: <b>%s</b><br/>'
                        'Producto: <b>%s</b><br/>'
                        'Cantidad cancelada: <b>%.3f</b><br/>'
                        'Motivo: %s'
                    ) % (
                        self.order_id.name,
                        self.product_id.display_name,
                        allocation_qty,
                        reason or 'Pendiente cancelado desde To Be Purchased.',
                    ))
                else:
                    result['confirmed_po_untouched'] += 1
                    po.message_post(body=(
                        '🧹 <b>Allocation cancelada desde To Be Purchased</b><br/>'
                        'Pedido: <b>%s</b><br/>'
                        'Producto: <b>%s</b><br/>'
                        'Cantidad cancelada de la allocation: <b>%.3f</b><br/>'
                        'La OC ya está confirmada; no se modificó la compra. '
                        'El material relacionado deberá quedar como stock libre al recibirse.<br/>'
                        'Motivo: %s'
                    ) % (
                        self.order_id.name,
                        self.product_id.display_name,
                        allocation_qty,
                        reason or 'Pendiente cancelado desde To Be Purchased.',
                    ))

            TransitLine = self.env['stock.transit.line'].sudo()
            transit_lines = TransitLine.search([
                ('allocation_id', '=', allocation.id),
                ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
            ])

            if transit_lines:
                for transit_line in transit_lines:
                    try:
                        transit_line._execute_release_logic()
                    except Exception as e:
                        _logger.warning(
                            '[TC_TBP_CANCEL] No se pudo ejecutar release en stock.transit.line %s: %s',
                            transit_line.id,
                            e,
                            exc_info=True,
                        )

                transit_lines.with_context(
                    skip_reservation_logic=True,
                    skip_transit_publication_sync=False,
                ).write({
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'notes': (reason or 'Liberado por cancelación desde To Be Purchased'),
                })

                result['transit_lines_released'] += len(transit_lines)

        result['po_names'] = sorted(po_names)
        return result

    def action_tc_cancel_purchase_pending(self, reason=False, closure_action=False):
        """
        Cancela el pendiente operativo desde To Be Purchased.

        Solo permite:
        - settle: cancelar sin descuento.
        - discount: cancelar aplicando descuento equivalente.

        No usa credit_note en este hub.
        """
        valid_actions = {'settle', 'discount'}
        action_value = closure_action or 'settle'

        if action_value not in valid_actions:
            raise UserError(_('Seleccione una acción válida para cancelar el pendiente de compra.'))

        for line in self:
            if line.display_type:
                continue

            raw_pending_qty = line._tc_get_raw_pending_allocation_qty()

            if line._tc_float_le_zero(raw_pending_qty):
                raise UserError(_(
                    'La línea "%s" no tiene pendiente de compra por cancelar.'
                ) % (line.product_id.display_name or line.name or line.id))

            close_reason = reason or _('Pendiente de compra cancelado desde To Be Purchased.')
            cleanup_result = line._tc_cancel_active_purchase_flow_from_tbp(reason=close_reason)

            line.action_tc_close_allocation_short(
                reason=close_reason,
                closure_action=action_value,
            )

            action_label = 'Cancelar sin descuento' if action_value == 'settle' else 'Cancelar aplicando descuento'

            line._tc_post_plain_message(
                _('🧹 Pendiente de compra cancelado desde To Be Purchased'),
                [
                    _('Producto: %s') % (line.product_id.display_name or ''),
                    _('Pedido: %s') % (line.order_id.name or ''),
                    _('Pendiente cancelado: %.3f') % raw_pending_qty,
                    _('Acción: %s') % action_label,
                    _('Allocations canceladas: %s') % cleanup_result.get('allocation_count', 0),
                    _('Cantidad cancelada en compras: %.3f') % cleanup_result.get('cancelled_qty', 0.0),
                    _('OC relacionadas: %s') % (', '.join(cleanup_result.get('po_names') or []) or 'N/A'),
                    _('Líneas de tránsito liberadas: %s') % cleanup_result.get('transit_lines_released', 0),
                    _('Motivo: %s') % close_reason,
                ],
            )

        return True

    def action_tc_send_to_purchase(self, reason=False):
        for line in self:
            if line.display_type:
                continue

            if line._tc_is_service_product():
                raise UserError(_(
                    'La línea "%s" es un servicio. Los servicios no se gestionan en To Be Allocated ni To Be Purchased.'
                ) % (line.product_id.display_name or line.name or line.id))

            pending_qty = line._tc_get_pending_allocation_qty()

            if line._tc_float_le_zero(pending_qty):
                raise UserError(_(
                    'La línea "%s" ya no tiene cantidad pendiente para mandar a pedir.'
                ) % (line.product_id.display_name or line.name or line.id))

            already_sent_to_purchase = bool(
                line.tc_stock_rejected
                and line.auto_transit_assign
            )

            previous_reason = line.tc_stock_rejected_reason or ''
            new_reason = reason or previous_reason or ''

            line.with_context(skip_tc_allocation_recovery=True).write({
                'tc_stock_rejected': True,
                'tc_stock_rejected_reason': new_reason,
                'tc_stock_rejected_by': self.env.user.id,
                'tc_stock_rejected_at': fields.Datetime.now(),
                'auto_transit_assign': True,
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_action': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
            })

            # Evita duplicar logs si el botón se ejecuta dos veces o si la línea
            # ya estaba marcada para compra.
            if already_sent_to_purchase and new_reason == previous_reason:
                continue

            line._tc_post_plain_message(
                _('📌 Mandar a pedir desde To Be Allocated'),
                [
                    _('Producto: %s') % (line.product_id.display_name or ''),
                    _('Cantidad pendiente: %.3f') % pending_qty,
                    _('El inventario disponible fue rechazado por el vendedor; compras debe generar o mantener la OC.'),
                ],
            )

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

    def action_tc_adjust_qty_to_selection(self):
        """
        Ajuste manual desde la propia línea de la orden de venta:
        iguala el Solicitado (product_uom_qty) a la cantidad EFECTIVAMENTE
        asignada por placas, AUNQUE SEA MENOR.

        Es el mismo override que 'Ajustar cantidad a la selección' de To Be
        Allocated, pero accionable directo en la línea. Útil cuando la regla
        ratchet mantuvo la demanda alta (no baja sola al quitar placas) y el
        cliente finalmente quiso solo lo seleccionado.
        """
        for line in self:
            if line.display_type or not line.product_id:
                continue

            before = line.product_uom_qty or 0.0
            line.with_context(tc_force_qty_to_selection=True)._tc_sync_requested_qty_from_lots()
            after = line.product_uom_qty or 0.0

            if float_compare(before, after, precision_rounding=line._tc_get_qty_rounding()) != 0:
                line._tc_post_plain_message(
                    _('✏️ Cantidad ajustada a la selección'),
                    [
                        _('Producto: %s') % (line.product_id.display_name or ''),
                        _('Solicitado anterior: %.3f') % before,
                        _('Solicitado actual: %.3f') % after,
                        _('Asignado: %.3f') % line._tc_get_assigned_lot_qty(),
                    ],
                )
        return True

    def action_tc_close_allocation_short(self, reason=False, closure_action=False):
        valid_actions = {'settle', 'discount', 'credit_note'}
        action_value = closure_action or 'settle'

        if action_value not in valid_actions:
            raise UserError(_('Seleccione una acción administrativa válida para cerrar el pendiente.'))

        for line in self:
            if line.display_type:
                continue

            raw_pending_qty = line._tc_get_raw_pending_allocation_qty()

            if line._tc_float_le_zero(raw_pending_qty):
                raise UserError(_(
                    'La línea "%s" no tiene pendiente por cerrar.'
                ) % (line.product_id.display_name or line.name or line.id))

            close_reason = reason or _('Cierre manual de pendiente')
            action_label = dict(line._fields['tc_closure_action'].selection).get(action_value, action_value)
            requested_qty = line.product_uom_qty or 0.0
            assigned_qty = line._tc_get_assigned_lot_qty()

            discount_result = {
                'discount_before': line._tc_get_discount_percent(),
                'discount_after': line._tc_get_discount_percent(),
                'discount_applied': False,
            }

            if action_value == 'discount':
                discount_result = line._tc_apply_short_close_discount(raw_pending_qty)

            line.with_context(
                skip_tc_allocation_recovery=True,
                skip_tc_qty_manual_reset=True,
            ).write({
                'tc_assignment_closed': True,
                'tc_closed_short_qty': raw_pending_qty,
                'tc_closure_reason': close_reason,
                'tc_closure_action': action_value,
                'tc_closure_by': self.env.user.id,
                'tc_closure_at': fields.Datetime.now(),
                'tc_stock_rejected': False,
                'tc_stock_rejected_reason': False,
                'tc_stock_rejected_by': False,
                'tc_stock_rejected_at': False,
                'auto_transit_assign': False,
            })

            if action_value == 'discount':
                commercial_note = _(
                    'Se aplicó descuento equivalente al faltante cerrado. La cantidad solicitada se mantiene intacta.'
                )
            elif action_value == 'credit_note':
                commercial_note = _(
                    'Se registró la intención de nota de crédito. La generación contable debe ejecutarse desde el flujo administrativo.'
                )
            else:
                commercial_note = _('La cantidad solicitada se mantiene intacta y no se aplicó descuento.')

            line._tc_post_plain_message(
                _('🔒 Pendiente de asignación cerrado'),
                [
                    _('Producto: %s') % (line.product_id.display_name or ''),
                    _('Solicitado: %.3f') % requested_qty,
                    _('Asignado: %.3f') % assigned_qty,
                    _('Diferencia cerrada: %.3f') % raw_pending_qty,
                    _('Acción administrativa: %s') % action_label,
                    _('Descuento anterior: %.4f%%') % discount_result.get('discount_before', 0.0),
                    _('Descuento actual: %.4f%%') % discount_result.get('discount_after', 0.0),
                    _('Motivo: %s') % close_reason,
                    commercial_note,
                ],
            )

        return True

    def action_tc_reopen_allocation(self):
        for line in self:
            if not line.tc_assignment_closed:
                continue

            line.with_context(
                skip_tc_allocation_recovery=True,
                skip_tc_qty_manual_reset=True,
            ).write({
                'tc_assignment_closed': False,
                'tc_closed_short_qty': 0.0,
                'tc_closure_reason': False,
                'tc_closure_action': False,
                'tc_closure_by': False,
                'tc_closure_at': False,
            })

            line._tc_post_plain_message(
                _('🔓 Asignación reabierta'),
                [
                    _('Producto: %s') % (line.product_id.display_name or ''),
                    _('Solicitado: %.3f') % (line.product_uom_qty or 0.0),
                    _('Asignado: %.3f') % line._tc_get_assigned_lot_qty(),
                    _('Pendiente actual: %.3f') % line._tc_get_raw_pending_allocation_qty(),
                ],
            )

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
        """
        'Mandar a pedir' es el interruptor de modo y es libremente activable:
        - Al ACTIVARLO, la cantidad pasa a ser manual; no se modifica el valor
          actual (el usuario podrá editarlo).
        - Al DESACTIVARLO, la cantidad vuelve a regirse por las placas con regla
          RATCHET: solo sube si la selección supera la cantidad actual; nunca baja
          sola (para bajarla está 'Ajustar cantidad a la selección' en TBA).
        """
        if self.auto_transit_assign:
            # Mutuamente excluyente con 'Por Asignar'.
            if self.por_asignar:
                self.por_asignar = False
            return

        # En modo 'Por Asignar' la cantidad escrita se conserva: no ratchet.
        if self.por_asignar:
            return

        if self.display_type or not self.product_id:
            return

        assigned_qty = self._tc_get_assigned_lot_qty()
        rounding = self._tc_get_qty_rounding()

        if float_compare(
            assigned_qty,
            self.product_uom_qty or 0.0,
            precision_rounding=rounding,
        ) > 0:
            self.product_uom_qty = assigned_qty

    @api.onchange('por_asignar')
    def _onchange_por_asignar(self):
        """
        'Por Asignar' desbloquea la edición manual de la cantidad solicitada y
        es mutuamente excluyente con 'Mandar a pedir'. La cantidad escrita se
        conserva siempre: nunca se sincroniza desde las placas.
        """
        if self.por_asignar and self.auto_transit_assign:
            self.auto_transit_assign = False