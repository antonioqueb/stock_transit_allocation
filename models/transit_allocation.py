# -*- coding: utf-8 -*-
from collections import defaultdict
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

_logger = logging.getLogger(__name__)


class TransitAllocationLogic(models.AbstractModel):
    """
    Hub operativo para asignar inventario EN TRÁNSITO a líneas de venta.

    No crea un flujo paralelo de reservas. Usa stock.transit.line como fuente
    operativa y deja que sus hooks actuales sincronicen:
    - partner_id / order_id / allocation_status en stock.transit.line
    - lot_ids / x_lot_breakdown_json en sale.order.line
    - estado comercial de publicación en stock.quant de tránsito
    """
    _name = 'transit.allocation.manager.logic'
    _inherit = 'allocation.hub.payment.mixin'
    _description = 'Lógica para el Tablero Transit Allocation'

    # ---------------------------------------------------------------------
    # DOMINIOS PROPIOS DEL HUB
    # ---------------------------------------------------------------------

    def _tal_get_sale_line_domain(self):
        SaleLine = self.env['sale.order.line']
        domain = [
            ('state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
            ('product_id', '!=', False),
        ]

        if 'tc_assignment_closed' in SaleLine._fields:
            domain.append(('tc_assignment_closed', '=', False))

        if 'product_uom_qty' in SaleLine._fields:
            domain.append(('product_uom_qty', '>', 0))

        return domain

    def _tal_get_transit_line_domain(self, product_ids=None, available_only=True):
        domain = [
            ('lot_id', '!=', False),
            ('product_id', '!=', False),
            ('product_uom_qty', '>', 0),
            ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
        ]

        if product_ids:
            domain.append(('product_id', 'in', list(product_ids)))

        if available_only:
            domain += [
                ('allocation_status', '=', 'available'),
                ('partner_id', '=', False),
                ('order_id', '=', False),
            ]

        return domain

    def _tal_resolve_valid_transit_quant(self, transit_line):
        """Confirma que la línea realmente tenga stock físico en ubicación tránsito."""
        if not transit_line or not transit_line.exists():
            return False

        quant = transit_line.quant_id

        if (not quant or not quant.exists()) and hasattr(transit_line, '_tc_resolve_transit_quant'):
            try:
                quant = transit_line._tc_resolve_transit_quant()
            except Exception as e:
                _logger.warning(
                    '[TransitAllocation] No se pudo resolver quant para transit_line=%s: %s',
                    transit_line.id,
                    e,
                )
                quant = False

        if not quant or not quant.exists():
            return False

        if not quant.location_id._som_is_transit():
            return False

        if quant.quantity <= 0:
            return False

        if transit_line.company_id and quant.company_id and quant.company_id.id != transit_line.company_id.id:
            return False

        if quant.product_id.id != transit_line.product_id.id:
            return False

        if quant.lot_id.id != transit_line.lot_id.id:
            return False

        return quant

    # ---------------------------------------------------------------------
    # LECTURA DE DATOS
    # ---------------------------------------------------------------------

    def _tal_get_available_transit_lines_by_product(self, product_ids):
        result = defaultdict(lambda: self.env['stock.transit.line'])

        if not product_ids:
            return result

        TransitLine = self.env['stock.transit.line'].sudo()
        transit_lines = TransitLine.search(
            self._tal_get_transit_line_domain(product_ids=product_ids, available_only=True),
            order='eta asc, voyage_id asc, product_id asc, id asc',
        )

        for line in transit_lines:
            quant = self._tal_resolve_valid_transit_quant(line)
            if not quant:
                continue
            result[line.product_id.id] |= line

        return result

    def _tal_status_label(self, voyage):
        if not voyage:
            return ''
        return dict(voyage._fields['custom_status'].selection).get(voyage.custom_status, voyage.custom_status)

    def _tal_transit_line_qty(self, transit_line):
        quant = self._tal_resolve_valid_transit_quant(transit_line)
        if quant:
            qty = min(quant.quantity or 0.0, transit_line.product_uom_qty or 0.0)
        else:
            qty = transit_line.product_uom_qty or 0.0

        rounding = 0.0001
        product = transit_line.product_id
        if product and product.uom_id and product.uom_id.rounding:
            rounding = product.uom_id.rounding

        return float_round(qty, precision_rounding=rounding)

    def _tal_make_transit_line_row(self, transit_line):
        product = transit_line.product_id
        voyage = transit_line.voyage_id
        quant = self._tal_resolve_valid_transit_quant(transit_line)
        qty = self._tal_transit_line_qty(transit_line)
        unit_label = self._get_product_unit_label(product)
        qty_m2, qty_pieces = self._split_qty_by_unit(product, qty)

        lot = transit_line.lot_id

        return {
            'id': transit_line.id,
            'product_id': product.id,
            'product_name': product.display_name,
            'lot_id': lot.id if lot else False,
            'lot_name': lot.name if lot else '',
            'qty': qty,
            'qty_m2': qty_m2,
            'qty_pieces': qty_pieces,
            'unit_label': unit_label,
            'unit_kind': self._get_product_unit_kind(product),
            'voyage_id': voyage.id if voyage else False,
            'voyage_name': voyage.name if voyage else '',
            'voyage_status': voyage.custom_status if voyage else '',
            'voyage_status_label': self._tal_status_label(voyage),
            'eta': voyage.eta.strftime('%Y-%m-%d') if voyage and voyage.eta else '',
            'container_number': transit_line.container_number or voyage.container_number or '',
            'purchase_id': transit_line.purchase_id.id if transit_line.purchase_id else False,
            'purchase_name': transit_line.purchase_id.name if transit_line.purchase_id else '',
            'vendor': transit_line.vendor_id.name if transit_line.vendor_id else '',
            'location': quant.location_id.complete_name if quant and quant.location_id else '',
            'x_bloque': getattr(lot, 'x_bloque', '') or '',
            'x_atado': getattr(lot, 'x_atado', '') or '',
            'x_grosor': getattr(lot, 'x_grosor', '') or '',
            'x_alto': getattr(lot, 'x_alto', 0.0) or 0.0,
            'x_ancho': getattr(lot, 'x_ancho', 0.0) or 0.0,
            'x_color': getattr(lot, 'x_color', '') or '',
            # Tipo de lote para que el front habilite parcialidad solo en
            # formatos/piezas (fraccionables); las placas van enteras.
            'x_tipo': (str(lot.x_tipo).lower() if lot and 'x_tipo' in lot._fields and lot.x_tipo else ''),
        }

    def _tal_make_sale_line_row(self, sale_line, metrics, payment_percent, allocation_info):
        row = self._hub_make_sale_line_row(
            sale_line,
            metrics,
            payment_percent=payment_percent,
            allocation_info=allocation_info,
        )

        row.update({
            'qty_raw_pending': metrics.get('raw_pending_qty', 0.0),
            'qty_transit_pending': metrics.get('pending_qty', 0.0),
            'purchase_intent': bool(metrics.get('purchase_intent')),
            'transit_status': sale_line.transit_status if 'transit_status' in sale_line._fields else '',
            'transit_eta': sale_line.transit_eta.strftime('%Y-%m-%d') if getattr(sale_line, 'transit_eta', False) else '',
            'transit_voyage_id': sale_line.transit_voyage_id.id if getattr(sale_line, 'transit_voyage_id', False) else False,
            'transit_voyage_name': sale_line.transit_voyage_id.name if getattr(sale_line, 'transit_voyage_id', False) else '',
        })

        row.update(self._split_qty_fields(sale_line.product_id, 'qty_raw_pending', row['qty_raw_pending']))
        row.update(self._split_qty_fields(sale_line.product_id, 'qty_transit_pending', row['qty_transit_pending']))
        return row

    @api.model
    def _tal_get_reserved_extra_by_line(self, sale_lines, metrics_by_line):
        """m² YA RESERVADOS en tránsito para cada línea que el cálculo de
        asignado no ve (reservas sin lote, o con lote sin medidas): sin
        esto, una orden 100%% preasignada seguía apareciendo en el hub
        con toda su demanda 'pendiente'.

        Anti doble conteo: si el lote de la reserva ya está en lot_ids de
        la línea Y aporta medidas (eso ya lo cuenta assigned_qty), la
        reserva no se vuelve a sumar. Si varias líneas comparten orden y
        producto, la reserva se reparte sin exceder el pendiente de cada
        una."""
        if not sale_lines:
            return {}
        TransitLine = self.env['stock.transit.line'].sudo()
        tls = TransitLine.search([
            ('order_id', 'in', sale_lines.mapped('order_id').ids),
            ('allocation_status', '=', 'reserved'),
            ('voyage_id.custom_status', 'not in', ('delivered', 'cancel')),
        ])
        by_key = defaultdict(float)
        lots_by_key = defaultdict(set)
        for tl in tls:
            if not tl.product_id:
                continue
            key = (tl.order_id.id, tl.product_id.id)
            lot = tl.lot_id
            if lot:
                lots_by_key[key].add(lot.id)
                try:
                    dims = float(lot.x_alto or 0.0) * float(lot.x_ancho or 0.0)
                except Exception:
                    dims = 0.0
            else:
                dims = 0.0
            # Con lote CON medidas: assigned_qty ya lo contará cuando esté
            # en lot_ids de la línea — se marca aparte y se decide abajo.
            by_key[key] += 0.0 if (lot and dims > 0) else self._tal_transit_line_qty(tl)

        remaining = dict(by_key)
        result = {}
        for line in sale_lines:
            key = (line.order_id.id, line.product_id.id)
            avail = remaining.get(key, 0.0)
            if avail <= 0:
                continue
            pending = (metrics_by_line.get(line.id, {}) or {}).get(
                'pending_qty', 0.0) or 0.0
            take = min(avail, pending)
            if take > 0:
                result[line.id] = take
                remaining[key] = avail - take
        return result

    @api.model
    def get_data(self):
        # @api.model es OBLIGATORIO: el hub llama por RPC sin ids y este
        # build de Odoo 19 truena con IndexError en call_kw si el método
        # se registra como de instancia (args[0] serían los ids).
        SaleLine = self.env['sale.order.line']
        sale_lines_all = SaleLine.search(self._tal_get_sale_line_domain(), order='order_id desc, id desc')
        sale_lines_all = sale_lines_all.filtered(lambda line: self._is_hub_stock_product(line.product_id))

        metrics_by_line, _free_qty_by_product, _product_ids = self._hub_compute_sale_line_metrics(sale_lines_all)

        # Descontar reservas de tránsito ya hechas: el pendiente EFECTIVO
        # es lo que aún no está cubierto ni por placas ni por tránsito.
        reserved_extra = self._tal_get_reserved_extra_by_line(
            sale_lines_all, metrics_by_line)
        for line_id, extra in reserved_extra.items():
            m = metrics_by_line.get(line_id)
            if m is not None:
                m = dict(m)
                m['pending_qty'] = max(
                    (m.get('pending_qty') or 0.0) - extra, 0.0)
                metrics_by_line[line_id] = m

        sale_lines = sale_lines_all.filtered(
            lambda line: self._hub_float_gt_zero(metrics_by_line.get(line.id, {}).get('pending_qty'))
        )

        product_ids = set(sale_lines.mapped('product_id').ids)
        transit_lines_by_product = self._tal_get_available_transit_lines_by_product(product_ids)
        product_ids_with_transit = set(transit_lines_by_product.keys())

        if not product_ids_with_transit:
            return []

        sale_lines = sale_lines.filtered(lambda line: line.product_id.id in product_ids_with_transit)
        payment_map = self._hub_get_payment_percent_map(sale_lines)
        allocation_info_by_line = self._hub_get_active_allocation_info_map(sale_lines.ids)

        lines_by_product = defaultdict(lambda: self.env['sale.order.line'])
        for line in sale_lines:
            lines_by_product[line.product_id.id] |= line

        products = self.env['product.product'].browse(list(product_ids_with_transit)).exists()
        result = []

        for product in products:
            transit_lines = transit_lines_by_product.get(product.id, self.env['stock.transit.line'])
            if not transit_lines:
                continue

            transit_rows = [self._tal_make_transit_line_row(line) for line in transit_lines]
            available_qty = sum(item['qty'] for item in transit_rows)

            so_details = []
            demanded_qty = 0.0

            for sale_line in lines_by_product.get(product.id, self.env['sale.order.line']):
                metrics = metrics_by_line.get(sale_line.id, {})
                pending_qty = metrics.get('pending_qty', 0.0)

                if not self._hub_float_gt_zero(pending_qty):
                    continue

                demanded_qty += pending_qty
                so_details.append(
                    self._tal_make_sale_line_row(
                        sale_line,
                        metrics,
                        payment_map.get(sale_line.order_id.id, 0.0),
                        allocation_info_by_line[sale_line.id],
                    )
                )

            if not so_details:
                continue

            so_details.sort(
                key=lambda item: (
                    -item.get('payment_percent', 0.0),
                    item.get('commitment_date') or '',
                    item.get('so_name') or '',
                )
            )

            transit_rows.sort(
                key=lambda item: (
                    item.get('eta') or '9999-12-31',
                    item.get('voyage_name') or '',
                    item.get('lot_name') or '',
                )
            )

            unit_kind = self._get_product_unit_kind(product)
            unit_label = self._get_product_unit_label(product)
            unit_group_label = self._get_product_unit_group_label(product)
            qty_to_allocate = min(available_qty, demanded_qty)

            vendors = [row['vendor'] for row in transit_rows if row.get('vendor')]
            voyages = [row['voyage_name'] for row in transit_rows if row.get('voyage_name')]

            row = {
                'id': product.id,
                'name': product.display_name,
                'type': self._display_value(
                    getattr(product, 'x_unidad_del_producto', False)
                    or getattr(product.product_tmpl_id, 'x_unidad_del_producto', False)
                ),
                'unit_kind': unit_kind,
                'unit_label': unit_label,
                'product_type': unit_group_label,
                'group': self._display_value(getattr(product, 'x_grupo', False)),
                'category': product.categ_id.name,
                'vendor': vendors[0] if vendors else 'SIN PROVEEDOR',
                'voyage_count': len(set(voyages)),
                'lot_count': len(transit_rows),
                'qty_so': demanded_qty,
                'qty_transit_available': available_qty,
                'qty_to_allocate': qty_to_allocate,
                'so_lines': so_details,
                'transit_lines': transit_rows,
            }

            row.update(self._split_qty_fields(product, 'qty_so', demanded_qty))
            row.update(self._split_qty_fields(product, 'qty_transit_available', available_qty))
            row.update(self._split_qty_fields(product, 'qty_to_allocate', qty_to_allocate))

            result.append(row)

        result.sort(
            key=lambda product: (
                -max([line.get('payment_percent', 0.0) for line in product.get('so_lines', [])] or [0.0]),
                product.get('name') or '',
            )
        )

        return result

    # ---------------------------------------------------------------------
    # ACCIONES
    # ---------------------------------------------------------------------

    def _tal_get_qty_rounding(self, product):
        if product and product.uom_id and product.uom_id.rounding:
            return product.uom_id.rounding
        return 0.0001

    def _tal_validate_sale_line_for_assignment(self, sale_line):
        if not sale_line or not sale_line.exists():
            raise UserError(_('No se encontró la línea de venta objetivo.'))

        if sale_line.display_type or not sale_line.product_id:
            raise UserError(_('La línea seleccionada no es una línea de producto válida.'))

        if sale_line.state not in ('sale', 'done'):
            raise UserError(_('Solo puede asignar inventario en tránsito a pedidos confirmados.'))

        if self._is_hub_stock_product(sale_line.product_id) is False:
            raise UserError(_('Los servicios no se gestionan desde Transit Allocation.'))

        if getattr(sale_line, 'tc_assignment_closed', False):
            raise UserError(_('La línea tiene la asignación cerrada. Reábrala antes de asignar inventario en tránsito.'))

        pending_qty = sale_line._tc_get_pending_allocation_qty() if hasattr(sale_line, '_tc_get_pending_allocation_qty') else sale_line.tc_qty_pending_allocation
        if not self._hub_float_gt_zero(pending_qty):
            raise UserError(_('La línea ya no tiene cantidad pendiente por cubrir.'))

        return pending_qty

    def _tal_validate_transit_lines_for_assignment(self, transit_lines, sale_line):
        if not transit_lines:
            raise UserError(_('Seleccione al menos un lote en tránsito para asignar.'))

        invalid = []
        selected_qty = 0.0

        for transit_line in transit_lines:
            if transit_line.product_id.id != sale_line.product_id.id:
                invalid.append(_('%s: producto distinto') % (transit_line.display_name or transit_line.id))
                continue

            if transit_line.voyage_id.custom_status in ('delivered', 'cancel'):
                invalid.append(_('%s: embarque cerrado o cancelado') % (transit_line.lot_id.display_name or transit_line.id))
                continue

            if transit_line.allocation_status != 'available' or transit_line.partner_id or transit_line.order_id:
                invalid.append(_('%s: ya no está disponible') % (transit_line.lot_id.display_name or transit_line.id))
                continue

            quant = self._tal_resolve_valid_transit_quant(transit_line)
            if not quant:
                invalid.append(_('%s: no tiene quant positivo en ubicación de tránsito') % (transit_line.lot_id.display_name or transit_line.id))
                continue

            selected_qty += self._tal_transit_line_qty(transit_line)

        if invalid:
            raise UserError(_('No se puede completar la asignación:\n\n%s') % '\n'.join(invalid[:80]))

        rounding = self._tal_get_qty_rounding(sale_line.product_id)
        selected_qty = float_round(selected_qty, precision_rounding=rounding)

        if float_compare(selected_qty, 0.0, precision_rounding=rounding) <= 0:
            raise UserError(_('La selección no tiene cantidad positiva.'))

        return selected_qty

    @api.model
    def assign_transit_lines(
        self,
        transit_line_ids,
        sale_line_id,
        reason=False,
        over_assignment_action=False,
        over_assignment_reason=False,
        partial_qty_by_line=False,
    ):
        sale_line = self.env['sale.order.line'].browse(sale_line_id).exists()
        pending_qty_before = self._tal_validate_sale_line_for_assignment(sale_line)

        # sudo(): la asignación se dispara desde la orden de venta; el
        # vendedor no necesita ACL de Torre de Control.
        transit_lines = self.env['stock.transit.line'].sudo().browse(transit_line_ids or []).exists()

        # Parcialidades (FORMATOS/PIEZAS): partir las líneas seleccionadas para
        # asignar solo lo elegido y dejar el saldo disponible en tránsito. Tras
        # el split, product_uom_qty refleja la parcialidad, por lo que la
        # validación, el conteo y el breakdown la respetan sin lógica adicional.
        transit_lines._tc_apply_partial_assignment_splits(partial_qty_by_line)

        selected_qty = self._tal_validate_transit_lines_for_assignment(transit_lines, sale_line)

        rounding = self._tal_get_qty_rounding(sale_line.product_id)
        requested_qty_before = sale_line.product_uom_qty or 0.0
        assigned_qty_before = sale_line._tc_get_assigned_lot_qty() if hasattr(sale_line, '_tc_get_assigned_lot_qty') else sale_line.tc_qty_assigned_lots
        projected_assigned_qty = float_round(assigned_qty_before + selected_qty, precision_rounding=rounding)
        over_assigned_qty = max(projected_assigned_qty - requested_qty_before, 0.0) if requested_qty_before > 0 else projected_assigned_qty
        has_over_assignment = float_compare(over_assigned_qty, 0.0, precision_rounding=rounding) > 0

        if has_over_assignment and over_assignment_action not in ('free', 'bill'):
            return {
                'success': False,
                'need_over_assignment_decision': True,
                'over_assigned_qty': over_assigned_qty,
                'message': _('La selección excede lo solicitado. Indique si el excedente se entrega sin cobrar o si se cobrará.'),
            }

        if has_over_assignment and over_assignment_action == 'free' and hasattr(sale_line, '_tc_require_discount_field'):
            sale_line._tc_require_discount_field()

        # Punto crítico: NO duplicamos lógica. Escribimos sobre stock.transit.line
        # para reutilizar validaciones, publicación y sincronización existentes.
        transit_lines.write({
            'order_id': sale_line.order_id.id,
        })

        over_admin_result = {
            'qty_before': requested_qty_before,
            'qty_after': sale_line.product_uom_qty or 0.0,
            'discount_before': sale_line._tc_get_discount_percent() if hasattr(sale_line, '_tc_get_discount_percent') else 0.0,
            'discount_after': sale_line._tc_get_discount_percent() if hasattr(sale_line, '_tc_get_discount_percent') else 0.0,
            'discount_applied': False,
            'qty_updated': False,
            'action_label': 'No aplica',
        }

        if has_over_assignment and hasattr(sale_line, '_tc_apply_over_assignment_admin_action'):
            over_admin_result = sale_line._tc_apply_over_assignment_admin_action(
                assigned_qty=projected_assigned_qty,
                requested_qty=requested_qty_before,
                over_assigned_qty=over_assigned_qty,
                action=over_assignment_action,
                reason=over_assignment_reason,
            )

        pending_qty_after = sale_line._tc_get_pending_allocation_qty() if hasattr(sale_line, '_tc_get_pending_allocation_qty') else sale_line.tc_qty_pending_allocation
        assigned_qty_after = sale_line._tc_get_assigned_lot_qty() if hasattr(sale_line, '_tc_get_assigned_lot_qty') else projected_assigned_qty

        if hasattr(sale_line, '_tc_post_plain_message'):
            sale_line._tc_post_plain_message(
                _('🚢 Asignación aplicada desde Transit Allocation'),
                [
                    _('Producto: %s') % (sale_line.product_id.display_name or ''),
                    _('Pedido: %s') % (sale_line.order_id.name or ''),
                    _('Cliente: %s') % (sale_line.order_id.partner_id.display_name or ''),
                    _('Seleccionado desde tránsito: %.3f') % selected_qty,
                    _('Asignado anterior: %.3f') % assigned_qty_before,
                    _('Asignado actual estimado: %.3f') % assigned_qty_after,
                    _('Pendiente anterior: %.3f') % pending_qty_before,
                    _('Pendiente actual: %.3f') % pending_qty_after,
                    _('Sobreasignado: %.3f') % over_assigned_qty,
                    _('Acción sobre exceso: %s') % over_admin_result.get('action_label', 'No aplica'),
                    _('Lotes en tránsito asignados: %s') % len(transit_lines),
                    _('Embarques: %s') % (', '.join(sorted(set(transit_lines.mapped('voyage_id.name')))) or 'N/A'),
                    _('Motivo: %s') % (reason or over_assignment_reason or 'Asignación operativa desde Transit Allocation.'),
                ],
            )

        for voyage in transit_lines.mapped('voyage_id'):
            voyage.message_post(body=(
                'Transit Allocation\n'
                'Pedido: %s\n'
                'Cliente: %s\n'
                'Producto: %s\n'
                'Lotes asignados: %s\n'
                'Cantidad asignada desde este hub: %.3f\n'
                'Motivo: %s'
            ) % (
                sale_line.order_id.name,
                sale_line.order_id.partner_id.display_name,
                sale_line.product_id.display_name,
                ', '.join(transit_lines.filtered(lambda line: line.voyage_id.id == voyage.id).mapped('lot_id.name')),
                sum(transit_lines.filtered(lambda line: line.voyage_id.id == voyage.id).mapped('product_uom_qty')),
                reason or over_assignment_reason or 'Asignación operativa desde Transit Allocation.',
            ))

        uom = sale_line._tc_get_line_uom() if hasattr(sale_line, '_tc_get_line_uom') else sale_line.product_id.uom_id

        # Cobrar excedente: avisar a FACTURACIÓN (Lourdes/Zulema) y COBRANZA
        # (Clara) reutilizando el motor del módulo sale_payment_proof. Integración
        # SUAVE (sin dependencia dura): solo si el método está disponible.
        if has_over_assignment and over_assignment_action == 'bill':
            order = sale_line.order_id
            if order and hasattr(order, '_overcharge_notify'):
                try:
                    over_amount = (over_assigned_qty or 0.0) * (sale_line.price_unit or 0.0)
                    order._overcharge_notify(
                        product_name=sale_line.product_id.display_name or '',
                        over_qty=over_assigned_qty,
                        uom_name=uom.display_name if uom else '',
                        amount=over_amount,
                        reason=over_assignment_reason or reason or '',
                    )
                except Exception:
                    _logger.exception(
                        "[TRANSIT ALLOC] No se pudo notificar el excedente cobrado a "
                        "facturación/cobranza"
                    )

        # Excedente NO cobrado ('free' = descuento): si el valor del descuento
        # supera el umbral en MXN, la orden queda BLOQUEADA hasta autorización
        # (mismo flujo que precios mínimos). Integración SUAVE con
        # inventory_shopping_cart: solo si el campo existe.
        if has_over_assignment and over_assignment_action == 'free':
            order = sale_line.order_id
            if order and 'x_discount_needs_auth' in order._fields:
                try:
                    if (order.x_discount_needs_auth
                            and not order.x_discount_auth_requested
                            and not self.env.user.has_group(
                                'inventory_shopping_cart.group_price_authorizer')):
                        order.action_request_discount_authorization()
                except Exception:
                    _logger.exception(
                        "[TRANSIT ALLOC] No se pudo solicitar autorización de descuento "
                        "por excedente"
                    )

        return {
            'success': True,
            'message': _('Inventario en tránsito asignado correctamente.'),
            'sale_line_id': sale_line.id,
            'transit_line_ids': transit_lines.ids,
            'selected_qty': selected_qty,
            'assigned_qty_before': assigned_qty_before,
            'assigned_qty_after': assigned_qty_after,
            'pending_qty_before': pending_qty_before,
            'pending_qty_after': pending_qty_after,
            'over_assigned_qty': over_assigned_qty,
            'over_assignment_action': over_assignment_action if has_over_assignment else False,
            'discount_before': over_admin_result.get('discount_before', 0.0),
            'discount_after': over_admin_result.get('discount_after', 0.0),
            'discount_applied': over_admin_result.get('discount_applied', False),
            'qty_updated': over_admin_result.get('qty_updated', False),
            'uom_name': uom.display_name if uom else '',
        }


class StockTransitLineTransitAllocationSync(models.Model):
    _inherit = 'stock.transit.line'

    def _tal_build_breakdown_from_transit_lines(self, sale_line, reserved_transit_lines):
        if 'x_lot_breakdown_json' not in sale_line._fields:
            return False

        breakdown = sale_line._tc_read_lot_breakdown() if hasattr(sale_line, '_tc_read_lot_breakdown') else {}
        breakdown = dict(breakdown or {})

        active_transit_lot_ids = self.env['stock.transit.line'].sudo().search([
            ('product_id', '=', sale_line.product_id.id),
            ('lot_id', '!=', False),
            ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
        ]).mapped('lot_id').ids

        reserved_lot_ids = set(reserved_transit_lines.mapped('lot_id').ids)

        # Quita breakdowns de lotes en tránsito que ya no estén reservados para esta SO.
        for lot_id in active_transit_lot_ids:
            if lot_id not in reserved_lot_ids:
                breakdown.pop(str(lot_id), None)

        # ACUMULAR entre líneas de tránsito del MISMO lote (split parcial
        # 20 + 30 reservado al mismo pedido): sobreescribir dejaba 30 en vez
        # de 50 → pendiente fantasma → doble asignación/compra. El primer
        # toque de cada lote en ESTE ciclo resetea el valor previo (que puede
        # venir viejo del breakdown de la línea de venta).
        refreshed_lots = set()
        for transit_line in reserved_transit_lines:
            lot = transit_line.lot_id
            if not lot:
                continue

            lot_type = ''
            if 'x_tipo' in lot._fields and lot.x_tipo:
                lot_type = str(lot.x_tipo).lower()

            if lot_type in ('formato', 'pieza'):
                key = str(lot.id)
                if key in refreshed_lots:
                    breakdown[key] += transit_line.product_uom_qty or 0.0
                else:
                    breakdown[key] = transit_line.product_uom_qty or 0.0
                    refreshed_lots.add(key)

        if hasattr(sale_line, '_tc_prepare_breakdown_value_for_line'):
            return sale_line._tc_prepare_breakdown_value_for_line(breakdown)

        return breakdown or False

    def _tc_sync_sale_line_lots_from_transit_assignment(self, order=False, product=False):
        """
        Override conservador del sync original.

        Mejora el flujo existente para que Transit Allocation y la asignación
        desde embarque compartan una sola fuente de verdad:
        - conserva lotes asignados manualmente desde stock físico interno;
        - agrega lotes reservados en tránsito para la SO/producto;
        - elimina de la línea de venta los lotes de tránsito que fueron liberados;
        - mantiene breakdown para formatos/piezas con la cantidad de stock.transit.line.
        """
        self.ensure_one()

        order = order or self.order_id
        product = product or self.product_id

        if not order or not product:
            return False

        TransitLine = self.env['stock.transit.line'].sudo()

        reserved_transit_lines = TransitLine.search([
            ('order_id', '=', order.id),
            ('product_id', '=', product.id),
            ('allocation_status', '=', 'reserved'),
            ('lot_id', '!=', False),
            ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
        ], order='voyage_id asc, id asc')

        active_transit_lot_ids = set(TransitLine.search([
            ('product_id', '=', product.id),
            ('lot_id', '!=', False),
            ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
        ]).mapped('lot_id').ids)

        # REPARTO POR LÍNEA: con varias líneas del mismo producto en la
        # orden, cada lote reservado se atribuye por capacidad pendiente.
        # Antes TODO el tránsito reservado se fusionaba en la primera línea
        # pendiente y las demás quedaban en cero.
        if hasattr(order, '_tc_distribute_transit_to_lines'):
            pairs = order._tc_distribute_transit_to_lines(
                product, reserved_transit_lines)
        else:
            single = self._tc_get_sale_line_for_assignment(
                order=order, product=product)
            pairs = [(single, reserved_transit_lines)] if single else []

        done = False
        for sale_line, subset in pairs:
            if not sale_line or 'lot_ids' not in sale_line._fields:
                continue
            done = self._tal_apply_reserved_to_sale_line(
                order, product, sale_line, subset,
                active_transit_lot_ids) or done
        return done

    def _tal_apply_reserved_to_sale_line(self, order, product, sale_line,
                                         reserved_transit_lines,
                                         active_transit_lot_ids):
        """Aplica a UNA línea de venta su subconjunto de tránsito
        reservado (cuerpo original del sync, ahora ejecutado por línea).
        Con subconjunto VACÍO también corre: limpia de la línea los lotes
        de tránsito que se redistribuyeron a otra línea."""
        reserved_lot_ids = reserved_transit_lines.mapped('lot_id').ids
        current_lot_ids = sale_line.lot_ids.ids if sale_line.lot_ids else []

        # Conserva manuales o tránsito todavía reservado para la orden.
        merged_lot_ids = []
        reserved_set = set(reserved_lot_ids)
        for lot_id in current_lot_ids:
            is_active_transit_lot = lot_id in active_transit_lot_ids
            if not is_active_transit_lot or lot_id in reserved_set:
                if lot_id not in merged_lot_ids:
                    merged_lot_ids.append(lot_id)

        for lot_id in reserved_lot_ids:
            if lot_id not in merged_lot_ids:
                merged_lot_ids.append(lot_id)

        vals = {
            'lot_ids': [(6, 0, merged_lot_ids)],
        }

        # CRÍTICO:
        # Al asignar lotes de tránsito a una línea que venía de "Mandar a pedir",
        # Odoo evalúa la restricción _check_transit_vs_lots en el mismo write().
        # Si auto_transit_assign sigue activo y la línea queda cubierta, se dispara:
        # "No puede marcarse como Mandar a pedir si no queda cantidad pendiente".
        # Por eso se limpia auto_transit_assign EN EL MISMO write que agrega lot_ids.
        if merged_lot_ids and 'auto_transit_assign' in sale_line._fields:
            vals['auto_transit_assign'] = False

        if 'x_lot_breakdown_json' in sale_line._fields:
            vals['x_lot_breakdown_json'] = self._tal_build_breakdown_from_transit_lines(
                sale_line,
                reserved_transit_lines,
            )

        sale_line.with_context(
            skip_stone_sync_picking=True,
            skip_stone_sync_so=True,
            skip_hold_validation=True,
            skip_picking_clean=True,
            skip_transit_sale_sync=True,
            # La asignación desde tránsito es PARCIAL por diseño: cubre lo que
            # viene en camino y el resto queda pendiente/compra. El tope de stock
            # (solicitado > disponible) no aplica aquí; bloquearía asignar
            # material real en tránsito solo porque la demanda total es mayor.
            skip_tc_stock_cap=True,
        ).write(vals)

        # Si la línea ya quedó cubierta, limpia intención de compra.
        if hasattr(sale_line, '_tc_get_pending_allocation_qty'):
            pending_qty = sale_line._tc_get_pending_allocation_qty()
            rounding = sale_line._tc_get_qty_rounding() if hasattr(sale_line, '_tc_get_qty_rounding') else 0.0001
            if float_compare(pending_qty, 0.0, precision_rounding=rounding) <= 0:
                clear_vals = {}
                if 'auto_transit_assign' in sale_line._fields:
                    clear_vals['auto_transit_assign'] = False
                if 'tc_stock_rejected' in sale_line._fields:
                    clear_vals.update({
                        'tc_stock_rejected': False,
                        'tc_stock_rejected_reason': False,
                        'tc_stock_rejected_by': False,
                        'tc_stock_rejected_at': False,
                    })
                if clear_vals:
                    sale_line.with_context(skip_tc_allocation_recovery=True).write(clear_vals)

        _logger.info(
            '[TC_ASSIGN_PRE][TransitAllocation] SO %s | Producto %s | transit_lots=%s | final_lots=%s',
            order.name,
            product.display_name,
            reserved_lot_ids,
            merged_lot_ids,
        )

        return True