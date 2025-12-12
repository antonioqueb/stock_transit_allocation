# -*- coding: utf-8 -*-
import logging
from odoo import fields, _

_logger = logging.getLogger(__name__)

class TransitManager:

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, new_order_id=False, notes=None, hold_order_obj=False):
        """
        Lógica central para reasignar y crear Órdenes de Reserva.
        """
        lot = transit_line.lot_id
        product = transit_line.product_id
        
        # 1. RECUPERACIÓN DE QUANT (CRÍTICO)
        quant = transit_line.quant_id
        
        if not quant or not quant.exists():
            _logger.info(f"TransitManager: Buscando Quant perdido para lote {lot.name}...")
            quant = env['stock.quant'].sudo().search([
                ('lot_id', '=', lot.id),
                ('product_id', '=', product.id),
                ('quantity', '>', 0),
                ('location_id.usage', '=', 'internal') 
            ], order='create_date desc, id desc', limit=1)
            
            if quant:
                transit_line.sudo().write({'quant_id': quant.id})
            else:
                _logger.warning(f"TransitManager: IMPOSIBLE encontrar Quant físico para lote {lot.name}.")

        # 2. Actualizar línea de tránsito (Asignación visual)
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'order_id': new_order_id.id if new_partner_id else False,
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # 3. GESTIÓN DE LA ORDEN DE RESERVA
        # Si no hay quant físico, no podemos reservar
        if not quant:
            return True 

        # Si se libera (no hay partner), cancelamos holds activos
        if not new_partner_id:
            existing_holds = env['stock.lot.hold'].sudo().search([
                ('quant_id', '=', quant.id),
                ('estado', '=', 'activo')
            ])
            for h in existing_holds:
                h.action_cancelar_hold()
            return True

        # === NUEVA LÓGICA: CREAR ORDEN DE RESERVA ===
        if new_partner_id:
            
            # A. Obtener Precio Máximo (USD 1)
            # Intentamos obtener x_price_usd_1, si no existe usamos list_price
            price_unit = 0.0
            if hasattr(product.product_tmpl_id, 'x_price_usd_1'):
                price_unit = product.product_tmpl_id.x_price_usd_1
            
            # Fallback a precio de lista si es 0
            if price_unit <= 0:
                price_unit = product.list_price

            # B. Gestión de la Orden Padre (Header)
            order = hold_order_obj
            created_local_order = False

            if not order:
                # Si no nos pasaron una orden (ej. asignación automática), creamos una individual
                project_id = False
                architect_id = False
                
                # Intentamos sacar datos del Sale Order si existe
                if new_order_id:
                    project_id = getattr(new_order_id, 'x_project_id', False)
                    architect_id = getattr(new_order_id, 'x_architect_id', False)
                    # Extraer IDs
                    project_id = project_id.id if project_id else False
                    architect_id = architect_id.id if architect_id else False

                # Buscar moneda USD o fallback a compañía
                currency = env['res.currency'].search([('name', '=', 'USD')], limit=1)
                if not currency:
                    currency = env.company.currency_id

                order = env['stock.lot.hold.order'].sudo().create({
                    'partner_id': new_partner_id.id,
                    'user_id': env.user.id,
                    'company_id': transit_line.company_id.id or env.company.id,
                    'project_id': project_id,
                    'arquitecto_id': architect_id,
                    'currency_id': currency.id,
                    'fecha_orden': fields.Datetime.now(),
                    'notas': (notes or '') + " (Generado desde Tránsito)",
                    # La fecha de expiración se calcula automáticamente en el create del modelo hold.order
                })
                created_local_order = True

            # C. Crear la Línea de la Orden de Reserva
            env['stock.lot.hold.order.line'].sudo().create({
                'order_id': order.id,
                'quant_id': quant.id,
                'lot_id': lot.id,
                'product_id': product.id,
                'cantidad_m2': transit_line.product_uom_qty, # Usamos la cantidad del tránsito
                'precio_unitario': price_unit,
            })

            # D. Si creamos la orden aquí mismo (no vino del wizard), la confirmamos ya
            if created_local_order:
                order.action_confirm()
                _logger.info(f"TransitManager: Orden de Reserva {order.name} creada y confirmada para {lot.name}")

        return True