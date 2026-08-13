# -*- coding: utf-8 -*-
from odoo import models, api, fields
from odoo.addons.stock_transit_allocation.models.som_date_format import som_format_date


class SupplierAccessTracking(models.Model):
    """Extiende la liga del portal con el dataset de seguimiento para la torre
    de control. La liga sigue 'en proceso' hasta que se valida la recepción en
    tránsito (picking.state == 'done')."""
    _inherit = 'stock.picking.supplier.access'

    @api.model
    def get_links_tracking(self):
        accesses = self.sudo().search([])
        Proforma = self.env['supplier.proforma.header'].sudo()
        Picking = self.env['stock.picking'].sudo()
        Shipment = self.env['supplier.shipment'].sudo()
        has_ship_field = 'supplier_shipment_id' in Picking._fields
        now = fields.Datetime.now()

        # Una liga puede amparar VARIAS POs (factura de carga): el universo de
        # órdenes es la unión de las POs cubiertas por cada acceso.
        def _covered(a):
            try:
                return a._covered_purchase_orders()
            except Exception:
                return a.purchase_id

        all_pos = self.env['purchase.order'].sudo()
        for a in accesses:
            all_pos |= _covered(a)
        proformas = Proforma.search(
            [('purchase_id', 'in', all_pos.ids)]) if all_pos else Proforma
        prof_by_po = {p.purchase_id.id: p for p in proformas}

        # Recepciones por embarque: con la carga multi-PO cada embarque tiene
        # UNA recepción POR PO. El embarque cuenta como recibido solo cuando
        # TODAS sus recepciones (no canceladas) están validadas.
        all_ship_ids = proformas.mapped('shipment_ids').ids
        done_ship_ids = set()
        if has_ship_field and all_ship_ids:
            picks = Picking.search([
                ('supplier_shipment_id', 'in', all_ship_ids),
                ('state', '!=', 'cancel'),
            ])
            states_by_ship = {}
            for pk in picks:
                states_by_ship.setdefault(
                    pk.supplier_shipment_id.id, []).append(pk.state)
            done_ship_ids = {
                sid for sid, states in states_by_ship.items()
                if states and all(st == 'done' for st in states)
            }

        Cargo = self.env['supplier.cargo.invoice'].sudo() \
            if 'supplier.cargo.invoice' in self.env else False

        def _capture_percent(header):
            """Precedencia: % reportado por el propio portal → 100 si la
            proforma está completa → cálculo interno de respaldo."""
            if not header:
                return 0
            if Cargo is not False and hasattr(Cargo, '_header_capture_percent'):
                return Cargo._header_capture_percent(header)
            # Misma precedencia que _header_capture_percent: COMPLETA manda
            # sobre el snapshot guardado (que puede quedarse viejo en <100).
            if (header.status or '') == 'complete':
                return 100
            stored = getattr(header, 'portal_overall_pct', 0) or 0
            if stored > 0:
                return stored
            if hasattr(header, '_portal_progress'):
                try:
                    return header._portal_progress().get('percent', 0)
                except Exception:
                    return 0
            return 0

        def _fmt(dt, with_time=False):
            if not dt:
                return ''
            local = fields.Datetime.context_timestamp(self, dt)
            return som_format_date(local, empty='', with_time=with_time)

        rows = []
        # SOLO tres estados (pedido explícito): no atendida / en captura /
        # terminada. La vigencia se muestra como dato de la fila, no como
        # estado propio, para no perder ligas entre el ruido.
        counts = {'total': 0, 'no_started': 0, 'in_progress': 0, 'done': 0}

        for a in accesses:
            po = a.purchase_id
            pos = _covered(a)
            is_cargo = len(pos) > 1
            partner = po.partner_id if po else False
            proforma = prof_by_po.get(po.id) if po else False
            headers = [
                prof_by_po[p.id] for p in pos if p.id in prof_by_po
            ]
            # Los embarques de la carga viven en la proforma principal, pero se
            # toma la unión por si algún flujo creó embarques en otra PI.
            ships = Shipment
            for h in headers:
                ships |= h.shipment_ids
            if not headers and proforma:
                ships = proforma.shipment_ids
            ship_total = len(ships)
            ship_done = sum(1 for s in ships if s.id in done_ship_ids)
            reception_validated = bool(ship_total and ship_done == ship_total)
            # Avance de captura: proformas CON embarques (donde sucede la
            # captura); las PI hermanas sin embarques no diluyen el promedio.
            capture_headers = [h for h in headers if h.shipment_ids] \
                or ([proforma] if proforma else [])
            percents = [p for p in (
                _capture_percent(h) for h in capture_headers) if p is not None]
            progress = round(sum(percents) / len(percents)) if percents else 0
            status = proforma.status if proforma else 'draft'

            if status == 'complete' or reception_validated:
                state = 'done'
            elif proforma and ships:
                state = 'in_progress'
            else:
                state = 'no_started'

            # LIGA TERMINADA ⇒ AVANCE 100. La fase de captura acabó por
            # definición (recepción en tránsito validada o PI completa);
            # promediar snapshots viejos del portal — típico en ligas
            # multi-proforma, donde las PI hermanas no re-almacenan su % —
            # dejaba ligas terminadas marcando avance incompleto.
            if state == 'done':
                progress = 100

            counts['total'] += 1
            counts[state] = counts.get(state, 0) + 1

            rows.append({
                'id': a.id,
                'token': a.access_token,
                'portal_url': a.portal_url,
                'partner': partner.display_name if partner else '—',
                'po_id': po.id if po else False,
                'po_name': ' · '.join(pos.mapped('name')) if is_cargo else (po.name if po else ''),
                'proforma_id': proforma.id if proforma else False,
                'proforma_number': ' · '.join(
                    (h.proforma_number or h.purchase_id.partner_ref or h.purchase_id.name or '')
                    for h in headers
                ) if is_cargo else ((proforma.proforma_number if proforma else '') or ''),
                'is_cargo': is_cargo,
                'cargo_name': a.cargo_invoice_id.name if getattr(a, 'cargo_invoice_id', False) else '',
                'po_count': len(pos),
                'status': status,
                'state': state,
                'progress': progress,
                'shipments_total': ship_total,
                'shipments_done': ship_done,
                'reception_validated': reception_validated,
                'created': _fmt(a.create_date),
                'days_open': (now - a.create_date).days if a.create_date else 0,
                'expiration': _fmt(a.expiration_date),
                'days_to_expire': (a.expiration_date - now).days if a.expiration_date else 0,
                'is_expired': a.is_expired,
                'last_access': _fmt(a.last_access, with_time=True),
                'last_access_days': (now - a.last_access).days if a.last_access else None,
            })

        # Orden: lo accionable arriba (no atendidas, en captura), luego por días abiertos.
        order = {'no_started': 0, 'in_progress': 1, 'done': 2}
        rows.sort(key=lambda r: (order.get(r['state'], 9), -r['days_open']))
        return {'rows': rows, 'counts': counts}
