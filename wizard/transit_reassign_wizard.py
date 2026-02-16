# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..models.utils.transit_manager import TransitManager


class TransitReassignWizard(models.TransientModel):
    _name = 'transit.reassign.wizard'
    _description = 'Wizard de Reasignación en Tránsito'

    line_ids = fields.Many2many('stock.transit.line', string='Líneas a Reasignar')
    
    current_partner_id = fields.Many2one('res.partner', string='Cliente Actual', readonly=True)
    current_order_id = fields.Many2one('sale.order', string='Orden Actual', readonly=True)
    
    # =========================================================================
    # CAMPOS COMPUTADOS PARA FILTROS INTELIGENTES
    # =========================================================================
    product_ids = fields.Many2many(
        'product.product',
        compute='_compute_product_ids',
        string='Productos en Líneas'
    )
    
    eligible_partner_ids = fields.Many2many(
        'res.partner',
        compute='_compute_eligible_partners',
        string='Clientes Elegibles'
    )
    
    new_partner_id = fields.Many2one(
        'res.partner', 
        string='Nuevo Cliente',
        domain="[('id', 'in', eligible_partner_ids)]",
        help="Solo muestra clientes con pedidos confirmados que incluyan los productos seleccionados y tengan cantidad pendiente de entrega. Dejar vacío para liberar a Stock."
    )
    
    eligible_order_ids = fields.Many2many(
        'sale.order',
        compute='_compute_eligible_orders',
        string='Órdenes Elegibles'
    )
    
    new_order_id = fields.Many2one(
        'sale.order', 
        string='Asignar a Orden',
        domain="[('id', 'in', eligible_order_ids)]",
        help="Solo muestra órdenes del cliente seleccionado que contengan los productos y tengan cantidad pendiente."
    )
    
    reason = fields.Text(string='Motivo / Notas', required=True)

    # =========================================================================
    # CÓMPUTOS
    # =========================================================================

    @api.depends('line_ids')
    def _compute_product_ids(self):
        for wiz in self:
            wiz.product_ids = wiz.line_ids.mapped('product_id')

    @api.depends('product_ids')
    def _compute_eligible_partners(self):
        """
        Clientes elegibles: tienen al menos una SO confirmada con alguno de los
        productos seleccionados Y con cantidad pendiente de entrega (qty_delivered < product_uom_qty).
        """
        for wiz in self:
            if not wiz.product_ids:
                wiz.eligible_partner_ids = [(5, 0, 0)]
                continue
            
            sale_lines = self.env['sale.order.line'].search([
                ('product_id', 'in', wiz.product_ids.ids),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])
            # Filtrar solo las que tengan cantidad pendiente
            pending_lines = sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
            partner_ids = pending_lines.mapped('order_id.partner_id').ids
            wiz.eligible_partner_ids = [(6, 0, partner_ids)]

    @api.depends('product_ids', 'new_partner_id')
    def _compute_eligible_orders(self):
        """
        Órdenes elegibles: del cliente seleccionado, que contengan alguno de los
        productos seleccionados y tengan cantidad pendiente de entrega.
        """
        for wiz in self:
            if not wiz.product_ids or not wiz.new_partner_id:
                wiz.eligible_order_ids = [(5, 0, 0)]
                continue
            
            sale_lines = self.env['sale.order.line'].search([
                ('product_id', 'in', wiz.product_ids.ids),
                ('order_id.partner_id', '=', wiz.new_partner_id.id),
                ('order_id.state', 'in', ['sale', 'done']),
                ('display_type', '=', False),
            ])
            pending_lines = sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
            order_ids = pending_lines.mapped('order_id').ids
            wiz.eligible_order_ids = [(6, 0, order_ids)]

    # =========================================================================
    # ONCHANGE
    # =========================================================================

    @api.onchange('new_partner_id')
    def _onchange_new_partner_id(self):
        """Limpiar orden si cambia el cliente, auto-seleccionar si solo hay una."""
        self.new_order_id = False
        if not self.new_partner_id:
            return
        
        if not self.product_ids:
            return
        
        sale_lines = self.env['sale.order.line'].search([
            ('product_id', 'in', self.product_ids.ids),
            ('order_id.partner_id', '=', self.new_partner_id.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
        ])
        pending_lines = sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
        eligible_orders = pending_lines.mapped('order_id')
        
        if len(eligible_orders) == 1:
            self.new_order_id = eligible_orders[0]

    # =========================================================================
    # ACCIÓN PRINCIPAL
    # =========================================================================

    def action_apply(self):
        """Aplica la reasignación con validaciones y crea Orden de Reserva consolidada"""
        self.ensure_one()
        
        if self.new_partner_id and not self.new_order_id:
            raise UserError(_("No puede asignar mercancía a un cliente sin especificar a qué Orden de Venta pertenece."))

        hold_order = False

        # -----------------------------------------------------------------
        # PASO 1: Crear cabecera de Orden de Reserva (UNA SOLA VEZ)
        # -----------------------------------------------------------------
        if self.new_partner_id:
            project_id = getattr(self.new_order_id, 'x_project_id', False)
            architect_id = getattr(self.new_order_id, 'x_architect_id', False)
            
            currency = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
            if not currency:
                currency = self.env.company.currency_id

            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': self.new_partner_id.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'project_id': project_id.id if project_id else False,
                'arquitecto_id': architect_id.id if architect_id else False,
                'currency_id': currency.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Reasignación desde Tránsito.\nMotivo: {self.reason}\nPedido Destino: {self.new_order_id.name}",
            })

        # -----------------------------------------------------------------
        # PASO 2: Iterar líneas
        # -----------------------------------------------------------------
        for line in self.line_ids:
            TransitManager.reassign_lot(
                self.env, 
                line, 
                self.new_partner_id, 
                self.new_order_id, 
                self.reason,
                hold_order_obj=hold_order 
            )
            
            msg = f"🔄 <b>Reasignación:</b> Lote {line.lot_id.name}<br/>"
            msg += f"De: {self.current_partner_id.name or 'Stock'} → A: {self.new_partner_id.name or 'Stock'} ({self.new_order_id.name or '-'})"
            if line.voyage_id:
                line.voyage_id.message_post(body=msg)

        # -----------------------------------------------------------------
        # PASO 3: Confirmar Orden de Reserva
        # -----------------------------------------------------------------
        if hold_order:
            if hold_order.hold_line_ids:
                hold_order.action_confirm()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Reasignación Exitosa',
                        'message': f'Se generó la Orden de Reserva {hold_order.name} correctamente.',
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            else:
                hold_order.unlink()

        return {'type': 'ir.actions.act_window_close'}