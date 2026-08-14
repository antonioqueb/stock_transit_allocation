# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockQuantTransitPublication(models.Model):
    _inherit = "stock.quant"

    transit_inventory_published = fields.Boolean(
        string="Inventario tránsito publicado",
        default=False,
        copy=False,
        index=True,
        help=(
            "Controla si este quant en ubicación de tránsito puede aparecer "
            "en el Inventario Visual."
        ),
    )

    transit_inventory_state = fields.Selection(
        selection=[
            ("hidden", "No publicado"),
            ("available", "Disponible en tránsito"),
            ("committed", "Comprometido en tránsito"),
        ],
        string="Estado comercial en tránsito",
        default="hidden",
        copy=False,
        index=True,
        help=(
            "Estado lógico usado por Inventario Visual para clasificar material en tránsito. "
            "No depende de reserved_quantity ni de holds."
        ),
    )

    transit_voyage_id = fields.Many2one(
        "stock.transit.voyage",
        string="Viaje publicado",
        copy=False,
        index=True,
        ondelete="set null",
    )

    transit_line_id = fields.Many2one(
        "stock.transit.line",
        string="Línea de tránsito publicada",
        copy=False,
        index=True,
        ondelete="set null",
    )


class StockTransitLinePublication(models.Model):
    _inherit = "stock.transit.line"

    inventory_published = fields.Boolean(
        string="Publicado en inventario",
        default=False,
        copy=False,
        index=True,
    )

    inventory_publication_state = fields.Selection(
        selection=[
            ("hidden", "No publicado"),
            ("available", "Disponible"),
            ("committed", "Committed"),
        ],
        string="Estado publicación inventario",
        default="hidden",
        copy=False,
        index=True,
    )

    inventory_published_at = fields.Datetime(
        string="Fecha publicación inventario",
        copy=False,
        readonly=True,
    )

    inventory_published_by = fields.Many2one(
        "res.users",
        string="Publicado por",
        copy=False,
        readonly=True,
    )

    def _tc_is_transit_quant(self, quant):
        self.ensure_one()
        return bool(
            quant
            and quant.exists()
            and quant.company_id.id == self.company_id.id
            and quant.product_id.id == self.product_id.id
            and quant.lot_id.id == self.lot_id.id
            and quant.quantity > 0
            and quant.location_id._som_is_transit()
        )

    def _tc_resolve_transit_quant(self):
        self.ensure_one()

        if self.quant_id and self._tc_is_transit_quant(self.quant_id):
            return self.quant_id

        if not self.lot_id or not self.product_id:
            return False

        quant = self.env["stock.quant"].sudo().search([
            ("company_id", "=", self.company_id.id),
            ("product_id", "=", self.product_id.id),
            ("lot_id", "=", self.lot_id.id),
            ("quantity", ">", 0),
        ] + self.env["stock.location"]._som_transit_quant_leaf(),
            order="id desc", limit=1)

        if quant:
            self.with_context(
                skip_reservation_logic=True,
                skip_transit_publication_sync=True,
            ).write({
                "quant_id": quant.id,
            })

        return quant

    def _tc_is_committed_for_publication(self):
        self.ensure_one()
        return bool(
            self.partner_id
            or self.order_id
            or self.allocation_status == "reserved"
        )

    def _tc_get_publication_state(self):
        self.ensure_one()
        return "committed" if self._tc_is_committed_for_publication() else "available"

    def _tc_cancel_transit_holds(self, quant=False):
        """
        En tránsito NO debe existir On Hold.
        Si por lógica anterior se generó un hold sobre un quant de tránsito,
        se cancela para no contaminar Inventario Visual.
        """
        self.ensure_one()

        quant = quant or self.quant_id
        if not quant or not quant.exists() or quant.location_id.usage != "transit":
            return

        if "stock.lot.hold" not in self.env.registry.models:
            return

        holds = self.env["stock.lot.hold"].sudo().search([
            ("quant_id", "=", quant.id),
            ("estado", "=", "activo"),
        ])

        for hold in holds:
            try:
                hold.action_cancelar_hold()
            except Exception as e:
                _logger.warning(
                    "[TC_PUBLICATION] No se pudo cancelar hold activo en tránsito. "
                    "Hold: %s | Quant: %s | Error: %s",
                    hold.id,
                    quant.id,
                    e,
                )

    def _tc_apply_inventory_publication(self, force_publish=False):
        """
        Publica o actualiza el estado comercial de la línea y su quant.

        Reglas:
        - Si la línea tiene cliente, SO o allocation_status reserved => committed.
        - Si no tiene asignación => available.
        - Nunca usa On Hold para tránsito.
        - Nunca usa reserved_quantity para clasificar tránsito.
        """
        now = fields.Datetime.now()

        for line in self:
            if not line.lot_id or not line.product_id or line.product_uom_qty <= 0:
                continue

            quant = line._tc_resolve_transit_quant()
            if not quant:
                continue

            line._tc_cancel_transit_holds(quant)

            publication_state = line._tc_get_publication_state()
            should_publish = force_publish or line.inventory_published

            if not should_publish:
                continue

            line.with_context(
                skip_reservation_logic=True,
                skip_transit_publication_sync=True,
            ).write({
                "inventory_published": True,
                "inventory_publication_state": publication_state,
                "inventory_published_at": line.inventory_published_at or now,
                "inventory_published_by": line.inventory_published_by.id or self.env.user.id,
            })

            # GEMELAS: el estado se escribe A NIVEL QUANT y las parcialidades
            # de un lote comparten quant. La última gemela procesada pisaba a
            # la reservada y el quant COMPLETO quedaba "available" con una
            # parcialidad Committed a un pedido (se podía vender material
            # comprometido). El quant es 'committed' si CUALQUIER gemela viva
            # está comprometida.
            committed_sibling = self.env["stock.transit.line"].sudo().search([
                ("quant_id", "=", quant.id),
                ("voyage_id.custom_status", "not in", ("cancel",)),
                "|", "|",
                ("order_id", "!=", False),
                ("partner_id", "!=", False),
                ("allocation_status", "=", "reserved"),
            ], limit=1)

            quant_state = "committed" if committed_sibling else publication_state
            reference_line = committed_sibling or line

            quant.sudo().write({
                "transit_inventory_published": True,
                "transit_inventory_state": quant_state,
                "transit_voyage_id": reference_line.voyage_id.id,
                "transit_line_id": reference_line.id,
            })

    def _execute_reservation_logic(self, partner, order):
        """
        Override crítico:
        Si el quant está en tránsito, NO se crea stock.lot.hold.
        La asignación en tránsito solo vive como estado lógico de Torre de Control.
        """
        self.ensure_one()

        quant = self.quant_id
        if not quant or not quant.exists():
            quant = self._tc_resolve_transit_quant()

        if quant and quant.location_id.usage == "transit":
            self._tc_cancel_transit_holds(quant)

            if self.inventory_published or self.voyage_id.transit_inventory_published:
                self._tc_apply_inventory_publication(force_publish=True)

            _logger.info(
                "[TC_PUBLICATION] Asignación en tránsito sin hold físico. "
                "Line: %s | Lot: %s | Partner: %s | Order: %s",
                self.id,
                self.lot_id.name if self.lot_id else "N/A",
                partner.display_name if partner else "N/A",
                order.name if order else "N/A",
            )
            return True

        return super()._execute_reservation_logic(partner, order)

    def _execute_release_logic(self):
        self.ensure_one()

        quant = self.quant_id
        if quant and quant.exists() and quant.location_id.usage == "transit":
            self._tc_cancel_transit_holds(quant)

            if self.inventory_published or self.voyage_id.transit_inventory_published:
                self._tc_apply_inventory_publication(force_publish=True)

            return True

        return super()._execute_release_logic()

    def write(self, vals):
        if self.env.context.get("skip_transit_publication_sync"):
            return super().write(vals)

        publication_sensitive_fields = {
            "partner_id",
            "order_id",
            "allocation_status",
            "lot_id",
            "quant_id",
            "product_uom_qty",
            "product_id",
        }

        should_sync_after = bool(publication_sensitive_fields.intersection(vals.keys()))

        res = super().write(vals)

        if should_sync_after:
            lines_to_sync = self.filtered(
                lambda line: line.inventory_published or line.voyage_id.transit_inventory_published
            )
            if lines_to_sync:
                lines_to_sync._tc_apply_inventory_publication(force_publish=True)

        return res


class StockTransitVoyagePublication(models.Model):
    _inherit = "stock.transit.voyage"

    transit_inventory_published = fields.Boolean(
        string="Inventario publicado",
        default=False,
        copy=False,
        tracking=True,
        help=(
            "Cuando está activo, los quants en tránsito vinculados a este viaje "
            "pueden aparecer en Inventario Visual como T. Available o T. Committed."
        ),
    )

    transit_inventory_published_at = fields.Datetime(
        string="Fecha publicación inventario",
        copy=False,
        readonly=True,
    )

    transit_inventory_published_by = fields.Many2one(
        "res.users",
        string="Publicado por",
        copy=False,
        readonly=True,
    )

    def _tc_get_publishable_lines(self):
        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: line.lot_id
            and line.product_id
            and line.product_uom_qty > 0
        )

    def _tc_validate_publishable_lines(self, lines):
        self.ensure_one()

        if not lines:
            raise UserError(_(
                "No hay líneas con lote y cantidad positiva para publicar en inventario."
            ))

        missing = []

        for line in lines:
            quant = line._tc_resolve_transit_quant()
            if not quant:
                missing.append(
                    "%s | %s | %.3f" % (
                        line.product_id.display_name,
                        line.lot_id.display_name if line.lot_id else "Sin lote",
                        line.product_uom_qty,
                    )
                )

        if missing:
            raise UserError(_(
                "No se puede publicar el inventario porque estas líneas no tienen "
                "quant positivo en una ubicación de tránsito:\n\n%s"
            ) % "\n".join(missing[:80]))

    def action_publish_transit_inventory(self):
        """
        Acción principal:
        Publica el inventario en tránsito para Inventario Visual.

        Resultado:
        - Líneas asignadas a cliente/SO/reserved => T. Committed.
        - Líneas sin asignación => T. Available.
        - Nada se manda a T. On Hold.
        """
        for voyage in self:
            if voyage.custom_status in ("delivered", "cancel"):
                raise UserError(_(
                    "No puede publicar inventario de un viaje entregado o cancelado."
                ))

            lines = voyage._tc_get_publishable_lines()
            voyage._tc_validate_publishable_lines(lines)

            lines._tc_apply_inventory_publication(force_publish=True)

            committed_lines = lines.filtered(
                lambda line: line.inventory_publication_state == "committed"
            )
            available_lines = lines.filtered(
                lambda line: line.inventory_publication_state == "available"
            )

            committed_qty = sum(committed_lines.mapped("product_uom_qty"))
            available_qty = sum(available_lines.mapped("product_uom_qty"))

            # REGLA DE NEGOCIO: el precio de venta ALL-IN se calcula AL
            # PUBLICAR el inventario (costo all-in + escalera de
            # utilidades). Guards por si el módulo de precios no está.
            templates = lines.mapped('product_id.product_tmpl_id')
            if templates and hasattr(templates, '_compute_costo_all_in'):
                try:
                    templates._compute_costo_all_in()
                    if hasattr(templates, '_calculate_escalera_precios'):
                        templates._calculate_escalera_precios()
                    voyage.message_post(body=(
                        '💲 Precios de venta all-in recalculados al '
                        'publicar (%s producto(s)).') % len(templates))
                except Exception:
                    _logger.exception(
                        '[TC_PUBLISH] Falló el recálculo de precios all-in '
                        'al publicar el viaje %s.', voyage.name)

            voyage.write({
                "transit_inventory_published": True,
                "transit_inventory_published_at": fields.Datetime.now(),
                "transit_inventory_published_by": self.env.user.id,
            })

            # ═══ DISPARADOR DEL COSTEO (acuerdo de negocio) ═══
            # El costo del producto se recalcula AL PUBLICAR: desde este
            # momento el material está disponible para que los vendedores lo
            # comercialicen. Ni la captura del proveedor ni la validación del
            # tránsito lo disparan. Defensivo: si logistica_tarifario no está
            # instalado, la publicación no se ve afectada.
            try:
                pos = voyage._tc_covered_purchase_orders() if hasattr(
                    voyage, '_tc_covered_purchase_orders') else voyage.purchase_id
                if pos and hasattr(pos, '_som_apply_costing_update'):
                    # Embarque con datos de naviera/forwarder: primero por
                    # vínculo directo al viaje; si no, el más reciente CON
                    # naviera de las proformas de las POs amparadas.
                    Shipment = self.env['supplier.shipment'].sudo()
                    shipment = Shipment.search(
                        [('voyage_id', '=', voyage.id)], limit=1)
                    if not shipment or not getattr(shipment, 'naviera_id', False):
                        headers = self.env['supplier.proforma.header'].sudo().search(
                            [('purchase_id', 'in', pos.ids)])
                        candidates = headers.mapped('shipment_ids').sorted(
                            'id', reverse=True)
                        with_carrier = candidates.filtered(
                            lambda sh: getattr(sh, 'naviera_id', False)
                            or getattr(sh, 'forwarder_id', False))
                        shipment = with_carrier[:1] or shipment or candidates[:1]
                    products = lines.mapped('product_id.product_tmpl_id')
                    templates = pos.filtered(
                        lambda po: po.state != 'cancel'
                    )._som_apply_costing_update(
                        products=products,
                        naviera=getattr(shipment, 'naviera_id', False) or False,
                        forwarder=getattr(shipment, 'forwarder_id', False) or False,
                        pol=getattr(shipment, 'pol_id', False) or False,
                        pod=getattr(shipment, 'pod_id', False) or False,
                    )
                    if templates:
                        voyage.message_post(body=_(
                            "💰 Costeo actualizado al publicar: %s producto(s) "
                            "recalculados con los datos de esta compra."
                        ) % len(templates))
            except Exception:
                _logger.exception(
                    "[TC_PUBLISH] Falló el recálculo de costos al publicar el "
                    "viaje %s (la publicación NO se revierte).", voyage.name,
                )

            voyage.message_post(body=_(
                "📢 Inventario en tránsito publicado.<br/>"
                "<b>Committed:</b> %(committed_count)s lote(s) / %(committed_qty).3f<br/>"
                "<b>Disponible:</b> %(available_count)s lote(s) / %(available_qty).3f<br/>"
                "Regla aplicada: material asignado a cliente/SO se publica como Committed; "
                "material sin asignación se publica como Disponible. On Hold no aplica para tránsito."
            ) % {
                "committed_count": len(committed_lines),
                "committed_qty": committed_qty,
                "available_count": len(available_lines),
                "available_qty": available_qty,
            })

            # NOTA: publicar YA NO crea la recepción ni pone el viaje "listo
            # para recibir". Publicar es un acto COMERCIAL (visibilidad en
            # Inventario Visual + costeo). Lo que alista la recepción es mover
            # el viaje a "Entrega en Sitio" en Viajes y Contenedores (o el
            # botón Generar Recepción), y "Entregado" solo lo pone la
            # VALIDACIÓN de la recepción física.

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Inventario publicado"),
                "message": _(
                    "El inventario en tránsito fue publicado correctamente para Inventario Visual."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_unpublish_transit_inventory(self):
        """
        Acción de reversa administrativa.
        Oculta nuevamente los quants en tránsito del Inventario Visual.
        """
        for voyage in self:
            lines = voyage.line_ids.filtered(lambda line: line.inventory_published)

            quants = lines.mapped("quant_id").filtered(
                lambda quant: quant and quant.exists() and quant.location_id.usage == "transit"
            )

            lines.with_context(
                skip_reservation_logic=True,
                skip_transit_publication_sync=True,
            ).write({
                "inventory_published": False,
                "inventory_publication_state": "hidden",
                "inventory_published_at": False,
                "inventory_published_by": False,
            })

            quants.sudo().write({
                "transit_inventory_published": False,
                "transit_inventory_state": "hidden",
                "transit_voyage_id": False,
                "transit_line_id": False,
            })

            voyage.write({
                "transit_inventory_published": False,
                "transit_inventory_published_at": False,
                "transit_inventory_published_by": False,
            })

            voyage.message_post(body=_(
                "🙈 Inventario en tránsito despublicado. "
                "Los quants de este viaje ya no aparecerán en Inventario Visual."
            ))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Inventario despublicado"),
                "message": _("El inventario en tránsito fue ocultado del Inventario Visual."),
                "type": "warning",
                "sticky": False,
            },
        }

class StockTransitVoyagePublicationPending(models.Model):
    """Detector de material con PL procesado pero inventario SIN publicar.

    Criterio (filtro "Pendiente de publicar" en Torre de Control/Cronograma):
    - El viaje está en Puerto Origen o etapa superior (sin entregar/cancelar).
    - Ya tiene Packing List procesado (existen líneas de tránsito).
    - Pasaron X días desde el procesamiento del PL (parámetro
      `stock_transit_allocation.publication_pending_days`, default 3).
    - Y al menos una línea sigue sin publicarse en el Inventario Visual.

    Al publicar todo, el viaje desaparece del filtro: el objetivo operativo
    es que ese filtro esté siempre vacío.
    """
    _inherit = "stock.transit.voyage"

    _TC_PUBLICATION_STAGES = (
        "puerto_origen", "on_sea", "puerto_destino",
        "arrived_port", "reception_pending",
    )

    tc_fully_received = fields.Boolean(
        string="Recibido al 100%",
        compute="_compute_tc_fully_received",
        help="Todas las recepciones físicas del embarque están validadas y "
             "no queda material pendiente: el tránsito terminó, es stock.",
    )

    def _compute_tc_fully_received(self):
        for rec in self:
            try:
                t = rec._tc_reception_totals()
                rec.tc_fully_received = bool(
                    t['done_count'] and not t['open']
                    and (t['pending'] or 0.0) <= 0.01)
            except Exception:
                rec.tc_fully_received = False

    tc_publication_pending = fields.Boolean(
        string="Pendiente de publicar",
        compute="_compute_tc_publication_pending",
        search="_search_tc_publication_pending",
        help=(
            "PL procesado, viaje en Puerto Origen o superior, pasados los días "
            "configurados y con inventario de tránsito sin publicar."
        ),
    )

    @api.model
    def _tc_publication_pending_days(self):
        icp = self.env["ir.config_parameter"].sudo()
        try:
            return int(icp.get_param(
                "stock_transit_allocation.publication_pending_days", "3"))
        except (TypeError, ValueError):
            return 3

    def _tc_check_publication_pending(self):
        self.ensure_one()

        if self.custom_status not in self._TC_PUBLICATION_STAGES:
            return False

        # Embarque recibido al 100%% con la operación validada: ya no hay
        # nada que publicar — dejó de ser tránsito, es stock.
        if self.tc_fully_received:
            return False

        lines = self.line_ids
        if not lines:
            # Sin líneas de tránsito = el PL aún no se procesa: no aplica.
            return False

        if not lines.filtered(lambda l: not l.inventory_published):
            return False

        # Días transcurridos desde el procesamiento del PL (las líneas nacen
        # al procesarlo; la más antigua marca el inicio del conteo).
        created = [d for d in lines.mapped("create_date") if d]
        if not created:
            return False
        elapsed = fields.Datetime.now() - min(created)
        return elapsed.days >= self._tc_publication_pending_days()

    def _compute_tc_publication_pending(self):
        for rec in self:
            rec.tc_publication_pending = rec._tc_check_publication_pending()

    def _search_tc_publication_pending(self, operator, value):
        if operator not in ("=", "!="):
            return [("id", "=", False)]

        want_pending = (operator == "=") == bool(value)

        candidates = self.search([
            ("custom_status", "in", list(self._TC_PUBLICATION_STAGES)),
        ])
        pending_ids = [
            rec.id for rec in candidates if rec._tc_check_publication_pending()
        ]

        if want_pending:
            return [("id", "in", pending_ids)]
        return [("id", "not in", pending_ids)]
