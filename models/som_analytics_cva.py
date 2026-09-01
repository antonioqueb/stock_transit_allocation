# -*- coding: utf-8 -*-
"""Lente administrativa (CVA) sobre SOM Analytics.

Cuando el usuario tiene encendida la VISTA ADMINISTRATIVA del módulo
``sale_admin_value_control``, el motor BI presenta los montos de venta con el
ajuste administrativo aplicado. Como el motor trabaja con SQL crudo, la lente
se aplica en el único embudo común: el ``cr.execute`` del cursor, SOLO durante
``get_dashboard`` / ``get_drill`` y SOLO para ese usuario. Las reescrituras
son textuales y conservadoras:

* ``so.amount_total|amount_untaxed|amount_tax`` -> ``COALESCE(so.x_cva_*, so.*)``
* ``sol.price_subtotal|price_total``            -> ``COALESCE(sol.x_cva_*, sol.*)``
* ``so.delivery_paid_amount``                   -> pagado × proporción administrativa de la orden
* ``m.amount_residual_signed|amount_residual``  -> residual × proporción administrativa de la factura
* ``SUM(amount_total)`` sin alias sobre ``sale_order`` -> versión administrativa

Si el módulo CVA no está instalado o la lente está apagada, este archivo no
cambia absolutamente nada (detección por campos, sin dependencia dura).
"""
import logging
import re
from contextlib import contextmanager

from odoo import models

_logger = logging.getLogger(__name__)

_RATIO_SO = ("CASE WHEN COALESCE(so.amount_total, 0) <> 0 THEN "
             "COALESCE(so.x_cva_amount_total, so.amount_total) / so.amount_total "
             "ELSE 1 END")
_RATIO_M = ("CASE WHEN COALESCE(m.amount_total, 0) <> 0 THEN "
            "COALESCE(m.x_cva_amount_total, m.amount_total) / m.amount_total "
            "ELSE 1 END")


class SomAnalyticsCva(models.AbstractModel):
    _inherit = 'som.analytics'

    # ------------------------------------------------------------------
    def _cva_lens_ready(self):
        try:
            if 'x_cva_amount_total' not in self.env['sale.order']._fields:
                return False
            users = self.env['res.users']
            return hasattr(users, '_cva_lens_active') and users._cva_lens_active()
        except Exception:  # noqa: BLE001 - jamás tumbar el dashboard
            return False

    @staticmethod
    def _cva_rewrite_sql(sql):
        if not isinstance(sql, str):
            return sql
        low = sql.lower()
        if 'select' not in low:
            return sql
        if 'sale_order' not in low and 'account_move' not in low:
            return sql
        out = sql
        # pagado real ajustado (el marcador evita reescrituras dobles)
        out = re.sub(r'\bso\.delivery_paid_amount\b',
                     '(COALESCE(so.delivery_paid_amount, 0) * __CVA_RATIO_SO__)',
                     out)
        # residuales de facturas — primero el _signed (lookahead evita pisarlo)
        out = re.sub(r'\bm\.amount_residual_signed\b',
                     '(m.amount_residual_signed * __CVA_RATIO_M__)', out)
        out = re.sub(r'\bm\.amount_residual\b(?!_)',
                     '(m.amount_residual * __CVA_RATIO_M__)', out)
        # montos de la orden y de la línea
        for real, adm in (('amount_total', 'x_cva_amount_total'),
                          ('amount_untaxed', 'x_cva_amount_untaxed'),
                          ('amount_tax', 'x_cva_amount_tax')):
            out = re.sub(r'\bso\.%s\b' % real,
                         'COALESCE(so.%s, so.%s)' % (adm, real), out)
        for real, adm in (('price_subtotal', 'x_cva_price_subtotal'),
                          ('price_total', 'x_cva_price_total')):
            out = re.sub(r'\bsol\.%s\b' % real,
                         'COALESCE(sol.%s, sol.%s)' % (adm, real), out)
        # SUM(amount_total) sin alias, solo si la consulta toca sale_order
        if re.search(r'\bfrom\s+sale_order\b', out, re.IGNORECASE):
            out = out.replace('SUM(amount_total)',
                              'SUM(COALESCE(x_cva_amount_total, amount_total))')
        out = out.replace('__CVA_RATIO_SO__', _RATIO_SO)
        out = out.replace('__CVA_RATIO_M__', _RATIO_M)
        return out

    @contextmanager
    def _cva_lens_cursor(self):
        cr = self.env.cr
        orig_execute = cr.execute
        rewrite = self._cva_rewrite_sql

        def execute(query, params=None, *args, **kwargs):
            try:
                query = rewrite(query)
            except Exception:  # noqa: BLE001
                _logger.exception('[SOM Analytics][CVA] reescritura falló')
            return orig_execute(query, params, *args, **kwargs)

        cr.execute = execute
        try:
            yield
        finally:
            cr.execute = orig_execute

    # ------------------------------------------------------------------
    def get_dashboard(self, domain, filters=None):
        if self._cva_lens_ready():
            with self._cva_lens_cursor():
                return super().get_dashboard(domain, filters)
        return super().get_dashboard(domain, filters)

    def get_drill(self, entity, value, label, filters=None):
        if self._cva_lens_ready():
            with self._cva_lens_cursor():
                return super().get_drill(entity, value, label, filters)
        return super().get_drill(entity, value, label, filters)
