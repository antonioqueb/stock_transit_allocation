# -*- coding: utf-8 -*-
"""Etiquetas de USUARIO (29 sep 2026): catálogo propio para organizar y
filtrar la lista de usuarios. No se reutilizan las etiquetas de contacto
(res.partner.category) para no mezclar "Almacén" con clientes/proveedores.
Solo organizan: no dan ni quitan permisos."""
from random import randint

from odoo import fields, models


class SomUserTag(models.Model):
    _name = 'som.user.tag'
    _description = 'Etiqueta de usuario'
    _order = 'name'

    def _default_color(self):
        return randint(1, 11)

    name = fields.Char(string='Etiqueta', required=True, translate=False)
    color = fields.Integer(string='Color', default=_default_color)
    active = fields.Boolean(default=True)
    user_ids = fields.Many2many(
        'res.users', 'som_user_tag_rel', 'tag_id', 'user_id',
        string='Usuarios')
    user_count = fields.Integer(
        string='Usuarios', compute='_compute_user_count')

    _name_uniq = models.Constraint(
        'unique(name)', 'Ya existe una etiqueta de usuario con ese nombre.')

    def _compute_user_count(self):
        for tag in self:
            tag.user_count = len(tag.user_ids)


class ResUsersSomTag(models.Model):
    _inherit = 'res.users'

    som_tag_ids = fields.Many2many(
        'som.user.tag', 'som_user_tag_rel', 'user_id', 'tag_id',
        string='Etiquetas de usuario')
