# -*- coding: utf-8 -*-
"""Sanea referencias colgantes en stock.transit.line.

Cuando se deduplican/fusionan contactos (o se borran clientes/órdenes), Odoo
repunta los modelos estándar, pero NO los modelos custom como
``stock.transit.line``. Eso deja líneas apuntando a un ``partner_id`` /
``order_id`` que ya no existe. Cualquier lectura posterior de ese registro
lanza ``MissingError`` y, en el formulario del Viaje, deja la línea imposible
de (re)asignar: el cliente borrado no tiene órdenes, así que el desplegable de
orden queda vacío y nunca se puede asignar.

Esta migración limpia esas referencias (idempotente):
- ``partner_id`` que ya no existe -> NULL
- ``order_id`` que ya no existe   -> NULL
- líneas sin cliente ni orden     -> allocation_status = 'available'
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT to_regclass('public.stock_transit_line')")
    if not cr.fetchone()[0]:
        return

    cr.execute(
        """
        UPDATE stock_transit_line t
           SET partner_id = NULL
         WHERE t.partner_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM res_partner p WHERE p.id = t.partner_id
           )
        """
    )
    cleaned_partners = cr.rowcount

    cr.execute(
        """
        UPDATE stock_transit_line t
           SET order_id = NULL
         WHERE t.order_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM sale_order o WHERE o.id = t.order_id
           )
        """
    )
    cleaned_orders = cr.rowcount

    # Coherencia: sin cliente ni orden, la línea está disponible.
    cr.execute(
        """
        UPDATE stock_transit_line
           SET allocation_status = 'available'
         WHERE partner_id IS NULL
           AND order_id IS NULL
           AND allocation_status IS DISTINCT FROM 'available'
        """
    )
    reset_status = cr.rowcount

    if cleaned_partners or cleaned_orders or reset_status:
        _logger.warning(
            "stock_transit_allocation: saneo de FK colgantes en "
            "stock.transit.line -> partner NULL: %s, order NULL: %s, "
            "status reseteado: %s.",
            cleaned_partners, cleaned_orders, reset_status,
        )
