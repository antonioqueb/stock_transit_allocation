# -*- coding: utf-8 -*-
import logging
from odoo import fields, _

_logger = logging.getLogger(__name__)

class TransitManager:

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, new_order_id=False, notes=None):
        """
        Lógica central para reasignar y crear reservas (Holds).
        V4.0: Búsqueda agresiva de Quant y tolerancia a fallos.
        """
        lot = transit_line.lot_id
        product = transit_line.product_id
        
        # 1. RECUPERACIÓN DE QUANT (CRÍTICO)
        # Forzamos una búsqueda en la base de datos ignorando caché.
        quant = transit_line.quant_id
        
        if not quant or not quant.exists():
            # Estrategia 1: Buscar por ubicación destino exacta
            _logger.info(f"TransitManager: Buscando Quant perdido para lote {lot.name}...")
            
            # Buscamos en TODAS las ubicaciones internas donde pueda estar este lote con stock positivo
            quant = env['stock.quant'].sudo().search([
                ('lot_id', '=', lot.id),
                ('product_id', '=', product.id),
                ('quantity', '>', 0),
                ('location_id.usage', '=', 'internal') 
            ], order='create_date desc, id desc', limit=1)
            
            if quant:
                # ¡Encontrado! Lo vinculamos.
                transit_line.sudo().write({'quant_id': quant.id})
                _logger.info(f"TransitManager: Quant encontrado y vinculado: {quant.id} en {quant.location_id.name}")
            else:
                _logger.warning(f"TransitManager: IMPOSIBLE encontrar Quant físico para lote {lot.name}. Se procederá solo con asignación visual.")

        # 2. Actualizar línea de tránsito (Asignación visual en Torre de Control)
        # Esto SIEMPRE debe suceder, tenga o no tenga Quant físico.
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'order_id': new_order_id.id if new_partner_id else False,
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # 3. GESTIÓN DE LA RESERVA (HOLD) EN EL MÓDULO DE DIMENSIONES
        # Solo podemos crear el Hold si tenemos un Quant físico real.
        if not quant:
            return True # Terminamos aquí si no hay stock físico, pero ya guardamos la asignación visual.

        HoldModel = env['stock.lot.hold'].sudo()
        
        existing_hold = HoldModel.search([
            ('quant_id', '=', quant.id),
            ('estado', '=', 'activo')
        ], limit=1)

        if new_partner_id:
            # Obtener datos extra de la Orden de Venta
            project_id = False
            architect_id = False
            
            if new_order_id:
                project_id = new_order_id.x_project_id.id if new_order_id.x_project_id else False
                architect_id = new_order_id.x_architect_id.id if new_order_id.x_architect_id else False

            nota_final = notes or ''
            if new_order_id:
                nota_final += f"\nOrigen: Pedido {new_order_id.name} (Asignación Automática)"

            hold_vals = {
                'lot_id': lot.id,
                'quant_id': quant.id,
                'partner_id': new_partner_id.id,
                'user_id': env.user.id,
                'fecha_inicio': fields.Datetime.now(),
                'fecha_expiracion': fields.Datetime.add(fields.Datetime.now(), days=30), 
                'notas': nota_final,
                'company_id': transit_line.company_id.id or env.company.id,
                'estado': 'activo',
                'project_id': project_id,
                'arquitecto_id': architect_id,
            }

            if existing_hold:
                if existing_hold.partner_id.id != new_partner_id.id:
                    existing_hold.action_cancelar_hold()
                    HoldModel.create(hold_vals)
            else:
                HoldModel.create(hold_vals)
                _logger.info(f"TransitManager: Hold creado exitosamente para {lot.name}")
        
        elif not new_partner_id and existing_hold:
            existing_hold.action_cancelar_hold()

        return True