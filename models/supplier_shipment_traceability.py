# -*- coding: utf-8 -*-
"""Trazabilidad de lotes dentro del formulario del embarque.

El inventario visual solo muestra existencias: un lote ya entregado desaparece
de ahí. Esta pestaña responde "¿qué pasó con cada lote del embarque?" sin
buscar placa por placa: estado actual, fechas clave, costo y acceso directo al
historial de movimientos.

Los lotes no guardan referencia a la fila del packing (la recepción física
puede renumerarlos), así que se resuelven por atributos en varios pases:
producto + bloque + no. placa → producto + no. placa + atado → producto +
bloque con candidato único.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError


TRACE_STATES = [
    ('en_transito', 'En tránsito'),
    ('recibido', 'Recibido'),
    ('libre', 'Libre'),
    ('hold', 'Apartado (hold)'),
    ('vendido', 'Vendido (por entregar)'),
    ('entregado', 'Entregado'),
    ('sin_stock', 'Sin existencia'),
    ('sin_lote', 'Sin lote'),
]


class SupplierShipment(models.Model):
    _inherit = 'supplier.shipment'

    packing_row_ids = fields.One2many(
        'supplier.shipment.packing.row', 'shipment_id',
        string='Líneas de todos los Packing Lists',
    )


class SupplierShipmentPackingRowTrace(models.Model):
    _inherit = 'supplier.shipment.packing.row'

    trace_lot_id = fields.Many2one(
        'stock.lot', string='Lote', compute='_compute_trace_info',
        compute_sudo=True,
    )
    trace_pi_po = fields.Char(
        string='PI / PO', compute='_compute_trace_pi_po',
        compute_sudo=True,
        help='Línea de compra (PO) y PI de las que proviene esta fila del PL, '
             'asignadas automáticamente al sincronizar el embarque.',
    )

    @api.depends('purchase_line_id', 'pi_header_id')
    def _compute_trace_pi_po(self):
        for row in self:
            parts = []
            line = row.purchase_line_id
            if line:
                parts.append(line.order_id.name or '')
                pi = (line.order_id.partner_ref
                      or (row.pi_header_id.proforma_number if row.pi_header_id else ''))
                if pi:
                    parts.append('PI %s' % pi)
            elif row.pi_header_id and row.pi_header_id.proforma_number:
                parts.append('PI %s' % row.pi_header_id.proforma_number)
            row.trace_pi_po = ' · '.join(p for p in parts if p)

    trace_state = fields.Selection(
        TRACE_STATES, string='Estado actual', compute='_compute_trace_info',
        compute_sudo=True,
    )
    trace_fecha_solicitud = fields.Date(
        string='Fecha de solicitud', compute='_compute_trace_info',
        compute_sudo=True,
        help='Fecha de la orden de compra.',
    )
    trace_fecha_atencion = fields.Date(
        string='Fecha de atención', compute='_compute_trace_info',
        compute_sudo=True,
        help='Fecha del Packing List capturado por el proveedor.',
    )
    trace_fecha_llegada = fields.Date(
        string='Llegada a bodega', compute='_compute_trace_info',
        compute_sudo=True,
        help='Fecha real del primer movimiento validado hacia una ubicación interna.',
    )
    trace_costo_unit = fields.Float(
        string='Costo unitario', compute='_compute_trace_info',
        compute_sudo=True, digits='Product Price',
        help='Precio unitario de la línea de la orden de compra.',
    )
    trace_subtotal = fields.Float(
        string='Subtotal', compute='_compute_trace_info',
        compute_sudo=True, digits='Product Price',
    )
    trace_sale_orders = fields.Char(
        string='Ventas', compute='_compute_trace_info', compute_sudo=True,
        help='Órdenes de venta donde el lote está o estuvo comprometido.',
    )

    # -------------------------------------------------------------------------
    # Resolución de lote por atributos
    # -------------------------------------------------------------------------
    def _trace_norm(self, value):
        return str(value or '').strip().upper()

    def _trace_build_lot_indexes(self):
        """Índices de lotes por atributos para todos los productos del set."""
        Lot = self.env['stock.lot'].sudo()
        product_ids = list(set(self.mapped('product_id').ids))
        if not product_ids:
            return {}, {}, {}

        lots = Lot.search([('product_id', 'in', product_ids)])

        by_bloque_placa = {}
        by_placa_atado = {}
        by_bloque = {}

        for lot in lots:
            pid = lot.product_id.id
            bloque = self._trace_norm(getattr(lot, 'x_bloque', ''))
            placa = self._trace_norm(getattr(lot, 'x_numero_placa', ''))
            atado = self._trace_norm(getattr(lot, 'x_atado', ''))

            if bloque and placa:
                by_bloque_placa.setdefault((pid, bloque, placa), []).append(lot)
            if placa:
                by_placa_atado.setdefault((pid, placa, atado), []).append(lot)
            if bloque:
                by_bloque.setdefault((pid, bloque), []).append(lot)

        return by_bloque_placa, by_placa_atado, by_bloque

    def _trace_resolve_lot(self, indexes):
        self.ensure_one()
        by_bloque_placa, by_placa_atado, by_bloque = indexes
        pid = self.product_id.id
        bloque = self._trace_norm(self.bloque)
        placa = self._trace_norm(self.numero_placa)
        atado = self._trace_norm(self.atado)

        candidates = by_bloque_placa.get((pid, bloque, placa)) or []
        if len(candidates) >= 1:
            return candidates[0]

        candidates = by_placa_atado.get((pid, placa, atado)) or []
        if len(candidates) == 1:
            return candidates[0]

        candidates = by_bloque.get((pid, bloque)) or []
        if len(candidates) == 1:
            return candidates[0]

        return self.env['stock.lot']

    # -------------------------------------------------------------------------
    # Cómputo principal (todo en batch: sin consultas por fila)
    # -------------------------------------------------------------------------
    def _compute_trace_info(self):
        indexes = self._trace_build_lot_indexes()

        # Resolver lote por fila y juntar el set completo.
        row_lot = {}
        all_lots = self.env['stock.lot'].sudo()
        for row in self:
            lot = row._trace_resolve_lot(indexes)
            row_lot[row.id] = lot
            all_lots |= lot

        lot_ids = all_lots.ids

        # --- Stock actual por lote (una consulta) ---
        internal_qty = {}
        transit_qty = {}
        production_qty = {}
        lot_has_hold = set()
        if lot_ids:
            Quant = self.env['stock.quant'].sudo()
            quants = Quant.search([
                ('lot_id', 'in', lot_ids),
                ('quantity', '>', 0),
            ])
            for q in quants:
                usage = q.location_id.usage
                lid = q.lot_id.id
                if usage == 'internal':
                    internal_qty[lid] = internal_qty.get(lid, 0.0) + q.quantity
                elif usage == 'transit':
                    transit_qty[lid] = transit_qty.get(lid, 0.0) + q.quantity
                elif usage == 'production':
                    production_qty[lid] = production_qty.get(lid, 0.0) + q.quantity
                if getattr(q, 'x_tiene_hold', False):
                    lot_has_hold.add(lid)

        # --- Ventas donde participa el lote (una consulta) ---
        lot_orders = {}
        SaleLine = self.env['sale.order.line'].sudo()
        if lot_ids and 'lot_ids' in SaleLine._fields:
            for sl in SaleLine.search([
                ('lot_ids', 'in', lot_ids),
                ('order_id.state', 'in', ['sale', 'done']),
            ]):
                for lot in sl.lot_ids:
                    if lot.id in set(lot_ids):
                        lot_orders.setdefault(lot.id, set()).add(sl.order_id.name)

        # --- Entregas al cliente y llegada a bodega (una consulta) ---
        delivered_qty = {}
        arrival_date = {}
        if lot_ids:
            MoveLine = self.env['stock.move.line'].sudo()
            done_mls = MoveLine.search(
                [('lot_id', 'in', lot_ids), ('state', '=', 'done')],
                order='date asc',
            )
            for ml in done_mls:
                lid = ml.lot_id.id
                dest_usage = ml.location_dest_id.usage
                if (
                    dest_usage == 'internal'
                    and ml.location_id.usage != 'internal'
                    and lid not in arrival_date
                ):
                    arrival_date[lid] = ml.date.date() if ml.date else False
                if dest_usage == 'customer':
                    qty = ml.quantity if 'quantity' in ml._fields else getattr(ml, 'qty_done', 0.0)
                    delivered_qty[lid] = delivered_qty.get(lid, 0.0) + (qty or 0.0)

        # --- Costos desde la orden de compra (en memoria) ---
        po_price = {}
        for row in self:
            po = row.purchase_id
            if po and po.id not in po_price:
                po_price[po.id] = {
                    line.product_id.id: line.price_unit
                    for line in po.order_line
                    if line.product_id
                }

        for row in self:
            lot = row_lot.get(row.id) or self.env['stock.lot']
            lid = lot.id if lot else 0

            row.trace_lot_id = lot
            row.trace_fecha_atencion = row.packing_id.packing_date or False

            po = row.purchase_id
            row.trace_fecha_solicitud = (
                po.date_order.date() if po and po.date_order else False
            )

            price = po_price.get(po.id, {}).get(row.product_id.id, 0.0) if po else 0.0
            row.trace_costo_unit = price
            row.trace_subtotal = price * (row.area_m2 or 0.0)

            if not lot:
                row.trace_state = 'sin_lote'
                row.trace_fecha_llegada = False
                row.trace_sale_orders = ''
                continue

            row.trace_fecha_llegada = arrival_date.get(lid, False)
            orders = sorted(lot_orders.get(lid, set()))
            row.trace_sale_orders = ', '.join(orders)

            if transit_qty.get(lid, 0.0) > 0:
                row.trace_state = 'en_transito'
            elif internal_qty.get(lid, 0.0) > 0:
                if lid in lot_has_hold:
                    row.trace_state = 'hold'
                elif orders:
                    row.trace_state = 'vendido'
                else:
                    row.trace_state = 'libre'
            elif production_qty.get(lid, 0.0) > 0:
                row.trace_state = 'recibido'
            elif delivered_qty.get(lid, 0.0) > 0:
                row.trace_state = 'entregado'
            elif arrival_date.get(lid):
                row.trace_state = 'sin_stock'
            else:
                row.trace_state = 'recibido' if lot else 'sin_lote'

    # -------------------------------------------------------------------------
    # Historial de movimientos del lote
    # -------------------------------------------------------------------------
    def action_view_lot_history(self):
        self.ensure_one()
        lot = self.trace_lot_id
        if not lot:
            raise UserError(_(
                'No se encontró un lote en el sistema para esta línea '
                '(aún no se recibe o sus datos cambiaron en la recepción física).'
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Historial de movimientos — %s') % lot.name,
            'res_model': 'stock.move.line',
            'view_mode': 'list,form',
            'domain': [('lot_id', '=', lot.id)],
            'context': {'create': False, 'edit': False},
        }


class StockLotTraceState(models.Model):
    """Estado actual + foto por lote, en batch, para el widget del viaje
    (Torre de Control): columnas tipo inventario visual en el PL a asignar."""
    _inherit = 'stock.lot'

    @api.model
    def som_trace_state_map(self, lot_ids):
        labels = dict(TRACE_STATES)
        lots = self.browse(lot_ids).exists()
        result = {}
        if not lots:
            return result

        ids = lots.ids

        internal, transit, production = {}, {}, {}
        has_hold = set()
        quant_by_lot = {}
        hold_info_by_lot = {}
        for q in self.env['stock.quant'].sudo().search([
            ('lot_id', 'in', ids), ('quantity', '>', 0),
        ]):
            usage = q.location_id.usage
            lid = q.lot_id.id
            if usage == 'internal':
                internal[lid] = internal.get(lid, 0.0) + q.quantity
                quant_by_lot.setdefault(lid, q.id)
            elif usage == 'transit':
                transit[lid] = transit.get(lid, 0.0) + q.quantity
            elif usage == 'production':
                production[lid] = production.get(lid, 0.0) + q.quantity
            if getattr(q, 'x_tiene_hold', False):
                has_hold.add(lid)
                # hold_info con la MISMA forma que el inventario visual
                hold = getattr(q, 'x_hold_activo_id', False)
                if hold and lid not in hold_info_by_lot:
                    hold_info_by_lot[lid] = {
                        'id': hold.id,
                        'partner_name': hold.partner_id.name if hold.partner_id else '',
                        'proyecto_nombre': hold.project_id.name if hasattr(hold, 'project_id') and hold.project_id else '',
                        'arquitecto_nombre': hold.arquitecto_id.name if hasattr(hold, 'arquitecto_id') and hold.arquitecto_id else '',
                        'vendedor_nombre': hold.user_id.name if hold.user_id else '',
                        'fecha_inicio': hold.fecha_inicio.strftime('%Y-%m-%d') if hasattr(hold, 'fecha_inicio') and hold.fecha_inicio else '',
                        'fecha_expiracion': hold.fecha_expiracion.strftime('%Y-%m-%d') if hasattr(hold, 'fecha_expiracion') and hold.fecha_expiracion else '',
                        'notas': hold.notas if hasattr(hold, 'notas') else '',
                    }

        in_sale = set()
        sale_orders_by_lot = {}
        SaleLine = self.env['sale.order.line'].sudo()
        if 'lot_ids' in SaleLine._fields:
            id_set = set(ids)
            for sl in SaleLine.search([
                ('lot_ids', 'in', ids),
                ('order_id.state', 'in', ['sale', 'done']),
            ]):
                for l in sl.lot_ids:
                    if l.id in id_set:
                        in_sale.add(l.id)
                        sale_orders_by_lot.setdefault(l.id, set()).add(
                            (sl.order_id.id, sl.order_id.name))

        delivered = set()
        lot_orders = {}
        for ml in self.env['stock.move.line'].sudo().search([
            ('lot_id', 'in', ids), ('state', '=', 'done'),
            ('location_dest_id.usage', '=', 'customer'),
        ]):
            lid = ml.lot_id.id
            delivered.add(lid)
            order = ml.move_id.sale_line_id.order_id or ml.picking_id.sale_id
            if order:
                lot_orders.setdefault(lid, set()).add((order.id, order.name))

        has_photo_field = 'x_fotografia_ids' in self._fields

        for lot in lots:
            lid = lot.id
            if transit.get(lid, 0.0) > 0:
                state = 'en_transito'
            elif internal.get(lid, 0.0) > 0:
                if lid in has_hold:
                    state = 'hold'
                elif lid in in_sale:
                    state = 'vendido'
                else:
                    state = 'libre'
            elif production.get(lid, 0.0) > 0:
                state = 'recibido'
            elif lid in delivered:
                state = 'entregado'
            else:
                state = 'sin_stock'

            photo_count = 0
            photo_id = False
            if has_photo_field and lot.x_fotografia_ids:
                photo_count = len(lot.x_fotografia_ids)
                photo_id = lot.x_fotografia_ids[0].id

            orders = set()
            orders |= sale_orders_by_lot.get(lid, set())
            orders |= lot_orders.get(lid, set())
            orders = sorted(orders, key=lambda o: o[1])

            result[lid] = {
                'state': state,
                'label': labels.get(state, state),
                'orders': [name for (_oid, name) in orders],
                'sale_order_ids': [oid for (oid, _name) in orders],
                'quant_id': quant_by_lot.get(lid, False),
                'tiene_hold': lid in has_hold,
                'hold_info': hold_info_by_lot.get(lid, False),
                'photo_count': photo_count,
                'photo_id': photo_id,
            }

        return result

    @api.model
    def som_get_lot_photos(self, lot_id):
        """Fotos del lote en base64 — mismo contrato que el inventario
        visual (get_lot_photos): la imagen viaja por RPC y se muestra en un
        diálogo, nunca vía /web/image."""
        lot = self.browse(lot_id).exists()
        if not lot:
            return {'error': 'Lote no encontrado'}

        photos = []
        if 'x_fotografia_ids' in lot._fields:
            for photo in lot.x_fotografia_ids:
                photos.append({
                    'id': photo.id,
                    'name': photo.name,
                    'image': photo.image,
                    'notas': getattr(photo, 'notas', '') or '',
                })

        return {
            'lot_name': lot.name,
            'product_name': lot.product_id.display_name,
            'photos': photos,
        }
