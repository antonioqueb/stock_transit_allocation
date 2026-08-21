# -*- coding: utf-8 -*-
"""Procesamiento del PL físico en SEGUNDO PLANO.

Un PL con cientos de filas tarda más que el límite del worker/proxy HTTP:
la petición muere sin respuesta y el usuario ve un "procesando" eterno sin
error. Con más de TC_PL_INLINE_MAX_ROWS filas el wizard ya no procesa en la
petición: crea un job, dispara el cron y responde al instante. El job corre
sin límite de tiempo de request, reporta avance en vivo (cursor separado:
la transacción principal sigue siendo atómica) y el banner de la recepción
lo muestra con polling.
"""
import logging
import time
import traceback
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Hasta aquí se procesa inline (respuesta inmediata en la misma petición).
TC_PL_INLINE_MAX_ROWS = 150


class TcPhysicalPlJob(models.Model):
    _name = "tc.physical.pl.job"
    _description = "Job de procesamiento de Packing List en segundo plano"
    _order = "id desc"

    picking_id = fields.Many2one(
        "stock.picking", string="Recepción", required=True,
        ondelete="cascade", index=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Solicitado por", required=True,
        default=lambda self: self.env.user,
    )
    # Si el PL vino como archivo subido al wizard se conserva aquí; si no,
    # el job re-lee el spreadsheet persistente del picking.
    excel_file = fields.Binary(string="Archivo Excel", attachment=False)
    excel_filename = fields.Char(string="Nombre del archivo")

    state = fields.Selection([
        ("pending", "En cola"),
        ("running", "Procesando"),
        ("done", "Terminado"),
        ("error", "Error"),
    ], default="pending", required=True, index=True)

    progress_done = fields.Integer(default=0)
    progress_total = fields.Integer(default=0)
    progress_label = fields.Char(default="En cola…")
    error_message = fields.Text()
    result_message = fields.Text()

    # ------------------------------------------------------------------
    #  ENCOLADO
    # ------------------------------------------------------------------

    @api.model
    def enqueue(self, picking, total_rows, excel_file=None, excel_filename=None):
        """Crea el job y dispara el cron. Un job pending/running por
        recepción: reintentar mientras corre no debe duplicar el trabajo."""
        # Antes de decidir, rescatar zombis: un 'running' muerto bloqueaba
        # el re-encolado para siempre.
        self._rescue_zombie_jobs()

        existing = self.search([
            ("picking_id", "=", picking.id),
            ("state", "in", ("pending", "running")),
        ], limit=1)
        if existing:
            # Re-disparar el cron SIEMPRE que quede un pending: el trigger
            # original pudo haberse perdido en un reinicio.
            if existing.state == "pending":
                cron = self.env.ref(
                    "stock_transit_allocation.ir_cron_tc_physical_pl_jobs",
                    raise_if_not_found=False,
                )
                if cron:
                    cron.sudo()._trigger()
            return existing

        job = self.create({
            "picking_id": picking.id,
            "excel_file": excel_file or False,
            "excel_filename": excel_filename or False,
            "progress_total": total_rows,
            "progress_label": _("En cola para procesarse…"),
        })
        cron = self.env.ref(
            "stock_transit_allocation.ir_cron_tc_physical_pl_jobs",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo()._trigger()
        return job

    # ------------------------------------------------------------------
    #  PROCESAMIENTO (cron)
    # ------------------------------------------------------------------

    # Un job 'running' reporta avance (write_date vía cursor separado) cada
    # pocos segundos. Sin latido en este lapso = el worker murió a media
    # transacción (reinicio del servidor durante un deploy, SIGKILL): la
    # transacción se revirtió sola pero el estado quedó 'running' para
    # siempre y ni el cron ni el usuario podían retomarlo.
    ZOMBIE_MINUTES = 10

    @api.model
    def _rescue_zombie_jobs(self):
        limit = fields.Datetime.now() - timedelta(minutes=self.ZOMBIE_MINUTES)
        zombies = self.search([
            ("state", "=", "running"),
            ("write_date", "<", limit),
        ])
        if zombies:
            _logger.warning(
                "[TC_PL_JOB] Rescatando %s job(s) zombi (running sin latido "
                "desde hace >%s min): %s",
                len(zombies), self.ZOMBIE_MINUTES, zombies.ids,
            )
            zombies.write({
                "state": "pending",
                "progress_label": _("Reanudando tras interrupción del servidor…"),
            })
        return zombies

    @api.model
    def _cron_process_jobs(self):
        self._rescue_zombie_jobs()
        jobs = self.search([("state", "=", "pending")], order="id")
        for job in jobs:
            job._process_one()
            # Cada job cierra su propia transacción: uno que falla no
            # arrastra a los demás.
            self.env.cr.commit()
        return True

    def _process_one(self):
        self.ensure_one()
        self.write({
            "state": "running",
            "progress_label": _("Leyendo el Packing List…"),
            "error_message": False,
        })
        # El estado 'running' queda visible aunque el procesamiento truene y
        # se haga rollback.
        self.env.cr.commit()

        picking = self.picking_id

        # La transacción del PL dura minutos: si un usuario o un cron toca
        # una de sus filas a la mitad, PostgreSQL aborta con "could not
        # serialize access due to concurrent update". Es transitorio y se
        # REINTENTA (igual que hace Odoo con las peticiones HTTP), no se
        # marca como error del PL.
        MAX_TRIES = 4
        for attempt in range(1, MAX_TRIES + 1):
            try:
                Wizard = (
                    self.env["packing.list.import.wizard"]
                    .with_user(self.user_id)
                    .with_company(picking.company_id)
                    .with_context(
                        tc_pl_progress_job_id=self.id,
                        tc_pl_force_inline=True,  # el job nunca se re-encola
                        allowed_company_ids=picking.company_id.ids,
                    )
                )
                wizard = Wizard.create({
                    "picking_id": picking.id,
                    "excel_file": self.excel_file or False,
                    "excel_filename": self.excel_filename or False,
                })

                rows = []
                if wizard.excel_file:
                    rows = wizard._get_data_from_excel_file()
                elif wizard.spreadsheet_id:
                    rows = wizard._get_data_from_spreadsheet()

                if not rows:
                    raise ValueError(_(
                        "No se encontraron filas válidas en el PL para procesar."
                    ))

                stats = wizard._tc_apply_physical_reception_pl(rows)

                self.write({
                    "state": "done",
                    "progress_done": self.progress_total or len(rows),
                    "progress_label": _("Terminado"),
                    "result_message": _(
                        "Líneas físicas: %(created)s. Lotes nuevos: %(new_lots)s. "
                        "Lotes reutilizados: %(reused_lots)s. Filas omitidas: "
                        "%(skipped)s. Ya recibidas antes: %(already_received)s."
                    ) % stats,
                })
                self.env.cr.commit()
                return
            except Exception as e:
                # Rollback COMPLETO del procesamiento: el PL queda como estaba.
                msg = getattr(e, "args", None) and e.args[0] or str(e)
                self.env.cr.rollback()
                self.env.clear()

                if self._is_retryable_error(e) and attempt < MAX_TRIES:
                    _logger.warning(
                        "[TC_PL_JOB] Job %s (%s): colisión de concurrencia "
                        "(intento %s/%s), reintentando: %s",
                        self.id, picking.name, attempt, MAX_TRIES, msg,
                    )
                    self.report_progress(
                        self.id, 0, self.progress_total or 0,
                        _("Colisión con otra operación: reintentando "
                          "(intento %s de %s)…") % (attempt + 1, MAX_TRIES),
                    )
                    # Backoff corto: dejar terminar a quien nos pisó.
                    time.sleep(2 * attempt)
                    continue

                _logger.error(
                    "[TC_PL_JOB] Job %s (%s) falló: %s\n%s",
                    self.id, picking.name, msg, traceback.format_exc(),
                )
                self.write({
                    "state": "error",
                    "progress_label": _("Error"),
                    "error_message": str(msg),
                })
                self.env.cr.commit()
                return

    @api.model
    def _is_retryable_error(self, error):
        """Errores transitorios de concurrencia que ameritan reintento."""
        try:
            from psycopg2 import errors as pg_errors
            if isinstance(error, (
                pg_errors.SerializationFailure,
                pg_errors.DeadlockDetected,
                pg_errors.LockNotAvailable,
            )):
                return True
        except ImportError:
            pass
        text = str(error) or ""
        return (
            "could not serialize access" in text
            or "deadlock detected" in text
            or "concurrent update" in text
        )

    # ------------------------------------------------------------------
    #  REPORTE DE AVANCE (cursor separado)
    # ------------------------------------------------------------------

    @api.model
    def report_progress(self, job_id, done, total, label):
        """Escribe el avance con un cursor propio: visible al instante para
        el polling del banner sin comprometer la atomicidad del job."""
        if not job_id:
            return
        try:
            with self.env.registry.cursor() as cr:
                cr.execute(
                    """
                    UPDATE tc_physical_pl_job
                       SET progress_done = %s,
                           progress_total = %s,
                           progress_label = %s,
                           write_date = (now() at time zone 'UTC')
                     WHERE id = %s
                    """,
                    (int(done), int(total), label or "", int(job_id)),
                )
        except Exception:
            # El avance es cosmético: jamás debe tumbar el procesamiento.
            _logger.warning("[TC_PL_JOB] No se pudo reportar avance", exc_info=True)


class StockPickingPlJob(models.Model):
    _inherit = "stock.picking"

    tc_pl_job_id = fields.Many2one(
        "tc.physical.pl.job", compute="_compute_tc_pl_job_id",
        string="Job de PL en curso",
    )

    def _compute_tc_pl_job_id(self):
        Job = self.env["tc.physical.pl.job"].sudo()
        for picking in self:
            picking.tc_pl_job_id = Job.search(
                [("picking_id", "=", picking.id)], limit=1,
            )
