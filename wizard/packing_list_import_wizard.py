# -*- coding: utf-8 -*-
import logging
import re
from collections import Counter

from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero, float_compare

_logger = logging.getLogger(__name__)


class PackingListImportWizardPhysicalReception(models.TransientModel):
    _inherit = "packing.list.import.wizard"

    # -------------------------------------------------------------------------
    #  ENTRYPOINT
    # -------------------------------------------------------------------------

    def _tc_reception_guard_context(self):
        ctx = dict(self.env.context or {})
        ctx.update({
            "tc_physical_reception_prepare": True,
            "tc_no_auto_validate": True,
            "skip_procurement": True,
            "skip_immediate_transfer": True,
            "skip_backorder": True,
            "skip_whole_lot": True,
            "skip_whole_lot_removal": True,
            "skip_whole_lot_reservation": True,
            "skip_whole_lot_strategy": True,
            "skip_auto_assign": True,
            "skip_auto_reserve": True,
            "tracking_disable": True,
        })
        return ctx

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
    #  HELPERS GENERALES
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
        text = str(value or "").strip().lower()

        if not text:
            return ""

        # '1', '01', '1.0' y la celda numérica 1.0 de Excel son la MISMA
        # identidad en bloque/nº placa/atado/grosor. Sin canonicalizar, la
        # firma exacta y el puntaje difuso fallaban por puro formato numérico
        # y el PL físico dejaba de reconocer placas que sí venían en el viaje.
        try:
            number = float(text)
        except ValueError:
            return text

        if number == int(number):
            return str(int(number))

        return "%g" % number

    def _tc_pairing_candidate_lots(self, voyage):
        """Lotes candidatos para emparejar filas del PL físico.

        El VIAJE es la fuente de verdad del contenido del embarque: sus
        líneas cargan las reservas a pedidos y son lo que la torre muestra.
        Los lotes que solo viven en las move lines de la recepción (resaca de
        una conciliación anterior cuya serie ya fue reemplazada en el viaje)
        NO participan cuando el viaje tiene lotes propios: emparejar contra
        ellos "roba" las filas de las placas reales y bloquea la importación
        con el guard de placas reservadas, aunque las placas sí vengan.
        """
        voyage_lots = voyage.line_ids.mapped("lot_id").filtered(
            lambda lot: lot and lot.name
        )

        if voyage_lots:
            return voyage_lots

        return (
            self.picking_id.move_line_ids.mapped("lot_id")
            | voyage.line_ids.mapped("lot_id")
        ).filtered(lambda lot: lot and lot.name)

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

    def _tc_parse_lot_name(self, lot_name):
        """
        Convierte:
            20206-01 -> ("20206", 1)

        El prefijo 20206 NO se trata como número literal de contenedor físico,
        sino como prefijo consecutivo del grupo/recepción/contenedor operativo.
        """
        raw = str(lot_name or "").strip()
        match = re.match(r"^(.+)-(\d+)$", raw)
        if not match:
            return False, 0

        prefix = match.group(1)
        try:
            seq = int(match.group(2))
        except Exception:
            seq = 0

        return prefix, seq

    def _tc_lot_container_value(self, lot):
        if not lot:
            return ""

        if "x_contenedor" in lot._fields and lot.x_contenedor:
            return str(lot.x_contenedor or "").strip()

        if getattr(lot, "ref", False):
            return str(lot.ref or "").strip()

        return ""

    def _tc_row_container_key(self, row, fallback_lot=False):
        container = str(row.get("contenedor") or "").strip()

        if not container and fallback_lot:
            container = self._tc_lot_container_value(fallback_lot)

        return container or "SN"

    def _tc_collect_existing_sequence_lots(self, voyage):
        """
        Lotes disponibles para detectar el prefijo correcto de la recepción.

        Mismos candidatos que el emparejamiento: el viaje manda. Si las move
        lines de la recepción arrastran una serie vieja (p.ej. S23-*) que el
        viaje ya reemplazó (S26-*), contarlas aquí hacía ganar al prefijo
        muerto y los lotes nuevos nacían en la serie equivocada.
        """
        return self._tc_pairing_candidate_lots(voyage)

    def _tc_choose_prefix_from_lots(self, lots):
        """
        Elige el prefijo dominante.

        Caso real:
        - Ya existen 20206-01 ... 20206-05
        - Por error se generaron 20207-01 ... 20207-03

        Debe seguir mandando 20206, porque es el prefijo dominante de la recepción.
        """
        prefix_counter = Counter()

        for lot in lots:
            prefix, seq = self._tc_parse_lot_name(lot.name)
            if prefix:
                prefix_counter[prefix] += 1

        if not prefix_counter:
            return False

        def sort_key(item):
            prefix, count = item
            # Igual que en _tc_sort_lots_for_sequence: la serie "S" no es
            # casteable con int() directo; se extrae la parte numérica para
            # que el desempate por prefijo menor funcione también en S49/S53.
            digits = re.search(r"\d+", prefix or "")
            numeric_prefix = int(digits.group()) if digits else 999999999
            # más frecuente primero; en empate, prefijo menor primero
            return (-count, numeric_prefix, prefix)

        return sorted(prefix_counter.items(), key=sort_key)[0][0]

    def _tc_existing_lots_for_container(self, voyage, container_key):
        all_lots = self._tc_collect_existing_sequence_lots(voyage)

        if not all_lots:
            return self.env["stock.lot"]

        container_key = str(container_key or "").strip()

        if container_key and container_key not in ("SN", "PENDIENTE", "False"):
            line_lots = voyage.line_ids.filtered(
                lambda line: (
                    line.lot_id
                    and str(line.container_number or "").strip() == container_key
                )
            ).mapped("lot_id")

            lot_field_lots = all_lots.filtered(
                lambda lot: self._tc_lot_container_value(lot) == container_key
            )

            scoped = (line_lots | lot_field_lots).filtered(lambda lot: lot and lot.name)
            if scoped:
                return scoped

        # Fallback importante:
        # Si no hay contenedor explícito o no se puede mapear,
        # se usa el universo de la recepción/viaje para mantener el prefijo actual.
        return all_lots

    def _tc_find_existing_prefix_for_container(self, voyage, container_key):
        lots = self._tc_existing_lots_for_container(voyage, container_key)
        return self._tc_choose_prefix_from_lots(lots)

    def _tc_get_sequence_for_container(self, voyage, container_key, container_sequences):
        """
        Devuelve la secuencia operativa para un contenedor/grupo.

        Cambio principal:
        - Antes, si la placa era nueva, se usaba _get_next_global_prefix().
        - Ahora primero se busca el prefijo ya usado en esa recepción/contenedor.
        - Solo si no existe ningún prefijo previo, se usa uno global nuevo.
        """
        key = str(container_key or "SN").strip() or "SN"

        if key in container_sequences:
            return container_sequences[key]

        existing_prefix = self._tc_find_existing_prefix_for_container(voyage, key)

        if existing_prefix:
            prefix = str(existing_prefix)
        else:
            if "_next_prefix" not in container_sequences:
                container_sequences["_next_prefix"] = self._get_next_global_prefix()

            prefix = str(container_sequences["_next_prefix"])
            container_sequences["_next_prefix"] += 1

        next_number = self._get_next_lot_number_for_prefix(prefix)

        container_sequences[key] = {
            "prefix": prefix,
            "num": next_number,
        }

        return container_sequences[key]

    def _tc_seed_sequence_from_existing_lot(self, voyage, row, lot, container_sequences):
        """
        Cuando una fila reutiliza un lote existente, si ese lote tiene prefijo,
        se usa para sembrar la secuencia del contenedor. Así, si después aparece
        una placa nueva, continúa como 20206-06 y no como 20207-01.
        """
        if not lot:
            return

        container_key = self._tc_row_container_key(row, fallback_lot=lot)
        prefix, seq = self._tc_parse_lot_name(lot.name)

        if not prefix:
            return

        if container_key not in container_sequences:
            container_sequences[container_key] = {
                "prefix": prefix,
                "num": self._get_next_lot_number_for_prefix(prefix),
            }
        else:
            current = container_sequences[container_key]
            if current.get("prefix") == prefix:
                current["num"] = max(
                    current.get("num") or 1,
                    seq + 1,
                    self._get_next_lot_number_for_prefix(prefix),
                )

    # -------------------------------------------------------------------------
    #  EXTRACCIÓN — LAYOUT PROPIO DEL PL FÍSICO
    # -------------------------------------------------------------------------

    def _extract_rows_from_index(self, idx, product):
        """
        El PL físico se GENERA (stock_picking_physical_pl) con un orden de
        columnas distinto al de la plantilla PL original:

            PL físico  (placa): A=Grosor | B=Alto | C=Largo | D=Peso | E=Notas ...
            PL original (placa): A=Largo | B=Alto | C=Grosor | D=Peso | E=Color ...

        El parser base asume el layout original, por lo que al procesar el PL
        físico intercambiaba grosor↔largo: los lotes quedaban con x_ancho=grosor
        y x_grosor=largo, los m² salían mal (alto×grosor) y la firma de
        emparejamiento fallaba (origen del bug de lotes duplicados).

        Además el PL físico trae la columna 'Ref. Interna' (nombre del lote
        original), que aquí se extrae para emparejar de forma determinística.
        """
        if not self._tc_is_physical_reception_import():
            return super()._extract_rows_from_index(idx, product)

        unit_type = product.product_tmpl_id.x_unidad_del_producto or "Placa"
        is_placa = str(unit_type).lower() == "placa"

        if is_placa:
            # A=Grosor B=Alto C=Largo D=Peso E=Notas F=Bloque G=No.Placa
            # H=Atado I=Grupo J=Pedimento K=Contenedor L=Ref.Prov M=Ref.Interna
            col = {
                "notas": 4, "bloque": 5, "placa": 6, "atado": 7, "grupo": 8,
                "pedimento": 9, "contenedor": 10, "ref": 11, "ref_interna": 12,
            }
        else:
            # A=Grosor B=Cantidad C=Peso D=Notas E=Bloque F=No.Placa
            # G=Atado H=Grupo I=Pedimento J=Contenedor K=Ref.Prov L=Ref.Interna
            col = {
                "notas": 3, "bloque": 4, "placa": 5, "atado": 6, "grupo": 7,
                "pedimento": 8, "contenedor": 9, "ref": 10, "ref_interna": 11,
            }

        rows = []
        filas_validas = 0
        filas_invalidas = 0

        for r in range(3, 300):
            raw_a = idx.value(0, r)
            raw_b = idx.value(1, r)
            raw_c = idx.value(2, r)

            val_b = self._to_float(raw_b)
            val_c = self._to_float(raw_c)

            if is_placa:
                # PL físico: B=Alto, C=Largo (el largo vive en x_ancho).
                es_valido = val_b > 0 and val_c > 0
            else:
                # Pieza/formato: B=Cantidad.
                es_valido = val_b > 0

            if not es_valido:
                if filas_invalidas < 5 and (raw_a or raw_b or raw_c):
                    _logger.info(
                        "[TC_PHYSICAL_PL] Fila %s inválida | A='%s' B='%s' C='%s'",
                        r + 1, raw_a, raw_b, raw_c,
                    )
                    filas_invalidas += 1
                continue

            filas_validas += 1
            rows.append({
                "product": product,
                "grosor": str(raw_a or "").strip(),
                "alto": val_b if is_placa else 0.0,
                "ancho": val_c if is_placa else 0.0,
                "quantity": val_b if not is_placa else 0.0,
                "color": str(idx.value(col["notas"], r) or "").strip(),
                "bloque": str(idx.value(col["bloque"], r) or "").strip(),
                "numero_placa": str(idx.value(col["placa"], r) or "").strip(),
                "atado": str(idx.value(col["atado"], r) or "").strip(),
                "tipo": unit_type,
                "grupo_name": str(idx.value(col["grupo"], r) or "").strip(),
                "pedimento": str(idx.value(col["pedimento"], r) or "").strip(),
                "contenedor": str(idx.value(col["contenedor"], r) or "SN").strip(),
                "ref_proveedor": str(idx.value(col["ref"], r) or "").strip(),
                "ref_interna": str(idx.value(col["ref_interna"], r) or "").strip(),
            })

        _logger.info(
            "[TC_PHYSICAL_PL] Layout físico | filas válidas: %s | inválidas con contenido: %s",
            filas_validas, filas_invalidas,
        )
        return rows

    # -------------------------------------------------------------------------
    #  LOTES
    # -------------------------------------------------------------------------

    def _tc_find_existing_lot(self, voyage, row, used_lot_ids):
        target_sig = self._tc_row_signature(row)

        candidates = self._tc_pairing_candidate_lots(voyage).filtered(
            lambda lot: (
                lot.product_id.id == row["product"].id
                and lot.id not in used_lot_ids
            )
        )

        for lot in candidates:
            if self._tc_lot_signature(lot) == target_sig:
                return lot

        return False

    def _tc_score_row_lot(self, row, lot):
        """
        Puntaje de similitud fila↔lote para el emparejamiento difuso.

        Solo suman los campos con valor en la fila (empatar vacío-con-vacío
        no aporta identidad y inflaría el puntaje).
        """
        score = 0

        def _match(row_key, lot_field, points):
            nonlocal score
            row_val = self._tc_normalize_text(row.get(row_key))
            if row_val and row_val == self._tc_normalize_text(getattr(lot, lot_field, "")):
                score += points

        _match("bloque", "x_bloque", 3)
        _match("numero_placa", "x_numero_placa", 3)
        _match("ref_proveedor", "x_referencia_proveedor", 2)
        _match("atado", "x_atado", 1)
        _match("grosor", "x_grosor", 1)

        # Dimensiones físicas (solo placas): alto/ancho casi nunca se editan
        # a la vez, son un identificador fuerte de la placa física.
        # El layout del PL físico no siempre trae columna 'tipo': si la fila
        # no lo declara, se toma el tipo del lote candidato (default placa);
        # antes la ausencia de la columna apagaba este criterio y el difuso
        # quedaba ciego justo donde más identidad hay.
        unit_type = self._tc_normalize_text(row.get("tipo")) \
            or self._tc_normalize_text(getattr(lot, "x_tipo", "")) \
            or "placa"
        if unit_type == "placa":
            alto = row.get("alto") or 0.0
            ancho = row.get("ancho") or 0.0
            if alto > 0 and ancho > 0:
                alto_ok = abs(alto - (getattr(lot, "x_alto", 0.0) or 0.0)) < 0.005
                ancho_ok = abs(ancho - (getattr(lot, "x_ancho", 0.0) or 0.0)) < 0.005
                if alto_ok and ancho_ok:
                    score += 3

        return score

    def _tc_pair_rows_with_lots(self, voyage, valid_rows):
        """
        Empareja TODAS las filas del PL físico con los lotes existentes de la
        recepción/viaje ANTES de crear nada, en tres pasadas globales:

        1. Firma exacta (comportamiento original).
        2. Difusa por puntaje: tolera que el usuario haya corregido uno o dos
           campos (ref. proveedor, atado, grosor...) sin perder la identidad
           del lote — evita duplicar el lote y liberar sus holds.
        3. Posicional: si tras 1 y 2 quedan N filas y N lotes sueltos del
           mismo producto (cuentas iguales ⇒ son los mismos fierros con datos
           corregidos), se emparejan en orden. Si las cuentas NO cuadran, no
           se arriesga: la fila generará lote nuevo y el sobrante se omite.

        Hacerlo global (y no fila-por-fila) evita que una fila editada le
        "robe" por coincidencia difusa el lote que le corresponde exactamente
        a otra fila posterior.

        Devuelve {índice_de_fila: stock.lot}.
        """
        candidates = self._tc_pairing_candidate_lots(voyage)

        pairing = {}
        used = set()

        # ── Pasada 0: Ref. Interna (nombre del lote original) ──────────────
        # El PL físico se genera con la columna 'Ref. Interna' prellenada con
        # el nombre del lote. Si el usuario no la borra, el emparejamiento es
        # determinístico: puede editar CUALQUIER otro campo sin duplicar lotes.
        lots_by_name = {lot.name: lot for lot in candidates}

        for i, (row, _qty) in enumerate(valid_rows):
            ref_interna = str(row.get("ref_interna") or "").strip()
            if not ref_interna:
                continue

            lot = lots_by_name.get(ref_interna)
            if lot and lot.id not in used and lot.product_id.id == row["product"].id:
                pairing[i] = lot
                used.add(lot.id)

        # ── Pasada 1: firma exacta ──────────────────────────────────────────
        for i, (row, _qty) in enumerate(valid_rows):
            target_sig = self._tc_row_signature(row)
            for lot in candidates:
                if lot.id in used or lot.product_id.id != row["product"].id:
                    continue
                if self._tc_lot_signature(lot) == target_sig:
                    pairing[i] = lot
                    used.add(lot.id)
                    break

        # ── Pasada 2: difusa por puntaje ────────────────────────────────────
        fuzzy_pairs = []
        for i, (row, _qty) in enumerate(valid_rows):
            if i in pairing:
                continue

            best_lot = False
            best_score = 0

            for lot in candidates:
                if lot.id in used or lot.product_id.id != row["product"].id:
                    continue
                score = self._tc_score_row_lot(row, lot)
                if score > best_score:
                    best_lot = lot
                    best_score = score

            # Umbral 3: exige al menos un identificador fuerte (bloque,
            # nº placa o dimensiones) o una combinación de débiles.
            if best_lot and best_score >= 3:
                pairing[i] = best_lot
                used.add(best_lot.id)
                fuzzy_pairs.append((row, best_lot, best_score))

        # ── Pasada 3: posicional por producto cuando las cuentas cuadran ───
        positional_pairs = []
        products = {row["product"].id for row, _qty in valid_rows}

        for product_id in products:
            leftover_rows = [
                i for i, (row, _qty) in enumerate(valid_rows)
                if i not in pairing and row["product"].id == product_id
            ]
            if not leftover_rows:
                continue

            leftover_lots = self._tc_sort_lots_for_sequence(candidates.filtered(
                lambda lot: lot.id not in used and lot.product_id.id == product_id
            ))

            if len(leftover_rows) != len(leftover_lots):
                _logger.info(
                    "[TC_PHYSICAL_PL] Pasada posicional omitida para producto %s: "
                    "%s filas sueltas vs %s lotes sueltos.",
                    product_id, len(leftover_rows), len(leftover_lots),
                )
                continue

            for i, lot in zip(leftover_rows, leftover_lots):
                pairing[i] = lot
                used.add(lot.id)
                positional_pairs.append((valid_rows[i][0], lot))

        # ── Trazabilidad en chatter ─────────────────────────────────────────
        if fuzzy_pairs or positional_pairs:
            lines = []
            for row, lot, score in fuzzy_pairs:
                lines.append(_(
                    "• %(lot)s ↔ fila con bloque '%(bloque)s' / placa '%(placa)s' "
                    "(coincidencia parcial, puntaje %(score)s)"
                ) % {
                    "lot": lot.name,
                    "bloque": row.get("bloque") or "-",
                    "placa": row.get("numero_placa") or "-",
                    "score": score,
                })
            for row, lot in positional_pairs:
                lines.append(_(
                    "• %(lot)s ↔ fila con bloque '%(bloque)s' / placa '%(placa)s' "
                    "(emparejado por posición: datos corregidos en PL físico)"
                ) % {
                    "lot": lot.name,
                    "bloque": row.get("bloque") or "-",
                    "placa": row.get("numero_placa") or "-",
                })

            self.picking_id.message_post(body=_(
                "🔗 PL físico: %(count)s lote(s) reutilizados con datos corregidos "
                "(en lugar de duplicarlos):<br/>%(detail)s"
            ) % {
                "count": len(fuzzy_pairs) + len(positional_pairs),
                "detail": "<br/>".join(lines),
            })

        return pairing

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

    def _tc_get_or_create_lot(self, voyage, row, used_lot_ids, container_sequences, forced_lot=False):
        existing_lot = forced_lot or self._tc_find_existing_lot(voyage, row, used_lot_ids)

        if existing_lot:
            vals = self._tc_prepare_lot_vals(row)
            vals.pop("product_id", None)
            vals.pop("company_id", None)

            existing_lot.write(vals)
            used_lot_ids.add(existing_lot.id)

            self._tc_seed_sequence_from_existing_lot(
                voyage,
                row,
                existing_lot,
                container_sequences,
            )

            return existing_lot, False

        container_key = self._tc_row_container_key(row)

        seq_data = self._tc_get_sequence_for_container(
            voyage,
            container_key,
            container_sequences,
        )

        # Guardia anti-duplicado: la restricción de stock.lot es única por
        # (producto, nombre, compañía incluso sin definir). _get_next_lot_number_for_prefix
        # filtra por compañía, así que un lote huérfano/omitido con el mismo
        # nombre puede escaparse. Se salta cualquier número ya ocupado.
        Lot = self.env["stock.lot"].sudo().with_context(active_test=False)
        lot_name = f"{seq_data['prefix']}-{seq_data['num']:02d}"
        while Lot.search_count([
            ("name", "=", lot_name),
            ("product_id", "=", row["product"].id),
        ]):
            seq_data["num"] += 1
            lot_name = f"{seq_data['prefix']}-{seq_data['num']:02d}"
        seq_data["num"] += 1

        lot = self.env["stock.lot"].create(
            self._tc_prepare_lot_vals(row, lot_name=lot_name)
        )

        used_lot_ids.add(lot.id)

        self.picking_id.message_post(body=_(
            "➕ Se agregó lote físico no declarado originalmente en PL/recepción en tránsito: "
            "%(lot)s | Producto: %(product)s | Cantidad estimada: %(qty).3f | Contenedor: %(container)s"
        ) % {
            "lot": lot.name,
            "product": product.display_name if (product := row.get("product")) else "",
            "qty": self._tc_effective_qty_from_row(row),
            "container": row.get("contenedor") or "SN",
        })

        return lot, True

    # -------------------------------------------------------------------------
    #  QUANTS / MOVIMIENTOS
    # -------------------------------------------------------------------------

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
        ctx = self._tc_reception_guard_context()
        move_map = {}

        existing_moves = picking.move_ids.filtered(lambda move: move.state not in ("done", "cancel"))

        for move in existing_moves:
            if move.state in ("assigned", "partially_available") and hasattr(move, "_do_unreserve"):
                move.with_context(
                    tc_physical_reception_prepare=True,
                    tc_no_auto_validate=True,
                    skip_procurement=True,
                )._do_unreserve()

            total_qty = product_totals.get(move.product_id.id, 0.0)

            if self._tc_float_is_zero(move.product_id, total_qty):
                move.with_context(
                    tc_physical_reception_prepare=True,
                    tc_no_auto_validate=True,
                    skip_procurement=True,
                ).unlink()
                continue

            vals = {}

            if self._tc_float_compare(move.product_id, move.product_uom_qty, total_qty) != 0:
                vals["product_uom_qty"] = total_qty

            if move.location_id.id != picking.location_id.id:
                vals["location_id"] = picking.location_id.id

            if move.location_dest_id.id != picking.location_dest_id.id:
                vals["location_dest_id"] = picking.location_dest_id.id

            if vals:
                move.with_context(
                    tc_physical_reception_prepare=True,
                    tc_no_auto_validate=True,
                    skip_procurement=True,
                ).write(vals)

            move_map[move.product_id.id] = move

        for product_id, total_qty in product_totals.items():
            product = self.env["product.product"].browse(product_id)

            if product_id in move_map:
                continue

            move = self.env["stock.move"].with_context(
                tc_physical_reception_prepare=True,
                tc_no_auto_validate=True,
                skip_procurement=True,
            ).create({
                "picking_id": picking.id,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": total_qty,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "company_id": picking.company_id.id,
            })
            move_map[product_id] = move

        # No se confirma ni reserva desde PL físico.
        # Confirmar aquí dispara stock_whole_lot_removal/_action_assign antes
        # del Worksheet y puede reconstruir líneas automáticas. La validación
        # manual de la recepción confirmará el traslado al final del flujo.
        if picking.state == "done":
            raise UserError(_(
                "Control Tower detuvo el flujo porque la recepción física %(picking)s "
                "quedó en HECHO al preparar movimientos desde el Packing List físico."
            ) % {
                "picking": picking.name or picking.display_name,
            })

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

        MoveLine = self.env["stock.move.line"]
        if "picked" in MoveLine._fields:
            vals["picked"] = False

        return MoveLine.with_context(
            self._tc_reception_guard_context()
        ).create(vals)

    # -------------------------------------------------------------------------
    #  RENOMBRADO FINAL DE LOTES
    # -------------------------------------------------------------------------

    def _tc_sort_lots_for_sequence(self, lots):
        def sort_key(lot):
            prefix, seq = self._tc_parse_lot_name(lot.name)
            # La serie "S" (S49, S53…) no es casteable con int() directo: si
            # todos los prefijos caen al mismo fallback, el orden queda solo
            # por secuencia y el renumerado intercala las series del mismo
            # contenedor (S49-01, S53-01, S49-02, S53-02…) → lotes pares/nones
            # por producto. Se extrae la parte numérica para mantener cada
            # serie contigua, y el prefijo textual desempata series distintas
            # con el mismo número.
            digits = re.search(r"\d+", prefix or "")
            prefix_num = int(digits.group()) if digits else 999999999

            return (
                prefix_num,
                prefix or "",
                seq or 999999,
                lot.id,
            )

        return lots.sorted(key=sort_key)

    def _tc_build_conflict_free_names(self, target_prefix, ordered_lots, assigned_names):
        """
        Genera nombres secuenciales ``prefijo-NN`` evitando la restricción de
        unicidad de stock.lot (producto + nombre + compañía, incluso sin definir).

        Caso real que reventaba el flujo:
        - El PL físico corrige datos de una placa y su firma ya no coincide con
          el lote original (p.ej. 6-19). Se crea un lote nuevo y el 6-19 viejo
          queda "omitido" (cantidad 0) pero conserva su nombre en BD.
        - El renombrado secuencial intentaba asignar 6-19 a otro lote del mismo
          producto → ValidationError de lote duplicado y rollback total.

        Estrategia:
        - Si el nombre deseado lo ocupa un lote ajeno al grupo pero "muerto"
          (sin existencias en ningún quant), se renombra ese lote huérfano a
          ``<nombre>-OBS-<id>`` para liberar el nombre y mantener la secuencia
          contigua.
        - Si el lote en conflicto sí tiene existencias, no se toca: se salta
          ese número y se continúa con el siguiente.
        """
        Lot = self.env["stock.lot"].sudo().with_context(active_test=False)
        picking = self.picking_id

        product_ids = ordered_lots.mapped("product_id").ids

        # Lotes fuera del grupo que podrían chocar con los nombres deseados.
        conflict_candidates = Lot.search([
            ("name", "=like", f"{target_prefix}-%"),
            ("product_id", "in", product_ids),
            ("id", "not in", ordered_lots.ids),
            ("company_id", "in", [False, picking.company_id.id]),
        ])
        conflicts_by_name = {}
        for lot in conflict_candidates:
            conflicts_by_name.setdefault(lot.name, Lot.browse())
            conflicts_by_name[lot.name] |= lot

        desired_names = {}
        idx = 1

        for lot in ordered_lots:
            while True:
                candidate = f"{target_prefix}-{idx:02d}"
                idx += 1

                if candidate in assigned_names:
                    continue

                conflicting = conflicts_by_name.get(candidate)
                if not conflicting:
                    desired_names[lot.id] = candidate
                    break

                # Solo se puede liberar el nombre si TODOS los lotes en
                # conflicto están muertos (sin existencias).
                stale = all(
                    self._tc_float_is_zero(
                        c.product_id,
                        sum(c.quant_ids.mapped("quantity")),
                    )
                    for c in conflicting
                )

                if stale:
                    for c in conflicting:
                        old_name = c.name
                        # El nombre apartado NO debe empezar con el prefijo
                        # numérico: _get_next_lot_number_for_prefix usa
                        # LIKE 'prefijo-%' y un nombre tipo '6-19-OBS-123'
                        # inflaría la secuencia siguiente.
                        c.write({"name": f"OBS-{c.id}-{old_name}"})
                        _logger.info(
                            "[TC_PHYSICAL_PL] Lote huérfano %s (id %s) renombrado a %s "
                            "para liberar el nombre en el renumerado.",
                            old_name, c.id, c.name,
                        )
                    conflicts_by_name.pop(candidate, None)
                    desired_names[lot.id] = candidate
                    break

                _logger.warning(
                    "[TC_PHYSICAL_PL] Nombre %s ocupado por lote con existencias "
                    "(ids %s); se salta ese número en la secuencia.",
                    candidate, conflicting.ids,
                )

        return desired_names

    def _tc_renumber_physical_reception_lots_by_container(self, voyage):
        """
        Normaliza nombres después de aplicar el PL físico.

        Corrige el caso:
            20206-01
            20206-02
            20206-03
            20206-04
            20206-05
            20207-01
            20207-02
            20207-03

        Resultado:
            20206-01
            20206-02
            20206-03
            20206-04
            20206-05
            20206-06
            20206-07
            20206-08
        """
        picking = self.picking_id

        move_lines = picking.move_line_ids.filtered(lambda ml: ml.lot_id)
        if not move_lines:
            return

        groups = {}

        for ml in move_lines:
            lot = ml.lot_id
            voyage_line = voyage.line_ids.filtered(lambda line: line.lot_id.id == lot.id)[:1]
            container = (
                (voyage_line.container_number if voyage_line else False)
                or self._tc_lot_container_value(lot)
                or "SN"
            )
            container = str(container or "SN").strip() or "SN"
            groups.setdefault(container, self.env["stock.lot"])
            groups[container] |= lot

        # Nombres ya asignados en esta corrida (evita colisión si dos
        # contenedores eligen el mismo prefijo dominante).
        assigned_names = set()

        for container, lots in groups.items():
            target_prefix = self._tc_choose_prefix_from_lots(lots)

            if not target_prefix:
                target_prefix = str(self._get_next_global_prefix())

            ordered_lots = self._tc_sort_lots_for_sequence(lots)

            desired_names = self._tc_build_conflict_free_names(
                target_prefix,
                ordered_lots,
                assigned_names,
            )
            assigned_names.update(desired_names.values())

            already_ok = all(lot.name == desired_names[lot.id] for lot in ordered_lots)
            if already_ok:
                continue

            # Renombrado en dos pasos para evitar colisiones temporales.
            temp_prefix = f"TMP-TC-{picking.id}-{container}".replace("/", "_").replace(" ", "_")

            for lot in ordered_lots:
                lot.write({
                    "name": f"{temp_prefix}-{lot.id}"
                })

            for lot in ordered_lots:
                lot.write({
                    "name": desired_names[lot.id]
                })

            picking.message_post(body=_(
                "🔢 Secuencia de lotes normalizada para contenedor/grupo %(container)s "
                "con prefijo %(prefix)s. Total lotes: %(count)s."
            ) % {
                "container": container,
                "prefix": target_prefix,
                "count": len(ordered_lots),
            })

    # -------------------------------------------------------------------------
    #  APLICACIÓN DEL PL FÍSICO
    # -------------------------------------------------------------------------

    def _tc_apply_physical_reception_pl(self, rows):
        picking = self.picking_id
        ctx = self._tc_reception_guard_context()
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

        # Emparejamiento global fila↔lote ANTES de crear nada (exacto →
        # difuso → posicional). Tolera correcciones de datos en el PL físico
        # sin duplicar lotes ni soltar holds.
        pairing = self._tc_pair_rows_with_lots(voyage, valid_rows)

        for row_index, (row, qty) in enumerate(valid_rows):
            product = row["product"]

            lot, was_new = self._tc_get_or_create_lot(
                voyage,
                row,
                used_lot_ids,
                container_sequences,
                forced_lot=pairing.get(row_index),
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

        # Marcar como omitidas las líneas anteriores que ya no vienen en el PL físico.
        omitted_lines = voyage.line_ids.filtered(
            lambda line: (
                line.lot_id
                and line.lot_id.id not in used_lot_ids
                and line.product_uom_qty > 0
            )
        )

        # GUARD: una línea omitida RESERVADA A UN PEDIDO no se desasigna en
        # silencio. Si el emparejamiento no encontró la placa (identidad muy
        # editada / placa extra), el usuario debe decidir: corregir el PL
        # físico o desasignar la placa del pedido primero. Antes el vendedor
        # perdía su asignación sin ningún aviso (solo una nota en la línea).
        omitted_reserved = omitted_lines.filtered(lambda l: l.order_id)
        if omitted_reserved:
            shown = omitted_reserved[:20]
            more = len(omitted_reserved) - len(shown)
            detail = "\n".join(
                "- %s → %s" % (l.lot_id.display_name, l.order_id.name)
                for l in shown
            )
            if more > 0:
                detail += _("\n… y %s placas reservadas más.") % more
            raise UserError(_(
                "El PL físico no incluye estas placas que están RESERVADAS a "
                "pedidos:\n%(detail)s\n\n"
                "El PL trae %(rows)s filas válidas; el viaje tiene %(lots)s "
                "placas activas (%(paired)s emparejadas).\n\n"
                "Corrige la identificación en el PL físico "
                "(bloque/placa/Ref. Interna) o desasigna las placas de sus "
                "pedidos antes de importar."
            ) % {
                "detail": detail,
                "rows": len(valid_rows),
                "lots": len(voyage.line_ids.filtered(
                    lambda l: l.lot_id and l.product_uom_qty > 0)),
                "paired": len(pairing),
            })

        for line in omitted_lines:
            if line.quant_id and line.quant_id.exists():
                qty_to_remove = line.quant_id.quantity or 0.0
                if qty_to_remove > 0:
                    self.env["stock.quant"].sudo()._update_available_quantity(
                        line.product_id,
                        line.quant_id.location_id,
                        -qty_to_remove,
                        lot_id=line.lot_id,
                    )

            try:
                line._execute_release_logic()
            except Exception as e:
                _logger.warning(
                    "[TC_PHYSICAL_PL] No se pudo liberar hold de lote omitido %s: %s",
                    line.lot_id.display_name,
                    e,
                )

            line.with_context(skip_reservation_logic=True).write({
                "product_uom_qty": 0.0,
                "quant_id": False,
                "allocation_status": "available",
                "partner_id": False,
                "order_id": False,
                "notes": ((line.notes or "") + "\n[Recepción Física] Omitido en PL físico corregido.").strip(),
            })

        # Reconstruir únicamente las líneas de la recepción física.
        if picking.move_line_ids:
            picking.move_line_ids.with_context(
                tc_physical_reception_prepare=True,
                tc_no_auto_validate=True,
                skip_procurement=True,
            ).unlink()

        move_map = self._tc_prepare_moves(product_totals)

        created = 0

        for item in prepared_lines:
            product = item["product"]
            move = move_map.get(product.id)

            if not move:
                raise UserError(_("No se encontró movimiento para %s.") % product.display_name)

            self._tc_create_move_line(move, item["lot"], item["qty"])
            created += 1

        # Normalizar nombres después de reconstruir líneas.
        self._tc_renumber_physical_reception_lots_by_container(voyage)

        if picking.ws_spreadsheet_id:
            picking.ws_spreadsheet_id.sudo().unlink()

        picking.with_context(ctx).write({
            "packing_list_imported": True,
            "worksheet_imported": False,
            "ws_spreadsheet_id": False,
        })

        if picking.state == "done":
            raise UserError(_(
                "Control Tower detuvo el flujo porque la recepción física %(picking)s "
                "se marcó como HECHO al procesar el Packing List físico. "
                "La validación solo puede ejecutarse después del Worksheet y mediante Validar."
            ) % {
                "picking": picking.name or picking.display_name,
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
