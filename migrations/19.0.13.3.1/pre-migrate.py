# -*- coding: utf-8 -*-
"""Deduplica supplier.shipment.block.image antes de aplicar el índice único.

El modelo ``supplier.shipment.block.image`` se declara en este módulo y en
``stock_lot_packing_import``, que es quien define la restricción
``_supplier_block_image_unique`` (UNIQUE(shipment_id, block_name, product_id)).
PostgreSQL no puede crear ese índice mientras existan registros duplicados,
p.ej. (shipment_id, block_name, product_id) = (7, 270735, 8551).

Como la actualización suele lanzarse con ``-u stock_transit_allocation`` se
replica aquí la limpieza —idempotente— para garantizar que se ejecute en fase
``pre`` antes de crear el índice. Conserva la fila con id más alto (la foto más
reciente) de cada grupo y elimina las anteriores.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT to_regclass('public.supplier_shipment_block_image')")
    if not cr.fetchone()[0]:
        return

    cr.execute(
        """
        SELECT shipment_id, block_name, product_id, COUNT(*) AS total
          FROM supplier_shipment_block_image
         GROUP BY shipment_id, block_name, product_id
        HAVING COUNT(*) > 1
        """
    )
    duplicates = cr.fetchall()
    if not duplicates:
        return

    total_extra = sum(row[3] - 1 for row in duplicates)
    _logger.warning(
        "stock_transit_allocation: %s combinaciones duplicadas en "
        "supplier_shipment_block_image; se eliminarán %s filas sobrantes "
        "para poder crear el índice único.",
        len(duplicates), total_extra,
    )

    cr.execute(
        """
        DELETE FROM supplier_shipment_block_image a
              USING supplier_shipment_block_image b
         WHERE a.shipment_id = b.shipment_id
           AND a.block_name  = b.block_name
           AND a.product_id  = b.product_id
           AND a.id < b.id
        """
    )
    _logger.warning(
        "stock_transit_allocation: %s filas duplicadas eliminadas de "
        "supplier_shipment_block_image.",
        cr.rowcount,
    )
