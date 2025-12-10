# -*- coding: utf-8 -*-
import logging
from odoo import fields

_logger = logging.getLogger(__name__)

class TransitManager:

    @staticmethod
    def reassign_lot(env, transit_line, new_partner_id, new_order_id=False, notes=None):
        """
        Lógica central para reasignar. 
        Ahora requiere new_order_id si hay partner.
        """
        old_partner = transit_line.partner_id
        lot = transit_line.lot_id
        quant = transit_line.quant_id

        # 1. Actualizar línea de tránsito
        transit_line.write({
            'partner_id': new_partner_id.id if new_partner_id else False,
            'order_id': new_order_id.id if new_partner_id else False, # Si borramos partner, borramos orden
            'allocation_status': 'reserved' if new_partner_id else 'available'
        })

        # 2. Gestionar Hold en stock_lot_dimensions
        existing_hold = env['stock.lot.hold'].search([
            ('quant_id', '=', quant.id),
            ('estado', '=', 'activo')
        ], limit=1)

        if new_partner_id:
            # Crear nota con referencia a la orden
            nota_final = notes or ''
            if new_order_id:
                nota_final += f" [Orden: {new_order_id.name}]"

            hold_vals = {
                'lot_id': lot.id,
                'quant_id': quant.id,
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