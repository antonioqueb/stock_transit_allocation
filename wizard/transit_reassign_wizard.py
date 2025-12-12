# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
# Asegúrate de que la ruta de importación coincida con tu estructura de carpetas
from ..models.utils.transit_manager import TransitManager

class TransitReassignWizard(models.TransientModel):
    _name = 'transit.reassign.wizard'
    _description = 'Wizard de Reasignación en Tránsito'

    line_ids = fields.Many2many('stock.transit.line', string='Líneas a Reasignar')
    
    current_partner_id = fields.Many2one('res.partner', string='Cliente Actual', readonly=True)
    current_order_id = fields.Many2one('sale.order', string='Orden Actual', readonly=True)
    
    new_partner_id = fields.Many2one('res.partner', string='Nuevo Cliente', 
        help="Dejar vacío para liberar a Stock")
    
    new_order_id = fields.Many2one('sale.order', string='Asignar a Orden', 
        domain="[('partner_id', '=', new_partner_id), ('state', 'in', ['sale', 'done'])]",
        help="Seleccione la Orden de Venta abierta de este cliente.")
    
    reason = fields.Text(string='Motivo / Notas', required=True)

    def action_apply(self):
        """Aplica la reasignación con validaciones y crea Orden de Reserva consolidada"""
        self.ensure_one()
        
        # Validación básica: Si hay cliente, debe haber pedido de venta
        if self.new_partner_id and not self.new_order_id:
            raise UserError(_("No puede asignar mercancía a un cliente sin especificar a qué Orden de Venta (Pedido) pertenece."))

        hold_order = False

        # -------------------------------------------------------------------------
        # PASO 1: Crear la cabecera de la Orden de Reserva (UNA SOLA VEZ)
        # -------------------------------------------------------------------------
        if self.new_partner_id:
            # Datos opcionales del proyecto/arquitecto desde la Sale Order (si existen campos 'x_')
            project_id = getattr(self.new_order_id, 'x_project_id', False)
            architect_id = getattr(self.new_order_id, 'x_architect_id', False)
            
            # Buscar moneda USD, fallback a moneda de la compañía
            currency = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
            if not currency:
                currency = self.env.company.currency_id

            # Creamos el objeto 'stock.lot.hold.order'
            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': self.new_partner_id.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'project_id': project_id.id if project_id else False,
                'arquitecto_id': architect_id.id if architect_id else False,
                'currency_id': currency.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Reasignación desde Tránsito.\nMotivo: {self.reason}\nPedido Origen: {self.new_order_id.name}",
            })

        # -------------------------------------------------------------------------
        # PASO 2: Iterar las líneas pasando el objeto 'hold_order' ya creado
        # -------------------------------------------------------------------------
        for line in self.line_ids:
            # Llamamos al Manager pasando 'hold_order_obj' para que NO cree una nueva, sino que use la existente
            TransitManager.reassign_lot(
                self.env, 
                line, 
                self.new_partner_id, 
                self.new_order_id, 
                self.reason,
                hold_order_obj=hold_order 
            )
            
            # Log en el chatter del viaje (Voyage)
            msg = f"🔄 <b>Reasignación:</b> Lote {line.lot_id.name}<br/>"
            msg += f"A: {self.new_partner_id.name or 'Stock'} ({self.new_order_id.name or '-'})"
            if line.voyage_id:
                line.voyage_id.message_post(body=msg)

        # -------------------------------------------------------------------------
        # PASO 3: Confirmar la Orden de Reserva al finalizar el bucle
        # -------------------------------------------------------------------------
        if hold_order:
            # Verificar si realmente se crearon líneas (puede que algunos quants no existieran y se saltaron)
            if hold_order.hold_line_ids:
                hold_order.action_confirm()
                
                # Notificación visual 'Sticky' de éxito
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
                # Si no se crearon líneas (ej. no había quants físicos encontrados), borramos la cabecera vacía
                hold_order.unlink()

        return {'type': 'ir.actions.act_window_close'}