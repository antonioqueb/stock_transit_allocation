# -*- coding: utf-8 -*-
from markupsafe import Markup
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class StockTransitLine(models.Model):
    _name = 'stock.transit.line'
    _description = 'Línea de Stock en Tránsito'
    _rec_name = 'lot_id'

    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='voyage_id.company_id', store=True)
    product_id = fields.Many2one('product.product', string='Descripción / Producto', required=True)

    lot_id = fields.Many2one('stock.lot', string='Lote / Placa', required=False)
    container_number = fields.Char(string='Contenedor')
    quant_id = fields.Many2one('stock.quant', string='Quant Físico')

    x_grosor = fields.Char(related='lot_id.x_grosor', string='Grosor', readonly=True)
    x_alto = fields.Float(related='lot_id.x_alto', string='Alto', readonly=True)
    x_ancho = fields.Float(related='lot_id.x_ancho', string='Ancho', readonly=True)

    product_uom_qty = fields.Float(string='M2 Embarcados', digits='Product Unit of Measure')

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

    allocation_id = fields.Many2one('purchase.order.line.allocation', string='Asignación Origen')

    allocation_status = fields.Selection([
        ('available', 'Disponible (Stock)'),
        ('reserved', 'Reservado / Vendido'),
    ], string='Estado Asignación', default='available', required=True)

    purchase_id = fields.Many2one('purchase.order', compute='_compute_purchase_id', string='OC Sistema', store=True)

    @api.depends('voyage_id.purchase_id', 'voyage_id.picking_id.purchase_id')
    def _compute_purchase_id(self):
        for line in self:
            line.purchase_id = line.voyage_id.purchase_id or line.voyage_id.picking_id.purchase_id

    date_order = fields.Datetime(related='purchase_id.date_order', string='Fecha OC', store=True)
    vendor_id = fields.Many2one('res.partner', related='purchase_id.partner_id', string='Proveedor', store=True)
    proforma_ref = fields.Char(related='purchase_id.partner_ref', string='Proforma / Ref Prov', store=True)
    salesperson_id = fields.Many2one('res.users', related='order_id.user_id', string='Vendedor', store=True)

    qty_proforma = fields.Float(string='Metraje Proforma', compute='_compute_po_so_qty', store=True)
    qty_original_demand = fields.Float(string='Metraje Pedido Original', compute='_compute_po_so_qty', store=True)

    voyage_status = fields.Selection(related='voyage_id.custom_status', string='Status', store=True)
    shipping_line = fields.Char(related='voyage_id.shipping_line', string='Naviera', store=True)
    bl_number = fields.Char(related='voyage_id.bl_number', string='Factura de Carga / BL', store=True)
    etd = fields.Date(related='voyage_id.etd', string='ETD', store=True)
    eta = fields.Date(related='voyage_id.eta', string='ETA', store=True)
    arrival_date = fields.Date(related='voyage_id.arrival_date', string='Llegada Real', store=True)
    notes = fields.Text(string='Comentarios')

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

    def write(self, vals):
        if self.env.context.get('skip_reservation_logic'):
            return super(StockTransitLine, self).write(vals)

        assignment_changed = 'partner_id' in vals or 'order_id' in vals

        old_assignments = {}
        if assignment_changed:
            for line in self:
                old_assignments[line.id] = {
                    'partner_id': line.partner_id.id if line.partner_id else False,
                    'order_id': line.order_id.id if line.order_id else False,
                }

        res = super(StockTransitLine, self).write(vals)

        if assignment_changed:
            for line in self:
                old = old_assignments.get(line.id, {})
                new_partner = line.partner_id
                new_order = line.order_id

                if old.get('partner_id') != (new_partner.id if new_partner else False) or \
                   old.get('order_id') != (new_order.id if new_order else False):

                    new_status = 'reserved' if (new_partner and new_order) else 'available'
                    if line.allocation_status != new_status:
                        super(StockTransitLine, line).write({'allocation_status': new_status})

                    if new_partner and new_order:
                        line._execute_reservation_logic(new_partner, new_order)
                    elif not new_partner:
                        line._execute_release_logic()

                    if line.voyage_id:
                        if new_partner and new_order:
                            msg = Markup("🔄 <b>Asignación:</b> %s<br/>→ %s / %s") % (
                                line.lot_id.name or line.product_id.name,
                                new_partner.name,
                                new_order.name,
                            )
                        elif new_partner and not new_order:
                            msg = Markup("👤 <b>Cliente asignado (sin orden aún):</b> %s → %s") % (
                                line.lot_id.name or line.product_id.name,
                                new_partner.name,
                            )
                        else:
                            msg = Markup("🔓 <b>Liberado a Stock:</b> %s") % (
                                line.lot_id.name or line.product_id.name,
                            )
                        line.voyage_id.message_post(body=msg)

        return res

    def _execute_reservation_logic(self, partner, order):
        self.ensure_one()

        if not self.lot_id or not self.quant_id:
            _logger.info(f"TransitLine {self.id}: Sin lote físico, solo asignación visual")
            return

        existing_hold = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo'),
        ], limit=1)

        if existing_hold:
            _logger.info(f"TransitLine {self.id}: Ya existe hold activo, verificando...")
            hold_partner = existing_hold.partner_id if hasattr(existing_hold, 'partner_id') else False
            if hold_partner and hold_partner == partner:
                return
            try:
                existing_hold.action_cancelar_hold()
            except Exception as e:
                _logger.warning(f"No se pudo cancelar hold existente: {e}")

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
            _logger.error(f"Error creando reserva: {e}")

    def _execute_release_logic(self):
        self.ensure_one()

        if not self.quant_id:
            return

        existing_holds = self.env['stock.lot.hold'].search([
            ('quant_id', '=', self.quant_id.id),
            ('estado', '=', 'activo'),
        ])

        for hold in existing_holds:
            try:
                hold.action_cancelar_hold()
                _logger.info(f"TransitLine {self.id}: Hold cancelado")
            except Exception as e:
                _logger.error(f"Error cancelando hold: {e}")

    @api.depends('purchase_id', 'order_id', 'product_id', 'allocation_id')
    def _compute_po_so_qty(self):
        for line in self:
            po_qty = line.allocation_id.quantity if line.allocation_id else 0.0
            so_qty = line.allocation_id.quantity if line.allocation_id else 0.0
            if not line.allocation_id:
                if line.purchase_id:
                    po_qty = sum(line.purchase_id.order_line.filtered(lambda l: l.product_id == line.product_id).mapped('product_qty'))
                if line.order_id:
                    so_qty = sum(line.order_id.order_line.filtered(lambda l: l.product_id == line.product_id).mapped('product_uom_qty'))
            line.qty_proforma = po_qty
            line.qty_original_demand = so_qty
