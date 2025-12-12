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
            
            # --- CORRECCIÓN: Búsqueda flexible ---
            # 1. Intentar ubicación del picking original del viaje
            domain = [
                ('lot_id', '=', lot.id),
                ('product_id', '=', product.id),
                ('quantity', '>', 0),
            ]
            
            location_dest = False
            if transit_line.voyage_id.picking_id:
                location_dest = transit_line.voyage_id.picking_id.location_dest_id
            
            if location_dest:
                # Búsqueda específica en la ubicación destino real
                search_domain = domain + [('location_id', '=', location_dest.id)]
                quant = env['stock.quant'].sudo().search(search_domain, limit=1)
            
            if not quant:
                # 2. Búsqueda amplia (Internal OR Transit)
                search_domain = domain + ['|', ('location_id.usage', '=', 'internal'), ('location_id.usage', '=', 'transit')]
                quant = env['stock.quant'].sudo().search(search_domain, order='create_date desc, id desc', limit=1)
            
            if quant:
                transit_line.sudo().write({'quant_id': quant.id})
                _logger.info(f"TransitManager: Quant recuperado: {quant.id}")
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

        # === CREAR ORDEN DE RESERVA ===
        if new_partner_id:
            
            # A. Obtener Precio Máximo (USD 1)
            price_unit = 0.0
            if hasattr(product.product_tmpl_id, 'x_price_usd_1'):
                price_unit = product.product_tmpl_id.x_price_usd_1
            
            if price_unit <= 0:
                price_unit = product.list_price

            # B. Gestión de la Orden Padre (Header)
            order = hold_order_obj
            created_local_order = False

            if not order:
                project_id = False
                architect_id = False
                
                if new_order_id:
                    project_id_obj = getattr(new_order_id, 'x_project_id', False)
                    architect_id_obj = getattr(new_order_id, 'x_architect_id', False)
                    project_id = project_id_obj.id if project_id_obj else False
                    architect_id = architect_id_obj.id if architect_id_obj else False

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
                })
                created_local_order = True

            # C. Crear la Línea de la Orden de Reserva
            env['stock.lot.hold.order.line'].sudo().create({
                'order_id': order.id,
                'quant_id': quant.id,
                'lot_id': lot.id,
                'product_id': product.id,
                'cantidad_m2': transit_line.product_uom_qty, 
                'precio_unitario': price_unit,
            })

            # D. Confirmar si es local
            if created_local_order:
                order.action_confirm()
                _logger.info(f"TransitManager: Orden de Reserva {order.name} creada y confirmada para {lot.name}")

        return True