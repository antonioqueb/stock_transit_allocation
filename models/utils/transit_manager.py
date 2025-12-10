# -*- coding: utf-8 -*-
import logging
from odoo import fields, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class TransitManager:

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, new_order_id=False, notes=None):
        """
        Lógica central para reasignar.
        Ahora incluye BÚSQUEDA INTELIGENTE de Quants perdidos.
        """
        old_partner = transit_line.partner_id
        lot = transit_line.lot_id
        
        # 1. Recuperación de Quant (Fix Missing required value)
        quant = transit_line.quant_id
        
        if not quant:
            # Estrategia de Rescate: Buscar el quant en ubicaciones de Tránsito
            # Solicitado: SOM/Transit
            _logger.info(f"TransitManager: Quant no vinculado en línea {transit_line.id}. Buscando en Tránsito...")
            
            domain = [
                ('lot_id', '=', lot.id),
                ('product_id', '=', transit_line.product_id.id),
                ('quantity', '>', 0),
                # Buscamos en cualquier ubicación que parezca ser de tránsito
                '|', ('location_id.name', 'ilike', 'Transit'), ('location_id.name', 'ilike', 'Trancit')
            ]
            
            # Intentar encontrarlo
            quant = env['stock.quant'].search(domain, order='id desc', limit=1)
            
            if quant:
                # Si lo encontramos, lo vinculamos para el futuro
                transit_line.write({'quant_id': quant.id})
                _logger.info(f"TransitManager: Quant encontrado y vinculado: {quant.id} en {quant.location_id.name}")
            else:
                # Si de plano no existe, es un error de datos físicos (no se recibió correctamente)
                # Intentamos una última búsqueda amplia en cualquier ubicación interna
                quant = env['stock.quant'].search([
                    ('lot_id', '=', lot.id),
                    ('location_id.usage', '=', 'internal'),
                    ('quantity', '>', 0)
                ], limit=1)
                
                if quant:
                     transit_line.write({'quant_id': quant.id})
        
        # Validación final antes de intentar crear el Hold
        if new_partner_id and not quant:
            # Si a pesar de todo no hay quant, no podemos crear el hold porque el módulo
            # stock_lot_dimensions lo requiere obligatoriamente.
            # Logueamos error pero no rompemos la transacción para no detener la validación del picking.
            _logger.error(f"CRITICAL: No se pudo crear Hold para Lote {lot.name}. No hay stock físico (Quant) detectado.")
            return False

        # 2. Actualizar línea de tránsito
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'order_id': new_order_id.id if new_partner_id else False,
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # 3. Gestionar Hold en stock_lot_dimensions
        # Ahora estamos seguros de que 'quant' existe si llegamos aquí
        existing_hold = env['stock.lot.hold'].search([
            ('quant_id', '=', quant.id),
            ('estado', '=', 'activo')
        ], limit=1)

        if new_partner_id:
            nota_final = notes or ''
            if new_order_id:
                nota_final += f" [Orden: {new_order_id.name}]"

            hold_vals = {
                'lot_id': lot.id,
                'quant_id': quant.id, # Aquí va el valor recuperado
                'partner_id': new_partner_id.id,
                'user_id': env.user.id,
                'fecha_inicio': fields.Datetime.now(),
                'fecha_expiracion': fields.Datetime.add(fields.Datetime.now(), days=30), 
                'notas': nota_final,
                'company_id': transit_line.company_id.id,
                'estado': 'activo'
            }

            if existing_hold:
                existing_hold.action_cancelar_hold()
                env['stock.lot.hold'].create(hold_vals)
            else:
                env['stock.lot.hold'].create(hold_vals)
        
        elif not new_partner_id and existing_hold:
            existing_hold.action_cancelar_hold()

        return True