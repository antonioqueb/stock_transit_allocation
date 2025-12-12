# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # Aseguramos que este campo sea visible y editable para nuestra lógica
    sale_line_id = fields.Many2one('sale.order.line', string='Línea de Venta Origen', copy=False)

    def _prepare_stock_moves(self, picking):
        """
        Sobrescribimos este método (o lo extendemos) para asegurar que cuando
        se cree el Picking de recepción, el movimiento de stock (stock.move)
        lleve guardado el sale_line_id.
        """
        res = super(PurchaseOrderLine, self)._prepare_stock_moves(picking)
        
        for move_vals in res:
            # Si la línea de compra tiene un vínculo con venta, se lo pasamos al movimiento
            if self.sale_line_id:
                move_vals['sale_line_id'] = self.sale_line_id.id
                
                # También intentamos propagar el Grupo de Abastecimiento si existe en la venta
                if self.sale_line_id.order_id.procurement_group_id:
                    move_vals['group_id'] = self.sale_line_id.order_id.procurement_group_id.id
        
        return res