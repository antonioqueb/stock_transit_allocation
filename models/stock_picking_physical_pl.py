# -*- coding: utf-8 -*-
import json
import logging
import re

from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPickingPhysicalPackingList(models.Model):
    _inherit = "stock.picking"

    tc_is_physical_reception = fields.Boolean(
        string="Recepción física TC",
        compute="_compute_tc_is_physical_reception",
        store=False,
    )

    @api.depends("picking_type_code", "origin")
    def _compute_tc_is_physical_reception(self):
        for picking in self:
            voyage = self.env["stock.transit.voyage"].search([
                ("reception_picking_id", "=", picking.id)
            ], limit=1)

            if not voyage and picking.origin:
                # Match exacto (ilike confundía VOY/001 con VOY/0010).
                origin_ref = picking.origin.split(" ")[0]
                voyage = self.env["stock.transit.voyage"].search([
                    ("name", "=", origin_ref),
                    ("reception_picking_id", "!=", False),
                ], limit=1)

            picking.tc_is_physical_reception = bool(
                picking.picking_type_code == "internal"
                and voyage
            )

    # -------------------------------------------------------------------------
    #  RECEPCIÓN FÍSICA DESDE TORRE DE CONTROL
    # -------------------------------------------------------------------------

    def _tc_get_physical_reception_voyage(self):
        self.ensure_one()

        voyage = self.env["stock.transit.voyage"].search([
            ("reception_picking_id", "=", self.id)
        ], limit=1)

        if not voyage and hasattr(self, "_get_linked_reception_voyage"):
            try:
                voyage = self._get_linked_reception_voyage()
            except Exception:
                _logger.exception(
                    "[TC_PHYSICAL_PL] Fallo resolviendo el viaje ligado al "
                    "picking %s.", self.name,
                )
                voyage = False

        return voyage

    def _tc_is_physical_reception(self):
        self.ensure_one()
        return bool(
            self.picking_type_code == "internal"
            and self._tc_get_physical_reception_voyage()
        )

    def _tc_move_line_qty(self, move_line):
        if "quantity" in move_line._fields:
            return move_line.quantity or 0.0
        return move_line.qty_done or 0.0

    def _tc_lot_value(self, lot, field_name, default=""):
        if lot and field_name in lot._fields:
            return getattr(lot, field_name) or default
        return default

    def _tc_lot_groups_value(self, lot):
        if lot and "x_grupo" in lot._fields and lot.x_grupo:
            return ", ".join(lot.x_grupo.mapped("name"))
        return ""

    def _tc_collect_physical_pl_lines(self, product):
        """
        Fuente de datos para el PL físico.

        Prioridad:
        1. voyage.line_ids (cantidad > 0): el viaje es la fuente de verdad del
           contenido del embarque y carga las reservas a pedidos. Las move
           lines pueden arrastrar lotes de una conciliación anterior cuya
           serie el viaje ya reemplazó; prellenar con ellos entrega al
           almacén Ref. Internas muertas y el import bloquea las placas
           reservadas reales.
        2. move_line_ids, como fallback si el viaje no tiene líneas.
        """
        self.ensure_one()

        result = []

        voyage = self._tc_get_physical_reception_voyage()

        transit_lines = voyage.line_ids.filtered(
            lambda line: (
                line.product_id.id == product.id
                and line.lot_id
                and line.product_uom_qty > 0
            )
        ).sorted(lambda line: line.lot_id.name or "") if voyage else []

        for line in transit_lines:
            lot = line.lot_id
            result.append({
                "lot": lot,
                "qty": line.product_uom_qty or 0.0,
                "grosor": self._tc_lot_value(lot, "x_grosor"),
                "alto": self._tc_lot_value(lot, "x_alto", 0.0),
                "ancho": self._tc_lot_value(lot, "x_ancho", 0.0),
                "color": self._tc_lot_value(lot, "x_color"),
                "bloque": self._tc_lot_value(lot, "x_bloque"),
                "numero_placa": self._tc_lot_value(lot, "x_numero_placa"),
                "atado": self._tc_lot_value(lot, "x_atado"),
                "grupo": self._tc_lot_groups_value(lot),
                "pedimento": self._tc_lot_value(lot, "x_pedimento"),
                "contenedor": line.container_number or self._tc_lot_value(lot, "x_contenedor"),
                "ref_proveedor": self._tc_lot_value(lot, "x_referencia_proveedor"),
                "ref_interna": lot.name or "",
            })

        if result:
            return result

        product_move_lines = self.move_line_ids.filtered(
            lambda ml: ml.product_id.id == product.id and ml.lot_id
        ).sorted(lambda ml: ml.lot_id.name or "")

        for ml in product_move_lines:
            lot = ml.lot_id
            result.append({
                "lot": lot,
                "qty": self._tc_move_line_qty(ml),
                "grosor": self._tc_lot_value(lot, "x_grosor"),
                "alto": self._tc_lot_value(lot, "x_alto", 0.0),
                "ancho": self._tc_lot_value(lot, "x_ancho", 0.0),
                "color": self._tc_lot_value(lot, "x_color"),
                "bloque": self._tc_lot_value(lot, "x_bloque"),
                "numero_placa": self._tc_lot_value(lot, "x_numero_placa"),
                "atado": self._tc_lot_value(lot, "x_atado"),
                "grupo": self._tc_lot_groups_value(lot),
                "pedimento": self._tc_lot_value(lot, "x_pedimento"),
                "contenedor": self._tc_lot_value(lot, "x_contenedor"),
                "ref_proveedor": self._tc_lot_value(lot, "x_referencia_proveedor"),
                "ref_interna": lot.name or "",
            })

        return result

    # -------------------------------------------------------------------------
    #  PL FÍSICO PREFILL
    # -------------------------------------------------------------------------

    def action_open_packing_list_spreadsheet(self):
        """
        En recepción física:
        - Si no existe Spreadsheet PL, lo crea desde move_line_ids o voyage.line_ids.
        - NO marca packing_list_imported=True al abrir.
        - El flag packing_list_imported se marca únicamente al procesar/reprocesar PL.
        """
        self.ensure_one()

        if self._tc_is_physical_reception():
            if self.state in ("done", "cancel"):
                raise UserError(_("La recepción física ya está cerrada o cancelada."))

            if not self.spreadsheet_id:
                self._tc_create_physical_packing_list_spreadsheet()

            return self._action_launch_spreadsheet(self.spreadsheet_id)

        return super().action_open_packing_list_spreadsheet()

    def action_regenerate_physical_pl_spreadsheet(self):
        """Descarta la plantilla del PL físico y crea una nueva con los datos
        VIGENTES del viaje (Ref. Interna = nombre actual del lote).

        Necesario tras un renumerado de lotes: la plantilla se crea una sola
        vez y conserva Ref. Internas viejas; con los nombres reacomodados, el
        emparejamiento por nombre se cruza y el import bloquea placas
        reservadas que sí vienen en el embarque."""
        self.ensure_one()

        if not self._tc_is_physical_reception():
            raise UserError(_("Solo aplica a recepciones físicas."))

        if self.state in ("done", "cancel"):
            raise UserError(_("La recepción física ya está cerrada o cancelada."))

        old_sheet = self.spreadsheet_id
        if old_sheet:
            old_sheet.sudo().unlink()
            self.spreadsheet_id = False

        self._tc_create_physical_packing_list_spreadsheet()

        self.message_post(body=_(
            "♻️ PL físico regenerado con los nombres y datos vigentes del "
            "viaje. La plantilla anterior se descartó (sus cambios no "
            "procesados se perdieron)."
        ))

        return self._action_launch_spreadsheet(self.spreadsheet_id)

    def action_tc_renumber_lots_by_product(self):
        """Renumera los lotes del embarque: por contenedor, en bloques
        CORRIDOS por producto (01..N el primero, N+1..M el siguiente).

        Corrige embarques cuyo renumerado corrió antes del fix del orden
        (series intercaladas: pares/nones con 2 productos, de 3 en 3 con
        tres). Es seguro para reservas/holds/parcialidades: todo referencia
        al lote por ID, no por nombre. Descarta el Worksheet en edición
        (si no está procesado) para que renazca con los nombres nuevos."""
        self.ensure_one()

        if not self._tc_is_physical_reception():
            raise UserError(_("Solo aplica a recepciones físicas."))

        voyage = self._tc_get_physical_reception_voyage()
        if not voyage:
            raise UserError(_("No se encontró el viaje vinculado a esta recepción física."))

        def parse(name):
            match = re.match(r"^(.+)-(\d+)$", name or "")
            if not match:
                return None, 999999
            return match.group(1), int(match.group(2))

        groups = {}
        for line in voyage.line_ids:
            if not line.lot_id:
                continue
            container = (line.container_number or "SN").strip() or "SN"
            groups.setdefault(container, self.env["stock.lot"])
            groups[container] |= line.lot_id

        summary = []
        for container, lots in groups.items():
            counts = {}
            for lot in lots:
                prefix, _seq = parse(lot.name)
                if prefix:
                    counts[prefix] = counts.get(prefix, 0) + 1
            if not counts:
                continue
            target = max(counts, key=lambda p: counts[p])

            # Productos en su orden físico (primera secuencia en que
            # aparecen); dentro de cada producto se respeta el orden actual.
            first_seq = {}
            for lot in lots:
                seq = parse(lot.name)[1]
                pid = lot.product_id.id
                first_seq[pid] = min(first_seq.get(pid, seq), seq)
            ordered = lots.sorted(key=lambda l: (
                first_seq[l.product_id.id],
                l.product_id.id,
                parse(l.name)[1],
                l.id,
            ))

            desired = {
                lot.id: "%s-%02d" % (target, idx)
                for idx, lot in enumerate(ordered, 1)
            }
            if all(lot.name == desired[lot.id] for lot in ordered):
                continue

            # Dos pasos para no chocar con la unicidad de nombres.
            for lot in ordered:
                lot.sudo().write({"name": "TMP-RNP-%s" % lot.id})
            for lot in ordered:
                lot.sudo().write({"name": desired[lot.id]})

            summary.append("%s: %s lotes → serie %s" % (
                container, len(ordered), target))

        if not summary:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Renumerado de lotes"),
                    "message": _("Los lotes ya estaban corridos por producto; no se cambió nada."),
                    "type": "info",
                },
            }

        # El Worksheet en edición quedó con nombres viejos: se descarta para
        # que renazca con los nombres nuevos (solo si aún no se procesó).
        if (self.state not in ("done", "cancel")
                and "ws_spreadsheet_id" in self._fields
                and self.ws_spreadsheet_id and not self.worksheet_imported):
            self.ws_spreadsheet_id.sudo().unlink()
            self.write({"ws_spreadsheet_id": False})

        body = Markup(
            "🔢 <b>Lotes renumerados por producto</b> (bloques corridos por "
            "contenedor): %s. El Worksheet en edición se descartó para "
            "regenerarse con los nombres nuevos."
        ) % "; ".join(summary)
        self.message_post(body=body)
        voyage.message_post(body=body)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Renumerado de lotes"),
                "message": "; ".join(summary),
                "type": "success",
            },
        }

    def _tc_create_physical_packing_list_spreadsheet(self):
        self.ensure_one()

        voyage = self._tc_get_physical_reception_voyage()

        products = self.move_line_ids.mapped("product_id") | self.move_ids.mapped("product_id")

        if voyage:
            products |= voyage.line_ids.mapped("product_id")

        products = products.sorted(lambda p: (p.default_code or p.name or "").lower())

        if not products:
            raise UserError(_("No hay productos/lotes para preparar el Packing List físico."))

        folder = self.env["documents.document"].search([("type", "=", "folder")], limit=1)

        common_headers_suffix = [
            "Peso (kg)",
            "Notas",
            "Bloque",
            "No. Placa",
            "Atado",
            "Grupo",
            "Pedimento",
            "Contenedor",
            "Ref. Proveedor",
            "Ref. Interna",
        ]

        sheets = []

        for product in products:
            cells = {}
            cells["A1"] = self._make_cell("PRODUCTO:")
            cells["B1"] = self._make_cell(f"{product.name} ({product.default_code or ''})")

            unit_type = product.product_tmpl_id.x_unidad_del_producto or "Placa"

            if unit_type == "Placa":
                headers = ["Grosor (cm)", "Alto (m)", "Largo (m)"] + common_headers_suffix
            else:
                headers = ["Grosor (cm)", "Cantidad"] + common_headers_suffix

            for i, header in enumerate(headers):
                cells[f"{self._get_col_letter(i)}3"] = self._make_cell(header, style=1)

            row_idx = 4
            physical_lines = self._tc_collect_physical_pl_lines(product)

            for item in physical_lines:
                lot = item["lot"]

                if unit_type == "Placa":
                    cells[f"A{row_idx}"] = self._make_cell(item.get("grosor"))
                    cells[f"B{row_idx}"] = self._make_cell(item.get("alto", 0.0))
                    cells[f"C{row_idx}"] = self._make_cell(item.get("ancho", 0.0))
                    cells[f"D{row_idx}"] = self._make_cell("")
                    cells[f"E{row_idx}"] = self._make_cell(item.get("color"))
                    cells[f"F{row_idx}"] = self._make_cell(item.get("bloque"))
                    cells[f"G{row_idx}"] = self._make_cell(item.get("numero_placa"))
                    cells[f"H{row_idx}"] = self._make_cell(item.get("atado"))
                    cells[f"I{row_idx}"] = self._make_cell(item.get("grupo"))
                    cells[f"J{row_idx}"] = self._make_cell(item.get("pedimento"))
                    cells[f"K{row_idx}"] = self._make_cell(item.get("contenedor"))
                    cells[f"L{row_idx}"] = self._make_cell(item.get("ref_proveedor"))
                    cells[f"M{row_idx}"] = self._make_cell(item.get("ref_interna") or lot.name)
                else:
                    cells[f"A{row_idx}"] = self._make_cell(item.get("grosor"))
                    cells[f"B{row_idx}"] = self._make_cell(item.get("qty", 0.0))
                    cells[f"C{row_idx}"] = self._make_cell("")
                    cells[f"D{row_idx}"] = self._make_cell(item.get("color"))
                    cells[f"E{row_idx}"] = self._make_cell(item.get("bloque"))
                    cells[f"F{row_idx}"] = self._make_cell(item.get("numero_placa"))
                    cells[f"G{row_idx}"] = self._make_cell(item.get("atado"))
                    cells[f"H{row_idx}"] = self._make_cell(item.get("grupo"))
                    cells[f"I{row_idx}"] = self._make_cell(item.get("pedimento"))
                    cells[f"J{row_idx}"] = self._make_cell(item.get("contenedor"))
                    cells[f"K{row_idx}"] = self._make_cell(item.get("ref_proveedor"))
                    cells[f"L{row_idx}"] = self._make_cell(item.get("ref_interna") or lot.name)

                row_idx += 1

            sheet_name = (product.default_code or product.name or f"P{product.id}")[:31]
            base_name = sheet_name
            count = 1

            while any(s["name"] == sheet_name for s in sheets):
                sheet_name = f"{base_name[:28]}_{count}"
                count += 1

            sheets.append({
                "id": f"pl_sheet_{product.id}",
                "name": sheet_name,
                "cells": cells,
                "colNumber": 14,
                "rowNumber": max(row_idx + 80, 250),
                "isProtected": True,
                "protectedRanges": [
                    {"range": f"A4:N{max(row_idx + 80, 250)}", "isProtected": False}
                ],
            })

        spreadsheet_data = {
            "version": 16,
            "sheets": sheets,
            "styles": {
                "1": {
                    "bold": True,
                    "fillColor": "#366092",
                    "textColor": "#FFFFFF",
                    "align": "center",
                }
            },
        }

        vals = {
            "name": f"PL Físico: {self.name}.osheet",
            "type": "binary",
            "handler": "spreadsheet",
            "mimetype": "application/o-spreadsheet",
            "spreadsheet_data": json.dumps(spreadsheet_data, ensure_ascii=False, default=str),
            "res_model": "stock.picking",
            "res_id": self.id,
        }

        if folder:
            vals["folder_id"] = folder.id

        self.spreadsheet_id = self.env["documents.document"].create(vals)

        self.message_post(
            body=_(
                "📋 Packing List físico preparado. "
                "Puede corregir/agregar placas y después usar Procesar PL físico."
            )
        )

        return self.spreadsheet_id