# -*- coding: utf-8 -*-
from odoo import fields, models


class StockLot(models.Model):
    _inherit = 'stock.lot'

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
