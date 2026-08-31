# -*- coding: utf-8 -*-
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..models.utils.transit_manager import TransitManager


class TransitReassignWizard(models.TransientModel):
    _name = 'transit.reassign.wizard'
    _description = 'Wizard de Reasignación en Tránsito'

    line_ids = fields.Many2many('stock.transit.line', string='Líneas a Reasignar')
    
    current_partner_id = fields.Many2one('res.partner', string='Cliente Actual', readonly=True)
    current_order_id = fields.Many2one('sale.order', string='Orden Actual', readonly=True)
    
    # Productos derivados de las líneas seleccionadas
    product_ids = fields.Many2many(
        'product.product',
        string='Productos en Líneas',
        readonly=True,
    )
    
    # =========================================================================
    # CAMPOS M2M REALES (no computados) para que el domain funcione
    # en modelos transient. Se llenan en default_get y onchange.
    # =========================================================================
    eligible_partner_ids = fields.Many2many(
        'res.partner',
        'transit_reassign_eligible_partners_rel',
        'wizard_id', 'partner_id',
        string='Clientes Elegibles',
    )
    
    eligible_order_ids = fields.Many2many(
        'sale.order',
        'transit_reassign_eligible_orders_rel',
        'wizard_id', 'order_id',
        string='Órdenes Elegibles',
    )
    
    new_partner_id = fields.Many2one(
        'res.partner', 
        string='Nuevo Cliente',
        domain="[('id', 'in', eligible_partner_ids)]",
        help="Solo clientes con pedidos confirmados que incluyan los productos seleccionados y tengan cantidad pendiente."
    )
    
    new_order_id = fields.Many2one(
        'sale.order', 
        string='Asignar a Orden',
        domain="[('id', 'in', eligible_order_ids)]",
        help="Solo órdenes del cliente con los productos y cantidad pendiente."
    )
    
    reason = fields.Text(string='Motivo / Notas', required=True)

    # =========================================================================
    # DEFAULT GET: Calcular elegibles al abrir el wizard
    # =========================================================================

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        
        active_ids = self.env.context.get('active_ids', [])
        if not active_ids:
            return res
        
        lines = self.env['stock.transit.line'].browse(active_ids)
        if not lines:
            return res
        
        product_ids = lines.mapped('product_id').ids

        # Calcular clientes elegibles (solo pedidos de la compañía de las líneas)
        partner_ids = self.with_context(
            tc_reassign_company_id=lines[:1].company_id.id or False,
        )._get_eligible_partner_ids(product_ids)
        
        # Info del cliente actual (tomamos el primero si todos son iguales)
        current_partners = lines.mapped('partner_id')
        current_orders = lines.mapped('order_id')
        
        res.update({
            'line_ids': [(6, 0, lines.ids)],
            'product_ids': [(6, 0, product_ids)],
            'eligible_partner_ids': [(6, 0, partner_ids)],
            'current_partner_id': current_partners[0].id if len(current_partners) == 1 else False,
            'current_order_id': current_orders[0].id if len(current_orders) == 1 else False,
        })
        
        return res

    # =========================================================================
    # MÉTODOS DE CÁLCULO DE ELEGIBLES
    # =========================================================================

    def _tc_company_domain(self):
        """Acota las ventas elegibles a la compañía de las líneas de tránsito
        (por contexto en default_get; por line_ids ya cargadas después)."""
        company_id = self.env.context.get('tc_reassign_company_id')
        if not company_id and self:
            company_id = self.line_ids[:1].company_id.id
        if not company_id:
            return []
        return [('order_id.company_id', '=', company_id)]

    @api.model
    def _get_eligible_partner_ids(self, product_ids):
        """Clientes con SO confirmadas que tengan estos productos con qty pendiente."""
        if not product_ids:
            return []

        sale_lines = self.env['sale.order.line'].search([
            ('product_id', 'in', product_ids),
            ('order_id.state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
        ] + self._tc_company_domain())
        pending_lines = sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
        return pending_lines.mapped('order_id.partner_id').ids

    def _get_eligible_order_ids(self, product_ids, partner_id):
        """Órdenes del partner con estos productos y qty pendiente."""
        if not product_ids or not partner_id:
            return []

        sale_lines = self.env['sale.order.line'].search([
            ('product_id', 'in', product_ids),
            ('order_id.partner_id', '=', partner_id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('display_type', '=', False),
        ] + self._tc_company_domain())
        pending_lines = sale_lines.filtered(lambda l: l.qty_delivered < l.product_uom_qty)
        return pending_lines.mapped('order_id').ids

    # =========================================================================
    # ONCHANGE
    # =========================================================================

    @api.onchange('new_partner_id')
    def _onchange_new_partner_id(self):
        """Al cambiar cliente: recalcular órdenes elegibles y limpiar selección."""
        self.new_order_id = False
        
        if not self.new_partner_id or not self.product_ids:
            self.eligible_order_ids = [(5, 0, 0)]
            return
        
        order_ids = self._get_eligible_order_ids(self.product_ids.ids, self.new_partner_id.id)
        self.eligible_order_ids = [(6, 0, order_ids)]
        
        # Auto-seleccionar si solo hay una orden
        if len(order_ids) == 1:
            self.new_order_id = order_ids[0]

    # =========================================================================
    # ACCIÓN PRINCIPAL
    # =========================================================================

    def action_apply(self):
        """Aplica la reasignación con validaciones y crea Orden de Reserva consolidada"""
        self.ensure_one()
        
        if self.new_partner_id and not self.new_order_id:
            raise UserError(_("No puede asignar mercancía a un cliente sin especificar a qué Orden de Venta pertenece."))

        hold_order = False

        if self.new_partner_id:
            project_id = getattr(self.new_order_id, 'x_project_id', False)
            architect_id = getattr(self.new_order_id, 'x_architect_id', False)

            # Compañía de las líneas de tránsito (documento), no la del usuario.
            company = self.line_ids[:1].company_id or self.env.company

            currency = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
            if not currency:
                currency = company.currency_id

            hold_order = self.env['stock.lot.hold.order'].with_company(company).create({
                'partner_id': self.new_partner_id.id,
                'user_id': self.env.user.id,
                'company_id': company.id,
                'project_id': project_id.id if project_id else False,
                'arquitecto_id': architect_id.id if architect_id else False,
                'currency_id': currency.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Reasignación desde Tránsito.\nMotivo: {self.reason}\nPedido Destino: {self.new_order_id.name}",
            })

        for line in self.line_ids:
            TransitManager.reassign_lot(
                self.env, 
                line, 
                self.new_partner_id, 
                self.new_order_id, 
                self.reason,
                hold_order_obj=hold_order 
            )
            
            old_name = self.current_partner_id.name or 'Stock'
            new_name = self.new_partner_id.name or 'Stock'
            msg = Markup("🔄 <b>Reasignación:</b> %s<br/>De: %s → A: %s (%s)") % (
                line.lot_id.name or line.product_id.name,
                old_name,
                new_name,
                self.new_order_id.name or '-'
            )
            if line.voyage_id:
                line.voyage_id.message_post(body=msg)

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