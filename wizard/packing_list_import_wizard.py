# -*- coding: utf-8 -*-
import logging

from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero, float_compare

_logger = logging.getLogger(__name__)


class PackingListImportWizardPhysicalReception(models.TransientModel):
    _inherit = "packing.list.import.wizard"

    # -------------------------------------------------------------------------
    #  ENTRYPOINT
    # -------------------------------------------------------------------------

    def action_import_excel(self):
        self.ensure_one()

        if not self._tc_is_physical_reception_import():
            return super().action_import_excel()

        if self.picking_id.state in ("done", "cancel"):
            raise UserError(_("La recepción física ya está cerrada o cancelada."))

        _logger.info("=== [TC_PHYSICAL_PL] INICIO RECONCILIACIÓN PL FÍSICO ===")

        rows = []
        if self.excel_file:
            rows = self._get_data_from_excel_file()
        elif self.spreadsheet_id:
            rows = self._get_data_from_spreadsheet()

        if not rows:
            raise UserError(_(
                "No se encontraron filas válidas para conciliar la recepción física. "
                "Revise que el PL tenga producto reconocible y filas con alto/ancho o cantidad mayor a cero."
            ))

        stats = self._tc_apply_physical_reception_pl(rows)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("PL físico conciliado"),
                "message": _(
                    "Líneas físicas: %(created)s. "
                    "Lotes nuevos: %(new_lots)s. "
                    "Lotes reutilizados: %(reused_lots)s. "
                    "Filas omitidas: %(skipped)s. "
                    "El Worksheet fue reiniciado para regenerarse con esta versión."
                ) % stats,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    # -------------------------------------------------------------------------
    #  DETECCIÓN
    # -------------------------------------------------------------------------

    def _tc_is_physical_reception_import(self):
        picking = self.picking_id
        if not picking or picking.picking_type_code != "internal":
            return False
        return bool(self._tc_get_physical_voyage())

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

    # -------------------------------------------------------------------------
    #  HELPERS
    # -------------------------------------------------------------------------

    def _tc_qty_field(self):
        return "quantity" if "quantity" in self.env["stock.move.line"]._fields else "qty_done"

    def _tc_qty_value(self, move_line):
        field = self._tc_qty_field()
        return getattr(move_line, field) or 0.0

    def _tc_rounding(self, product):
        return product.uom_id.rounding or 0.0001

    def _tc_float_is_zero(self, product, qty):
        return float_is_zero(qty or 0.0, precision_rounding=self._tc_rounding(product))

    def _tc_float_compare(self, product, a, b):
        return float_compare(
            a or 0.0,
            b or 0.0,
            precision_rounding=self._tc_rounding(product),
        )

    def _tc_normalize_text(self, value):
        return str(value or "").strip().lower()

    def _tc_effective_qty_from_row(self, row):
        product = row["product"]
        unit_type = row.get("tipo") or product.product_tmpl_id.x_unidad_del_producto or "Placa"

        if str(unit_type).lower() == "placa":
            return round((row.get("alto") or 0.0) * (row.get("ancho") or 0.0), 3)

        return row.get("quantity") or 0.0

    def _tc_row_signature(self, row):
        product = row["product"]
        return (
            product.id,
            self._tc_normalize_text(row.get("grosor")),
            self._tc_normalize_text(row.get("bloque")),
            self._tc_normalize_text(row.get("numero_placa")),
            self._tc_normalize_text(row.get("atado")),
            self._tc_normalize_text(row.get("ref_proveedor")),
        )

    def _tc_lot_signature(self, lot):
        return (
            lot.product_id.id,
            self._tc_normalize_text(getattr(lot, "x_grosor", "")),
            self._tc_normalize_text(getattr(lot, "x_bloque", "")),
            self._tc_normalize_text(getattr(lot, "x_numero_placa", "")),
            self._tc_normalize_text(getattr(lot, "x_atado", "")),
            self._tc_normalize_text(getattr(lot, "x_referencia_proveedor", "")),
        )

    def _tc_find_existing_lot(self, voyage, row, used_lot_ids):
        target_sig = self._tc_row_signature(row)

        candidates = (
            self.picking_id.move_line_ids.mapped("lot_id")
            | voyage.line_ids.mapped("lot_id")
        ).filtered(lambda lot: lot and lot.product_id.id == row["product"].id and lot.id not in used_lot_ids)

        for lot in candidates:
            if self._tc_lot_signature(lot) == target_sig:
                return lot

        return False

    def _tc_prepare_lot_vals(self, row, lot_name=False):
        product = row["product"]
        unit_type = row.get("tipo") or product.product_tmpl_id.x_unidad_del_producto or "Placa"
        lot_model = self.env["stock.lot"]

        vals = {
            "product_id": product.id,
            "company_id": self.picking_id.company_id.id,
        }

        if lot_name:
            vals["name"] = lot_name

        field_map = {
            "x_grosor": row.get("grosor"),
            "x_alto": row.get("alto") if str(unit_type).lower() == "placa" else 0.0,
            "x_ancho": row.get("ancho") if str(unit_type).lower() == "placa" else 0.0,
            "x_color": row.get("color"),
            "x_bloque": row.get("bloque"),
            "x_numero_placa": row.get("numero_placa"),
            "x_atado": row.get("atado"),
            "x_tipo": str(unit_type).lower(),
            "x_pedimento": row.get("pedimento"),
            "x_contenedor": row.get("contenedor") or "SN",
            "x_referencia_proveedor": row.get("ref_proveedor"),
        }

        for field_name, value in field_map.items():
            if field_name in lot_model._fields:
                vals[field_name] = value

        if "x_grupo" in lot_model._fields and row.get("grupo_name"):
            group_name = str(row.get("grupo_name") or "").strip()
            if group_name:
                group = self.env["stock.lot.group"].search([("name", "=", group_name)], limit=1)
                if not group:
                    group = self.env["stock.lot.group"].create({"name": group_name})
                vals["x_grupo"] = [(6, 0, [group.id])]

        return vals

    def _tc_get_or_create_lot(self, voyage, row, used_lot_ids, container_sequences):
        existing_lot = self._tc_find_existing_lot(voyage, row, used_lot_ids)
        if existing_lot:
            vals = self._tc_prepare_lot_vals(row)
            vals.pop("product_id", None)
            vals.pop("company_id", None)
            existing_lot.write(vals)
            used_lot_ids.add(existing_lot.id)
            return existing_lot, False

        cont = (row.get("contenedor") or "SN").strip() or "SN"
        if cont not in container_sequences:
            next_prefix = container_sequences.setdefault("_next_prefix", self._get_next_global_prefix())
            container_sequences[cont] = {
                "prefix": str(next_prefix),
                "num": self._get_next_lot_number_for_prefix(str(next_prefix)),
            }
            container_sequences["_next_prefix"] = next_prefix + 1

        seq_data = container_sequences[cont]
        lot_name = f"{seq_data['prefix']}-{seq_data['num']:02d}"
        seq_data["num"] += 1

        lot = self.env["stock.lot"].create(self._tc_prepare_lot_vals(row, lot_name=lot_name))
        used_lot_ids.add(lot.id)
        return lot, True

    def _tc_set_source_quant_qty(self, product, lot, location, target_qty):
        Quant = self.env["stock.quant"].sudo()

        quants = Quant.search([
            ("company_id", "=", self.picking_id.company_id.id),
            ("product_id", "=", product.id),
            ("lot_id", "=", lot.id),
            ("location_id", "=", location.id),
        ])

        current_qty = sum(quants.mapped("quantity"))
        diff = (target_qty or 0.0) - current_qty

        if self._tc_float_compare(product, diff, 0.0) != 0:
            Quant._update_available_quantity(
                product,
                location,
                diff,
                lot_id=lot,
            )

        return Quant.search([
            ("company_id", "=", self.picking_id.company_id.id),
            ("product_id", "=", product.id),
            ("lot_id", "=", lot.id),
            ("location_id", "=", location.id),
            ("quantity", ">", 0),
        ], order="id desc", limit=1)

    def _tc_sync_voyage_line(self, voyage, row, lot, quant, qty):
        existing = voyage.line_ids.filtered(lambda line: line.lot_id.id == lot.id)[:1]
        vals = {
            "product_id": row["product"].id,
            "lot_id": lot.id,
            "quant_id": quant.id if quant else False,
            "product_uom_qty": qty,
            "container_number": row.get("contenedor") or getattr(lot, "x_contenedor", False) or "SN",
        }

        if existing:
            existing.with_context(skip_reservation_logic=True).write(vals)
            return existing

        vals.update({
            "voyage_id": voyage.id,
            "allocation_status": "available",
            "partner_id": False,
            "order_id": False,
            "allocation_id": False,
        })
        return self.env["stock.transit.line"].create(vals)

    def _tc_prepare_moves(self, product_totals):
        picking = self.picking_id
        move_map = {}

        existing_moves = picking.move_ids.filtered(lambda move: move.state not in ("done", "cancel"))

        for move in existing_moves:
            total_qty = product_totals.get(move.product_id.id, 0.0)

            if self._tc_float_is_zero(move.product_id, total_qty):
                move.unlink()
                continue

            vals = {}
            if self._tc_float_compare(move.product_id, move.product_uom_qty, total_qty) != 0:
                vals["product_uom_qty"] = total_qty
            if move.location_id.id != picking.location_id.id:
                vals["location_id"] = picking.location_id.id
            if move.location_dest_id.id != picking.location_dest_id.id:
                vals["location_dest_id"] = picking.location_dest_id.id
            if vals:
                move.write(vals)

            move_map[move.product_id.id] = move

        for product_id, total_qty in product_totals.items():
            product = self.env["product.product"].browse(product_id)
            if product_id in move_map:
                continue

            move = self.env["stock.move"].create({
                "picking_id": picking.id,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": total_qty,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "company_id": picking.company_id.id,
            })
            move_map[product_id] = move

        draft_moves = picking.move_ids.filtered(lambda move: move.state == "draft")
        if draft_moves:
            draft_moves._action_confirm()

        return move_map

    def _tc_create_move_line(self, move, lot, qty):
        qty_field = self._tc_qty_field()

        vals = {
            "picking_id": self.picking_id.id,
            "move_id": move.id,
            "company_id": self.picking_id.company_id.id,
            "product_id": move.product_id.id,
            "product_uom_id": move.product_id.uom_id.id,
            "lot_id": lot.id,
            "location_id": self.picking_id.location_id.id,
            "location_dest_id": self.picking_id.location_dest_id.id,
            qty_field: qty,
        }

        return self.env["stock.move.line"].create(vals)

    # -------------------------------------------------------------------------
    #  APLICACIÓN DEL PL FÍSICO
    # -------------------------------------------------------------------------

    def _tc_apply_physical_reception_pl(self, rows):
        picking = self.picking_id
        voyage = self._tc_get_physical_voyage()

        if not voyage:
            raise UserError(_("No se encontró el viaje vinculado a esta recepción física."))

        source_location = picking.location_id
        if not source_location:
            raise UserError(_("La recepción física no tiene ubicación origen."))

        valid_rows = []
        skipped = 0

        for row in rows:
            product = row.get("product")
            if not product:
                skipped += 1
                continue

            qty = self._tc_effective_qty_from_row(row)
            if self._tc_float_is_zero(product, qty) or qty < 0:
                skipped += 1
                continue

            valid_rows.append((row, qty))

        if not valid_rows:
            raise UserError(_("No hay filas válidas con cantidad física positiva."))

        used_lot_ids = set()
        container_sequences = {}
        prepared_lines = []
        product_totals = {}
        new_lots = 0
        reused_lots = 0

        for row, qty in valid_rows:
            product = row["product"]

            lot, was_new = self._tc_get_or_create_lot(
                voyage,
                row,
                used_lot_ids,
                container_sequences,
            )

            if was_new:
                new_lots += 1
            else:
                reused_lots += 1

            quant = self._tc_set_source_quant_qty(
                product,
                lot,
                source_location,
                qty,
            )

            self._tc_sync_voyage_line(voyage, row, lot, quant, qty)

            product_totals.setdefault(product.id, 0.0)
            product_totals[product.id] += qty

            prepared_lines.append({
                "product": product,
                "lot": lot,
                "qty": qty,
            })

        # Reconstruir únicamente las líneas de la recepción física.
        if picking.move_line_ids:
            picking.move_line_ids.unlink()

        move_map = self._tc_prepare_moves(product_totals)

        created = 0
        for item in prepared_lines:
            product = item["product"]
            move = move_map.get(product.id)
            if not move:
                raise UserError(_("No se encontró movimiento para %s.") % product.display_name)

            self._tc_create_move_line(move, item["lot"], item["qty"])
            created += 1

        if picking.ws_spreadsheet_id:
            picking.ws_spreadsheet_id.sudo().unlink()

        picking.write({
            "packing_list_imported": True,
            "worksheet_imported": False,
            "ws_spreadsheet_id": False,
        })

        picking.message_post(body=_(
            "📋 PL físico conciliado desde Torre de Control. "
            "Líneas reconstruidas: %(created)s. Lotes nuevos: %(new_lots)s. "
            "Lotes reutilizados: %(reused_lots)s. Filas omitidas: %(skipped)s."
        ) % {
            "created": created,
            "new_lots": new_lots,
            "reused_lots": reused_lots,
            "skipped": skipped,
        })

        return {
            "created": created,
            "new_lots": new_lots,
            "reused_lots": reused_lots,
            "skipped": skipped,
        }