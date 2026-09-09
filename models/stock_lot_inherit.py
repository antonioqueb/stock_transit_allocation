# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

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

    @api.model
    def _som_lot_ids_in_transit(self, lot_ids, company_id=None):
        """Subconjunto de ``lot_ids`` que REALMENTE viene en tránsito.

        Un lote cuenta como en tránsito solo si su existencia en ubicaciones
        de tránsito es MAYOR que lo que ya salió de tránsito hacia almacén
        (move lines hechas tránsito → interna). Así se descartan los
        RESIDUOS: placas que la recepción física movió por la cantidad de la
        línea del viaje (p.ej. 4.07) cuando el quant traía la del packing
        list (4.08) y dejaron 0.01 m² colgando en SOM/TRANSIT. Incidencia
        S51 (9 sep 2026): 31 residuos de 0.01–0.10 m² pintaban 28 placas ya
        ENTREGADAS como "prealocadas en tránsito" con 1.47 m².
        """
        ids = [int(i) for i in (lot_ids or []) if i]
        if not ids:
            return set()
        Quant = self.env['stock.quant'].sudo()
        Loc = self.env['stock.location']
        dom = [('lot_id', 'in', ids), ('quantity', '>', 0)] + Loc._som_transit_quant_leaf()
        if company_id:
            dom.append(('company_id', 'in', [False, company_id]))
        transit_qty = {}
        for lot, qty in Quant._read_group(dom, ['lot_id'], ['quantity:sum']):
            transit_qty[lot.id] = qty or 0.0
        if not transit_qty:
            return set()
        # Lo que ya SALIÓ de tránsito a una ubicación interna (recepción
        # física validada); se compara contra lo que sigue en tránsito.
        Ml = self.env['stock.move.line'].sudo()
        left_dom = [
            ('lot_id', 'in', list(transit_qty)),
            ('state', '=', 'done'),
            ('location_dest_id.usage', '=', 'internal'),
        ]
        left_qty = {}
        for ml in Ml.search(left_dom):
            src = ml.location_id
            if not src or not src._som_is_transit():
                continue
            left_qty[ml.lot_id.id] = left_qty.get(ml.lot_id.id, 0.0) + (ml.quantity or 0.0)
        result = set()
        for lot_id, qty in transit_qty.items():
            left = left_qty.get(lot_id, 0.0)
            if left <= 0.0 or qty > left + 0.0001:
                result.add(lot_id)
        return result

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
