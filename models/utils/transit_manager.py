# -*- coding: utf-8 -*-
import logging
from odoo import fields, _

_logger = logging.getLogger(__name__)

class TransitManager:

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, new_order_id=False, notes=None, hold_order_obj=False):
        """
        Lógica central para reasignar y crear Órdenes de Reserva.
        Soporta reasignación visual (sin lote) y física (con lote/quant).
        """
        lot = transit_line.lot_id
        product = transit_line.product_id
        
        # =====================================================================
        # 1. VALIDACIÓN PARA ESTADO "SOLICITUD" (Sin Lote aún)
        # =====================================================================
        if not lot:
            # Si no hay lote, es una línea preventiva (etapa solicitud/producción)
            # Solo actualizamos la asignación visual en la línea de tránsito
            transit_line.write({
                'partner_id': new_partner_id.id if new_partner_id else False,
                'order_id': new_order_id.id if new_partner_id else False,
                'allocation_status': 'reserved' if new_partner_id else 'available'
            })
            _logger.info(f"TransitManager: Reasignación visual para producto {product.name} (Sin lote)")
            return True

        # =====================================================================
        # 2. RECUPERACIÓN DE QUANT (Cuando SÍ hay lote)
        # =====================================================================
        quant = transit_line.quant_id
        
        if not quant or not quant.exists():
            _logger.info(f"TransitManager: Buscando Quant para lote {lot.name}...")
            
            # Búsqueda flexible
            domain = [
                ('lot_id', '=', lot.id),
                ('product_id', '=', product.id),
                ('quantity', '>', 0),
            ]
            
            # Intentar ubicación del picking o búsqueda amplia
            location_dest = False
            if transit_line.voyage_id.picking_id:
                location_dest = transit_line.voyage_id.picking_id.location_dest_id
            
            if location_dest:
                search_domain = domain + [('location_id', '=', location_dest.id)]
                quant = env['stock.quant'].sudo().search(search_domain, limit=1)
            
            if not quant:
                search_domain = domain + ['|', ('location_id.usage', '=', 'internal'), ('location_id.usage', '=', 'transit')]
                quant = env['stock.quant'].sudo().search(search_domain, order='create_date desc, id desc', limit=1)
            
            if quant:
                transit_line.sudo().write({'quant_id': quant.id})
            else:
                _logger.warning(f"TransitManager: No se encontró quant físico para el lote {lot.name}")

        # =====================================================================
        # 3. ACTUALIZACIÓN VISUAL DE LA LÍNEA
        # =====================================================================
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'order_id': new_order_id.id if new_partner_id else False,
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # Si no hay quant físico localizado, no podemos realizar la reserva en el inventario
        if not quant:
            return True 

        # =====================================================================
        # 4. GESTIÓN DE LA ORDEN DE RESERVA (Hold Order)
        # =====================================================================
        
        # Caso: Liberación a Stock (No hay nuevo partner)
        if not new_partner_id:
            existing_holds = env['stock.lot.hold'].sudo().search([
                ('quant_id', '=', quant.id),
                ('estado', '=', 'activo')
            ])
            for h in existing_holds:
                h.action_cancelar_hold()
            return True

        # Caso: Asignación a nuevo cliente
        if new_partner_id:
            # Obtener precio para la reserva
            price_unit = 0.0
            if hasattr(product.product_tmpl_id, 'x_price_usd_1'):
                price_unit = product.product_tmpl_id.x_price_usd_1
            
            if price_unit <= 0:
                price_unit = product.list_price

            # Gestión de la cabecera (Header)
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
                    'notas': (notes or '') + " (Generado desde Torre de Control)",
                })
                created_local_order = True

            # Crear la línea de reserva
            env['stock.lot.hold.order.line'].sudo().create({
                'order_id': order.id,
                'quant_id': quant.id,
                'lot_id': lot.id,
                'product_id': product.id,
                'cantidad_m2': transit_line.product_uom_qty, 
                'precio_unitario': price_unit,
            })

            # Confirmar si la orden fue creada en este proceso
            if created_local_order:
                order.action_confirm()
                _logger.info(f"TransitManager: Reserva {order.name} confirmada para lote {lot.name}")

        return True