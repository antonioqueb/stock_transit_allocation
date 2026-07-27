"""Desactiva el cron diario duplicado de ShipsGo en bases existentes.

Los crons se cargan con noupdate="1": quitar el <record> del XML no borra el
registro ya creado. Este cron ejecutaba el MISMO action_cron_sync_shipsgo que
el cron horario; a las 5am ambos corrían en paralelo sobre los mismos viajes
(estados/ETA pisados y doble consumo de cuota de la API externa).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_cron
        SET active = FALSE
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'stock_transit_allocation'
              AND name = 'ir_cron_shipsgo_sync_daily'
              AND model = 'ir.cron'
        )
        """
    )
    _logger.info(
        '[stock_transit_allocation] Cron diario duplicado de ShipsGo desactivado (%s).',
        cr.rowcount,
    )
