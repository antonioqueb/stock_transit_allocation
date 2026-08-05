# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

TRANSIT_TOKENS = ('TRANSIT', 'TRANSITO', 'TRANCIT')
LEGACY_TRANSIT_IDS = (128, 1019)


class StockLocation(models.Model):
    _inherit = 'stock.location'

    def _som_is_transit(self):
        """La ubicación funge como tránsito: usage nativo, id histórico o
        nombre/ruta conteniendo TRANSIT/TRANSITO/TRANCIT (sin acentos ni
        mayúsculas). Mismo criterio que la detección de button_validate."""
        self.ensure_one()
        if self.usage == 'transit' or self.id in LEGACY_TRANSIT_IDS:
            return True
        loc_text = (
            '%s %s' % (self.name or '', self.complete_name or '')
        ).upper().replace('Á', 'A')
        return any(tok in loc_text for tok in TRANSIT_TOKENS)

    @api.model
    def _som_transit_quant_leaf(self):
        """Fragmento de dominio (auto-contenido, en OR) para búsquedas de
        stock.quant en ubicaciones de tránsito con el criterio tolerante."""
        return [
            '|', '|', '|', '|',
            ('location_id.usage', '=', 'transit'),
            ('location_id.complete_name', 'ilike', 'transit'),
            ('location_id.complete_name', 'ilike', 'tránsit'),
            ('location_id.complete_name', 'ilike', 'trancit'),
            ('location_id', 'in', list(LEGACY_TRANSIT_IDS)),
        ]

    @api.model
    def _som_normalize_transit_locations(self):
        """Las ubicaciones nombradas como tránsito pero creadas como
        'internal' (default de las hijas de un almacén, p.ej. SOM/TRANSIT)
        rompen toda la maquinaria que filtra por usage='transit'. Se
        normalizan aquí; se invoca desde data XML en cada actualización."""
        candidates = self.sudo().search([
            ('usage', '=', 'internal'),
            '|', '|',
            ('complete_name', 'ilike', 'transit'),
            ('complete_name', 'ilike', 'tránsit'),
            ('complete_name', 'ilike', 'trancit'),
        ])
        to_fix = candidates.filtered(
            lambda l: l._som_is_transit() and not l.child_ids)
        if to_fix:
            to_fix.write({'usage': 'transit'})
            _logger.info(
                '[SOM] Ubicaciones normalizadas a usage=transit: %s',
                to_fix.mapped('complete_name'),
            )
        return True
