# -*- coding: utf-8 -*-
"""
Medición de actividad de usuarios (SOM).

QUÉ GUARDA ODOO DE FÁBRICA (y por qué no alcanza):
  · `res.users.log`: un renglón por LOGIN. Da la hora de entrada, nada más.
  · `res.users.login_date`: el último login, calculado del anterior.
  · `bus.presence`: un ÚNICO renglón por usuario con `last_poll`/`last_presence`
    y estado online/away. Se pisa a sí mismo: sirve para el semáforo del chat,
    no para historia.
  · `create_date`/`write_date` de cada registro y el chatter: dicen CUÁNDO se
    guardó algo, jamás cuánto tiempo costó hacerlo.
  · `ir.logging`: apagado por defecto y es log técnico, no de negocio.
Es decir: hora de conexión sí; tiempo de pantalla, atención, espera o cuánto
tarda alguien en llenar una cotización, NO. Eso es lo que agregan estos
modelos, alimentados por el servicio `som_activity_tracker` del webclient.

REGLA DE CONFIANZA: el navegador puede mentir. Todo lo que llega se recorta
contra la duración real del intervalo y contra topes duros antes de guardar.
"""
from odoo import models, fields, api

import logging

_logger = logging.getLogger(__name__)

# Un latido normal es de 15 s y el envío cada 60 s. Nada por encima de 15 min
# en un solo evento es creíble (pestaña dormida, reloj del cliente movido).
MAX_EVENT_SECONDS = 15 * 60


class SomUserSession(models.Model):
    _name = 'som.user.session'
    _description = 'Sesión de trabajo de usuario (SOM)'
    _order = 'started_at desc'
    _rec_name = 'user_id'

    user_id = fields.Many2one(
        'res.users', string='Usuario', required=True, index=True,
        ondelete='cascade')
    token = fields.Char(
        string='Token de pestaña', required=True, index=True,
        help='UUID que genera el navegador por carga de página. Es lo que '
             'permite distinguir dos pestañas abiertas del mismo usuario.')
    started_at = fields.Datetime(string='Conexión', required=True, index=True)
    last_seen_at = fields.Datetime(string='Última señal', required=True, index=True)
    device = fields.Char(string='Dispositivo')
    active_seconds = fields.Integer(string='Segundos activos', default=0)
    idle_seconds = fields.Integer(string='Segundos inactivos', default=0)

    duration_minutes = fields.Float(
        string='Duración (min)', compute='_compute_duration', store=True)

    _sql_constraints = [
        ('som_user_session_token_uniq', 'unique(token)',
         'Ya existe una sesión con ese token.'),
    ]

    @api.depends('started_at', 'last_seen_at')
    def _compute_duration(self):
        for rec in self:
            if rec.started_at and rec.last_seen_at:
                delta = rec.last_seen_at - rec.started_at
                rec.duration_minutes = max(0.0, delta.total_seconds() / 60.0)
            else:
                rec.duration_minutes = 0.0


class SomUserActivity(models.Model):
    _name = 'som.user.activity'
    _description = 'Actividad de usuario por pantalla (SOM)'
    _order = 'start_at desc'
    _rec_name = 'screen_key'

    user_id = fields.Many2one(
        'res.users', string='Usuario', required=True, index=True,
        ondelete='cascade')
    session_id = fields.Many2one(
        'som.user.session', string='Sesión', index=True, ondelete='set null')
    day = fields.Date(string='Día', required=True, index=True)
    hour = fields.Integer(
        string='Hora del día', index=True,
        help='Hora local (0-23) en que ocurrió el intervalo. Se guarda '
             'calculada para poder graficar horarios sin recalcular husos.')
    start_at = fields.Datetime(string='Inicio', required=True, index=True)
    end_at = fields.Datetime(string='Fin', required=True)

    screen_key = fields.Char(string='Pantalla', required=True, index=True)
    screen_label = fields.Char(string='Nombre de la pantalla')
    model_name = fields.Char(string='Modelo', index=True)
    res_id = fields.Integer(string='ID del registro', index=True)

    # Activo = hubo teclado/mouse en la ventana. Inactivo = la pantalla
    # estaba visible y abierta, pero nadie la tocaba. El tiempo con la
    # pestaña en segundo plano NO se cuenta: no es tiempo de trabajo.
    active_seconds = fields.Integer(string='Segundos activos', default=0)
    idle_seconds = fields.Integer(string='Segundos inactivos', default=0)

    # Tiempo de RESPUESTA del sistema medido desde el navegador: incluye red
    # + servidor, que es lo que el usuario realmente espera.
    rpc_count = fields.Integer(string='Llamadas', default=0)
    rpc_ms_total = fields.Float(string='Espera total (ms)', default=0.0)
    rpc_ms_max = fields.Float(string='Espera máxima (ms)', default=0.0)

    @api.model
    def _tracking_enabled(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'som.activity_tracking', 'on')
        return str(param).strip().lower() not in ('0', 'off', 'false', 'no')

    @api.model
    def som_record_batch(self, payload):
        """Guarda un lote de latidos. La llama el controlador `/som/activity/ping`.

        Devuelve cuántos intervalos se guardaron (útil para depurar desde la
        consola del navegador).
        """
        if not self._tracking_enabled():
            return {'stored': 0, 'disabled': True}

        payload = payload or {}
        events = payload.get('events') or []
        if not events:
            return {'stored': 0}

        user = self.env.user
        now = fields.Datetime.now()
        session = self._som_touch_session(payload, now)

        vals_list = []
        for event in events[:200]:
            vals = self._som_prepare_event(event, user, session, now)
            if vals:
                vals_list.append(vals)

        if not vals_list:
            return {'stored': 0}

        self.sudo().create(vals_list)

        if session:
            session.sudo().write({
                'last_seen_at': now,
                'active_seconds': session.active_seconds + sum(
                    v['active_seconds'] for v in vals_list),
                'idle_seconds': session.idle_seconds + sum(
                    v['idle_seconds'] for v in vals_list),
            })

        return {'stored': len(vals_list)}

    @api.model
    def _som_touch_session(self, payload, now):
        token = (payload.get('session') or '').strip()[:64]
        if not token:
            return self.env['som.user.session']
        Session = self.env['som.user.session'].sudo()
        session = Session.search([('token', '=', token)], limit=1)
        if session:
            return session
        return Session.create({
            'user_id': self.env.user.id,
            'token': token,
            'started_at': now,
            'last_seen_at': now,
            'device': (payload.get('device') or '')[:32],
        })

    @api.model
    def _som_prepare_event(self, event, user, session, now):
        if not isinstance(event, dict):
            return None

        start = fields.Datetime.to_datetime(event.get('start'))
        end = fields.Datetime.to_datetime(event.get('end'))
        if not start or not end or end <= start:
            return None

        # El reloj del cliente no manda: nada del futuro ni de hace días.
        if start > now or (now - start).total_seconds() > 86400:
            return None

        span = min((end - start).total_seconds(), MAX_EVENT_SECONDS)

        def _clamp(value):
            try:
                value = int(value or 0)
            except (TypeError, ValueError):
                value = 0
            return max(0, min(value, int(span)))

        active = _clamp(event.get('active'))
        idle = _clamp(event.get('idle'))
        if active + idle > span:
            idle = max(0, int(span) - active)
        if not active and not idle:
            return None

        local = fields.Datetime.context_timestamp(self, start)

        return {
            'user_id': user.id,
            'session_id': session.id if session else False,
            'day': local.date(),
            'hour': local.hour,
            'start_at': start,
            'end_at': end,
            'screen_key': (event.get('screen') or 'otro')[:64],
            'screen_label': (event.get('label') or '')[:128],
            'model_name': (event.get('model') or '')[:64] or False,
            'res_id': int(event.get('res_id') or 0),
            'active_seconds': active,
            'idle_seconds': idle,
            'rpc_count': max(0, int(event.get('rpc_count') or 0)),
            'rpc_ms_total': max(0.0, float(event.get('rpc_ms_total') or 0.0)),
            'rpc_ms_max': max(0.0, float(event.get('rpc_ms_max') or 0.0)),
        }

    @api.model
    def _som_gc_activity(self, days=400):
        """Poda: la medición es para gestionar el día a día, no un archivo
        histórico. Se conservan ~13 meses (comparativo año contra año)."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        old = self.sudo().search([('start_at', '<', limit)])
        count = len(old)
        old.unlink()
        sessions = self.env['som.user.session'].sudo().search(
            [('last_seen_at', '<', limit)])
        sessions.unlink()
        _logger.info('[SOM ACTIVITY] Poda: %s intervalos borrados.', count)
        return count
