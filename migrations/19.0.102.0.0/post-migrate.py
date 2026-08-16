"""La app Recepciones deja de colgar de stock.group_stock_user.

El menú raíz de Recepciones estaba gateado por 'usuario de inventario', que
TODO vendedor trae para poder ver existencias — así que la app de almacén
le aparecía a media empresa. Ahora va con el grupo Torre de Control ›
Recepciones (group_transit_receptions), que existía justo para eso y ya
implica stock.group_stock_user.

Esta migración solo evita que el almacén se quede sin el menú el lunes:

  · GARANTIZA el grupo a los GERENTES de inventario (stock.group_stock_manager).
    Es un piso conservador: quien manda en el almacén no puede perder la
    pantalla con la que recibe. Los de Tránsito (Usuario/Gerente) ya lo
    traen implicado y no se tocan.

  · NO reparte el grupo a nadie más. A los almacenistas que solo tienen
    'usuario de inventario' hay que asignárselos a mano en
    Ajustes › Usuarios › Torre de Control = Recepciones. Repartirlo solo
    por tener inventario reproduciría el problema que venimos a arreglar.

Para que la decisión sea auditable, deja en el log la lista EXACTA de quién
tenía inventario y se queda sin el menú.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    recepciones = env.ref(
        'stock_transit_allocation.group_transit_receptions',
        raise_if_not_found=False)
    inv_user = env.ref('stock.group_stock_user', raise_if_not_found=False)
    inv_manager = env.ref('stock.group_stock_manager',
                          raise_if_not_found=False)
    if not recepciones or not inv_user:
        _logger.warning(
            '[stock_transit_allocation] Grupos de recepciones no encontrados; '
            'no se ajusta nada.')
        return

    # OJO (Odoo 19): user_ids trae SOLO miembros directos. Para saber quién
    # tiene un grupo de verdad hay que leer all_user_ids, que incluye a los
    # que lo traen implicado por otro grupo.
    ya_tienen = recepciones.all_user_ids
    con_inventario = inv_user.all_user_ids   # se lee ANTES de escribir

    # Piso conservador: los gerentes de inventario conservan la pantalla.
    nuevos = env['res.users']
    if inv_manager:
        nuevos = inv_manager.all_user_ids - ya_tienen
        if nuevos:
            recepciones.write({'user_ids': [(4, u.id) for u in nuevos]})
            _logger.info(
                '[stock_transit_allocation] Recepciones concedido a %s '
                'gerente(s) de inventario: %s',
                len(nuevos), ', '.join(nuevos.mapped('login')))

    # Quién pierde el menú (tenía inventario, no tiene Recepciones).
    pierden = (con_inventario - ya_tienen - nuevos).filtered(
        lambda u: u.active and not u.has_group('base.group_system'))
    if pierden:
        _logger.warning(
            '[stock_transit_allocation] La app Recepciones YA NO es visible '
            'para %s usuario(s) con inventario. Si alguno recibe material, '
            'asígnale Torre de Control = Recepciones: %s',
            len(pierden), ', '.join(pierden.mapped('login')))
    else:
        _logger.info(
            '[stock_transit_allocation] Ningún usuario con inventario pierde '
            'la app Recepciones.')
