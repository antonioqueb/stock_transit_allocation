# -*- coding: utf-8 -*-
"""Historial de observaciones de Compras por línea del To Be Purchased.

Cada observación es una entrada propia con fecha y hora (28 sep 2026): el
botón (i) del tablero las lista de la más nueva a la más vieja y agrega
nuevas sin pisar las anteriores. `sale.order.line.tbp_note*` queda como
copia de la ÚLTIMA entrada (tooltip, búsqueda y color del botón).
"""
from odoo import api, fields, models
from odoo.tools.sql import column_exists


class TbpLineNote(models.Model):
    _name = 'tbp.line.note'
    _description = 'Observación de compra (To Be Purchased)'
    _order = 'note_date desc, id desc'

    sale_line_id = fields.Many2one(
        'sale.order.line', string='Línea de venta', required=True,
        index=True, ondelete='cascade')
    note = fields.Text(string='Observación', required=True)
    note_date = fields.Datetime(
        string='Fecha y hora', required=True, default=fields.Datetime.now, index=True)
    user_id = fields.Many2one(
        'res.users', string='Escribió', default=lambda self: self.env.user)
    company_id = fields.Many2one(
        related='sale_line_id.company_id', store=True, readonly=True)

    def init(self):
        # Migración: la observación única que ya existía (versión anterior
        # del (i)) pasa a ser la primera entrada del historial. Idempotente.
        if not column_exists(self.env.cr, "sale_order_line", "tbp_note"):
            return
        self.env.cr.execute("""
            INSERT INTO tbp_line_note (sale_line_id, note, note_date, user_id,
                                       company_id, create_uid, create_date,
                                       write_uid, write_date)
            SELECT sol.id, sol.tbp_note,
                   COALESCE(sol.tbp_note_date, sol.write_date, NOW() AT TIME ZONE 'UTC'),
                   sol.tbp_note_user_id, sol.company_id,
                   sol.tbp_note_user_id, NOW() AT TIME ZONE 'UTC',
                   sol.tbp_note_user_id, NOW() AT TIME ZONE 'UTC'
              FROM sale_order_line sol
             WHERE COALESCE(TRIM(sol.tbp_note), '') <> ''
               AND NOT EXISTS (SELECT 1 FROM tbp_line_note n WHERE n.sale_line_id = sol.id)
        """)

    @api.model
    def _som_sync_line_cache(self, sale_lines):
        """Copia la última entrada a los campos tbp_note* de la línea."""
        for line in sale_lines:
            last = self.search([('sale_line_id', '=', line.id)], limit=1)
            line.write({
                'tbp_note': last.note if last else False,
                'tbp_note_date': last.note_date if last else False,
                'tbp_note_user_id': last.user_id.id if last else False,
            })
