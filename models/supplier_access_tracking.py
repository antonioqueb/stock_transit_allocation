# -*- coding: utf-8 -*-
from odoo import models, api, fields


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

        po_ids = [a.purchase_id.id for a in accesses if a.purchase_id]
        proformas = Proforma.search([('purchase_id', 'in', po_ids)]) if po_ids else Proforma
        prof_by_po = {p.purchase_id.id: p for p in proformas}

        # Recepciones validadas (done) por embarque, en un solo query.
        all_ship_ids = proformas.mapped('shipment_ids').ids
        done_ship_ids = set()
        if has_ship_field and all_ship_ids:
            done = Picking.search([
                ('supplier_shipment_id', 'in', all_ship_ids),
                ('state', '=', 'done'),
            ])
            done_ship_ids = set(done.mapped('supplier_shipment_id').ids)

        def _fmt(dt, with_time=False):
            if not dt:
                return ''
            local = fields.Datetime.context_timestamp(self, dt)
            return local.strftime('%d/%m/%Y %H:%M' if with_time else '%d/%m/%Y')

        rows = []
        counts = {'total': 0, 'active': 0, 'no_started': 0, 'in_progress': 0,
                  'captured': 0, 'done': 0, 'expired': 0}

        for a in accesses:
            po = a.purchase_id
            partner = po.partner_id if po else False
            proforma = prof_by_po.get(po.id) if po else False
            ships = proforma.shipment_ids if proforma else Shipment
            ship_total = len(ships)
            ship_done = sum(1 for s in ships if s.id in done_ship_ids)
            reception_validated = bool(ship_total and ship_done == ship_total)
            # Defensivo: el % vive en stock_lot_packing_import. Si ese módulo aún
            # no se recargó con _portal_progress, no truena (muestra 0 mientras).
            progress = 0
            if proforma and hasattr(proforma, '_portal_progress'):
                progress = proforma._portal_progress().get('percent', 0)
            status = proforma.status if proforma else 'draft'

            if reception_validated:
                state = 'done'
            elif a.is_expired:
                state = 'expired'
            elif status == 'complete':
                state = 'captured'
            elif proforma and ships:
                state = 'in_progress'
            else:
                state = 'no_started'

            counts['total'] += 1
            counts[state] = counts.get(state, 0) + 1
            if not a.is_expired and not reception_validated:
                counts['active'] += 1

            rows.append({
                'id': a.id,
                'token': a.access_token,
                'portal_url': a.portal_url,
                'partner': partner.display_name if partner else '—',
                'po_id': po.id if po else False,
                'po_name': po.name if po else '',
                'proforma_id': proforma.id if proforma else False,
                'proforma_number': (proforma.proforma_number if proforma else '') or '',
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

        # Orden: vencidas y en proceso primero (lo accionable arriba), luego por días abiertos.
        order = {'expired': 0, 'in_progress': 1, 'captured': 2, 'no_started': 3, 'done': 4}
        rows.sort(key=lambda r: (order.get(r['state'], 9), -r['days_open']))
        return {'rows': rows, 'counts': counts}
