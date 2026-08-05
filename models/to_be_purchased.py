# -*- coding: utf-8 -*-
from collections import defaultdict
import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AllocationHubPaymentMixin(models.AbstractModel):
    _name = 'allocation.hub.payment.mixin'
    _description = 'Helpers de pago para hubs de asignación'

    def _get_payment_percent(self, order):
        if not order or not order.amount_total:
            return 0.0

        invoices = order.invoice_ids.filtered(
            lambda inv: inv.state == 'posted'
            and inv.move_type in ('out_invoice', 'out_refund')
        )

        if not invoices:
            return 0.0

        total = sum(invoices.mapped('amount_total'))
        residual = sum(invoices.mapped('amount_residual'))
        paid = max(total - residual, 0.0)

        if total <= 0:
            return 0.0

        return min(100.0, round((paid / order.amount_total) * 100, 2))

    def _display_value(self, value, fallback='N/A'):
        if not value:
            return fallback

        if isinstance(value, str):
            return value

        if isinstance(value, (int, float)):
            return str(value)

        if hasattr(value, 'display_name'):
            return value.display_name or fallback

        if hasattr(value, 'mapped'):
            names = value.mapped('display_name')
            return ', '.join(names) if names else fallback

        return str(value)

    def _get_product_unit_kind(self, product):
        """Normaliza la unidad comercial para los hubs: m2 o pieces."""
        if not product:
            return 'm2'

        values = []

        for candidate in (
            getattr(product, 'x_unidad_del_producto', False),
            getattr(product.product_tmpl_id, 'x_unidad_del_producto', False) if product.product_tmpl_id else False,
            product.uom_id.name if product.uom_id else False,
            product.uom_id.display_name if product.uom_id else False,
        ):
            if candidate:
                values.append(str(candidate).lower())

        text = ' '.join(values)

        # OJO: 'formato' NO clasifica como pieza. Los productos de formato se
        # comercializan por metros cuadrados; solo las unidades explícitamente
        # de pieza/unidad van al grupo de piezas.
        if any(token in text for token in ['pieza', 'pza', 'pzas', 'unidad', 'unit']):
            return 'pieces'

        return 'm2'

    def _get_product_unit_label(self, product):
        return 'pzas' if self._get_product_unit_kind(product) == 'pieces' else 'm²'

    def _get_product_unit_group_label(self, product):
        return 'Piezas' if self._get_product_unit_kind(product) == 'pieces' else 'Metros cuadrados'

    def _split_qty_by_unit(self, product, qty):
        qty = qty or 0.0
        if self._get_product_unit_kind(product) == 'pieces':
            return 0.0, qty
        return qty, 0.0

    def _split_qty_fields(self, product, prefix, qty):
        qty_m2, qty_pieces = self._split_qty_by_unit(product, qty)
        return {
            f'{prefix}_m2': qty_m2,
            f'{prefix}_pieces': qty_pieces,
        }

    def _get_days_without_assignment(self, order):
        if not order or not order.date_order:
            return 0

        today = fields.Date.context_today(self)
        order_date = fields.Date.to_date(order.date_order)

        if not order_date:
            return 0

        return max((today - order_date).days, 0)

    def _get_empty_allocation_info(self):
        return {
            'allocation': False,
            'po_name': '',
            'po_qty': 0.0,
            'po_id': False,
            'po_state': '',
            'po_partner_id': False,
            'po_partner_name': '',
        }

    def _get_active_purchase_allocations(self, sale_line):
        """
        Allocations activas que ya cubren demanda de compra de una línea.

        No se toma ciegamente allocation.quantity como verdad absoluta, porque
        una allocation histórica pudo haberse inflado al editar la demanda de
        la SO. La cobertura real se calcula después contra la capacidad real
        de la purchase.order.line.
        """
        if not sale_line or not sale_line.exists():
            return self.env['purchase.order.line.allocation']

        Allocation = self.env['purchase.order.line.allocation'].sudo()
        allocations = Allocation.search([
            ('sale_line_id', '=', sale_line.id),
            ('state', 'not in', ['cancelled', 'done']),
        ], order='id desc')

        return allocations.filtered(
            lambda alloc: alloc.purchase_order_id
            and alloc.purchase_order_id.exists()
            and alloc.purchase_order_id.state != 'cancel'
            and alloc.purchase_line_id
            and alloc.purchase_line_id.exists()
        )

    def _get_purchase_coverage_by_sale_line(self, sale_line_ids):
        """
        Cobertura real de compra por sale.order.line.

        Por qué existe:
        - Si una línea vendida nació con 100 m² y se compraron 100 m², existe
          una allocation activa.
        - Si después la demanda sube a 200 m², To Be Purchased debe mostrar
          solo el diferencial: 100 m².
        - Si por una lógica anterior la allocation quedó inflada a 200 m² pero
          la línea real de OC sigue siendo 100 m², no debemos considerar 200 m²
          como comprado.

        Regla: la suma de allocations de una purchase.order.line se topa contra
        la cantidad real de esa línea de OC. Si varias SO comparten la misma
        línea de compra, se reparte proporcionalmente.
        """
        if not sale_line_ids:
            return {}

        Allocation = self.env['purchase.order.line.allocation'].sudo()
        allocations = Allocation.search([
            ('sale_line_id', 'in', list(sale_line_ids)),
            ('state', 'not in', ['cancelled', 'done']),
            ('purchase_order_id.state', '!=', 'cancel'),
            ('purchase_line_id', '!=', False),
        ], order='purchase_line_id, id')

        allocations = allocations.filtered(
            lambda alloc: alloc.purchase_line_id
            and alloc.purchase_line_id.exists()
            and alloc.purchase_order_id
            and alloc.purchase_order_id.exists()
            and alloc.purchase_order_id.state != 'cancel'
        )

        # ALLOCATIONS OBSOLETAS: si TODOS los viajes de la OC ya terminaron
        # (delivered/cancel) y la allocation sigue 'pending' sin línea de
        # tránsito reservada, ese material llegó y se fue a otro lado — la
        # allocation jamás se resolverá y contarla como cobertura OCULTABA la
        # demanda del cliente para siempre.
        if allocations:
            Voyage = self.env['stock.transit.voyage'].sudo()
            po_ids = allocations.mapped('purchase_order_id').ids
            voyages = Voyage.search([('purchase_id', 'in', po_ids)])
            pos_with_voyage = set(voyages.mapped('purchase_id').ids)
            pos_with_active_voyage = set(
                voyages.filtered(
                    lambda v: v.custom_status not in ('delivered', 'cancel')
                ).mapped('purchase_id').ids
            )
            reserved_alloc_ids = set(
                self.env['stock.transit.line'].sudo().search([
                    ('allocation_id', 'in', allocations.ids),
                ]).mapped('allocation_id').ids
            )

            allocations = allocations.filtered(
                lambda alloc:
                    alloc.state != 'pending'
                    or alloc.purchase_order_id.id not in pos_with_voyage
                    or alloc.purchase_order_id.id in pos_with_active_voyage
                    or alloc.id in reserved_alloc_ids
            )

        coverage_by_line = defaultdict(float)
        allocations_by_po_line = defaultdict(lambda: self.env['purchase.order.line.allocation'].sudo())

        for allocation in allocations:
            allocations_by_po_line[allocation.purchase_line_id.id] |= allocation

        for _po_line_id, po_allocations in allocations_by_po_line.items():
            po_line = po_allocations[0].purchase_line_id
            po_capacity = max(float(po_line.product_qty or 0.0), 0.0)

            # Nunca una línea de OC puede cubrir más que lo comprado realmente.
            total_declared = sum(max(float(alloc.quantity or 0.0), 0.0) for alloc in po_allocations)

            if po_capacity <= 0.0 or total_declared <= 0.0:
                continue

            if total_declared <= po_capacity:
                for alloc in po_allocations:
                    if alloc.sale_line_id:
                        coverage_by_line[alloc.sale_line_id.id] += max(float(alloc.quantity or 0.0), 0.0)
                continue

            # Caso defensivo: allocations infladas contra una PO line menor.
            # Se distribuye la capacidad real de la OC proporcionalmente.
            factor = po_capacity / total_declared
            for alloc in po_allocations:
                if alloc.sale_line_id:
                    coverage_by_line[alloc.sale_line_id.id] += max(float(alloc.quantity or 0.0), 0.0) * factor

        return dict(coverage_by_line)

    def _get_active_purchase_qty_for_line(self, sale_line):
        if not sale_line or not sale_line.exists():
            return 0.0
        return self._get_purchase_coverage_by_sale_line([sale_line.id]).get(sale_line.id, 0.0)

    def _get_purchase_gap_qty_for_line(self, sale_line, raw_pending_qty=False):
        """
        Pendiente incremental para To Be Purchased.

        raw_pending_qty = demanda viva pendiente de cubrir físicamente.
        active_purchase_qty = cantidad real ya cubierta por líneas de OC activas.
        """
        if not sale_line or not sale_line.exists():
            return 0.0

        if raw_pending_qty is False:
            if hasattr(sale_line, '_tc_get_pending_allocation_qty'):
                raw_pending_qty = sale_line._tc_get_pending_allocation_qty()
            else:
                raw_pending_qty = sale_line.tc_qty_pending_allocation

        active_purchase_qty = self._get_active_purchase_qty_for_line(sale_line)
        return max(float(raw_pending_qty or 0.0) - float(active_purchase_qty or 0.0), 0.0)

    def _get_active_allocation_info(self, sale_line):
        allocations = self._get_active_purchase_allocations(sale_line)

        if not allocations:
            return self._get_empty_allocation_info()

        purchase_orders = allocations.mapped('purchase_order_id').filtered(
            lambda po: po and po.exists() and po.state != 'cancel'
        )
        latest_allocation = allocations[:1]
        latest_po = latest_allocation.purchase_order_id if latest_allocation else False
        total_qty = sum(allocations.mapped('quantity'))

        po_names = []
        for po in purchase_orders:
            if po.name and po.name not in po_names:
                po_names.append(po.name)

        return {
            'allocation': latest_allocation,
            'po_name': ', '.join(po_names),
            'po_qty': total_qty,
            'po_id': latest_po.id if latest_po else False,
            'po_state': latest_po.state if latest_po else '',
            'po_partner_id': latest_po.partner_id.id if latest_po and latest_po.partner_id else False,
            'po_partner_name': latest_po.partner_id.name if latest_po and latest_po.partner_id else '',
        }

    def _get_free_internal_qty_for_product(self, product):
        if not product:
            return 0.0

        Quant = self.env['stock.quant'].sudo()

        domain = [
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('reserved_quantity', '=', 0),
        ]

        if 'x_tiene_hold' in Quant._fields:
            domain.append(('x_tiene_hold', '=', False))

        if hasattr(Quant, '_get_committed_lot_ids'):
            committed_lot_ids = Quant._get_committed_lot_ids(product.id)
            if committed_lot_ids:
                domain.append(('lot_id', 'not in', committed_lot_ids))

        quants = Quant.search(domain)
        return sum(quants.mapped('quantity'))


    def _is_hub_stock_product(self, product):
        """Devuelve False para servicios: los hubs solo gestionan material físico."""
        if not product or not product.exists():
            return False

        product_type = False

        if 'detailed_type' in product._fields:
            product_type = product.detailed_type
        elif 'type' in product._fields:
            product_type = product.type

        if product_type == 'service':
            return False

        template = product.product_tmpl_id
        if template:
            template_type = False

            if 'detailed_type' in template._fields:
                template_type = template.detailed_type
            elif 'type' in template._fields:
                template_type = template.type

            if template_type == 'service':
                return False

        return True


    # -------------------------------------------------------------------------
    # OPTIMIZACIÓN HUBS
    # -------------------------------------------------------------------------

    def _hub_float_gt_zero(self, qty, precision=0.0001):
        return float(qty or 0.0) > precision

    def _hub_parse_json_value(self, raw):
        if not raw:
            return {}

        if isinstance(raw, dict):
            return raw

        if isinstance(raw, str):
            try:
                value = json.loads(raw)
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}

        return {}

    def _hub_get_candidate_sale_lines(self):
        """
        Candidatos base para ambos hubs.

        Antes el tablero cargaba TODAS las líneas confirmadas y después leía
        campos compute línea por línea. Eso dispara búsquedas repetidas sobre
        stock.quant, stock.lot y allocations. Aquí solo se leen campos
        almacenados y las cantidades operativas se calculan en lote.
        """
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

        sale_lines = SaleLine.search(domain, order='order_id desc, id desc')

        return sale_lines.filtered(
            lambda line: self._is_hub_stock_product(line.product_id)
        )

    def _hub_get_quant_sum(self, domain, groupby):
        Quant = self.env['stock.quant'].sudo()
        groups = Quant.read_group(
            domain,
            ['quantity:sum'],
            groupby,
            lazy=False,
        )
        return groups

    def _hub_get_internal_qty_by_product_lot(self, product_ids, lot_ids):
        if not product_ids or not lot_ids:
            return {}

        Quant = self.env['stock.quant'].sudo()
        domain = [
            ('product_id', 'in', list(product_ids)),
            ('lot_id', 'in', list(lot_ids)),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if 'company_id' in Quant._fields and self.env.company:
            domain.append(('company_id', 'in', [False, self.env.company.id]))

        qty_map = {}
        for group in self._hub_get_quant_sum(domain, ['product_id', 'lot_id']):
            product = group.get('product_id')
            lot = group.get('lot_id')
            if not product or not lot:
                continue
            qty_map[(product[0], lot[0])] = group.get('quantity') or group.get('quantity_sum') or 0.0

        return qty_map

    def _hub_get_free_internal_qty_by_product(self, product_ids):
        if not product_ids:
            return {}

        Quant = self.env['stock.quant'].sudo()
        domain = [
            ('product_id', 'in', list(product_ids)),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if 'reserved_quantity' in Quant._fields:
            domain.append(('reserved_quantity', '=', 0))

        if 'x_tiene_hold' in Quant._fields:
            domain.append(('x_tiene_hold', '=', False))

        if 'company_id' in Quant._fields and self.env.company:
            domain.append(('company_id', 'in', [False, self.env.company.id]))

        committed_lot_ids = set()
        if hasattr(Quant, '_get_committed_lot_ids'):
            for product_id in product_ids:
                try:
                    committed_lot_ids.update(Quant._get_committed_lot_ids(product_id) or [])
                except Exception as e:
                    _logger.warning(
                        '[AllocationHub] No se pudieron resolver committed_lot_ids para product_id=%s: %s',
                        product_id,
                        e,
                    )

        if committed_lot_ids:
            domain.append(('lot_id', 'not in', list(committed_lot_ids)))

        qty_map = defaultdict(float)
        for group in self._hub_get_quant_sum(domain, ['product_id']):
            product = group.get('product_id')
            if not product:
                continue
            qty_map[product[0]] += group.get('quantity') or group.get('quantity_sum') or 0.0

        return dict(qty_map)

    def _hub_get_transit_qty_by_product(self, product_ids):
        if not product_ids:
            return {}

        domain = [
            ('product_id', 'in', list(product_ids)),
            ('quantity', '>', 0),
        ] + self.env['stock.location']._som_transit_quant_leaf()

        qty_map = defaultdict(float)
        for group in self._hub_get_quant_sum(domain, ['product_id']):
            product = group.get('product_id')
            if not product:
                continue
            qty_map[product[0]] += group.get('quantity') or group.get('quantity_sum') or 0.0

        return dict(qty_map)

    def _hub_get_free_transit_info_by_product(self, product_ids):
        """
        Contexto resumido de inventario EN TRÁNSITO LIBRE por producto.

        Regla funcional:
        - libre = stock.transit.line con allocation_status available, sin cliente,
          sin SO y con viaje activo;
        - se valida quant positivo en ubicación tránsito;
        - NO se devuelve una línea por lote/placa para evitar payloads y tablas
          inmanejables cuando hay miles de metros/lotes libres;
        - la salida queda agregada por embarque para responder: cuánto viene
          libre, en qué embarque y cuándo llega.
        """
        if not product_ids:
            return {}

        TransitLine = self.env['stock.transit.line'].sudo()
        Quant = self.env['stock.quant'].sudo()

        transit_lines = TransitLine.search([
            ('product_id', 'in', list(product_ids)),
            ('lot_id', '!=', False),
            ('product_uom_qty', '>', 0),
            ('allocation_status', '=', 'available'),
            ('partner_id', '=', False),
            ('order_id', '=', False),
            ('voyage_id.custom_status', 'not in', ['delivered', 'cancel']),
        ], order='eta asc, voyage_id asc, product_id asc, id asc')

        if not transit_lines:
            return {}

        def _is_valid_transit_quant(line, quant):
            return bool(
                quant
                and quant.exists()
                and quant.product_id.id == line.product_id.id
                and quant.lot_id.id == line.lot_id.id
                and quant.quantity > 0
                and quant.location_id._som_is_transit()
                and (
                    not quant.company_id
                    or not line.company_id
                    or quant.company_id.id == line.company_id.id
                )
            )

        valid_quant_by_line = {}
        missing_lines = self.env['stock.transit.line'].sudo()

        for line in transit_lines:
            if _is_valid_transit_quant(line, line.quant_id):
                valid_quant_by_line[line.id] = line.quant_id
            else:
                missing_lines |= line

        if missing_lines:
            quant_domain = [
                ('product_id', 'in', missing_lines.mapped('product_id').ids),
                ('lot_id', 'in', missing_lines.mapped('lot_id').ids),
                ('quantity', '>', 0),
            ] + self.env['stock.location']._som_transit_quant_leaf()

            if 'company_id' in Quant._fields and self.env.company:
                quant_domain.append(('company_id', 'in', [False, self.env.company.id]))

            quant_map = {}
            for quant in Quant.search(quant_domain, order='id desc'):
                key = (quant.product_id.id, quant.lot_id.id)
                if key not in quant_map:
                    quant_map[key] = quant

            for line in missing_lines:
                quant = quant_map.get((line.product_id.id, line.lot_id.id))
                if quant and _is_valid_transit_quant(line, quant):
                    valid_quant_by_line[line.id] = quant

        voyage_ids = transit_lines.mapped('voyage_id').ids
        shipment_by_voyage_id = {}

        if voyage_ids and 'supplier.shipment' in self.env.registry.models:
            shipments = self.env['supplier.shipment'].sudo().search([
                ('voyage_id', 'in', voyage_ids),
            ], order='id asc')
            for shipment in shipments:
                if shipment.voyage_id and shipment.voyage_id.id not in shipment_by_voyage_id:
                    shipment_by_voyage_id[shipment.voyage_id.id] = shipment

        result = defaultdict(lambda: {
            'qty': 0.0,
            'qty_m2': 0.0,
            'qty_pieces': 0.0,
            'count': 0,
            'shipment_count': 0,
            'shipment_groups': [],
            'eta_count': 0,
            'eta_groups': [],
            '_shipment_group_map': {},
            '_eta_group_map': {},
        })

        status_labels_cache = {}

        def _voyage_status_label(voyage):
            if not voyage:
                return ''
            model_name = voyage._name
            if model_name not in status_labels_cache:
                status_labels_cache[model_name] = dict(voyage._fields['custom_status'].selection)
            return status_labels_cache[model_name].get(voyage.custom_status, voyage.custom_status or '')

        def _shipment_status_label(shipment):
            if not shipment or 'status' not in shipment._fields:
                return ''
            labels = dict(shipment._fields['status'].selection)
            return labels.get(shipment.status, shipment.status or '')

        def _add_limited_name(target_set, value):
            if not value:
                return
            for part in str(value).split(','):
                clean = part.strip()
                if clean and clean not in ('PENDIENTE', 'SN', 'False'):
                    target_set.add(clean)

        for line in transit_lines:
            quant = valid_quant_by_line.get(line.id)
            if not quant:
                continue

            qty = min(line.product_uom_qty or 0.0, quant.quantity or 0.0)
            if not self._hub_float_gt_zero(qty):
                continue

            product = line.product_id
            voyage = line.voyage_id
            shipment = shipment_by_voyage_id.get(voyage.id) if voyage else False
            qty_m2, qty_pieces = self._split_qty_by_unit(product, qty)

            eta_date = voyage.eta if voyage else False
            eta_key = eta_date.strftime('%Y-%m-%d') if eta_date else 'sin_eta'
            eta_label = eta_date.strftime('%d/%m/%Y') if eta_date else 'Sin ETA'
            sort_key = eta_key if eta_date else '9999-12-31'

            shipment_key = 'voyage_%s' % voyage.id if voyage else 'line_%s' % line.id
            shipment_name = (
                (shipment.name if shipment else '')
                or (voyage.name if voyage else '')
                or 'Sin embarque'
            )

            product_bucket = result[product.id]
            product_bucket['qty'] += qty
            product_bucket['qty_m2'] += qty_m2
            product_bucket['qty_pieces'] += qty_pieces
            product_bucket['count'] += 1

            # Grupo principal: embarque/voyage.
            shipment_group_map = product_bucket['_shipment_group_map']
            if shipment_key not in shipment_group_map:
                shipment_group_map[shipment_key] = {
                    'key': shipment_key,
                    'shipment_id': shipment.id if shipment else False,
                    'shipment_name': shipment_name,
                    'shipment_model': 'supplier.shipment' if shipment else ('stock.transit.voyage' if voyage else ''),
                    'shipment_res_id': shipment.id if shipment else (voyage.id if voyage else False),
                    'voyage_id': voyage.id if voyage else False,
                    'voyage_name': voyage.name if voyage else '',
                    'eta': eta_key if eta_date else '',
                    'eta_label': eta_label,
                    'sort_key': sort_key,
                    'qty': 0.0,
                    'qty_m2': 0.0,
                    'qty_pieces': 0.0,
                    'lot_count': 0,
                    'container_count': 0,
                    'container_numbers': '',
                    'vendor_count': 0,
                    'vendor_names': '',
                    'purchase_count': 0,
                    'purchase_names': '',
                    'purchase_refs': [],
                    'status': voyage.custom_status if voyage else '',
                    'status_label': _voyage_status_label(voyage) or _shipment_status_label(shipment),
                    'bl_number': (shipment.bl_number if shipment and 'bl_number' in shipment._fields else '') or (voyage.bl_number if voyage else '') or '',
                    '_container_numbers': set(),
                    '_vendor_names': set(),
                    '_purchase_names': set(),
                    '_purchase_refs': {},
                }

            shipment_group = shipment_group_map[shipment_key]
            shipment_group['qty'] += qty
            shipment_group['qty_m2'] += qty_m2
            shipment_group['qty_pieces'] += qty_pieces
            shipment_group['lot_count'] += 1

            container_number = line.container_number or (voyage.container_number if voyage else '') or ''
            _add_limited_name(shipment_group['_container_numbers'], container_number)

            vendor_name = (
                (line.vendor_id.name if line.vendor_id else '')
                or (line.purchase_id.partner_id.name if line.purchase_id and line.purchase_id.partner_id else '')
                or (voyage.purchase_id.partner_id.name if voyage and voyage.purchase_id and voyage.purchase_id.partner_id else '')
                or (shipment.purchase_id.partner_id.name if shipment and shipment.purchase_id and shipment.purchase_id.partner_id else '')
            )
            _add_limited_name(shipment_group['_vendor_names'], vendor_name)

            purchase = line.purchase_id or (voyage.purchase_id if voyage else False) or (shipment.purchase_id if shipment else False)
            purchase_name = purchase.name if purchase else ''
            _add_limited_name(shipment_group['_purchase_names'], purchase_name)

            if purchase and purchase.exists() and purchase.name:
                shipment_group['_purchase_refs'][purchase.id] = purchase.name

            # Grupo secundario por ETA para conservar compatibilidad y tooltip.
            eta_group_map = product_bucket['_eta_group_map']
            if eta_key not in eta_group_map:
                eta_group_map[eta_key] = {
                    'key': eta_key,
                    'eta': eta_key if eta_date else '',
                    'eta_label': eta_label,
                    'sort_key': sort_key,
                    'qty': 0.0,
                    'qty_m2': 0.0,
                    'qty_pieces': 0.0,
                    'lot_count': 0,
                    'voyage_count': 0,
                    'voyage_names': '',
                    'container_count': 0,
                    'container_numbers': '',
                    'vendor_count': 0,
                    'vendor_names': '',
                    '_voyage_names': set(),
                    '_container_numbers': set(),
                    '_vendor_names': set(),
                }

            eta_group = eta_group_map[eta_key]
            eta_group['qty'] += qty
            eta_group['qty_m2'] += qty_m2
            eta_group['qty_pieces'] += qty_pieces
            eta_group['lot_count'] += 1
            _add_limited_name(eta_group['_voyage_names'], shipment_name)
            _add_limited_name(eta_group['_container_numbers'], container_number)
            _add_limited_name(eta_group['_vendor_names'], vendor_name)

        final_result = {}
        for product_id, bucket in result.items():
            shipment_groups = list(bucket.pop('_shipment_group_map', {}).values())
            eta_groups = list(bucket.pop('_eta_group_map', {}).values())

            for group in shipment_groups:
                container_numbers = sorted(group.pop('_container_numbers', set()))
                vendor_names = sorted(group.pop('_vendor_names', set()))
                purchase_names = sorted(group.pop('_purchase_names', set()))
                purchase_refs_map = group.pop('_purchase_refs', {})
                purchase_refs = [
                    {'id': purchase_id, 'name': purchase_name}
                    for purchase_id, purchase_name in sorted(
                        purchase_refs_map.items(),
                        key=lambda item: item[1] or '',
                    )
                ]

                group['container_count'] = len(container_numbers)
                group['vendor_count'] = len(vendor_names)
                group['purchase_count'] = len(purchase_names)
                group['container_numbers'] = ', '.join(container_numbers[:3])
                group['vendor_names'] = ', '.join(vendor_names[:3])
                group['purchase_names'] = ', '.join(purchase_names[:3])
                group['purchase_refs'] = purchase_refs[:3]
                group['purchase_more_count'] = max(len(purchase_refs) - 3, 0)

                if len(container_numbers) > 3:
                    group['container_numbers'] += ' +%s' % (len(container_numbers) - 3)
                if len(vendor_names) > 3:
                    group['vendor_names'] += ' +%s' % (len(vendor_names) - 3)
                if len(purchase_names) > 3:
                    group['purchase_names'] += ' +%s' % (len(purchase_names) - 3)

            for group in eta_groups:
                voyage_names = sorted(group.pop('_voyage_names', set()))
                container_numbers = sorted(group.pop('_container_numbers', set()))
                vendor_names = sorted(group.pop('_vendor_names', set()))

                group['voyage_count'] = len(voyage_names)
                group['container_count'] = len(container_numbers)
                group['vendor_count'] = len(vendor_names)
                group['voyage_names'] = ', '.join(voyage_names[:3])
                group['container_numbers'] = ', '.join(container_numbers[:3])
                group['vendor_names'] = ', '.join(vendor_names[:3])

                if len(voyage_names) > 3:
                    group['voyage_names'] += ' +%s' % (len(voyage_names) - 3)
                if len(container_numbers) > 3:
                    group['container_numbers'] += ' +%s' % (len(container_numbers) - 3)
                if len(vendor_names) > 3:
                    group['vendor_names'] += ' +%s' % (len(vendor_names) - 3)

            shipment_groups.sort(key=lambda item: (
                item.get('sort_key') or '9999-12-31',
                item.get('shipment_name') or '',
                item.get('key') or '',
            ))
            eta_groups.sort(key=lambda item: (item.get('sort_key') or '9999-12-31', item.get('key') or ''))

            bucket['shipment_groups'] = shipment_groups
            bucket['shipment_count'] = len(shipment_groups)
            bucket['eta_groups'] = eta_groups
            bucket['eta_count'] = len(eta_groups)
            bucket['lines'] = []  # compatibilidad frontend; no se envían lotes individuales.

            if shipment_groups:
                next_group = shipment_groups[0]
                bucket['next_eta'] = next_group.get('eta') or ''
                bucket['next_eta_label'] = next_group.get('eta_label') or ''
                bucket['next_eta_qty'] = next_group.get('qty') or 0.0
                bucket['next_eta_qty_m2'] = next_group.get('qty_m2') or 0.0
                bucket['next_eta_qty_pieces'] = next_group.get('qty_pieces') or 0.0
            else:
                bucket['next_eta'] = ''
                bucket['next_eta_label'] = ''
                bucket['next_eta_qty'] = 0.0
                bucket['next_eta_qty_m2'] = 0.0
                bucket['next_eta_qty_pieces'] = 0.0

            final_result[product_id] = bucket

        return final_result

    def _hub_get_open_po_qty_by_product(self, product_ids):
        if not product_ids:
            return {}

        PurchaseLine = self.env['purchase.order.line']
        po_lines = PurchaseLine.search([
            ('product_id', 'in', list(product_ids)),
            ('state', 'in', ['draft', 'sent', 'purchase']),
        ])

        qty_map = defaultdict(float)
        for po_line in po_lines:
            pending = (po_line.product_qty or 0.0) - (po_line.qty_received or 0.0)
            if pending > 0:
                qty_map[po_line.product_id.id] += pending

        return dict(qty_map)

    def _hub_get_lot_metadata(self, lot_ids):
        if not lot_ids:
            return {}

        lots = self.env['stock.lot'].browse(list(lot_ids)).exists()
        metadata = {}

        for lot in lots:
            lot_type = 'placa'
            if 'x_tipo' in lot._fields and lot.x_tipo:
                lot_type = str(lot.x_tipo).lower()

            fallback_qty = 0.0
            if 'x_alto' in lot._fields and 'x_ancho' in lot._fields:
                try:
                    fallback_qty = float(lot.x_alto or 0.0) * float(lot.x_ancho or 0.0)
                except Exception:
                    fallback_qty = 0.0

            metadata[lot.id] = {
                'type': lot_type,
                'fallback_qty': fallback_qty,
            }

        return metadata

    def _hub_get_line_lot_ids(self, sale_lines):
        if not sale_lines or 'lot_ids' not in sale_lines._fields:
            return {}, set()

        line_lot_ids = {}
        all_lot_ids = set()

        for line in sale_lines:
            ids = line.lot_ids.ids if line.lot_ids else []
            line_lot_ids[line.id] = ids
            all_lot_ids.update(ids)

        return line_lot_ids, all_lot_ids

    def _hub_get_breakdown_for_line(self, line):
        if 'x_lot_breakdown_json' not in line._fields:
            return {}
        return self._hub_parse_json_value(line.x_lot_breakdown_json)

    def _hub_get_active_purchase_qty_by_sale_line(self, sale_line_ids):
        return self._get_purchase_coverage_by_sale_line(sale_line_ids)

    def _hub_compute_sale_line_metrics(self, sale_lines):
        """Calcula cantidades de asignación en lote para evitar N+1 queries."""
        if not sale_lines:
            return {}, {}, {}

        product_ids = set(sale_lines.mapped('product_id').ids)
        line_lot_ids, all_lot_ids = self._hub_get_line_lot_ids(sale_lines)
        lot_metadata = self._hub_get_lot_metadata(all_lot_ids)
        internal_qty_by_product_lot = self._hub_get_internal_qty_by_product_lot(product_ids, all_lot_ids)
        free_qty_by_product = self._hub_get_free_internal_qty_by_product(product_ids)
        active_purchase_qty_by_line = self._hub_get_active_purchase_qty_by_sale_line(sale_lines.ids)

        metrics = {}

        for line in sale_lines:
            product = line.product_id
            requested_qty = float(line.product_uom_qty or 0.0)
            delivered_qty = float(line.qty_delivered or 0.0)
            breakdown = self._hub_get_breakdown_for_line(line)
            assigned_qty = 0.0

            for lot_id in line_lot_ids.get(line.id, []):
                lot_info = lot_metadata.get(lot_id, {})
                lot_type = lot_info.get('type') or 'placa'
                lot_key = str(lot_id)

                if lot_type in ('formato', 'pieza') and lot_key in breakdown:
                    try:
                        qty = float(breakdown.get(lot_key) or 0.0)
                    except Exception:
                        qty = 0.0
                else:
                    qty = internal_qty_by_product_lot.get((product.id, lot_id), 0.0)
                    if not self._hub_float_gt_zero(qty):
                        qty = lot_info.get('fallback_qty') or 0.0

                assigned_qty += qty or 0.0

            covered_qty = max(assigned_qty, delivered_qty)
            raw_pending_qty = max(requested_qty - covered_qty, 0.0)
            pending_qty = 0.0 if getattr(line, 'tc_assignment_closed', False) else raw_pending_qty
            active_purchase_qty = active_purchase_qty_by_line.get(line.id, 0.0)
            purchase_pending_qty = 0.0 if getattr(line, 'tc_assignment_closed', False) else max(raw_pending_qty - active_purchase_qty, 0.0)
            over_assigned_qty = max(assigned_qty - requested_qty, 0.0) if requested_qty > 0 else assigned_qty
            purchase_intent = bool(
                getattr(line, 'tc_stock_rejected', False)
                or getattr(line, 'auto_transit_assign', False)
            )
            available_qty = free_qty_by_product.get(product.id, 0.0)

            if requested_qty <= 0:
                assignment_state = 'no_demand'
                hub_state = 'nothing'
            elif getattr(line, 'tc_assignment_closed', False):
                assignment_state = 'closed_short' if self._hub_float_gt_zero(raw_pending_qty) else 'complete'
                hub_state = 'nothing'
            elif self._hub_float_gt_zero(over_assigned_qty):
                assignment_state = 'over_assigned'
                hub_state = 'allocated' if not self._hub_float_gt_zero(pending_qty) else 'to_be_allocated'
            elif not self._hub_float_gt_zero(raw_pending_qty):
                assignment_state = 'complete'
                hub_state = 'allocated'
            elif purchase_intent:
                assignment_state = 'to_purchase'
                hub_state = 'to_be_purchased'
            elif self._hub_float_gt_zero(covered_qty):
                assignment_state = 'partial'
                hub_state = 'to_be_allocated' if self._hub_float_gt_zero(available_qty) else 'to_be_purchased'
            else:
                assignment_state = 'open'
                hub_state = 'to_be_allocated' if self._hub_float_gt_zero(available_qty) else 'to_be_purchased'

            assigned_percent = (assigned_qty / requested_qty) * 100.0 if requested_qty > 0 else 0.0

            metrics[line.id] = {
                'requested_qty': requested_qty,
                'assigned_qty': assigned_qty,
                'covered_qty': covered_qty,
                'raw_pending_qty': raw_pending_qty,
                'pending_qty': pending_qty,
                'purchase_allocated_qty': active_purchase_qty,
                'purchase_pending_qty': purchase_pending_qty,
                'over_assigned_qty': over_assigned_qty,
                'available_qty': available_qty,
                'assigned_percent': assigned_percent,
                'assignment_state': assignment_state,
                'hub_state': hub_state,
                'purchase_intent': purchase_intent,
            }

        return metrics, free_qty_by_product, product_ids

    def _hub_get_payment_percent_map(self, sale_lines):
        orders = sale_lines.mapped('order_id')
        result = {}

        for order in orders:
            result[order.id] = self._get_payment_percent(order)

        return result

    def _hub_get_active_allocation_info_map(self, sale_line_ids):
        empty = self._get_empty_allocation_info()

        if not sale_line_ids:
            return {}

        Allocation = self.env['purchase.order.line.allocation'].sudo()
        allocations = Allocation.search([
            ('sale_line_id', 'in', list(sale_line_ids)),
            ('state', 'not in', ['cancelled', 'done']),
            ('purchase_order_id.state', '!=', 'cancel'),
        ], order='id desc')

        grouped = defaultdict(lambda: self.env['purchase.order.line.allocation'])

        for allocation in allocations:
            if allocation.sale_line_id:
                grouped[allocation.sale_line_id.id] |= allocation

        result = {}

        for sale_line_id, line_allocations in grouped.items():
            latest_allocation = line_allocations[:1]
            latest_po = latest_allocation.purchase_order_id if latest_allocation else False
            purchase_orders = line_allocations.mapped('purchase_order_id').filtered(
                lambda po: po and po.exists() and po.state != 'cancel'
            )
            po_names = []
            for po in purchase_orders:
                if po.name and po.name not in po_names:
                    po_names.append(po.name)

            result[sale_line_id] = {
                'allocation': latest_allocation,
                'po_name': ', '.join(po_names),
                'po_qty': sum(line_allocations.mapped('quantity')),
                'po_id': latest_po.id if latest_po else False,
                'po_state': latest_po.state if latest_po else '',
                'po_partner_id': latest_po.partner_id.id if latest_po and latest_po.partner_id else False,
                'po_partner_name': latest_po.partner_id.name if latest_po and latest_po.partner_id else '',
            }

        return defaultdict(lambda: dict(empty), result)

    def _hub_make_sale_line_row(self, line, metrics, payment_percent=False, allocation_info=False):
        order = line.order_id
        product = line.product_id
        unit_kind = self._get_product_unit_kind(product)
        unit_label = self._get_product_unit_label(product)
        unit_group_label = self._get_product_unit_group_label(product)
        requested_qty = metrics.get('requested_qty', line.product_uom_qty or 0.0)
        assigned_qty = metrics.get('assigned_qty', 0.0)
        pending_qty = metrics.get('pending_qty', 0.0)

        row = {
            'id': line.id,
            'so_id': order.id,
            'so_name': order.name,
            'client_ref': order.client_order_ref or '',
            'date': order.date_order.strftime('%Y-%m-%d') if order.date_order else '',
            'commitment_date': order.commitment_date.strftime('%Y-%m-%d') if order.commitment_date else 'N/A',
            'customer': order.partner_id.name,
            'customer_id': order.partner_id.id,
            'salesperson': order.user_id.name if order.user_id else '',
            'salesperson_id': order.user_id.id if order.user_id else False,
            'product_id': product.id,
            'product_name': product.display_name,
            'product_type': unit_group_label,
            'unit_kind': unit_kind,
            'unit_label': unit_label,
            'description': line.name or '',
            'qty_requested': requested_qty,
            'qty_ordered': requested_qty,
            'qty_assigned': assigned_qty,
            'qty_pending': pending_qty,
            'qty_available': metrics.get('available_qty', 0.0),
            'assignment_percent': metrics.get('assigned_percent', 0.0),
            'assignment_state': metrics.get('assignment_state') or '',
            'days_unassigned': self._get_days_without_assignment(order),
            'payment_percent': payment_percent if payment_percent is not False else 0.0,
            'note': order.note or '',
        }

        if allocation_info is not False:
            row.update({
                'location': order.partner_shipping_id.city or '',
                'qty_orig': requested_qty,
                'po_name': allocation_info.get('po_name', ''),
                'po_qty': allocation_info.get('po_qty', 0.0),
                'po_id': allocation_info.get('po_id', False),
                'po_state': allocation_info.get('po_state', ''),
                'po_partner_id': allocation_info.get('po_partner_id', False),
                'po_partner_name': allocation_info.get('po_partner_name', ''),
                'qty_purchase_covered': allocation_info.get('po_qty', 0.0),
                'stock_rejected': bool(getattr(line, 'tc_stock_rejected', False)),
            })
            row.update(self._split_qty_fields(product, 'qty_orig', requested_qty))
            row.update(self._split_qty_fields(product, 'po_qty', allocation_info.get('po_qty', 0.0)))

        row.update(self._split_qty_fields(product, 'qty_requested', requested_qty))
        row.update(self._split_qty_fields(product, 'qty_ordered', requested_qty))
        row.update(self._split_qty_fields(product, 'qty_assigned', assigned_qty))
        row.update(self._split_qty_fields(product, 'qty_pending', pending_qty))
        row.update(self._split_qty_fields(product, 'qty_available', metrics.get('available_qty', 0.0)))

        return row


class ToBeAllocatedLogic(models.AbstractModel):
    _name = 'sale.allocation.manager.logic'
    _inherit = 'allocation.hub.payment.mixin'
    _description = 'Lógica para el Tablero To Be Allocated'

    @api.model
    def get_data(self):
        sale_lines = self._hub_get_candidate_sale_lines()
        metrics_by_line, _free_qty_by_product, _product_ids = self._hub_compute_sale_line_metrics(sale_lines)
        payment_map = self._hub_get_payment_percent_map(sale_lines)

        result = []

        for line in sale_lines:
            metrics = metrics_by_line.get(line.id, {})

            if metrics.get('hub_state') != 'to_be_allocated':
                continue

            if not self._hub_float_gt_zero(metrics.get('pending_qty')):
                continue

            if not self._hub_float_gt_zero(metrics.get('available_qty')):
                continue

            result.append(
                self._hub_make_sale_line_row(
                    line,
                    metrics,
                    payment_percent=payment_map.get(line.order_id.id, 0.0),
                )
            )

        result.sort(
            key=lambda item: (
                -item.get('payment_percent', 0.0),
                item.get('commitment_date') or '',
                item.get('so_name') or '',
            )
        )

        return result

    @api.model
    def send_to_purchase(self, sale_line_ids, reason=False):
        lines = self.env['sale.order.line'].browse(sale_line_ids).exists()
        lines = lines.filtered(lambda line: self._is_hub_stock_product(line.product_id))

        if not lines:
            return {'error': 'No se encontraron líneas válidas de producto físico'}

        lines.action_tc_send_to_purchase(reason=reason)

        return {
            'success': True,
            'message': 'Línea(s) enviada(s) a To Be Purchased',
        }

    @api.model
    def close_short(self, sale_line_ids, reason=False, closure_action=False):
        lines = self.env['sale.order.line'].browse(sale_line_ids).exists()
        lines = lines.filtered(lambda line: self._is_hub_stock_product(line.product_id))

        if not lines:
            return {'error': 'No se encontraron líneas válidas de producto físico'}

        lines.action_tc_close_allocation_short(reason=reason, closure_action=closure_action)

        return {
            'success': True,
            'message': 'Pendiente cerrado correctamente',
        }


class ToBePurchasedLogic(models.AbstractModel):
    _name = 'purchase.manager.logic'
    _inherit = 'allocation.hub.payment.mixin'
    _description = 'Lógica para el Tablero To Be Purchased'

    @api.model
    def get_data(self):
        sale_lines_all = self._hub_get_candidate_sale_lines()
        metrics_by_line, free_qty_by_product, product_ids = self._hub_compute_sale_line_metrics(sale_lines_all)
        payment_map = self._hub_get_payment_percent_map(sale_lines_all)

        # Solo se muestran órdenes con pago/anticipo registrado. Si la SO no
        # tiene ningún cobro posteado (payment_percent == 0) no debe aparecer
        # en el tablero To Be Purchased.
        sale_lines = sale_lines_all.filtered(
            lambda line: metrics_by_line.get(line.id, {}).get('hub_state') == 'to_be_purchased'
            and self._hub_float_gt_zero(metrics_by_line.get(line.id, {}).get('purchase_pending_qty'))
            and self._hub_float_gt_zero(payment_map.get(line.order_id.id, 0.0))
        )

        product_ids = set(sale_lines.mapped('product_id').ids)
        products = self.env['product.product'].browse(list(product_ids))
        free_transit_info_by_product = self._hub_get_free_transit_info_by_product(product_ids)
        open_po_qty_by_product = self._hub_get_open_po_qty_by_product(product_ids)
        allocation_info_by_line = self._hub_get_active_allocation_info_map(sale_lines.ids)

        lines_by_product = defaultdict(lambda: self.env['sale.order.line'])
        for line in sale_lines:
            lines_by_product[line.product_id.id] |= line

        result = []

        for product in products:
            unit_kind = self._get_product_unit_kind(product)
            unit_label = self._get_product_unit_label(product)
            unit_group_label = self._get_product_unit_group_label(product)
            product_sale_lines = lines_by_product.get(product.id, self.env['sale.order.line'])

            so_details = []
            total_demanded = 0.0

            for sol in product_sale_lines:
                metrics = metrics_by_line.get(sol.id, {})
                raw_pending = metrics.get('pending_qty', 0.0)
                purchase_pending = metrics.get('purchase_pending_qty', 0.0)
                purchase_covered = metrics.get('purchase_allocated_qty', 0.0)

                if not self._hub_float_gt_zero(purchase_pending):
                    continue

                total_demanded += purchase_pending

                display_metrics = dict(metrics)
                display_metrics['pending_qty'] = purchase_pending

                row = self._hub_make_sale_line_row(
                    sol,
                    display_metrics,
                    payment_percent=payment_map.get(sol.order_id.id, 0.0),
                    allocation_info=allocation_info_by_line[sol.id],
                )
                row.update({
                    'qty_raw_pending': raw_pending,
                    'qty_purchase_pending': purchase_pending,
                    'qty_purchase_covered': purchase_covered,
                })
                row.update(self._split_qty_fields(product, 'qty_raw_pending', raw_pending))
                row.update(self._split_qty_fields(product, 'qty_purchase_pending', purchase_pending))
                row.update(self._split_qty_fields(product, 'qty_purchase_covered', purchase_covered))
                so_details.append(row)

            if not so_details:
                continue

            so_details.sort(
                key=lambda item: (
                    -item.get('payment_percent', 0.0),
                    item.get('commitment_date') or '',
                    item.get('so_name') or '',
                )
            )

            vendors = []
            for seller in product.seller_ids:
                vendors.append({
                    'id': seller.partner_id.id,
                    'name': seller.partner_id.name,
                    'price': seller.price,
                })

            vendor_name = vendors[0]['name'] if vendors else 'SIN PROVEEDOR'
            transit_info = free_transit_info_by_product.get(product.id, {})
            qty_a = free_qty_by_product.get(product.id, 0.0)
            qty_i = transit_info.get('qty', 0.0)
            qty_i_m2 = transit_info.get('qty_m2', 0.0)
            qty_i_pieces = transit_info.get('qty_pieces', 0.0)
            transit_free_shipment_groups = transit_info.get('shipment_groups', [])
            transit_free_eta_groups = transit_info.get('eta_groups', [])
            transit_free_count = transit_info.get('count', 0)
            transit_free_shipment_count = transit_info.get('shipment_count', len(transit_free_shipment_groups))
            transit_free_eta_count = transit_info.get('eta_count', len(transit_free_eta_groups))
            qty_p = open_po_qty_by_product.get(product.id, 0.0)
            # total_demanded ya viene neto de allocations/OC activas vinculadas a SO.
            # No se resta qty_p aquí porque eso descontaría de nuevo la OC ya ligada
            # y ocultaría incrementos reales de demanda.
            qty_to_buy = total_demanded

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
                'vendor': vendor_name,
                'vendors': vendors,
                'qty_a': qty_a,
                'qty_i': qty_i,
                'qty_i_free': qty_i,
                'qty_i_free_m2': qty_i_m2,
                'qty_i_free_pieces': qty_i_pieces,
                'transit_free_count': transit_free_count,
                'transit_free_shipment_count': transit_free_shipment_count,
                'transit_free_shipment_groups': transit_free_shipment_groups,
                'transit_free_eta_count': transit_free_eta_count,
                'transit_free_eta_groups': transit_free_eta_groups,
                'transit_free_next_eta': transit_info.get('next_eta', ''),
                'transit_free_next_eta_label': transit_info.get('next_eta_label', ''),
                'transit_free_next_qty': transit_info.get('next_eta_qty', 0.0),
                'transit_free_next_qty_m2': transit_info.get('next_eta_qty_m2', 0.0),
                'transit_free_next_qty_pieces': transit_info.get('next_eta_qty_pieces', 0.0),
                'transit_free_lines': [],
                'qty_p': qty_p,
                'qty_total': qty_a + qty_i + qty_p,
                'qty_so': total_demanded,
                'qty_to_buy': qty_to_buy,
                'so_lines': so_details,
            }

            row.update(self._split_qty_fields(product, 'qty_a', qty_a))
            row.update({
                'qty_i_m2': qty_i_m2,
                'qty_i_pieces': qty_i_pieces,
            })
            row.update(self._split_qty_fields(product, 'qty_p', qty_p))
            row.update(self._split_qty_fields(product, 'qty_total', qty_a + qty_i + qty_p))
            row.update(self._split_qty_fields(product, 'qty_so', total_demanded))
            row.update(self._split_qty_fields(product, 'qty_to_buy', qty_to_buy))

            result.append(row)

        result.sort(
            key=lambda product: (
                -max([
                    line.get('payment_percent', 0.0)
                    for line in product.get('so_lines', [])
                ] or [0.0]),
                product.get('name') or '',
            )
        )

        return result

    @api.model
    def get_open_purchase_orders(self, vendor_id):
        if not vendor_id:
            return []

        pos = self.env['purchase.order'].search([
            ('partner_id', '=', vendor_id),
            ('state', 'in', ['draft', 'sent', 'purchase']),
        ], order='create_date desc')

        state_labels = dict(self.env['purchase.order']._fields['state'].selection)

        return [{
            'id': po.id,
            'name': po.name,
            'date': po.date_order.strftime('%Y-%m-%d') if po.date_order else '',
            'origin': po.origin or '',
            'amount': po.amount_total,
            'lines_count': len(po.order_line),
            'state': po.state,
            'state_label': state_labels.get(po.state, po.state),
        } for po in pos]

    @api.model
    def get_all_vendors(self):
        partners = self.env['res.partner'].search([
            ('supplier_rank', '>', 0),
            ('active', '=', True),
        ], order='name')

        return [{'id': partner.id, 'name': partner.name} for partner in partners]

    def _get_transit_picking_type(self):
        domain_loc = [
            ('company_id', '=', self.env.company.id),
            '|', '|',
            ('name', '=', 'SOM/Transit'),
            ('name', 'ilike', 'Transit'),
            ('usage', '=', 'transit'),
        ]

        transit_loc = self.env['stock.location'].search(
            domain_loc,
            limit=1,
            order='name desc',
        )

        if not transit_loc:
            _logger.warning(
                "To Be Purchased: No se encontró ubicación de tránsito. "
                "La OC usará la ubicación por defecto."
            )
            return False

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('default_location_dest_id', '=', transit_loc.id),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

        if picking_type:
            _logger.info(
                "To Be Purchased: Asignado Picking Type %s (Destino: %s) a nueva OC.",
                picking_type.name,
                transit_loc.name,
            )
            return picking_type

        _logger.warning(
            "To Be Purchased: Ubicación %s encontrada, pero no hay Tipo de Operación Incoming asociado.",
            transit_loc.name,
        )
        return False

    def _prepare_purchase_line_uom_vals(self, product):
        PurchaseLine = self.env['purchase.order.line']
        vals = {}

        if 'product_uom_id' in PurchaseLine._fields:
            vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in PurchaseLine._fields:
            vals['product_uom'] = product.uom_id.id

        return vals

    def _float_differs(self, product, qty_a, qty_b):
        rounding = 0.0001
        if product and product.uom_id and product.uom_id.rounding:
            rounding = product.uom_id.rounding
        return abs((qty_a or 0.0) - (qty_b or 0.0)) > rounding

    def _sync_existing_purchase_allocation(self, sale_line, pending_qty):
        """
        Compatibilidad: ya no bloquea la creación de nuevas allocations.

        Antes este método devolvía True cuando encontraba cualquier OC activa,
        lo que hacía que To Be Purchased ignorara incrementos posteriores de
        demanda. Ahora solo calcula si queda un gap real por comprar.
        """
        return self._get_purchase_gap_qty_for_line(sale_line, raw_pending_qty=pending_qty) <= 0

    @api.model
    def cancel_pending(self, sale_line_ids, reason=False, closure_action=False):
        """
        Cancela/cierra pendientes desde To Be Purchased.

        Acciones permitidas en este hub:
        - settle: limpia el pendiente sin modificar cantidad ni descuento.
        - discount: limpia el pendiente y aplica descuento equivalente al faltante.

        No expone nota de crédito porque este flujo se usa para limpieza operativa
        y ajuste comercial por descuento.
        """
        action_value = closure_action or 'settle'

        if action_value not in ('settle', 'discount'):
            return {
                'error': 'Seleccione una acción válida: cancelar sin descuento o cancelar aplicando descuento.',
            }

        lines = self.env['sale.order.line'].browse(sale_line_ids).exists()

        lines = lines.filtered(
            lambda line: self._is_hub_stock_product(line.product_id)
            and line.state in ('sale', 'done')
            and line.tc_allocation_hub_state == 'to_be_purchased'
            and line.tc_qty_pending_allocation > 0
            and not line.tc_assignment_closed
        )

        if not lines:
            return {'error': 'No hay líneas válidas pendientes por cancelar en To Be Purchased'}

        if hasattr(lines, 'action_tc_cancel_purchase_pending'):
            lines.action_tc_cancel_purchase_pending(
                reason=reason or 'Pendiente de compra cancelado desde To Be Purchased.',
                closure_action=action_value,
            )
        else:
            lines.action_tc_close_allocation_short(
                reason=reason or 'Pendiente de compra cancelado desde To Be Purchased.',
                closure_action=action_value,
            )

        return {
            'success': True,
            'message': 'Pendiente(s) cancelado(s) correctamente',
            'closed_count': len(lines),
        }

    @api.model
    def create_purchase_orders(self, selected_line_ids, vendor_id=False, existing_po_id=False):
        candidate_lines = self.env['sale.order.line'].browse(selected_line_ids).exists()

        candidate_lines = candidate_lines.filtered(
            lambda line: self._is_hub_stock_product(line.product_id)
            and line.state in ('sale', 'done')
            and not line.tc_assignment_closed
        )

        line_data = []

        for line in candidate_lines:
            if hasattr(line, '_tc_get_pending_allocation_qty'):
                raw_pending = line._tc_get_pending_allocation_qty()
            else:
                raw_pending = line.tc_qty_pending_allocation

            purchase_gap = self._get_purchase_gap_qty_for_line(
                line,
                raw_pending_qty=raw_pending,
            )

            if self._hub_float_gt_zero(purchase_gap):
                line_data.append({
                    'sale_line': line,
                    'qty_pending': purchase_gap,
                    'raw_pending': raw_pending,
                    'already_purchased': max((raw_pending or 0.0) - purchase_gap, 0.0),
                })

        if not line_data:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin pendiente nuevo por comprar',
                    'message': 'La demanda seleccionada no tiene diferencial nuevo por comprar. Revise la columna OC vinculada/cubierto o use Mostrar todo para auditar.',
                    'type': 'warning',
                    'sticky': False,
                },
            }

        if not vendor_id:
            return {'error': 'Debe seleccionar un proveedor'}

        vendor = self.env['res.partner'].browse(vendor_id)
        if not vendor.exists():
            return {'error': 'Proveedor no encontrado'}

        po_vals = {
            'partner_id': vendor.id,
            'origin': ', '.join(sorted(set(data['sale_line'].order_id.name for data in line_data))),
            'company_id': self.env.company.id,
        }

        picking_type = self._get_transit_picking_type()
        if picking_type:
            po_vals['picking_type_id'] = picking_type.id

        if existing_po_id:
            po = self.env['purchase.order'].browse(existing_po_id)

            if not po.exists() or po.state not in ['draft', 'sent', 'purchase']:
                return {'error': 'La orden de compra no existe o no permite agregar pendientes.'}

            if po.partner_id.id != vendor.id:
                return {'error': 'La OC seleccionada pertenece a otro proveedor.'}

            if 'locked' in po._fields and po.locked:
                return {'error': 'La OC seleccionada está bloqueada. Cree una OC nueva para este incremento.'}

            new_origins = [data['sale_line'].order_id.name for data in line_data]
            current_origin = po.origin or ''

            for name in new_origins:
                if name and name not in current_origin:
                    current_origin += f", {name}" if current_origin else name

            po.write({'origin': current_origin})
        else:
            po = self.env['purchase.order'].create(po_vals)

        po_line_by_product = {}
        created_allocation_count = 0
        total_added_qty = 0.0

        for data in line_data:
            sale_line = data['sale_line']
            product = sale_line.product_id
            qty_pending = data['qty_pending']
            product_id = product.id

            linked_allocations_on_po = self._get_active_purchase_allocations(sale_line).filtered(
                lambda alloc: alloc.purchase_order_id.id == po.id
                and alloc.purchase_line_id
                and alloc.purchase_line_id.product_id.id == product_id
            )

            po_line_created_now = False

            if linked_allocations_on_po:
                po_line = linked_allocations_on_po[0].purchase_line_id
            elif product_id in po_line_by_product:
                po_line = po_line_by_product[product_id]
            else:
                existing_po_line = po.order_line.filtered(
                    lambda purchase_line: purchase_line.product_id.id == product_id
                )[:1]

                if existing_po_line:
                    po_line = existing_po_line
                else:
                    so_refs = ', '.join(sorted(set(
                        item['sale_line'].order_id.name
                        for item in line_data
                        if item['sale_line'].product_id.id == product_id
                    )))

                    po_line_vals = {
                        'order_id': po.id,
                        'product_id': product_id,
                        'product_qty': qty_pending,
                        'price_unit': product.standard_price,
                        'name': f"[{so_refs}] {product.name}",
                        'date_planned': fields.Datetime.now(),
                    }
                    po_line_vals.update(self._prepare_purchase_line_uom_vals(product))

                    po_line = self.env['purchase.order.line'].create(po_line_vals)
                    po_line_created_now = True

                po_line_by_product[product_id] = po_line

            if not po_line_created_now:
                po_line.write({
                    'product_qty': (po_line.product_qty or 0.0) + qty_pending,
                })

            self.env['purchase.order.line.allocation'].create({
                'purchase_line_id': po_line.id,
                'sale_line_id': sale_line.id,
                'quantity': qty_pending,
                'state': 'pending',
            })

            created_allocation_count += 1
            total_added_qty += qty_pending

            sale_line.with_context(skip_tc_allocation_recovery=True).write({
                'auto_transit_assign': True,
                'tc_stock_rejected': True,
                'tc_stock_rejected_reason': sale_line.tc_stock_rejected_reason or 'Pendiente enviado a compra desde To Be Purchased',
                'tc_stock_rejected_by': sale_line.tc_stock_rejected_by.id or self.env.user.id,
                'tc_stock_rejected_at': sale_line.tc_stock_rejected_at or fields.Datetime.now(),
            })

        if po.state == 'purchase':
            voyages = self.env['stock.transit.voyage'].search([
                ('purchase_id', '=', po.id),
                ('custom_status', '!=', 'cancel'),
            ])
            for voyage in voyages:
                voyage.action_load_from_purchase()

        po.message_post(body=(
            'To Be Purchased: pendiente incremental agregado.\n'
            'Cantidad agregada: %.3f\n'
            'Allocations nuevas: %s\n'
            'Pedidos: %s'
        ) % (
            total_added_qty,
            created_allocation_count,
            ', '.join(sorted(set(data['sale_line'].order_id.name for data in line_data))),
        ))

        return {
            'name': 'Orden de Compra',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }