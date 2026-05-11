# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class StockTransitVoyagePackingReport(models.Model):
    _inherit = 'stock.transit.voyage'

    tc_has_packing_list = fields.Boolean(
        string='Tiene Packing List',
        compute='_compute_tc_packing_list_flags',
        compute_sudo=True,
    )

    tc_packing_list_count = fields.Integer(
        string='Packing Lists',
        compute='_compute_tc_packing_list_flags',
        compute_sudo=True,
    )

    # -------------------------------------------------------------------------
    # CÓMPUTOS
    # -------------------------------------------------------------------------

    def _compute_tc_packing_list_flags(self):
        for voyage in self:
            shipments = voyage._tc_get_supplier_shipments_for_packing_report()
            packing_count = 0
            has_rows = False

            for shipment in shipments:
                packing_count += len(shipment.packing_ids)
                if any(packing.row_ids for packing in shipment.packing_ids):
                    has_rows = True

            fallback_lines = voyage._tc_get_reportable_transit_lines_for_packing()

            voyage.tc_packing_list_count = packing_count
            voyage.tc_has_packing_list = bool(has_rows or fallback_lines)

    # -------------------------------------------------------------------------
    # RESOLUCIÓN DE DATOS
    # -------------------------------------------------------------------------

    def _tc_get_supplier_shipments_for_packing_report(self):
        self.ensure_one()

        if 'supplier.shipment' not in self.env.registry.models:
            return self.env['stock.transit.line']

        Shipment = self.env['supplier.shipment'].sudo()

        shipments = Shipment.search([
            ('voyage_id', '=', self.id),
        ], order='sequence asc, id asc')

        if not shipments and self.purchase_id:
            shipments = Shipment.search([
                ('purchase_id', '=', self.purchase_id.id),
            ], order='sequence asc, id asc')

        return shipments

    def _tc_get_reportable_transit_lines_for_packing(self):
        self.ensure_one()

        return self.line_ids.filtered(
            lambda line: line.product_id
            and line.product_uom_qty > 0
            and (
                line.lot_id
                or line.container_number
                or line.x_bloque
                or line.x_atado
            )
        )

    def _tc_lot_value_for_packing_report(self, lot, field_name, default=''):
        if lot and field_name in lot._fields:
            return getattr(lot, field_name) or default
        return default

    def _tc_lot_group_value_for_packing_report(self, lot):
        if lot and 'x_grupo' in lot._fields and lot.x_grupo:
            return ', '.join(lot.x_grupo.mapped('name'))
        return ''

    def _tc_owner_label_for_packing_report(self, transit_line):
        if not transit_line:
            return ''

        partner = transit_line.partner_id
        order = transit_line.order_id

        if transit_line.allocation_id:
            partner = partner or transit_line.allocation_id.partner_id
            order = order or transit_line.allocation_id.sale_order_id

        if order and not partner:
            partner = order.partner_id

        if partner and order:
            return '%s / %s' % (partner.display_name or partner.name or '', order.name or '')

        if partner:
            return partner.display_name or partner.name or ''

        if order:
            return order.name or ''

        if transit_line.allocation_status == 'available':
            return 'Stock libre'

        return ''

    def _tc_container_label_for_shipment(self, shipment, packing=False, row=False):
        container_names = []

        if row and row.container_id:
            container_names.append(row.container_id.container_number)

        if packing and packing.container_ids:
            container_names += packing.container_ids.mapped('container_number')

        if shipment and shipment.container_ids:
            container_names += shipment.container_ids.mapped('container_number')

        clean = []
        for name in container_names:
            if name and name not in clean:
                clean.append(name)

        if clean:
            return ', '.join(clean)

        return self.container_number or ''

    def _tc_find_matching_transit_line_for_packing_row(self, row):
        self.ensure_one()

        if not row or not row.product_id:
            return self.env['stock.transit.line']

        candidates = self.line_ids.filtered(
            lambda line: line.product_id.id == row.product_id.id
        )

        if not candidates:
            return self.env['stock.transit.line']

        if row.ref_proveedor:
            ref_candidates = candidates.filtered(
                lambda line: line.lot_id
                and (
                    self._tc_lot_value_for_packing_report(line.lot_id, 'x_referencia_proveedor')
                    == row.ref_proveedor
                    or line.lot_id.name == row.ref_proveedor
                )
            )
            if ref_candidates:
                return ref_candidates[:1]

        if row.bloque or row.numero_placa:
            block_candidates = candidates.filtered(
                lambda line: line.lot_id
                and (
                    not row.bloque
                    or self._tc_lot_value_for_packing_report(line.lot_id, 'x_bloque') == row.bloque
                )
                and (
                    not row.numero_placa
                    or self._tc_lot_value_for_packing_report(line.lot_id, 'x_numero_placa') == row.numero_placa
                )
            )
            if block_candidates:
                return block_candidates[:1]

        return self.env['stock.transit.line']

    def _tc_get_packing_report_rows_from_supplier_shipments(self):
        self.ensure_one()

        rows = []

        for shipment in self._tc_get_supplier_shipments_for_packing_report():
            for packing in shipment.packing_ids.sorted(lambda p: (p.packing_date or fields.Date.today(), p.id)):
                for row in packing.row_ids.sorted(lambda r: (
                    r.product_id.display_name or '',
                    r.container_id.container_number or '',
                    r.bloque or '',
                    r.numero_placa or '',
                    r.id,
                )):
                    product = row.product_id
                    tipo = row.tipo or 'Placa'

                    qty = row.area_m2 if tipo == 'Placa' else row.quantity
                    qty = qty or 0.0

                    matched_line = self._tc_find_matching_transit_line_for_packing_row(row)
                    owner_name = self._tc_owner_label_for_packing_report(matched_line) if matched_line else ''

                    rows.append({
                        'source': 'supplier_packing',
                        'shipment_name': shipment.name or '',
                        'packing_name': packing.packing_number or '',
                        'packing_date': packing.packing_date,
                        'container': self._tc_container_label_for_shipment(shipment, packing=packing, row=row),
                        'product_key': product.id,
                        'product_name': product.display_name or '',
                        'product_code': product.default_code or '',
                        'uom': product.uom_id.name or '',
                        'tipo': tipo,
                        'ref_interna': '',
                        'ref_proveedor': row.ref_proveedor or '',
                        'bloque': row.bloque or '',
                        'numero_placa': row.numero_placa or '',
                        'atado': row.atado or '',
                        'grupo': row.grupo_name or '',
                        'pedimento': row.pedimento or '',
                        'grosor': row.grosor or '',
                        'alto': row.alto or 0.0,
                        'ancho': row.ancho or 0.0,
                        'qty': qty,
                        'peso': row.peso or 0.0,
                        'color': row.color or '',
                        'owner': owner_name,
                    })

        return rows

    def _tc_get_packing_report_rows_from_transit_lines(self):
        self.ensure_one()

        rows = []

        for line in self._tc_get_reportable_transit_lines_for_packing().sorted(
            lambda l: (
                l.product_id.display_name or '',
                l.container_number or '',
                l.lot_id.name or '',
                l.id,
            )
        ):
            product = line.product_id
            lot = line.lot_id

            lot_type = ''
            if lot and 'x_tipo' in lot._fields and lot.x_tipo:
                lot_type = lot.x_tipo
            elif product.product_tmpl_id and 'x_unidad_del_producto' in product.product_tmpl_id._fields:
                lot_type = product.product_tmpl_id.x_unidad_del_producto or ''
            tipo = lot_type or 'Placa'

            alto = self._tc_lot_value_for_packing_report(lot, 'x_alto', 0.0)
            ancho = self._tc_lot_value_for_packing_report(lot, 'x_ancho', 0.0)

            rows.append({
                'source': 'transit_line',
                'shipment_name': self.name or '',
                'packing_name': '',
                'packing_date': False,
                'container': line.container_number or self.container_number or '',
                'product_key': product.id,
                'product_name': product.display_name or '',
                'product_code': product.default_code or '',
                'uom': product.uom_id.name or '',
                'tipo': tipo,
                'ref_interna': lot.name if lot else '',
                'ref_proveedor': self._tc_lot_value_for_packing_report(lot, 'x_referencia_proveedor'),
                'bloque': self._tc_lot_value_for_packing_report(lot, 'x_bloque') or line.x_bloque or '',
                'numero_placa': self._tc_lot_value_for_packing_report(lot, 'x_numero_placa'),
                'atado': self._tc_lot_value_for_packing_report(lot, 'x_atado') or line.x_atado or '',
                'grupo': self._tc_lot_group_value_for_packing_report(lot),
                'pedimento': self._tc_lot_value_for_packing_report(lot, 'x_pedimento'),
                'grosor': self._tc_lot_value_for_packing_report(lot, 'x_grosor') or line.x_grosor or '',
                'alto': alto or 0.0,
                'ancho': ancho or 0.0,
                'qty': line.product_uom_qty or 0.0,
                'peso': self._tc_lot_value_for_packing_report(lot, 'x_peso', 0.0),
                'color': self._tc_lot_value_for_packing_report(lot, 'x_color') or line.notes or '',
                'owner': self._tc_owner_label_for_packing_report(line),
            })

        return rows

    def _tc_get_packing_report_rows(self):
        self.ensure_one()

        rows = self._tc_get_packing_report_rows_from_supplier_shipments()

        if not rows:
            rows = self._tc_get_packing_report_rows_from_transit_lines()

        rows.sort(key=lambda r: (
            r.get('product_name') or '',
            r.get('container') or '',
            r.get('bloque') or '',
            r.get('numero_placa') or '',
            r.get('ref_interna') or '',
            r.get('ref_proveedor') or '',
        ))

        return rows

    def _tc_get_packing_report_totals(self):
        self.ensure_one()

        rows = self._tc_get_packing_report_rows()
        total_qty = sum(row.get('qty') or 0.0 for row in rows)
        total_weight = sum(row.get('peso') or 0.0 for row in rows)
        total_lines = len(rows)

        by_product = defaultdict(float)
        for row in rows:
            by_product[row.get('product_name') or 'Sin producto'] += row.get('qty') or 0.0

        return {
            'total_qty': total_qty,
            'total_weight': total_weight,
            'total_lines': total_lines,
            'product_count': len(by_product),
        }

    # -------------------------------------------------------------------------
    # ACCIONES
    # -------------------------------------------------------------------------

    def action_print_transit_packing_list(self):
        self.ensure_one()

        if not self.tc_has_packing_list:
            raise UserError(_(
                'Este embarque no tiene Packing List cargado ni líneas con lote para imprimir.'
            ))

        return self.env.ref(
            'stock_transit_allocation.action_report_transit_packing_list_voyage'
        ).report_action(self)


class PurchaseOrderTransitPackingReport(models.Model):
    _inherit = 'purchase.order'

    tc_has_transit_packing_list = fields.Boolean(
        string='Tiene Packing List de Embarque',
        compute='_compute_tc_transit_packing_list_flags',
        compute_sudo=True,
    )

    tc_transit_packing_list_count = fields.Integer(
        string='Packing Lists de Embarque',
        compute='_compute_tc_transit_packing_list_flags',
        compute_sudo=True,
    )

    def _tc_get_packing_report_voyages(self):
        self.ensure_one()

        if 'stock.transit.voyage' not in self.env.registry.models:
            return self.env['purchase.order']

        Voyage = self.env['stock.transit.voyage'].sudo()

        voyages = Voyage.search([
            ('purchase_id', '=', self.id),
            ('custom_status', '!=', 'cancel'),
        ], order='eta asc, id asc')

        voyages = voyages.filtered(lambda voyage: voyage.tc_has_packing_list)

        return voyages

    def _compute_tc_transit_packing_list_flags(self):
        for po in self:
            voyages = po._tc_get_packing_report_voyages()
            po.tc_has_transit_packing_list = bool(voyages)
            po.tc_transit_packing_list_count = sum(voyages.mapped('tc_packing_list_count')) or len(voyages)

    def action_print_transit_packing_list(self):
        self.ensure_one()

        voyages = self._tc_get_packing_report_voyages()

        if not voyages:
            raise UserError(_(
                'Esta orden de compra no tiene embarques con Packing List cargado.'
            ))

        return self.env.ref(
            'stock_transit_allocation.action_report_transit_packing_list_purchase'
        ).report_action(self)