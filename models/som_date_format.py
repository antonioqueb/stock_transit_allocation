# -*- coding: utf-8 -*-
"""Formato de fecha único del sistema: "13 ago 2026".

Día en número, mes abreviado en palabra (español) y año en número. Es el
gemelo en Python de static/src/utils/som_date.js — si cambia uno, cambia
el otro, o la misma fecha se vería distinta según la pinte el servidor o
el navegador.

Solo para ETIQUETAS que ve el usuario. Cualquier fecha que viaje como
dato (clave de agrupación, valor a ordenar, dominio, payload al backend)
se queda en ISO: este formato NO se puede volver a convertir a fecha.
"""

MESES_ES = ('ene', 'feb', 'mar', 'abr', 'may', 'jun',
            'jul', 'ago', 'sep', 'oct', 'nov', 'dic')


def som_format_date(value, empty='—', with_time=False):
    """
    :param value: date/datetime (ya en la zona del usuario si trae hora).
    :param empty: qué devolver cuando no hay fecha.
    :param with_time: agrega " 14:30" al final.
    :return: "13 ago 2026"
    """
    if not value:
        return empty
    try:
        out = '%02d %s %d' % (
            value.day, MESES_ES[value.month - 1], value.year)
    except (AttributeError, IndexError, TypeError):
        return empty
    if with_time and hasattr(value, 'hour'):
        out += ' %02d:%02d' % (value.hour, value.minute)
    return out
