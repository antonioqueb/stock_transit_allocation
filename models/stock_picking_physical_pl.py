# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPickingPhysicalPackingList(models.Model):
    _inherit = "stock.picking"

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

    # -------------------------------------------------------------------------
    #  PL FÍSICO PREFILL
    # -------------------------------------------------------------------------

    def action_open_packing_list_spreadsheet(self):
        """
        En recepción física:
        - Si no existe Spreadsheet PL, lo crea desde las move lines actuales.
        - Permite que el usuario agregue placas omitidas o quite/corrija placas.
        - Después el wizard de PL físico reconcilia el picking y permite generar WS.
        """
        self.ensure_one()

        if self._tc_is_physical_reception():
            if self.state in ("done", "cancel"):
                raise UserError(_("La recepción física ya está cerrada o cancelada."))

            if not self.packing_list_imported:
                self.write({"packing_list_imported": True})

            if not self.spreadsheet_id:
                self._tc_create_physical_packing_list_spreadsheet()

            return self._action_launch_spreadsheet(self.spreadsheet_id)

        return super().action_open_packing_list_spreadsheet()

    def _tc_create_physical_packing_list_spreadsheet(self):
        self.ensure_one()

        products = (self.move_line_ids.mapped("product_id") | self.move_ids.mapped("product_id"))
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
                headers = ["Grosor (cm)", "Alto (m)", "Ancho (m)"] + common_headers_suffix
            else:
                headers = ["Grosor (cm)", "Cantidad"] + common_headers_suffix

            for i, header in enumerate(headers):
                cells[f"{self._get_col_letter(i)}3"] = self._make_cell(header, style=1)

            row_idx = 4
            product_move_lines = self.move_line_ids.filtered(
                lambda ml: ml.product_id.id == product.id and ml.lot_id
            ).sorted(lambda ml: ml.lot_id.name or "")

            for ml in product_move_lines:
                lot = ml.lot_id
                qty = self._tc_move_line_qty(ml)

                if unit_type == "Placa":
                    cells[f"A{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_grosor"))
                    cells[f"B{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_alto", 0.0))
                    cells[f"C{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_ancho", 0.0))
                    cells[f"D{row_idx}"] = self._make_cell("")
                    cells[f"E{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_color"))
                    cells[f"F{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_bloque"))
                    cells[f"G{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_numero_placa"))
                    cells[f"H{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_atado"))
                    cells[f"I{row_idx}"] = self._make_cell(self._tc_lot_groups_value(lot))
                    cells[f"J{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_pedimento"))
                    cells[f"K{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_contenedor"))
                    cells[f"L{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_referencia_proveedor"))
                    cells[f"M{row_idx}"] = self._make_cell(lot.name)
                else:
                    cells[f"A{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_grosor"))
                    cells[f"B{row_idx}"] = self._make_cell(qty)
                    cells[f"C{row_idx}"] = self._make_cell("")
                    cells[f"D{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_color"))
                    cells[f"E{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_bloque"))
                    cells[f"F{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_numero_placa"))
                    cells[f"G{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_atado"))
                    cells[f"H{row_idx}"] = self._make_cell(self._tc_lot_groups_value(lot))
                    cells[f"I{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_pedimento"))
                    cells[f"J{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_contenedor"))
                    cells[f"K{row_idx}"] = self._make_cell(self._tc_lot_value(lot, "x_referencia_proveedor"))
                    cells[f"L{row_idx}"] = self._make_cell(lot.name)

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
                "📋 Packing List físico preparado desde los lotes actuales de la recepción. "
                "Puede corregir/agregar placas y después usar Reprocesar PL."
            )
        )

        return self.spreadsheet_id