# -*- coding: utf-8 -*-
"""Correos de alerta de ETA del embarque (próximo a llegar / vencido).

Antes el aviso era una línea en el chatter ("el embarque X tenía ETA Y y
aún no ha llegado"). Ahora es un correo (SOM) estructurado con lo que se
necesita para decidir: orden de compra y proveedor, naviera / buque / BL,
contenedores, ruta y fechas, materiales con cantidades por unidad, y las
ventas que dependen del embarque.
"""
import logging

from markupsafe import Markup

from odoo import models
from odoo import fields as fields_module
from odoo.tools import html_escape

from odoo.addons.stock_transit_allocation.models.som_date_format import som_format_date

_logger = logging.getLogger(__name__)


def _esc(value):
    return html_escape(value if value is not None else '')


def _fmt_qty(qty, decimals=2):
    try:
        return ('{:,.%df}' % decimals).format(float(qty or 0.0))
    except (TypeError, ValueError):
        return '0.00'


class StockTransitVoyageEtaMail(models.Model):
    _inherit = 'stock.transit.voyage'

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------
    def _tc_eta_mail_containers(self):
        """Lista de contenedores del viaje (líneas + embarque del portal)."""
        self.ensure_one()
        containers = []
        if self.container_number:
            containers = [c.strip() for c in
                          self.container_number.replace(';', ',').split(',')
                          if c.strip() and c.strip().upper() != 'PENDIENTE']
        Shipment = self.env['supplier.shipment'].sudo()
        for sh in Shipment.search([('voyage_id', '=', self.id)]):
            for c in sh.container_ids:
                num = (c.container_number or '').strip()
                if num and num not in containers:
                    containers.append(num)
        return containers

    def _tc_eta_mail_materials(self):
        """Materiales del viaje agrupados por producto: cantidad por unidad,
        lotes, contenedores y cuánto está comprometido a ventas."""
        self.ensure_one()
        by_product = {}
        for line in self.line_ids:
            product = line.product_id
            if not product:
                continue
            data = by_product.setdefault(product.id, {
                'name': product.display_name or '',
                'uom': (product.uom_id.name or '') if product.uom_id else '',
                'qty': 0.0,
                'reserved_qty': 0.0,
                'lots': set(),
                'containers': set(),
                'orders': {},
            })
            qty = line.product_uom_qty or 0.0
            data['qty'] += qty
            if line.lot_id:
                data['lots'].add(line.lot_id.id)
            cont = (line.container_number or '').strip()
            if cont and cont.upper() != 'PENDIENTE':
                data['containers'].add(cont)
            if line.order_id and line.allocation_status == 'reserved':
                data['reserved_qty'] += qty
                o = data['orders'].setdefault(line.order_id.id, {
                    'name': line.order_id.name or '',
                    'partner': line.order_id.partner_id.name or '',
                    'seller': line.order_id.user_id.name or '',
                    'commitment': line.order_id.commitment_date,
                    'qty': 0.0,
                })
                o['qty'] += qty
        result = sorted(by_product.values(), key=lambda d: -d['qty'])
        return result

    def _tc_eta_mail_orders(self, materials):
        """Ventas que dependen del embarque (consolidado de todos los productos)."""
        orders = {}
        for m in materials:
            for oid, o in m['orders'].items():
                agg = orders.setdefault(oid, dict(o, qty=0.0, products=0))
                agg['qty'] += o['qty']
                agg['products'] += 1
        return sorted(orders.values(), key=lambda o: -o['qty'])

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------
    def _tc_eta_mail_wrap(self, kicker, title, subtitle, inner_html):
        """Esqueleto (SOM) del manual de identidad, mismo que ventas/compras."""
        self.ensure_one()
        company = self.company_id or self.env.company
        contact_bits = [company.name or 'SOM Group']
        if company.phone:
            contact_bits.append(company.phone)
        if company.email:
            contact_bits.append(company.email)
        return (
            '<div style="margin:0;padding:16px 8px;background-color:#E2DED5;'
            "font-family:'Anderson Grotesk','Helvetica Neue',Helvetica,Arial,"
            'sans-serif;">'
            '<table role="presentation" width="100%%" cellpadding="0" '
            'cellspacing="0" style="max-width:640px;margin:0 auto;'
            'background:#ffffff;">'
            '<tr><td style="height:4px;background:#2C221B;font-size:0;'
            'line-height:0;">&#160;</td></tr>'
            '<tr><td style="padding:22px 28px 0;">'
            '<table role="presentation" width="100%%" cellpadding="0" '
            'cellspacing="0"><tr>'
            '<td style="vertical-align:middle;">'
            '<img src="%(base)s/theme_list_modern/static/img/logosom.png" '
            'alt="(SOM)" style="height:28px;width:auto;display:block;"/></td>'
            '<td style="vertical-align:middle;text-align:right;font-size:9px;'
            'letter-spacing:.24em;text-transform:uppercase;color:#8A8072;'
            'white-space:nowrap;">%(kicker)s</td>'
            '</tr></table>'
            '<div style="margin-top:16px;border-top:1px solid #2C221B;"></div>'
            '</td></tr>'
            '<tr><td style="padding:18px 28px 0;">'
            '<div style="font-size:24px;font-weight:300;letter-spacing:.01em;'
            'color:#2C221B;line-height:1.25;">%(title)s</div>'
            '<div style="font-size:10px;letter-spacing:.16em;'
            'text-transform:uppercase;color:#8A8072;margin-top:8px;'
            'line-height:1.7;">%(subtitle)s</div>'
            '</td></tr>'
            '<tr><td style="padding:16px 28px 24px;font-size:14px;'
            'color:#3D352C;line-height:1.7;">%(inner)s</td></tr>'
            '<tr><td style="padding:20px 28px;background:#2C221B;'
            'text-align:center;">'
            '<div style="font-size:13px;letter-spacing:.3em;color:#E2DED5;">'
            '(SOM)<span style="font-size:8px;vertical-align:super;">&#174;'
            '</span></div>'
            '<div style="font-size:8px;letter-spacing:.26em;'
            'text-transform:uppercase;color:#A79C8C;font-style:italic;'
            'margin-top:5px;">Recubrimientos &#218;nicos</div>'
            '<div style="font-size:9px;letter-spacing:.14em;'
            'text-transform:uppercase;color:#A79C8C;margin-top:12px;'
            'line-height:1.9;">%(contact)s</div>'
            '</td></tr>'
            '</table></div>'
        ) % {
            'base': self.get_base_url(),
            'kicker': kicker,
            'title': title,
            'subtitle': subtitle,
            'inner': inner_html,
            'contact': '<br/>'.join(_esc(b) for b in contact_bits),
        }

    @staticmethod
    def _tc_eta_mail_section(title):
        return (
            '<div style="font-size:10px;letter-spacing:.18em;text-transform:'
            'uppercase;color:#8A8072;margin:18px 0 6px;border-bottom:1px solid '
            '#E2DED5;padding-bottom:4px;">%s</div>' % _esc(title)
        )

    @staticmethod
    def _tc_eta_mail_rows(rows):
        """Filas etiqueta / valor. Se omiten las vacías."""
        out = []
        for label, value in rows:
            if value in (None, '', False):
                continue
            out.append(
                '<tr>'
                '<td style="padding:5px 0;font-size:11px;letter-spacing:.08em;'
                'text-transform:uppercase;color:#8A8072;width:42%%;'
                'vertical-align:top;">%s</td>'
                '<td style="padding:5px 0;font-size:14px;color:#2C221B;'
                'vertical-align:top;">%s</td>'
                '</tr>' % (_esc(label), value)
            )
        if not out:
            return ''
        return ('<table role="presentation" width="100%%" cellpadding="0" '
                'cellspacing="0">%s</table>' % ''.join(out))

    def _tc_eta_mail_inner(self, kind, today=None):
        """Cuerpo del aviso. ``kind``: 'overdue' | 'warning'."""
        self.ensure_one()
        today = today or fields_module.Date.today()
        status_label = dict(
            self._fields['custom_status']._description_selection(self.env)
        ).get(self.custom_status, self.custom_status or '')
        po = self.purchase_id
        supplier = (
            self.tc_supplier_id.display_name if self.tc_supplier_id
            else (po.partner_id.display_name if po and po.partner_id else '')
        )
        containers = self._tc_eta_mail_containers()
        materials = self._tc_eta_mail_materials()
        orders = self._tc_eta_mail_orders(materials)
        days_overdue = (today - self.eta).days if self.eta else 0
        total_qty = sum(m['qty'] for m in materials)
        reserved_qty = sum(m['reserved_qty'] for m in materials)
        lots_count = sum(len(m['lots']) for m in materials)

        parts = []

        # ---- Resumen ejecutivo ------------------------------------------
        if kind == 'overdue':
            lead = (
                'El embarque <b>%s</b> tenía ETA <b>%s</b> y lleva '
                '<b>%d día%s</b> sin llegar. Estado actual: <b>%s</b>.'
            ) % (_esc(self.name), _esc(som_format_date(self.eta)),
                 days_overdue, '' if days_overdue == 1 else 's',
                 _esc(status_label))
        else:
            lead = (
                'El embarque <b>%s</b> tiene ETA <b>mañana (%s)</b>. '
                'Estado actual: <b>%s</b>.'
            ) % (_esc(self.name), _esc(som_format_date(self.eta)),
                 _esc(status_label))
        parts.append('<p style="margin:0 0 8px;">%s</p>' % lead)

        # Banda tipo recibo con las cifras clave
        parts.append(
            '<table role="presentation" width="100%%" cellpadding="0" '
            'cellspacing="0" style="background:#F4F1EC;margin:8px 0 4px;">'
            '<tr>%s</tr></table>' % ''.join(
                '<td style="padding:10px 8px;text-align:center;">'
                '<div style="font-size:18px;font-weight:300;color:#2C221B;">%s</div>'
                '<div style="font-size:9px;letter-spacing:.16em;text-transform:'
                'uppercase;color:#8A8072;margin-top:2px;">%s</div></td>' % (v, k)
                for k, v in [
                    ('Contenedores', str(len(containers)) if containers else '—'),
                    ('Materiales', str(len(materials))),
                    ('Lotes', str(lots_count) if lots_count else '—'),
                    ('Cantidad', _fmt_qty(total_qty)),
                    ('Comprometido', '%d%%' % round(reserved_qty / total_qty * 100)
                     if total_qty else '—'),
                ]
            )
        )

        # ---- Compra y proveedor -----------------------------------------
        parts.append(self._tc_eta_mail_section('Compra y proveedor'))
        parts.append(self._tc_eta_mail_rows([
            ('Orden de compra', _esc(po.name) if po else ''),
            ('Referencia proveedor', _esc(po.partner_ref) if po and po.partner_ref else ''),
            ('Proveedor', _esc(supplier)),
            ('Comprador', _esc(po.user_id.name) if po and po.user_id else ''),
            ('Fecha de compra', _esc(som_format_date(po.date_order, empty=''))
             if po and po.date_order else ''),
        ]))

        # ---- Logística --------------------------------------------------
        route = ' → '.join(x for x in [
            self.port_origin or (self.pol_id.name if self.pol_id else ''),
            self.port_destination or (self.pod_id.name if self.pod_id else ''),
        ] if x)
        vessel = ' / '.join(x for x in [self.vessel_name or '', self.voyage_number or ''] if x)
        parts.append(self._tc_eta_mail_section('Logística'))
        parts.append(self._tc_eta_mail_rows([
            ('Ruta', _esc(route)),
            ('Puerto destino', _esc(self.port_destination or (self.pod_id.name if self.pod_id else ''))),
            ('Naviera', _esc(self.shipping_line or (self.naviera_id.name if self.naviera_id else ''))),
            ('Forwarder', _esc(self.forwarder_id.name) if self.forwarder_id else ''),
            ('Buque / viaje', _esc(vessel)),
            ('B/L', _esc(self.bl_number or '')),
            ('ETD', _esc(som_format_date(self.etd, empty='')) if self.etd else ''),
            ('ETA', '<b>%s</b>' % _esc(som_format_date(self.eta))),
            ('Retraso', '<b style="color:#B3261E;">%d día%s</b>' % (
                days_overdue, '' if days_overdue == 1 else 's')
             if kind == 'overdue' and days_overdue > 0 else ''),
            ('Estado', _esc(status_label)),
            ('Contenedores', '<span style="font-family:monospace;font-size:13px;">%s</span>'
             % _esc(', '.join(containers)) if containers else ''),
        ]))

        # ---- Materiales -------------------------------------------------
        parts.append(self._tc_eta_mail_section('Materiales en el embarque'))
        if materials:
            head = (
                '<tr>'
                '<th style="text-align:left;padding:6px 4px;font-size:10px;'
                'letter-spacing:.12em;text-transform:uppercase;color:#8A8072;'
                'border-bottom:1px solid #2C221B;">Material</th>'
                '<th style="text-align:right;padding:6px 4px;font-size:10px;'
                'letter-spacing:.12em;text-transform:uppercase;color:#8A8072;'
                'border-bottom:1px solid #2C221B;white-space:nowrap;">Cantidad</th>'
                '<th style="text-align:right;padding:6px 4px;font-size:10px;'
                'letter-spacing:.12em;text-transform:uppercase;color:#8A8072;'
                'border-bottom:1px solid #2C221B;">Lotes</th>'
                '<th style="text-align:right;padding:6px 4px;font-size:10px;'
                'letter-spacing:.12em;text-transform:uppercase;color:#8A8072;'
                'border-bottom:1px solid #2C221B;">Vendido</th>'
                '</tr>'
            )
            body = []
            for m in materials:
                conts = ', '.join(sorted(m['containers']))
                sub = ('<div style="font-size:11px;color:#8A8072;">%s</div>'
                       % _esc(conts)) if conts else ''
                body.append(
                    '<tr>'
                    '<td style="padding:7px 4px;border-bottom:1px solid #E2DED5;'
                    'font-size:13px;color:#2C221B;">%s%s</td>'
                    '<td style="padding:7px 4px;border-bottom:1px solid #E2DED5;'
                    'text-align:right;white-space:nowrap;font-size:13px;">%s %s</td>'
                    '<td style="padding:7px 4px;border-bottom:1px solid #E2DED5;'
                    'text-align:right;font-size:13px;">%s</td>'
                    '<td style="padding:7px 4px;border-bottom:1px solid #E2DED5;'
                    'text-align:right;white-space:nowrap;font-size:13px;">%s</td>'
                    '</tr>' % (
                        _esc(m['name']), sub,
                        _fmt_qty(m['qty']), _esc(m['uom']),
                        len(m['lots']) or '—',
                        ('%s %s' % (_fmt_qty(m['reserved_qty']), _esc(m['uom'])))
                        if m['reserved_qty'] else '—',
                    )
                )
            parts.append(
                '<table role="presentation" width="100%%" cellpadding="0" '
                'cellspacing="0">%s%s</table>' % (head, ''.join(body))
            )
        else:
            parts.append('<p style="margin:0;color:#8A8072;">Sin líneas de '
                         'material capturadas en el viaje.</p>')

        # ---- Ventas que dependen del embarque ---------------------------
        if orders:
            parts.append(self._tc_eta_mail_section(
                'Ventas que esperan este material (%d)' % len(orders)))
            rows = []
            for o in orders[:15]:
                commit = som_format_date(o['commitment'], empty='') if o['commitment'] else ''
                late = ''
                if o['commitment'] and o['commitment'] < today:
                    late = ' <span style="color:#B3261E;">(compromiso vencido)</span>'
                rows.append(
                    '<tr>'
                    '<td style="padding:6px 4px;border-bottom:1px solid #E2DED5;'
                    'font-size:13px;color:#2C221B;"><b>%s</b> · %s'
                    '<div style="font-size:11px;color:#8A8072;">%s%s</div></td>'
                    '<td style="padding:6px 4px;border-bottom:1px solid #E2DED5;'
                    'text-align:right;white-space:nowrap;font-size:13px;">%s</td>'
                    '</tr>' % (
                        _esc(o['name']), _esc(o['partner']),
                        _esc(' · '.join(x for x in [
                            o['seller'] and 'Vendedor: %s' % o['seller'],
                            commit and 'Compromiso: %s' % commit,
                        ] if x)),
                        late,
                        _fmt_qty(o['qty']),
                    )
                )
            if len(orders) > 15:
                rows.append('<tr><td colspan="2" style="padding:6px 4px;font-size:'
                            '11px;color:#8A8072;">… y %d venta(s) más</td></tr>'
                            % (len(orders) - 15))
            parts.append(
                '<table role="presentation" width="100%%" cellpadding="0" '
                'cellspacing="0">%s</table>' % ''.join(rows)
            )
        else:
            parts.append(self._tc_eta_mail_section('Ventas que esperan este material'))
            parts.append('<p style="margin:0;color:#8A8072;">Ninguna: el material '
                         'viene para stock.</p>')

        # ---- Botón ------------------------------------------------------
        url = '%s/odoo/action-stock_transit_allocation.action_stock_transit_voyage_main/%s' % (
            self.get_base_url(), self.id)
        parts.append(
            '<div style="text-align:center;margin-top:22px;">'
            '<a href="%s" style="display:inline-block;padding:12px 26px;'
            'background:#2C221B;color:#E2DED5;text-decoration:none;font-size:11px;'
            'letter-spacing:.2em;text-transform:uppercase;">Abrir embarque</a>'
            '</div>' % _esc(url)
        )
        return ''.join(parts)

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------
    def _tc_eta_mail_send(self, kind, responsible):
        """Publica el aviso en el chatter (con el detalle) y manda el correo
        (SOM) al responsable. Devuelve True si el correo salió."""
        self.ensure_one()
        today = fields_module.Date.today()
        inner = self._tc_eta_mail_inner(kind, today=today)
        days_overdue = (today - self.eta).days if self.eta else 0
        if kind == 'overdue':
            kicker = 'Torre de control · Embarque vencido'
            title = 'Embarque vencido: %s' % _esc(self.name)
            subtitle = 'ETA %s · %d día%s de retraso' % (
                _esc(som_format_date(self.eta)), days_overdue,
                '' if days_overdue == 1 else 's')
            subject = '🚨 Embarque vencido %s · ETA %s · %d día%s sin llegar' % (
                self.name, som_format_date(self.eta), days_overdue,
                '' if days_overdue == 1 else 's')
            chat_head = '🚨 <b>Embarque vencido</b>'
        else:
            kicker = 'Torre de control · Llegada próxima'
            title = 'Llega mañana: %s' % _esc(self.name)
            subtitle = 'ETA %s' % _esc(som_format_date(self.eta))
            subject = '⚠️ Embarque %s llega mañana (%s)' % (
                self.name, som_format_date(self.eta))
            chat_head = '⚠️ <b>Embarque próximo a llegar</b>'

        # Chatter: el mismo detalle, sin la envoltura de marca (sin
        # partner_ids para no duplicar el correo).
        self.message_post(
            body=Markup('%s<br/>%s') % (Markup(chat_head), Markup(inner)),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        email_to = responsible.partner_id.email or responsible.email
        if not email_to:
            _logger.warning('[TC_ETA] %s: el responsable %s no tiene correo; '
                            'solo quedó la nota en el chatter.',
                            self.name, responsible.name)
            return False
        html = self._tc_eta_mail_wrap(kicker, title, subtitle, inner)
        company = self.company_id or self.env.company
        try:
            self.env['mail.mail'].sudo().create({
                'subject': subject,
                'body_html': html,
                'email_to': email_to,
                'email_from': company.email or self.env.user.email_formatted,
                'model': 'stock.transit.voyage',
                'res_id': self.id,
                'auto_delete': False,
            }).send()
        except Exception:
            _logger.exception('[TC_ETA] %s: no se pudo enviar el correo de %s',
                              self.name, kind)
            return False
        return True
