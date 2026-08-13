# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class StockLot(models.Model):
    _inherit = 'stock.lot'

    def write(self, vals):
        res = super().write(vals)
        # PISO SOLICITADO ≥ ASIGNADO tras cambiar MEDIDAS: el Asignado de la
        # línea de venta se calcula EN VIVO desde el lote, así que corregir
        # alto/ancho (worksheet, recla, captura) puede subirlo sin que nadie
        # escriba la línea — dejando Solicitado < Asignado sin que el piso de
        # write() pudiera intervenir. Se re-corre el ratchet (solo SUBE).
        if {'x_alto', 'x_ancho'} & set(vals or {}):
            self._tc_ratchet_open_sale_lines()
        return res

    def _tc_ratchet_open_sale_lines(self):
        """Re-aplica el ratchet Solicitado≥Asignado en las líneas de venta
        ABIERTAS que tienen estos lotes asignados."""
        if not self:
            return
        lines = self.env['sale.order.line'].sudo().search([
            ('lot_ids', 'in', self.ids),
            ('order_id.state', 'in', ('draft', 'sent', 'sale')),
            ('display_type', '=', False),
        ])
        if not lines:
            return
        try:
            lines._tc_sync_requested_qty_from_lots()
        except Exception:
            _logger.exception(
                '[TC FLOOR] Fallo re-aplicando el ratchet tras cambio de '
                'medidas en lotes %s.', self.mapped('name'))

    # Bitácora de etiquetado ZPL (KPI 6.4): se estampa la PRIMERA vez que el
    # lote sale en una impresión de etiquetas de recepción.
    x_zpl_printed_at = fields.Datetime(
        string='Etiqueta ZPL impresa el',
        readonly=True,
        copy=False,
    )


class StockTransitVoyageEtaOriginal(models.Model):
    _inherit = 'stock.transit.voyage'

    # ETA original (KPI 4.4 OTIF): la primera ETA prometida se conserva
    # aunque la API/usuario la actualice después.
    eta_original = fields.Date(
        string='ETA original',
        readonly=True,
        copy=False,
        help='Primera ETA registrada del viaje. La ETA vigente puede '
             'actualizarse (ShipsGo/manual); esta no, para medir OTIF.',
    )

    def write(self, vals):
        if vals.get('eta'):
            for voyage in self:
                if not voyage.eta_original:
                    vals_v = dict(vals)
                    vals_v['eta_original'] = voyage.eta or vals['eta']
                    super(StockTransitVoyageEtaOriginal, voyage).write(vals_v)
                else:
                    super(StockTransitVoyageEtaOriginal, voyage).write(vals)
            return True
        return super().write(vals)

    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]
        for vals in vals_list:
            if vals.get('eta') and not vals.get('eta_original'):
                vals['eta_original'] = vals['eta']
        return super().create(vals_list)
