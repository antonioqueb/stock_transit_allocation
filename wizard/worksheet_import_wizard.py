# -*- coding: utf-8 -*-
import logging

from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero, float_compare

_logger = logging.getLogger(__name__)


class WorksheetImportWizardPhysicalReception(models.TransientModel):
    _inherit = "worksheet.import.wizard"

    # -------------------------------------------------------------------------
    #  ENTRYPOINT
    # -------------------------------------------------------------------------

    def action_import_worksheet(self):
        self.ensure_one()

        if not self._tc_is_physical_reception_ws():
            return super().action_import_worksheet()

        if self.picking_id.state == "done":
            raise UserError(_("La recepción física ya está validada. No se puede procesar el Worksheet."))

        if not self.ws_spreadsheet_id and not self.excel_file:
            raise UserError(_("No se encontró el Spreadsheet del Worksheet ni se subió un archivo Excel."))

        rows_data = self._get_data_from_excel() if self.excel_file else self._get_data_from_spreadsheet()

        if not rows_data:
            raise UserError(_("No se encontraron datos de medidas reales para procesar."))

        stats = self._tc_apply_physical_worksheet(rows_data)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Worksheet físico procesado"),
                "message": _(
                    "Actualizados: %(updated)s. "
                    "Marcados como faltantes: %(missing)s. "
                    "No encontrados: %(not_found)s. "
                    "La recepción física quedó lista para validar."
                ) % stats,
                "type": "warning" if stats.get("missing") else "success",
                "sticky": bool(stats.get("missing")),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    # -------------------------------------------------------------------------
    #  DETECCIÓN
    # -------------------------------------------------------------------------

    def _tc_get_physical_voyage(self):
        picking = self.picking_id
        if not picking:
            return False

        voyage = self.env["stock.transit.voyage"].search([
            ("reception_picking_id", "=", picking.id)
        ], limit=1)

        if not voyage and hasattr(picking, "_get_linked_reception_voyage"):
            try:
                voyage = picking._get_linked_reception_voyage()
            except Exception:
                voyage = False

        return voyage

    def _tc_is_physical_reception_ws(self):
        return bool(
            self.picking_id
            and self.picking_id.picking_type_code == "internal"
            and self._tc_get_physical_voyage()
        )

    # -------------------------------------------------------------------------
    #  HELPERS
    # -------------------------------------------------------------------------

    def _tc_qty_field(self):
        return "quantity" if "quantity" in self.env["stock.move.line"]._fields else "qty_done"

    def _tc_qty_value(self, move_line):
        return getattr(move_line, self._tc_qty_field()) or 0.0

    def _tc_rounding(self, product):
        return product.uom_id.rounding or 0.0001

    def _tc_is_zero(self, product, qty):
        return float_is_zero(qty or 0.0, precision_rounding=self._tc_rounding(product))

    def _tc_compare(self, product, a, b):
        return float_compare(
            a or 0.0,
            b or 0.0,
            precision_rounding=self._tc_rounding(product),
        )

    def _tc_set_move_line_qty(self, move_line, qty):
        vals = {
            self._tc_qty_field(): qty,
        }

        if "picked" in move_line._fields:
            vals["picked"] = False

        move_line.with_context(
            tc_physical_reception_prepare=True,
            tc_no_auto_validate=True,
        ).write(vals)

    def _tc_effective_real_qty(self, product, lot, alto_real, ancho_real, fallback_qty):
        unit_type = product.product_tmpl_id.x_unidad_del_producto or getattr(lot, "x_tipo", "Placa") or "Placa"

        if str(unit_type).lower() == "placa":
            return round((alto_real or 0.0) * (ancho_real or 0.0), 3)

        return fallback_qty or 0.0

    def _tc_source_quant(self, voyage, lot, product):
        line = voyage.line_ids.filtered(lambda l: l.lot_id.id == lot.id)[:1]
        if line and line.quant_id and line.quant_id.exists():
            return line.quant_id

        return self.env["stock.quant"].sudo().search([
            ("company_id", "=", self.picking_id.company_id.id),
            ("lot_id", "=", lot.id),
            ("product_id", "=", product.id),
            ("location_id.usage", "=", "transit"),
        ], order="id desc", limit=1)

    def _tc_set_quant_qty(self, product, lot, location, target_qty):
        Quant = self.env["stock.quant"].sudo()

        quants = Quant.search([
            ("company_id", "=", self.picking_id.company_id.id),
            ("lot_id", "=", lot.id),
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ])

        current_qty = sum(quants.mapped("quantity"))
        diff = (target_qty or 0.0) - current_qty

        if self._tc_compare(product, diff, 0.0) != 0:
            Quant._update_available_quantity(
                product,
                location,
                diff,
                lot_id=lot,
            )

        return Quant.search([
            ("company_id", "=", self.picking_id.company_id.id),
            ("lot_id", "=", lot.id),
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
            ("quantity", ">", 0),
        ], order="id desc", limit=1)

    def _tc_zero_quant(self, product, lot, quant):
        if not quant or not quant.exists():
            return

        qty = quant.quantity or 0.0
        if self._tc_is_zero(product, qty):
            return

        self.env["stock.quant"].sudo()._update_available_quantity(
            product,
            quant.location_id,
            -qty,
            lot_id=lot,
        )

    def _tc_recompute_moves_from_move_lines(self):
        picking = self.picking_id

        product_totals = {}
        for ml in picking.move_line_ids:
            if not ml.product_id:
                continue
            product_totals.setdefault(ml.product_id.id, 0.0)
            product_totals[ml.product_id.id] += self._tc_qty_value(ml)

        for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
            qty = product_totals.get(move.product_id.id, 0.0)
            if self._tc_is_zero(move.product_id, qty):
                move.unlink()
            else:
                move.write({
                    "product_uom_qty": qty,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                })

    # -------------------------------------------------------------------------
    #  APLICACIÓN WS FÍSICO
    # -------------------------------------------------------------------------

    def _tc_apply_physical_worksheet(self, rows_data):
        picking = self.picking_id
        voyage = self._tc_get_physical_voyage()

        if not voyage:
            raise UserError(_("No se encontró el viaje vinculado a esta recepción física."))

        updated = 0
        missing = 0
        not_found = 0

        for data in rows_data:
            product = data["product"]
            lot_name = data["lot_name"]

            domain = [
                ("picking_id", "=", picking.id),
                ("lot_id.name", "=", lot_name),
            ]

            move_line = self.env["stock.move.line"].search(
                domain + [("product_id", "=", product.id)],
                limit=1,
            )

            if not move_line:
                move_line = self.env["stock.move.line"].search(domain, limit=1)

            if not move_line or not move_line.lot_id:
                not_found += 1
                continue

            lot = move_line.lot_id
            alto_real = data.get("alto_real") or 0.0
            ancho_real = data.get("ancho_real") or 0.0

            voyage_line = voyage.line_ids.filtered(lambda l: l.lot_id.id == lot.id)[:1]
            source_quant = self._tc_source_quant(voyage, lot, product)

            # 0 / 0 = pieza no encontrada físicamente.
            if self._tc_is_zero(product, alto_real) and self._tc_is_zero(product, ancho_real):
                if source_quant:
                    self._tc_zero_quant(product, lot, source_quant)

                if voyage_line:
                    notes = (voyage_line.notes or "")
                    notes += "\n[Recepción Física] Marcado como faltante desde Worksheet."
                    voyage_line.with_context(skip_reservation_logic=True).write({
                        "product_uom_qty": 0.0,
                        "quant_id": False,
                        "notes": notes.strip(),
                    })

                move_line.unlink()
                missing += 1
                continue

            fallback_qty = self._tc_qty_value(move_line)
            new_qty = self._tc_effective_real_qty(
                product,
                lot,
                alto_real,
                ancho_real,
                fallback_qty,
            )

            if self._tc_is_zero(product, new_qty):
                not_found += 1
                continue

            lot_vals = {}
            if "x_alto" in lot._fields:
                lot_vals["x_alto"] = alto_real
            if "x_ancho" in lot._fields:
                lot_vals["x_ancho"] = ancho_real
            if lot_vals:
                lot.write(lot_vals)

            if source_quant:
                source_location = source_quant.location_id
            else:
                source_location = picking.location_id

            new_quant = self._tc_set_quant_qty(
                product,
                lot,
                source_location,
                new_qty,
            )

            self._tc_set_move_line_qty(move_line, new_qty)

            if voyage_line:
                voyage_line.with_context(skip_reservation_logic=True).write({
                    "product_uom_qty": new_qty,
                    "quant_id": new_quant.id if new_quant else False,
                })

            updated += 1

        self._tc_recompute_moves_from_move_lines()

        picking.write({
            "worksheet_imported": True,
        })

        if picking.state == "done":
            raise UserError(_(
                "Control Tower detuvo el flujo porque la recepción física %(picking)s "
                "se marcó como HECHO al procesar el Worksheet. "
                "Después del Worksheet debe quedar lista para validación manual, no validada automáticamente."
            ) % {
                "picking": picking.name or picking.display_name,
            })

        picking.message_post(body=_(
            "📐 Worksheet físico procesado. "
            "Actualizados: %(updated)s. Faltantes: %(missing)s. No encontrados: %(not_found)s."
        ) % {
            "updated": updated,
            "missing": missing,
            "not_found": not_found,
        })

        return {
            "updated": updated,
            "missing": missing,
            "not_found": not_found,
        }