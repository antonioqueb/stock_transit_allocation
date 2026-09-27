# -*- coding: utf-8 -*-


def run_reverting_unfinished(env, method, *args, **kwargs):
    """Ejecuta una asignación dentro de un savepoint y lo REVIERTE si la
    asignación no se completó (``success`` False o
    ``need_over_assignment_decision``).

    Las parcialidades de formato/pieza se parten ANTES de medir el excedente;
    si el usuario cancelaba el popup de decisión (o faltaba la línea de
    venta), el split quedaba confirmado: dos gemelas disponibles y el chatter
    diciendo "Asignado al pedido: 20" sin asignación real.

    OJO Odoo 19: Savepoint.close() revierte por defecto; en el camino
    exitoso se cierra con rollback=False."""
    savepoint = env.cr.savepoint()
    try:
        result = method(*args, **kwargs)
    except Exception:
        savepoint.close(rollback=True)
        raise
    unfinished = isinstance(result, dict) and (
        result.get('success') is False
        or result.get('need_over_assignment_decision')
    )
    savepoint.close(rollback=bool(unfinished))
    return result
