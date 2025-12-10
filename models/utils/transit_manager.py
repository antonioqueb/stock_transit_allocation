# -*- coding: utf-8 -*-
import logging
from odoo import fields

_logger = logging.getLogger(__name__)

class TransitManager:
    """
    Clase utilitaria para centralizar la lógica de negocio de reasignaciones.
    Sigue el principio DRY para no repetir lógica en Wizards y Modelos.
    """

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, notes=None):
        """
        Lógica central para reasignar un lote en tránsito.
        1. Actualiza la línea de tránsito.
        2. Gestiona el stock.lot.hold (crear/actualizar/cancelar).
        """
        old_partner = transit_line.partner_id
        lot = transit_line.lot_id
        quant = transit_line.quant_id

        # 1. Actualizar línea de tránsito
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # 2. Gestionar Hold en stock_lot_dimensions
        # Buscar hold activo existente
        existing_hold = env['stock.lot.hold'].search([
            ('quant_id', '=', quant.id),
            ('estado', '=', 'activo')
        ], limit=1)

        # Si hay nuevo cliente, crear o actualizar hold
        if new_partner_id:
            hold_vals = {
                'lot_id': lot.id,
                'quant_id': quant.id,
                'partner_id': new_partner_id.id,
                'user_id': env.user.id,
                'fecha_inicio': fields.Datetime.now(),
                # Asumimos 30 días de tránsito o lógica de negocio
                'fecha_expiracion': fields.Datetime.add(fields.Datetime.now(), days=30), 
                'notas': notes or f'Asignación en Tránsito desde Viaje {transit_line.voyage_id.name}',
                'company_id': transit_line.company_id.id,
                'estado': 'activo'
            }

            if existing_hold:
                # Si el hold existe pero es para otro cliente, lo cancelamos y creamos uno nuevo
                # para mantener historial limpio, o lo actualizamos si es política laxa.
                # Aquí optamos por cancelar y crear para trazabilidad.
                existing_hold.action_cancelar_hold()
                env['stock.lot.hold'].create(hold_vals)
            else:
                env['stock.lot.hold'].create(hold_vals)
        
        # Si no hay nuevo cliente (liberación a stock) y existía hold
        elif not new_partner_id and existing_hold:
            existing_hold.action_cancelar_hold()

        return True
