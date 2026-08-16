# -*- coding: utf-8 -*-
"""Bitácora de PERMISOS: quién le dio o le quitó qué a quién.

EL PROBLEMA
-----------
Varias personas pueden repartir permisos. Aparece alguien con un acceso que
no le toca y nadie lo hizo. Odoo de fábrica NO guarda nada de esto: los
grupos son un many2many y los cambios de m2m no dejan rastro en el chatter
ni en ningún lado.

QUÉ HACE
--------
Cada alta o baja de permiso queda escrita en el chatter del USUARIO
afectado, con quién lo hizo, cuándo, y el nombre completo de cada permiso
(nativo o nuestro). Cubre los DOS caminos por los que se reparten permisos:

  · Ajustes › Usuarios › (usuario) — se editan sus grupos.
  · Ajustes › Grupos › (grupo) — se agregan/quitan usuarios de la lista.

El segundo es el que se olvida siempre y por el que "nadie lo hizo".

POR QUÉ VIVE AQUÍ
-----------------
Este módulo ya es el dueño de la vigilancia de usuarios (som.user.activity
y la pestaña Analytics › Control › Usuarios). Poner el rastro de permisos
en otro lado partiría en dos la misma pregunta: qué hacen y qué pueden
hacer las personas.
"""
import logging

from odoo import api, models, _

_logger = logging.getLogger(__name__)


def _groups_field(model):
    """Odoo 19 renombró res.users.groups_id → group_ids. Se detecta, no se
    asume: escribir un campo que no existe truena el registro."""
    for name in ('group_ids', 'groups_id'):
        if name in model._fields:
            return name
    return None


def _debe_registrar(env):
    """Solo se registra lo que hace una PERSONA.

    Instalar o actualizar módulos reparte grupos a mansalva (implied_ids,
    grupos nuevos, migraciones). Registrar eso llenaría el chatter de todos
    los usuarios de ruido y ahogaría justo lo que se quiere ver. El
    registro aún no está 'ready' mientras Odoo carga: ese es el corte.
    """
    if env.context.get('som_skip_permission_log'):
        return False
    return bool(getattr(env.registry, 'ready', True))


def _label(group):
    """Nombre completo del permiso: 'Inventario / Administrador'. Sin la
    categoría, media docena de grupos se llaman 'Usuario'."""
    if 'full_name' in group._fields and group.full_name:
        return group.full_name
    categoria = group.category_id.name if group.category_id else ''
    return '%s / %s' % (categoria, group.name) if categoria else (group.name or '')


class ResUsers(models.Model):
    _name = 'res.users'
    _inherit = ['res.users', 'mail.thread']

    # ── Escritura del rastro ──────────────────────────────────────────
    def _som_post_permission_change(self, agregados, quitados, origen=''):
        """Escribe en el chatter del usuario afectado."""
        self.ensure_one()
        if not agregados and not quitados:
            return

        partes = []
        if agregados:
            partes.append(
                '<p><b>➕ Permisos otorgados</b></p><ul>%s</ul>' % ''.join(
                    '<li>%s</li>' % _label(g) for g in agregados))
        if quitados:
            partes.append(
                '<p><b>➖ Permisos retirados</b></p><ul>%s</ul>' % ''.join(
                    '<li>%s</li>' % _label(g) for g in quitados))
        if origen:
            partes.append('<p style="color:#666;font-size:11px;">%s</p>'
                          % origen)

        # sudo() para poder escribir el mensaje aunque quien reparte
        # permisos no sea seguidor del usuario; el AUTOR sigue siendo la
        # persona real (sudo conserva el uid), que es todo el punto.
        #
        # try/except a propósito: si la bitácora falla, se pierde el rastro
        # pero el administrador NO se queda sin poder trabajar. El fallo
        # queda en el log del servidor como ERROR, no en silencio.
        try:
            self.sudo().message_post(
                body=''.join(partes),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
        except Exception:
            _logger.exception(
                '[SOM PERMISOS] No se pudo escribir la bitácora de %s. '
                'El cambio de permisos SÍ se aplicó.', self.login)
        _logger.info(
            '[SOM PERMISOS] %s modificó a %s → +[%s] -[%s] (%s)',
            self.env.user.login,
            self.login,
            ', '.join(_label(g) for g in agregados),
            ', '.join(_label(g) for g in quitados),
            origen or 'formulario de usuario',
        )

    def _som_log_group_diff(self, antes, origen=''):
        """antes = {user_id: set(ids de grupo)} tomado ANTES de escribir."""
        gfield = _groups_field(self)
        if not gfield:
            return
        Group = self.env['res.groups'].sudo()
        for user in self:
            previos = antes.get(user.id, set())
            actuales = set(user.sudo()[gfield].ids)
            user._som_post_permission_change(
                Group.browse(sorted(actuales - previos)),
                Group.browse(sorted(previos - actuales)),
                origen=origen,
            )

    # ── Ganchos ───────────────────────────────────────────────────────
    def write(self, vals):
        gfield = _groups_field(self)
        vigilar = bool(gfield and gfield in vals and _debe_registrar(self.env))
        antes = {}
        if vigilar:
            # Se fotografía ANTES: comparar estados es a prueba de balas,
            # interpretar los comandos (4,), (3,), (6,0,[...]) no lo es.
            antes = {u.id: set(u.sudo()[gfield].ids) for u in self}
        res = super().write(vals)
        if vigilar:
            self._som_log_group_diff(antes)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        usuarios = super().create(vals_list)
        gfield = _groups_field(self)
        if not gfield or not _debe_registrar(self.env):
            return usuarios
        Group = self.env['res.groups'].sudo()
        for user in usuarios:
            grupos = user.sudo()[gfield]
            if grupos:
                user._som_post_permission_change(
                    Group.browse(grupos.ids), Group.browse(),
                    origen=_('Permisos con los que se creó la cuenta.'),
                )
        return usuarios


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        """El camino olvidado: agregar/quitar usuarios DESDE el grupo.

        Sin esto, quien reparte permisos por Ajustes › Grupos no dejaba
        ningún rastro — y es justo por donde se cuelan los accesos que
        'nadie dio'.
        """
        vigilar = 'user_ids' in vals and _debe_registrar(self.env)
        antes = {}
        if vigilar:
            antes = {g.id: set(g.sudo().user_ids.ids) for g in self}
        res = super().write(vals)
        if not vigilar:
            return res

        Users = self.env['res.users'].sudo()
        for group in self:
            previos = antes.get(group.id, set())
            actuales = set(group.sudo().user_ids.ids)
            etiqueta = _label(group)
            origen = _('Cambiado desde el formulario del grupo "%s".') % etiqueta
            for user in Users.browse(sorted(actuales - previos)):
                user._som_post_permission_change(group, self.env['res.groups'],
                                                 origen=origen)
            for user in Users.browse(sorted(previos - actuales)):
                user._som_post_permission_change(self.env['res.groups'], group,
                                                 origen=origen)
        return res
