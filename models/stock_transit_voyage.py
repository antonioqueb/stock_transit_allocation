# -*- coding: utf-8 -*-
import json
import logging
import re

import requests

from markupsafe import Markup
from odoo.addons.stock_transit_allocation.models.som_date_format import som_format_date

from odoo import models, api, _
from odoo import fields as fields_module
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_compare, float_is_zero
from odoo.tools import html_escape

fields = fields_module

_logger = logging.getLogger(__name__)

try:
    import folium
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False
    _logger.warning("Folium no está instalado. pip install folium --break-system-packages")


ETA_DRAMATIC_CHANGE_DAYS = 5
ETA_WARNING_DAYS_BEFORE = 1
ETA_OVERDUE_DAYS_AFTER = 1


class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields_module.Char(
        string='Referencia Viaje',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )

    custom_status = fields_module.Selection(
        [
            ('solicitud', 'Solicitud'),
            ('production', 'Producción'),
            ('booking', 'Booking'),
            ('puerto_origen', 'Origen'),
            ('on_sea', 'Altamar'),
            ('puerto_destino', 'Destino'),
            ('arrived_port', 'Arribo'),
            ('reception_pending', 'Recepción'),
            ('delivered', 'Entregado'),
            ('cancel', 'Cancelado'),
        ],
        string='Estado',
        default='solicitud',
        tracking=True,
    )

    # ── SUB-ESTADO DE ETIQUETADO (vive DENTRO de Entregado) ──
    # 'delivered' sigue siendo el estado logístico terminal: hay 73
    # referencias en el sistema que lo tratan así (syncs, hubs, analytics).
    # El avance del etiquetado se registra aparte y solo matiza cómo se ve
    # un viaje entregado: Entregado (naranja) → En Impresión (amarillo,
    # AUTOMÁTICO al generarse la primera impresión de etiquetas) →
    # Etiquetado (verde, check MANUAL desde el embarque/viaje).
    tc_labeling_status = fields_module.Selection([
        ('none', 'Entregado'),
        ('printing', 'En Impresión'),
        ('labeled', 'Etiquetado'),
    ], string='Etiquetado', default='none', tracking=True, copy=False)
    tc_label_print_count = fields_module.Integer(
        string='Impresiones de etiquetas', copy=False)
    tc_label_first_print_at = fields_module.Datetime(
        string='Primera impresión', copy=False)
    tc_label_last_print_at = fields_module.Datetime(
        string='Última impresión', copy=False)
    tc_labeled_at = fields_module.Datetime(string='Etiquetado el', copy=False)
    tc_labeled_by = fields_module.Many2one(
        'res.users', string='Etiquetado por', copy=False)

    shipping_line = fields_module.Char(
        string='Naviera',
        tracking=True,
    )
    transit_days_expected = fields_module.Integer(
        string='Tiempo Tránsito (Días)',
    )
    vessel_name = fields_module.Char(
        string='Buque / Barco',
        tracking=True,
    )
    voyage_number = fields_module.Char(
        string='No. Viaje',
        tracking=True,
    )

    container_number = fields_module.Char(
        string='Contenedores',
        compute='_compute_container_number',
        store=True,
        tracking=True,
        help="Resumen automático de contenedores presentes en las líneas del viaje",
    )

    bl_number = fields_module.Char(
        string='Folio Compra / BL',
        tracking=True,
    )

    tc_reception_pending_at = fields_module.Datetime(
        string='En recepción desde',
        copy=False,
        readonly=True,
        help='Momento en que el viaje quedó LISTO PARA RECIBIR (Entrega en '
             'Sitio). Arranca el contador de días sin recibir; termina al '
             'validar la recepción física.',
    )

    etd = fields_module.Date(
        string='ETD (Salida Estimada)',
    )
    eta = fields_module.Date(
        string='ETA (Llegada Estimada)',
        required=False,
        tracking=True,
    )
    eta_original = fields_module.Date(
        string='ETA Original',
        readonly=True,
        copy=False,
        tracking=True,
    )

    delay_days = fields_module.Integer(
        string='Días de Retraso',
        compute='_compute_delay_days',
        store=True,
    )

    eta_alert_level = fields_module.Selection(
        [
            ('ok', 'En Tiempo'),
            ('warning', 'Próximo a Vencer'),
            ('danger', 'Vencido'),
            ('done', 'Entregado'),
        ],
        string='Alerta ETA',
        compute='_compute_eta_alert',
        store=True,
    )

    eta_warning_notified = fields_module.Boolean(
        string='Notificación "Próximo a Vencer" enviada',
        default=False,
        copy=False,
    )
    eta_overdue_notified = fields_module.Boolean(
        string='Notificación "Vencido" enviada',
        default=False,
        copy=False,
    )

    arrival_date = fields_module.Date(
        string='Llegada Real',
        tracking=True,
    )
    arrival_date_bodega = fields_module.Date(
        string='Entregado en Bodega',
        tracking=True,
    )

    picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción (Tránsito)',
        domain=[('picking_type_code', '=', 'incoming')],
    )

    # EMBARQUE MULTI-PROFORMA (factura de carga con liga única de portal):
    # cada proforma/OC amparada genera SU propia recepción a tránsito, pero
    # todas alimentan ESTE MISMO embarque — el folio del viaje cuenta
    # embarques físicos, no recepciones administrativas. picking_id se
    # conserva como recepción primaria (compatibilidad); la composición
    # completa vive aquí.
    picking_ids = fields_module.Many2many(
        'stock.picking',
        'stock_transit_voyage_picking_rel', 'voyage_id', 'picking_id',
        string='Recepciones que componen el embarque',
        copy=False,
    )

    tc_composition_summary = fields_module.Char(
        string='Composición del embarque',
        compute='_compute_tc_composition',
        compute_sudo=True,
    )
    tc_pending_reception_count = fields_module.Integer(
        string='Recepciones pendientes',
        compute='_compute_tc_composition',
        compute_sudo=True,
    )

    reception_picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')],
        readonly=True,
    )

    purchase_id = fields_module.Many2one(
        'purchase.order',
        string='Orden de Compra Origen',
        readonly=True,
    )
    tc_supplier_id = fields_module.Many2one(
        'res.partner',
        string='Proveedor',
        related='purchase_id.partner_id',
        store=True,
        readonly=True,
    )

    company_id = fields_module.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
    )

    line_ids = fields_module.One2many(
        'stock.transit.line',
        'voyage_id',
        string='Contenido (Lotes)',
    )

    total_m2 = fields_module.Float(
        string='Total m²',
        compute='_compute_totals',
        store=True,
        compute_sudo=True,
    )

    allocated_m2 = fields_module.Float(
        string='Asignado m²',
        compute='_compute_totals',
        store=True,
        compute_sudo=True,
    )

    allocation_percent = fields_module.Float(
        string='% Asignación',
        compute='_compute_allocation_percent',
        store=False,
        compute_sudo=False,
    )

    pending_order_count = fields_module.Integer(
        string='Clientes sin pedido',
        compute='_compute_pending_order',
        compute_sudo=True,
        help='Líneas con cliente asignado pero sin orden de venta: falta '
             'asignarles su pedido.',
    )

    pending_order_summary = fields_module.Char(
        string='Detalle clientes sin pedido',
        compute='_compute_pending_order',
        compute_sudo=True,
    )

    shipsgo_last_sync = fields_module.Datetime(
        string="Última Sincronización API",
        readonly=True,
    )

    shipsgo_payload = fields_module.Text(
        string="Datos Geoespaciales (JSON)",
        readonly=True,
    )

    shipsgo_map_html = fields_module.Html(
        string="Mapa de Seguimiento",
        sanitize=False,
        readonly=True,
    )

    transit_progress = fields_module.Integer(
        string='Progreso Viaje',
        compute='_compute_transit_progress',
        store=True,
        readonly=False,
    )

    # =========================================================================
    # SHIPSGO
    # =========================================================================

    def _shipsgo_get_config(self):
        Config = self.env['ir.config_parameter'].sudo()
        api_url = Config.get_param('stock_transit.shipsgo_api_url', 'https://api.shipsgo.com/v2')
        api_token = Config.get_param('stock_transit.shipsgo_api_token', '')

        if not api_token:
            raise UserError(_("No se ha configurado el Token de ShipsGo en Parámetros del Sistema."))

        return api_url, api_token

    def _shipsgo_headers(self, json_body=False):
        api_url, api_token = self._shipsgo_get_config()

        headers = {
            "Accept": "application/json",
            "User-Agent": "OdooControlTower/1.0",
            "X-Shipsgo-User-Token": api_token,
        }

        if json_body:
            headers["Content-Type"] = "application/json"

        return api_url, headers

    def _normalize_container_number(self, value):
        return (value or '').strip().upper()

    def _validate_container_number(self, value):
        if not re.fullmatch(r'^[A-Z]{4}[0-9]{7}$', value or ''):
            raise UserError(
                _("El contenedor '%s' no cumple el formato esperado AAAA9999999.") % (value or '')
            )

    def _extract_shipment_from_response(self, payload):
        if not isinstance(payload, dict):
            return {}

        if isinstance(payload.get('shipment'), dict):
            return payload['shipment']

        if isinstance(payload.get('data'), dict):
            return payload['data']

        return payload

    def _make_shipsgo_reference(self, container_ref, shipment_container=False):
        self.ensure_one()

        parts = []

        if shipment_container and shipment_container.shipment_id:
            parts.append(shipment_container.shipment_id.name or '')

        if self.name:
            parts.append(self.name)

        if self.purchase_id:
            parts.append(self.purchase_id.name or '')

        parts.append(container_ref)

        reference = " | ".join([p for p in parts if p]).strip()

        if len(reference) < 5:
            reference = f"{self.name or 'VOYAGE'}-{container_ref}"

        return reference[:128]

    def _find_shipsgo_shipment_by_container(self, container_ref):
        self.ensure_one()

        api_url, headers = self._shipsgo_headers()

        try:
            r = requests.get(
                f"{api_url}/ocean/shipments",
                headers=headers,
                params={"filters[container_number]": f"eq:{container_ref}"},
                timeout=20,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise UserError(_("Error buscando shipment existente en ShipsGo: %s") % str(e))

        shipments = payload.get('shipments') or payload.get('data') or []
        return shipments[0] if shipments else False

    def _create_or_link_shipsgo_tracking_for_container(self, container_ref, shipment_container=False):
        self.ensure_one()

        container_ref = self._normalize_container_number(container_ref)
        if not container_ref:
            raise UserError(_("No se recibió un número de contenedor válido para crear tracking."))

        self._validate_container_number(container_ref)

        if shipment_container and shipment_container.shipsgo_shipment_id:
            return {
                'id': shipment_container.shipsgo_shipment_id,
                'reference': shipment_container.shipsgo_reference,
                'container_number': container_ref,
            }

        existing = self._find_shipsgo_shipment_by_container(container_ref)

        if existing:
            shipment_id = existing.get('id')
            reference = existing.get('reference') or self._make_shipsgo_reference(
                container_ref,
                shipment_container=shipment_container,
            )

            if shipment_container and shipment_id:
                shipment_container.with_context(skip_auto_shipsgo=True).write({
                    'shipsgo_shipment_id': shipment_id,
                    'shipsgo_reference': reference,
                    'shipsgo_last_create': fields_module.Datetime.now(),
                    'shipsgo_last_error': False,
                })

            self.message_post(body=Markup(
                "🔁 <b>ShipsGo ya existente</b><br/>"
                "Contenedor: {container}<br/>"
                "Shipment ID: {shipment_id}"
            ).format(
                container=container_ref,
                shipment_id=shipment_id or 'N/A',
            ))

            return existing

        api_url, headers = self._shipsgo_headers(json_body=True)
        reference = self._make_shipsgo_reference(
            container_ref,
            shipment_container=shipment_container,
        )

        payload = {
            "reference": reference,
            "container_number": container_ref,
        }

        carrier_candidate = False

        if shipment_container and shipment_container.shipment_id and shipment_container.shipment_id.shipping_line:
            carrier_candidate = shipment_container.shipment_id.shipping_line.strip().upper()
        elif self.shipping_line:
            carrier_candidate = self.shipping_line.strip().upper()

        if carrier_candidate and re.fullmatch(r'^(SG_)?[A-Z0-9]{4}$', carrier_candidate):
            payload["carrier"] = carrier_candidate

        try:
            r = requests.post(
                f"{api_url}/ocean/shipments",
                headers=headers,
                json=payload,
                timeout=20,
            )

            try:
                response_payload = r.json() if r.content else {}
            except Exception:
                response_payload = {}

        except Exception as e:
            raise UserError(_("Error creando shipment en ShipsGo: %s") % str(e))

        if r.status_code in (200, 201):
            shipment = self._extract_shipment_from_response(response_payload)
        elif r.status_code == 409:
            shipment = self._extract_shipment_from_response(response_payload)
            if not shipment:
                shipment = self._find_shipsgo_shipment_by_container(container_ref) or {}
        elif r.status_code == 402:
            raise UserError(_("ShipsGo reportó que no hay créditos suficientes para crear el tracking."))
        elif r.status_code == 429:
            raise UserError(_("ShipsGo rechazó la creación por demasiadas solicitudes concurrentes. Intente de nuevo."))
        else:
            message = response_payload.get('message') if isinstance(response_payload, dict) else False
            raise UserError(
                _("ShipsGo devolvió un error al crear el tracking (%s): %s")
                % (r.status_code, message or r.text)
            )

        shipment_id = shipment.get('id')
        resolved_reference = shipment.get('reference') or reference

        if shipment_container:
            shipment_container.with_context(skip_auto_shipsgo=True).write({
                'shipsgo_shipment_id': shipment_id or 0,
                'shipsgo_reference': resolved_reference,
                'shipsgo_last_create': fields_module.Datetime.now(),
                'shipsgo_last_error': False,
            })

        self.message_post(body=Markup(
            "🆕 <b>Tracking ShipsGo creado</b><br/>"
            "Contenedor: {container}<br/>"
            "Shipment ID: {shipment_id}<br/>"
            "Reference: {reference}"
        ).format(
            container=container_ref,
            shipment_id=shipment_id or 'N/A',
            reference=resolved_reference,
        ))

        return shipment

    def _clean_coord(self, lat, lng):
        try:
            if lat is None or lng is None:
                return None

            f_lat = float(lat)
            f_lng = float(lng)

            if f_lat == 0.0 and f_lng == 0.0:
                return None

            return [f_lat, f_lng]

        except (ValueError, TypeError):
            return None

    def _generate_folium_map(self, map_data):
        if not HAS_FOLIUM:
            return self._generate_fallback_map_html(map_data)

        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')

        all_points = []

        if origin_loc and len(origin_loc) == 2:
            all_points.append(origin_loc)
        if dest_loc and len(dest_loc) == 2:
            all_points.append(dest_loc)
        if current_loc and len(current_loc) == 2:
            all_points.append(current_loc)

        if current_loc and len(current_loc) == 2:
            center = current_loc
            zoom = 6
        elif all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lng = sum(p[1] for p in all_points) / len(all_points)
            center = [avg_lat, avg_lng]
            zoom = 4
        else:
            center = [20, -40]
            zoom = 2

        m = folium.Map(
            location=center,
            zoom_start=zoom,
            tiles='cartodbpositron',
            width='100%',
            height='600px',
            scrollWheelZoom=False,
        )

        if origin_loc and len(origin_loc) == 2:
            origin_name = map_data.get('origin', {}).get('name', 'Puerto Origen')
            origin_country = map_data.get('origin', {}).get('country', '')
            origin_date = map_data.get('origin', {}).get('date', '')

            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b>⚓ Origen</b><br/>"
                f"<b>{origin_name}</b>"
                f"{'<br/>' + origin_country if origin_country else ''}"
                f"{'<br/>Salida: ' + origin_date if origin_date else ''}"
                f"</div>"
            )

            folium.Marker(
                location=origin_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"Origen: {origin_name}",
                icon=folium.Icon(color='green', icon='anchor', prefix='fa'),
            ).add_to(m)

        if dest_loc and len(dest_loc) == 2:
            dest_name = map_data.get('destination', {}).get('name', 'Puerto Destino')
            dest_country = map_data.get('destination', {}).get('country', '')
            dest_date = map_data.get('destination', {}).get('date', '')

            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b>🏁 Destino</b><br/>"
                f"<b>{dest_name}</b>"
                f"{'<br/>' + dest_country if dest_country else ''}"
                f"{'<br/>Llegada est.: ' + dest_date if dest_date else ''}"
                f"</div>"
            )

            folium.Marker(
                location=dest_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"Destino: {dest_name}",
                icon=folium.Icon(color='red', icon='flag', prefix='fa'),
            ).add_to(m)

        if current_loc and len(current_loc) == 2:
            container = map_data.get('container', 'N/A')
            vessel = map_data.get('vessel', 'N/A')
            status = map_data.get('status', 'En tránsito')
            pct = map_data.get('transit_pct', 0)

            popup_html = (
                f"<div style='min-width:180px;text-align:center'>"
                f"<b>🚢 {container}</b><br/>"
                f"<span style='background:#2563eb;color:#fff;padding:2px 8px;"
                f"border-radius:12px;font-size:11px'>{status}</span><br/>"
                f"<small>Buque: {vessel}</small><br/>"
                f"<small>Progreso: {pct}%</small>"
                f"</div>"
            )

            ship_icon = folium.DivIcon(
                html='<div style="font-size:28px;text-align:center;'
                     'filter:drop-shadow(0 2px 3px rgba(0,0,0,0.3))">🚢</div>',
                icon_size=(32, 32),
                icon_anchor=(16, 16),
            )

            folium.Marker(
                location=current_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{container} - {status}",
                icon=ship_icon,
            ).add_to(m)

        route = map_data.get('route', {})

        for line_coords in route.get('past', []):
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#6b7280',
                    weight=3,
                    opacity=0.7,
                ).add_to(m)

        current_past = route.get('current_past', [])
        if len(current_past) >= 2:
            folium.PolyLine(
                locations=current_past,
                color='#2563eb',
                weight=4,
                opacity=0.85,
            ).add_to(m)

        current_future = route.get('current_future', [])
        if len(current_future) >= 2:
            folium.PolyLine(
                locations=current_future,
                color='#2563eb',
                weight=3,
                opacity=0.5,
                dash_array='8 10',
            ).add_to(m)

        for line_coords in route.get('future', []):
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#9ca3af',
                    weight=3,
                    opacity=0.5,
                    dash_array='8 10',
                ).add_to(m)

        return m._repr_html_()

    def _generate_fallback_map_html(self, map_data):
        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')

        container = map_data.get('container', 'N/A')
        vessel = map_data.get('vessel', 'N/A')
        status = map_data.get('status', 'En tránsito')
        pct = map_data.get('transit_pct', 0)
        origin_name = map_data.get('origin', {}).get('name', 'Origen')
        dest_name = map_data.get('destination', {}).get('name', 'Destino')

        markers_js = ""
        bounds_js = "var bounds = [];\n"

        if origin_loc:
            markers_js += f"""
            L.marker([{origin_loc[0]}, {origin_loc[1]}], {{
                icon: L.divIcon({{html:'⚓', className:'', iconSize:[22,22], iconAnchor:[11,11]}})
            }}).addTo(map).bindPopup('<b>Origen:</b> {origin_name}');
            bounds.push([{origin_loc[0]}, {origin_loc[1]}]);
            """

        if dest_loc:
            markers_js += f"""
            L.marker([{dest_loc[0]}, {dest_loc[1]}], {{
                icon: L.divIcon({{html:'🏁', className:'', iconSize:[22,22], iconAnchor:[11,11]}})
            }}).addTo(map).bindPopup('<b>Destino:</b> {dest_name}');
            bounds.push([{dest_loc[0]}, {dest_loc[1]}]);
            """

        if current_loc:
            markers_js += f"""
            L.marker([{current_loc[0]}, {current_loc[1]}], {{
                icon: L.divIcon({{html:'🚢', className:'', iconSize:[28,28], iconAnchor:[14,14]}})
            }}).addTo(map).bindPopup('<b>{container}</b><br/>{status}<br/>Buque: {vessel}<br/>Progreso: {pct}%').openPopup();
            bounds.push([{current_loc[0]}, {current_loc[1]}]);
            """

        bounds_js += """
        if(bounds.length > 1) map.fitBounds(bounds, {padding:[50,50], maxZoom:8});
        else if(bounds.length === 1) map.setView(bounds[0], 5);
        """

        return f"""
        <div style="width:100%;height:1200px;position:relative;">
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <div id="fallback_map" style="width:100%;height:100%;"></div>
            <script>
                (function() {{
                    var map = L.map('fallback_map', {{scrollWheelZoom: false}}).setView([20, -40], 2);
                    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                        attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19
                    }}).addTo(map);
                    {markers_js}
                    {bounds_js}
                }})();
            </script>
        </div>
        """

    def action_sync_shipsgo(self):
        self.ensure_one()

        api_url, headers = self._shipsgo_headers()

        def safe_get(d, keys, default=None):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return default
            return d if d is not None else default

        Container = self.env['supplier.shipment.container']

        linked_container = Container.search([
            ('shipment_id.voyage_id', '=', self.id),
            ('container_number', '!=', False),
        ], order='shipsgo_shipment_id desc, id asc', limit=1)

        container_ref = False
        shipment_id = False
        shipment_data = {}

        if linked_container:
            container_ref = self._normalize_container_number(linked_container.container_number)

            if linked_container.shipsgo_shipment_id:
                shipment_id = linked_container.shipsgo_shipment_id
            else:
                shipment_data = self._create_or_link_shipsgo_tracking_for_container(
                    container_ref=container_ref,
                    shipment_container=linked_container,
                )
                shipment_id = shipment_data.get('id')
        else:
            for line in self.line_ids:
                candidate = self._normalize_container_number(line.container_number)
                if candidate and candidate not in ('PENDIENTE', 'SN', 'FALSE'):
                    container_ref = candidate
                    break

            if not container_ref and self.container_number and 'PENDIENTE' not in (self.container_number or ''):
                container_ref = self._normalize_container_number(
                    str(self.container_number).split(',')[0].strip()
                )

            if not container_ref:
                raise UserError(_("No se encontró un número de contenedor válido en las líneas o en el embarque vinculado."))

            shipment_data = self._find_shipsgo_shipment_by_container(container_ref) or {}
            shipment_id = shipment_data.get('id')

            if not shipment_id:
                shipment_data = self._create_or_link_shipsgo_tracking_for_container(container_ref)
                shipment_id = shipment_data.get('id')

        if shipment_id:
            try:
                sr = requests.get(
                    f"{api_url}/ocean/shipments/{shipment_id}",
                    headers=headers,
                    timeout=20,
                )
                sr.raise_for_status()

                try:
                    shipment_detail_payload = sr.json()
                except Exception:
                    shipment_detail_payload = {}

                shipment_data = self._extract_shipment_from_response(shipment_detail_payload)

            except Exception as e:
                _logger.warning("[ShipsGo] No se pudo obtener detalle de shipment %s: %s", shipment_id, e)

        if not shipment_data and container_ref:
            shipment_data = self._find_shipsgo_shipment_by_container(container_ref) or {}

        if not shipment_data:
            self.message_post(body=_("⚠️ ShipsGo no devolvió datos para %s.") % container_ref)
            self.write({'shipsgo_last_sync': fields_module.Datetime.now()})
            return

        shipment_id = shipment_data.get('id') or shipment_id

        geojson_data = {}
        current_location = None
        vessel_name = ''
        voyage_number = ''
        past_lines = []
        current_lines = []
        future_lines = []
        pol_coordinates = None
        pod_coordinates = None
        all_pol_candidates = []
        all_pod_candidates = []

        if shipment_id:
            try:
                gr = requests.get(
                    f"{api_url}/ocean/shipments/{shipment_id}/geojson",
                    headers=headers,
                    timeout=20,
                )
                gr.raise_for_status()

                try:
                    geojson_data = gr.json()
                except Exception:
                    geojson_data = {}

            except Exception as e:
                _logger.warning("[ShipsGo] No se pudo obtener GeoJSON para %s: %s", shipment_id, e)

        route_info = safe_get(shipment_data, ['route'], {})
        transit_pct = route_info.get('transit_percentage', 0) or 0
        status_text = shipment_data.get('status', 'N/A')
        checked_at = shipment_data.get('checked_at', '')
        carrier_name = safe_get(shipment_data, ['carrier', 'name'], '')

        pol_name = safe_get(route_info, ['port_of_loading', 'location', 'name'], '')
        pod_name = safe_get(route_info, ['port_of_discharge', 'location', 'name'], '')
        date_loading = safe_get(route_info, ['port_of_loading', 'date_of_loading'], '')
        date_discharge = safe_get(route_info, ['port_of_discharge', 'date_of_discharge'], '')
        pol_country = safe_get(route_info, ['port_of_loading', 'location', 'country', 'code'], '')
        pod_country = safe_get(route_info, ['port_of_discharge', 'location', 'country', 'code'], '')

        features = safe_get(geojson_data, ['geojson', 'features'], [])

        for feature in features:
            geom_type = feature.get('geometry', {}).get('type')
            props = feature.get('properties', {})
            status = props.get('status')
            coords_raw = feature.get('geometry', {}).get('coordinates', [])

            if current_location is None and props.get('current') is not None:
                cur = props['current']
                lon, lat = cur['coordinates'][0], cur['coordinates'][1]
                current_location = [lat, lon]
                vessel_name = safe_get(props, ['vessel', 'name'], '')
                voyage_number = props.get('voyage', '')

            if geom_type == 'Point':
                loc_name = safe_get(props, ['location', 'name'], '')
                lat_lon = (coords_raw[1], coords_raw[0])

                if status == 'PAST':
                    all_pol_candidates.append({'coords': lat_lon, 'name': loc_name})
                elif status == 'FUTURE':
                    all_pod_candidates.append({'coords': lat_lon, 'name': loc_name})

            elif geom_type == 'LineString':
                line_coords = [(c[1], c[0]) for c in coords_raw]

                if status == 'PAST':
                    past_lines.append(line_coords)
                elif status == 'CURRENT':
                    current_lines.append({'coords': line_coords, 'props': props})
                elif status == 'FUTURE':
                    future_lines.append(line_coords)

        if all_pol_candidates:
            pol_coordinates = list(all_pol_candidates[0]['coords'])
            if not pol_name:
                pol_name = all_pol_candidates[0]['name']

        if all_pod_candidates:
            pod_coordinates = list(all_pod_candidates[-1]['coords'])
            if not pod_name:
                pod_name = all_pod_candidates[-1]['name']

        current_past_coords = []
        current_future_coords = []

        for seg in current_lines:
            cur_prop = seg['props'].get('current')
            if cur_prop:
                idx = cur_prop.get('index', -1)
                all_c = seg['coords']
                if idx >= 0:
                    current_past_coords = all_c[:idx + 1]
                    current_future_coords = all_c[idx:]
                else:
                    current_future_coords = all_c
            else:
                current_future_coords = seg['coords']

        map_data = {
            'container': container_ref,
            'shipment_id': shipment_id,
            'current_loc': current_location,
            'vessel': vessel_name or shipment_data.get('vessel_name', ''),
            'voyage': voyage_number,
            'status': status_text,
            'transit_pct': int(transit_pct),
            'checked_at': checked_at,
            'carrier': carrier_name,
            'origin': {
                'name': pol_name,
                'loc': pol_coordinates,
                'country': pol_country,
                'date': date_loading,
            },
            'destination': {
                'name': pod_name,
                'loc': pod_coordinates,
                'country': pod_country,
                'date': date_discharge,
            },
            'route': {
                'past': past_lines,
                'current_past': current_past_coords,
                'current_future': current_future_coords,
                'future': future_lines,
            },
        }

        try:
            map_html = self._generate_folium_map(map_data)
        except Exception as e:
            _logger.error("[ShipsGo] Error generando mapa Folium: %s", e)
            map_html = False

        old_eta = self.eta
        new_eta_from_api = False

        if date_discharge:
            try:
                new_eta_from_api = fields_module.Date.from_string(date_discharge[:10])
            except Exception:
                new_eta_from_api = False

        eta_changed_dramatically = False
        days_diff = 0

        if old_eta and new_eta_from_api and old_eta != new_eta_from_api:
            days_diff = abs((new_eta_from_api - old_eta).days)
            if days_diff >= ETA_DRAMATIC_CHANGE_DAYS:
                eta_changed_dramatically = True

        vals = {
            'shipsgo_last_sync': fields_module.Datetime.now(),
            'shipsgo_payload': json.dumps(map_data),
            'shipsgo_map_html': map_html,
            'transit_progress': int(transit_pct),
        }

        if vessel_name:
            vals['vessel_name'] = vessel_name
        if carrier_name:
            vals['shipping_line'] = carrier_name
        if date_discharge:
            vals['eta'] = date_discharge

        no_more_coordinates = current_location is None
        is_completed = int(transit_pct) >= 100

        if (
            (is_completed or no_more_coordinates)
            and self.custom_status not in ('arrived_port', 'reception_pending', 'delivered', 'cancel')
        ):
            vals['custom_status'] = 'arrived_port'
            self.message_post(body=Markup(
                "🏁 <b>Cambio automático de estado:</b> El tracking de ShipsGo "
                "indica que el contenedor llegó (Progreso: {pct}%, Coordenadas: {coords}). "
                "Estado actualizado a <b>Arribo a Puerto</b>. La sincronización automática se detiene."
            ).format(
                pct=int(transit_pct),
                coords='Sí' if current_location else 'No',
            ))

        self.with_context(
            shipsgo_api_update=True,
            eta_dramatic_change=eta_changed_dramatically,
            eta_dramatic_diff=days_diff,
        ).write(vals)

        if linked_container:
            linked_container.write({
                'shipsgo_last_sync': fields_module.Datetime.now(),
                'shipsgo_last_error': False,
            })

        self.message_post(body=Markup(
            "📡 <b>Sincronización ShipsGo</b><br/>"
            "Contenedor: {container} | Shipment ID: {shipment_id} | Estado: {status}<br/>"
            "Progreso: {pct}% | Buque: {vessel}<br/>"
            "POL: {pol} → POD: {pod}<br/>"
            "Pos. actual: {loc}"
        ).format(
            container=container_ref,
            shipment_id=shipment_id or 'N/A',
            status=status_text,
            pct=int(transit_pct),
            vessel=vessel_name or 'N/A',
            pol=pol_name or 'N/A',
            pod=pod_name or 'N/A',
            loc=str(current_location) if current_location else '⚠️ sin coordenadas',
        ))

    # =========================================================================
    # CÓMPUTOS
    # =========================================================================

    @api.depends('line_ids.container_number')
    def _compute_container_number(self):
        for rec in self:
            containers = set()

            for line in rec.line_ids:
                if line.container_number and line.container_number not in ('', 'PENDIENTE', 'SN', 'False'):
                    containers.add(line.container_number)

            rec.container_number = ', '.join(sorted(containers)) if containers else 'PENDIENTE'

    @api.depends('eta', 'eta_original', 'arrival_date_bodega')
    def _compute_delay_days(self):
        for rec in self:
            if not rec.eta_original:
                rec.delay_days = 0
                continue

            reference_end = rec.arrival_date_bodega or rec.eta

            if reference_end:
                rec.delay_days = (reference_end - rec.eta_original).days
            else:
                rec.delay_days = 0

    @api.depends('eta', 'custom_status')
    def _compute_eta_alert(self):
        today = fields_module.Date.today()

        for rec in self:
            if rec.custom_status == 'delivered':
                rec.eta_alert_level = 'done'
            elif not rec.eta:
                rec.eta_alert_level = 'ok'
            elif today > rec.eta:
                rec.eta_alert_level = 'danger'
            elif (rec.eta - today).days <= ETA_WARNING_DAYS_BEFORE:
                rec.eta_alert_level = 'warning'
            else:
                rec.eta_alert_level = 'ok'

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status')
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('product_uom_qty'))
            allocated = sum(
                rec.line_ids.filtered(
                    lambda l: l.allocation_status == 'reserved'
                ).mapped('product_uom_qty')
            )

            rec.total_m2 = total
            rec.allocated_m2 = allocated

    @api.depends('total_m2', 'allocated_m2')
    def _compute_allocation_percent(self):
        for rec in self:
            rec.allocation_percent = (
                (rec.allocated_m2 / rec.total_m2) * 100
                if rec.total_m2 > 0 else 0
            )

    @api.depends('line_ids.partner_id', 'line_ids.order_id')
    def _compute_pending_order(self):
        """Líneas con CLIENTE pero SIN PEDIDO: asignación incompleta.

        Se le asignó material a un cliente pero todavía no se indicó a qué
        orden de venta corresponde; hay que completar el pedido."""
        for rec in self:
            pending = rec.line_ids.filtered(
                lambda l: l.partner_id and not l.order_id
            )
            rec.pending_order_count = len(pending)

            partners = pending.mapped('partner_id')
            rec.pending_order_summary = ', '.join(partners.mapped('name')) if partners else ''

    @api.depends('etd', 'eta', 'custom_status', 'create_date', 'shipsgo_payload')
    def _compute_transit_progress(self):
        today = fields_module.Date.today()

        status_floor = {
            'solicitud': 5,
            'production': 15,
            'booking': 25,
            'puerto_origen': 40,
            'on_sea': 60,
            'puerto_destino': 85,
            'arrived_port': 100,
            'reception_pending': 100,
            'delivered': 100,
            'cancel': 0,
        }

        for rec in self:
            if rec.custom_status == 'cancel':
                rec.transit_progress = 0
                continue

            if rec.custom_status in ('arrived_port', 'reception_pending', 'delivered'):
                rec.transit_progress = 100
                continue

            payload_progress = None

            if rec.shipsgo_payload:
                try:
                    payload = json.loads(rec.shipsgo_payload)
                    if isinstance(payload, dict) and payload.get('transit_pct') is not None:
                        payload_progress = int(float(payload.get('transit_pct') or 0))
                except Exception:
                    payload_progress = None

            if payload_progress is not None:
                rec.transit_progress = max(0, min(100, payload_progress))
                continue

            start_date = rec.etd or (rec.create_date.date() if rec.create_date else False)

            if not start_date or not rec.eta:
                rec.transit_progress = status_floor.get(rec.custom_status, 0)
                continue

            if today < start_date:
                date_progress = 0
            elif today > rec.eta:
                date_progress = 95
            else:
                total_days = (rec.eta - start_date).days
                elapsed = (today - start_date).days

                if total_days > 0:
                    date_progress = int((elapsed / total_days) * 100)
                    date_progress = max(0, min(95, date_progress))
                else:
                    date_progress = status_floor.get(rec.custom_status, 0)

            rec.transit_progress = max(
                status_floor.get(rec.custom_status, 0),
                date_progress,
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')

            vals.pop('container_number', None)

            if vals.get('eta') and not vals.get('eta_original'):
                vals['eta_original'] = vals['eta']

        records = super(StockTransitVoyage, self).create(vals_list)
        
        # SINCRONIZACIÓN AUTOMÁTICA AL CREAR — solo valores CAPTURADOS.
        # `field in record` en Odoo verifica _fields (siempre True): se
        # sincronizaba siempre, propagando False y BORRANDO fechas/BL ya
        # capturados en la OC y los embarques.
        if not self.env.context.get('skip_date_sync'):
            for record in records:
                sync_vals = {
                    field: record[field]
                    for field in ('bl_number', 'eta', 'etd')
                    if record[field]
                }
                if sync_vals:
                    record._sync_dates_to_others(sync_vals)

        return records

    def write(self, vals):
        # Contador de 'días sin recibir': se estampa al ENTRAR a
        # reception_pending y se conserva al pasar a delivered (el contador
        # simplemente termina); si el viaje regresa a una etapa anterior,
        # se limpia para que un futuro arribo arranque de cero.
        if vals.get('custom_status') == 'reception_pending':
            for rec in self:
                if not rec.tc_reception_pending_at:
                    vals_rec = dict(vals,
                                    tc_reception_pending_at=fields_module.Datetime.now())
                    super(StockTransitVoyage, rec).write(vals_rec)
                else:
                    super(StockTransitVoyage, rec).write(vals)
            return True
        if vals.get('custom_status') and vals['custom_status'] not in (
                'reception_pending', 'delivered'):
            vals = dict(vals, tc_reception_pending_at=False)

        # ENTREGA EN SITIO ≠ RECIBIDO: 'delivered' solo puede persistirse si la
        # recepción física está VALIDADA (picking done). Cualquier otro camino
        # (arrastrar la tarjeta a "Entrega en Sitio" en el kanban de Viajes y
        # Contenedores, el formulario, un flujo viejo) se re-enruta a
        # 'reception_pending': se crea la recepción física si falta y el viaje
        # queda LISTO PARA RECIBIR en el tablero de Recepciones. El 'Entregado'
        # real lo pone _auto_finalize_after_reception al validar la recepción.
        if vals.get('custom_status') == 'delivered':
            pending_reception = self.filtered(
                lambda v: not (
                    v.reception_picking_id
                    and v.reception_picking_id.state == 'done'
                )
            )
            if pending_reception:
                deliverable = self - pending_reception
                if deliverable:
                    deliverable.write(vals)

                pending_reception.write(
                    dict(vals, custom_status='reception_pending'))

                for rec in pending_reception:
                    if not rec.reception_picking_id:
                        try:
                            rec.action_generate_reception()
                        except Exception:
                            _logger.exception(
                                '[TC_VOYAGE] No se pudo crear la recepción '
                                'automática del viaje %s al moverlo a Entrega '
                                'en Sitio.', rec.name,
                            )
                    rec.message_post(body=Markup(_(
                        "🚚 <b>Entrega en Sitio:</b> el viaje quedó <b>LISTO "
                        "PARA RECIBIR</b> (no Entregado). Se marcará Entregado "
                        "automáticamente cuando el almacén VALIDE la recepción "
                        "física%s."
                    )) % (
                        ' %s' % rec.reception_picking_id.name
                        if rec.reception_picking_id else ''
                    ))
                return True

        if 'eta' in vals:
            for rec in self:
                if not rec.eta_original and vals.get('eta'):
                    super(StockTransitVoyage, rec).write({
                        'eta_original': vals['eta'],
                    })

        is_api_update = self.env.context.get('shipsgo_api_update', False)

        if 'eta' in vals and not is_api_update:
            for rec in self:
                if rec.eta != vals.get('eta'):
                    super(StockTransitVoyage, rec).write({
                        'eta_warning_notified': False,
                        'eta_overdue_notified': False,
                    })

        # Viajes que ENTRAN a 'arrived_port' con este write (capturado antes
        # del super para conocer el estado previo): al arribar a puerto
        # destino se agenda la actividad de recepción para inventarios.
        arriving = self.browse()
        if vals.get('custom_status') == 'arrived_port':
            arriving = self.filtered(lambda v: v.custom_status != 'arrived_port')

        res = super().write(vals)

        if arriving:
            arriving._som_schedule_reception_activity()

        # ---------------------------------------------------------
        # SINCRONIZACIÓN BIDIRECCIONAL A OC Y PORTAL
        # ---------------------------------------------------------
        if not self.env.context.get('skip_date_sync'):
            sync_fields = {'bl_number', 'eta', 'etd'}
            if sync_fields.intersection(vals.keys()):
                for voyage in self:
                    voyage._sync_dates_to_others(vals)


        if 'custom_status' in vals or 'eta' in vals:
            transit_lines = self.mapped('line_ids')
            order_ids = transit_lines.mapped('order_id')

            if order_ids:
                sol = self.env['sale.order.line'].search([
                    ('order_id', 'in', order_ids.ids),
                    ('auto_transit_assign', '=', True),
                ])
                sol._compute_transit_info()

        if is_api_update and self.env.context.get('eta_dramatic_change'):
            for rec in self:
                rec._notify_dramatic_eta_change(
                    self.env.context.get('eta_dramatic_diff', 0)
                )

        if 'eta' in vals or 'custom_status' in vals:
            self._check_eta_alerts()

        return res

    # Usuario que procesa las recepciones físicas y captura el worksheet
    # con las medidas. Si el login cambia, actualizar aquí.
    _SOM_RECEPTION_NOTIFY_LOGIN = 'inventarios@somgroup.mx'

    def _som_schedule_reception_activity(self):
        """Al arribar a puerto destino, agenda una actividad al usuario de
        inventarios SOBRE LA RECEPCIÓN (el folio que debe procesar): la
        actividad abre directo el documento donde se presiona Recibir y se
        captura el worksheet con las medidas.

        La nota se construye SIN HTML crudo: texto plano escapado línea por
        línea (html_escape) unido con <br/> vía Markup — nunca se muestran
        etiquetas al usuario.
        """
        user = self.env['res.users'].sudo().search(
            ['|',
             ('login', '=', self._SOM_RECEPTION_NOTIFY_LOGIN),
             ('email', '=', self._SOM_RECEPTION_NOTIFY_LOGIN)],
            limit=1,
        )
        if not user:
            _logger.warning(
                "[TC ARRIBO] No existe el usuario %s: no se agenda la "
                "actividad de recepción.", self._SOM_RECEPTION_NOTIFY_LOGIN,
            )
            return

        for voyage in self:
            # Documento objetivo: la recepción de tránsito (incoming). Si el
            # viaje aún no la tiene, cualquier recepción pendiente de la OC.
            picking = voyage.picking_id
            if (not picking or picking.state in ('done', 'cancel')) and voyage.purchase_id:
                picking = voyage.purchase_id.picking_ids.filtered(
                    lambda p: p.picking_type_code == 'incoming'
                    and p.state not in ('done', 'cancel')
                )[:1]

            target = picking or voyage
            summary = _("Arribo a puerto: procesar recepción %s") % (
                picking.name if picking else (voyage.name or '')
            )

            # Anti-duplicado: si el estado rebota y vuelve a entrar a
            # 'arrived_port', no agendar la misma actividad dos veces.
            existing = self.env['mail.activity'].sudo().search_count([
                ('res_model', '=', target._name),
                ('res_id', '=', target.id),
                ('user_id', '=', user.id),
                ('summary', '=', summary),
            ])
            if existing:
                continue

            lines = [
                _("El material del viaje %s llegó a puerto destino.") % (voyage.name or ''),
            ]
            if voyage.purchase_id:
                lines.append(_("Orden de compra: %s") % voyage.purchase_id.name)
            if picking:
                lines.append(_("Recepción a procesar: %s") % picking.name)

            moves = picking.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
            ) if picking else self.env['stock.move']
            if moves:
                lines.append(_("Materiales a recibir:"))
                for move in moves[:30]:
                    product = move.product_id
                    ref = f"[{product.default_code}] " if product.default_code else ""
                    uom = move.product_uom.name or ''
                    lines.append(f"  - {ref}{product.name}: {move.product_uom_qty:g} {uom}")
                if len(moves) > 30:
                    lines.append(_("  … y %s materiales más.") % (len(moves) - 30))

            lines.append(_(
                "Al recibir, procesa el worksheet con las medidas "
                "correspondientes."
            ))

            note = Markup('<br/>').join(html_escape(line) for line in lines)

            target.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=summary,
                note=note,
                user_id=user.id,
                date_deadline=fields_module.Date.context_today(voyage),
            )
            _logger.info(
                "[TC ARRIBO] Actividad de recepción agendada a %s sobre %s,%s",
                user.login, target._name, target.id,
            )

    def _sync_dates_to_others(self, vals):
        """Helper para sincronizar fechas logísticas con Orden de Compra y Portal Proveedor"""
        for voyage in self:
            # Sincronizar hacia Orden de Compra
            if voyage.purchase_id:
                po_vals = {}
                if 'bl_number' in vals: po_vals['bl_number'] = vals['bl_number']
                if 'eta' in vals: po_vals['eta_date'] = vals['eta']
                if po_vals:
                    voyage.purchase_id.with_context(skip_date_sync=True).write(po_vals)

            # Sincronizar hacia Portal (Embarque). La Torre/OC MANDA: sus
            # valores pisan al portal, pero los vacíos no viajan (editar
            # otra cosa en el viaje no borra lo capturado por el proveedor).
            if 'supplier.shipment' in self.env.registry:
                shipments = self.env['supplier.shipment'].sudo().search([('voyage_id', '=', voyage.id)])
                s_vals = {}
                for f in ('bl_number', 'eta', 'etd',
                          'shipping_line', 'vessel_name'):
                    if f in vals and vals[f] and vals[f] != 'Por Definir':
                        s_vals[f] = vals[f]
                if s_vals:
                    shipments.with_context(
                        skip_date_sync=True, som_carrier_sync=True,
                    ).write(s_vals)

    # =========================================================================
    # NOTIFICACIONES
    # =========================================================================

    def _get_notification_recipient(self):
        self.ensure_one()

        if self.purchase_id and self.purchase_id.user_id:
            return self.purchase_id.user_id

        return False

    def _notify_dramatic_eta_change(self, days_diff):
        self.ensure_one()

        responsible = self._get_notification_recipient()

        if not responsible:
            return

        if self.custom_status in ('delivered', 'cancel'):
            return

        eta_str = som_format_date(self.eta)

        body = Markup(
            "📅 <b>Cambio importante de ETA detectado</b><br/>"
            "El embarque <b>%s</b> tuvo un ajuste de <b>%s días</b> en su fecha de llegada según ShipsGo.<br/>"
            "Nuevo ETA: <b>%s</b>"
        ) % (self.name, days_diff, eta_str)

        self.message_post(
            body=body,
            partner_ids=responsible.partner_id.ids,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _check_eta_alerts(self):
        today = fields_module.Date.today()

        for rec in self:
            if rec.custom_status in ('delivered', 'cancel'):
                continue

            if not rec.eta:
                continue

            responsible = rec._get_notification_recipient()

            if not responsible:
                continue

            days_to_eta = (rec.eta - today).days

            if days_to_eta == ETA_WARNING_DAYS_BEFORE and not rec.eta_warning_notified:
                eta_str = som_format_date(rec.eta)
                body = Markup(
                    "⚠️ <b>Embarque próximo a llegar</b><br/>"
                    "El embarque <b>%s</b> tiene ETA <b>mañana (%s)</b> y está en estado <b>%s</b>."
                ) % (
                    rec.name,
                    eta_str,
                    dict(rec._fields['custom_status'].selection).get(rec.custom_status, rec.custom_status),
                )

                rec.message_post(
                    body=body,
                    partner_ids=responsible.partner_id.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )

                super(StockTransitVoyage, rec).write({
                    'eta_warning_notified': True,
                })

            days_overdue = (today - rec.eta).days

            if days_overdue == ETA_OVERDUE_DAYS_AFTER and not rec.eta_overdue_notified:
                eta_str = som_format_date(rec.eta)
                body = Markup(
                    "🚨 <b>Embarque vencido</b><br/>"
                    "El embarque <b>%s</b> tenía ETA <b>%s</b> y aún no ha llegado. "
                    "Estado actual: <b>%s</b>."
                ) % (
                    rec.name,
                    eta_str,
                    dict(rec._fields['custom_status'].selection).get(rec.custom_status, rec.custom_status),
                )

                rec.message_post(
                    body=body,
                    partner_ids=responsible.partner_id.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )

                super(StockTransitVoyage, rec).write({
                    'eta_overdue_notified': True,
                })

    @api.model
    def _cron_check_eta_alerts(self):
        voyages = self.search([
            ('custom_status', 'not in', ['delivered', 'cancel']),
            ('eta', '!=', False),
        ])
        voyages._check_eta_alerts()

    # =========================================================================
    # ESTADOS
    # =========================================================================

    STATUS_SEQUENCE = [
        'solicitud',
        'production',
        'booking',
        'puerto_origen',
        'on_sea',
        'puerto_destino',
        'arrived_port',
        'reception_pending',
        'delivered',
    ]

    STATUS_LABELS = {
        'solicitud': 'Solicitud Enviada',
        'production': 'Producción',
        'booking': 'Booking',
        'puerto_origen': 'Puerto Origen',
        'on_sea': 'En Altamar',
        'puerto_destino': 'Puerto Destino',
        'arrived_port': 'Arribo a Puerto',
        'reception_pending': 'En Recepción',
        'delivered': 'Entregado en Almacén',
        'cancel': 'Cancelado',
    }

    @api.model
    @api.model
    def tv_get_fleet_map_data(self):
        """Mapa global de Embarques (vista principal de Torre de Control).

        Devuelve TODOS los viajes no cancelados con su ruta ShipsGo ya
        parseada (shipsgo_payload guarda el map_data del refresh: rutas
        pasado/actual/futuro, puertos y posición del buque), más la ficha
        comercial: OC, proveedor, embarque, contenedores, ETD/ETA."""
        voyages = self.sudo().search(
            [('custom_status', '!=', 'cancel')],
            order='eta asc, id desc', limit=500,
        )

        Shipment = self.env['supplier.shipment'].sudo()
        shipments = Shipment.search([('voyage_id', 'in', voyages.ids)])
        ship_by_voyage = {}
        for sh in shipments:
            ship_by_voyage.setdefault(sh.voyage_id.id, sh)

        status_labels = dict(
            self._fields['custom_status']._description_selection(self.env))

        out = []
        for v in voyages:
            route = {}
            if v.shipsgo_payload:
                try:
                    route = json.loads(v.shipsgo_payload)
                except Exception:
                    route = {}

            sh = ship_by_voyage.get(v.id)
            containers = []
            if v.container_number:
                containers = [c.strip() for c in
                              v.container_number.replace(';', ',').split(',')
                              if c.strip()]
            if sh:
                for c in sh.container_ids:
                    num = (c.container_number or '').strip()
                    if num and num not in containers:
                        containers.append(num)

            po = v.purchase_id
            out.append({
                'id': v.id,
                'name': v.name or '',
                'status': v.custom_status,
                'status_label': status_labels.get(
                    v.custom_status, v.custom_status),
                'labeling_status': v.tc_labeling_status or 'none',
                'po_name': po.name if po else '',
                'partner_ref': (po.partner_ref or '') if po else '',
                'supplier': (
                    v.tc_supplier_id.display_name if v.tc_supplier_id
                    else (po.partner_id.name if po else '')
                ),
                'shipment_name': sh.name if sh else '',
                'proforma_ref': (
                    sh.proforma_id.display_name
                    if sh and sh.proforma_id else ''
                ),
                'containers': containers,
                'bl_number': v.bl_number or '',
                'vessel_name': v.vessel_name or route.get('vessel') or '',
                'shipping_line': v.shipping_line or '',
                'etd': v.etd.isoformat() if v.etd else '',
                'eta': v.eta.isoformat() if v.eta else '',
                'etd_label': som_format_date(v.etd, empty=''),
                'eta_label': som_format_date(v.eta, empty=''),
                'delay_days': v.delay_days or 0,
                'eta_alert_level': v.eta_alert_level or '',
                'total_m2': v.total_m2 or 0.0,
                'allocation_percent': v.allocation_percent or 0.0,
                'transit_pct': route.get('transit_pct') or v.transit_progress or 0,
                'current_loc': route.get('current_loc') or None,
                'origin': route.get('origin') or {},
                'destination': route.get('destination') or {},
                'route': route.get('route') or {},
                'carrier': route.get('carrier') or '',
                'checked_at': route.get('checked_at') or '',
            })
        return {'voyages': out}

    def tk_get_kanban_records(self):
        """Tarjetas del kanban Viajes y Contenedores en UNA llamada.

        Además de los campos base del viaje incluye la referencia interna
        (PI / referencia de la OC) y las facturas de carga que amparan la OC,
        resueltas en bloque para no disparar N lecturas desde el frontend."""
        voyages = self.search(
            [('custom_status', '!=', 'cancel')],
            order='eta asc, id desc', limit=500,
        )

        cargo_by_po = {}
        if 'supplier.cargo.invoice' in self.env:
            po_ids = voyages.mapped('purchase_id').ids
            if po_ids:
                cargos = self.env['supplier.cargo.invoice'].sudo().search([
                    ('purchase_ids', 'in', po_ids),
                ])
                for cargo in cargos:
                    if not cargo.name:
                        continue
                    for po in cargo.purchase_ids:
                        names = cargo_by_po.setdefault(po.id, [])
                        if cargo.name not in names:
                            names.append(cargo.name)

        status_labels = dict(
            self._fields['custom_status']._description_selection(self.env))

        out = []
        for v in voyages:
            po = v.purchase_id
            supplier = v.tc_supplier_id
            out.append({
                'id': v.id,
                'name': v.name or '',
                'custom_status': v.custom_status,
                'status_label': status_labels.get(
                    v.custom_status, v.custom_status),
                'purchase_id': [po.id, po.name] if po else False,
                'partner_ref': (po.partner_ref or '') if po else '',
                'tc_supplier_id': (
                    [supplier.id, supplier.display_name] if supplier else False
                ),
                'cargo_invoices': ', '.join(cargo_by_po.get(po.id, [])) if po else '',
                'container_number': v.container_number or '',
                'bl_number': v.bl_number or '',
                'vessel_name': v.vessel_name or '',
                'shipping_line': v.shipping_line or '',
                'eta': v.eta.isoformat() if v.eta else False,
                'etd': v.etd.isoformat() if v.etd else False,
                'create_date': (
                    v.create_date.isoformat() if v.create_date else False),
                'allocation_percent': v.allocation_percent or 0.0,
                'total_m2': v.total_m2 or 0.0,
                'labeling_status': v.tc_labeling_status or 'none',
                'label_print_count': v.tc_label_print_count or 0,
                'tc_publication_pending': bool(v.tc_publication_pending),
                'reception_pending_at': (
                    v.tc_reception_pending_at.isoformat()
                    if v.tc_reception_pending_at else (
                        v.reception_picking_id.create_date.isoformat()
                        if v.reception_picking_id
                        and v.custom_status in ('reception_pending', 'delivered')
                        and v.reception_picking_id.create_date else False
                    )
                ),
                'company_id': v.company_id.id if v.company_id else False,
            })
        return out

    def action_open_unassign_wizard(self):
        """Abre el wizard de desasignación masiva para este viaje.

        Permite liberar a Stock varios materiales asignados por error, sin
        tener que limpiar cliente/orden línea por línea."""
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Desasignar materiales en tránsito'),
            'res_model': 'transit.unassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': dict(
                self.env.context,
                active_model='stock.transit.voyage',
                active_id=self.id,
                active_ids=self.ids,
            ),
        }

    # =========================================================================
    # COMPOSICIÓN MULTI-PROFORMA (un embarque, varias recepciones)
    # =========================================================================

    def _tc_component_pickings(self):
        """Recepciones a tránsito que YA alimentan este embarque."""
        self.ensure_one()
        pickings = self.picking_ids
        if self.picking_id:
            pickings |= self.picking_id
        return pickings

    def _tc_linked_portal_shipments(self):
        """Embarques del portal del proveedor ligados a este viaje."""
        self.ensure_one()

        if 'supplier.shipment' not in self.env:
            return None

        Shipment = self.env['supplier.shipment'].sudo()
        shipments = Shipment.search([('voyage_id', '=', self.id)])

        component = self._tc_component_pickings()
        if component and 'supplier_shipment_id' in component._fields:
            shipments |= component.sudo().mapped('supplier_shipment_id')

        return shipments

    def _tc_expected_component_pickings(self):
        """TODAS las recepciones a tránsito que deben alimentar este embarque.

        Con factura de carga (liga única de portal) el embarque del portal
        crea una recepción POR PROFORMA: aunque solo una esté validada, las
        hermanas del mismo supplier.shipment también son parte del embarque
        y el viaje no está completo hasta que todas lleguen.
        """
        self.ensure_one()

        expected = self._tc_component_pickings()
        shipments = self._tc_linked_portal_shipments()

        Picking = self.env['stock.picking'].sudo()
        if shipments and 'supplier_shipment_id' in Picking._fields:
            siblings = Picking.search([
                ('supplier_shipment_id', 'in', shipments.ids),
                ('picking_type_code', '=', 'incoming'),
                ('state', '!=', 'cancel'),
            ])
            # Devoluciones no componen embarques (misma regla que la
            # creación automática de viajes).
            siblings = siblings.filtered(
                lambda p: p.location_id.usage != 'customer'
                and not ('return_id' in p._fields and p.return_id)
            )
            expected |= siblings

        return expected

    def _tc_pending_component_pickings(self):
        """Recepciones del embarque que aún no se validan."""
        self.ensure_one()
        return self._tc_expected_component_pickings().filtered(
            lambda p: p.state != 'done'
        )

    def _compute_tc_composition(self):
        for rec in self:
            rec.tc_composition_summary = ''
            rec.tc_pending_reception_count = 0

            if not rec.id:
                continue

            expected = rec._tc_expected_component_pickings()
            if len(expected) <= 1:
                continue

            pending = expected.filtered(lambda p: p.state != 'done')
            parts = []
            for pick in expected.sorted('id'):
                po = pick.purchase_id
                label = po.name if po else (pick.origin or pick.name)
                parts.append('%s %s' % (
                    label, '✓' if pick.state == 'done' else '⏳'))

            rec.tc_pending_reception_count = len(pending)
            rec.tc_composition_summary = _(
                'Compuesto por %(count)s proformas: %(detail)s'
            ) % {
                'count': len(expected),
                'detail': ' · '.join(parts),
            }

    def _tc_assert_components_complete(self, operation_label):
        """Un embarque multi-proforma no avanza a recepción/cierre hasta que
        TODAS sus recepciones componentes estén validadas."""
        self.ensure_one()

        pending = self._tc_pending_component_pickings()
        if not pending:
            return

        detail = '\n'.join(
            '- %s (%s)' % (
                p.purchase_id.name if p.purchase_id else (p.origin or ''),
                p.name,
            )
            for p in pending.sorted('id')
        )
        raise UserError(_(
            'No se puede %(operation)s: este embarque está compuesto por '
            '%(total)s proformas y aún faltan recepciones por validar:\n\n'
            '%(detail)s\n\n'
            'Valide las recepciones pendientes; cada una alimenta este '
            'mismo embarque (%(voyage)s).'
        ) % {
            'operation': operation_label,
            'total': len(self._tc_expected_component_pickings()),
            'detail': detail,
            'voyage': self.name,
        })

    def action_advance_status(self):
        self.ensure_one()

        if self.custom_status in ('delivered', 'cancel'):
            return

        self._do_advance_status(notes=False)

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_retreat_status(self):
        self.ensure_one()

        if self.custom_status in ('solicitud', 'delivered', 'cancel'):
            return

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'transit.status.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_direction': 'retreat',
            },
        }

    def _do_advance_status(self, notes=None):
        self.ensure_one()

        current = self.custom_status

        if current in ('cancel', 'delivered'):
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        next_idx = idx + 1

        if next_idx >= len(self.STATUS_SEQUENCE):
            return

        next_status = self.STATUS_SEQUENCE[next_idx]

        # Embarque multi-proforma: no entra a recepción ni se cierra hasta
        # que TODAS las recepciones que lo componen estén validadas.
        if next_status in ('reception_pending', 'delivered'):
            self._tc_assert_components_complete(
                _('avanzar el embarque a %s')
                % self.STATUS_LABELS.get(next_status, next_status)
            )

        if next_status == 'delivered':
            if self.reception_picking_id and self.reception_picking_id.state != 'done':
                raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))

            if self.reception_picking_id:
                self._auto_finalize_after_reception()
            else:
                # Sin recepción física NO se marca Entregado ni se dan por
                # recibidas las allocations: se crea la recepción y el viaje
                # queda LISTO PARA RECIBIR. El cierre real llega al VALIDAR.
                self.action_generate_reception()
        else:
            if next_status == 'on_sea':
                # Todas las OC que componen el embarque (multi-proforma),
                # no solo la de la recepción primaria.
                purchases = self._tc_component_pickings().mapped('purchase_id')
                if not purchases and self.purchase_id:
                    purchases = self.purchase_id
                if purchases:
                    allocations = self.env['purchase.order.line.allocation'].search([
                        ('purchase_order_id', 'in', purchases.ids),
                        ('state', '=', 'pending'),
                    ])
                    allocations.action_mark_in_transit()

            self.write({'custom_status': next_status})

        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(self.custom_status, self.custom_status)

        msg_parts = [
            Markup("⏩ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]

        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)

        self.message_post(body=Markup('').join(msg_parts))

    def _do_retreat_status(self, notes=None):
        self.ensure_one()

        current = self.custom_status

        if current == 'cancel':
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        if idx <= 0:
            return

        prev_status = self.STATUS_SEQUENCE[idx - 1]

        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(prev_status, prev_status)

        self.write({'custom_status': prev_status})

        msg_parts = [
            Markup("⏪ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]

        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)

        self.message_post(body=Markup('').join(msg_parts))

    # =========================================================================
    # CARGA Y RECEPCIÓN
    # =========================================================================

    def _get_qty_rounding(self, product):
        self.ensure_one()

        rounding = 0.0001

        if product and getattr(product, 'uom_id', False) and product.uom_id.rounding:
            rounding = product.uom_id.rounding

        return rounding

    def _normalize_product_qty(self, product, qty):
        rounding = self._get_qty_rounding(product)
        return float_round(qty or 0.0, precision_rounding=rounding)

    def _qty_differs(self, product, qty_a, qty_b):
        rounding = self._get_qty_rounding(product)
        return float_compare(
            qty_a or 0.0,
            qty_b or 0.0,
            precision_rounding=rounding,
        ) != 0

    def action_load_from_purchase(self):
        self.ensure_one()

        if not self.purchase_id:
            return

        existing_alloc_ids = self.line_ids.mapped('allocation_id.id')

        allocations = self.env['purchase.order.line.allocation'].search([
            ('purchase_order_id', '=', self.purchase_id.id),
            ('id', 'not in', existing_alloc_ids),
        ])

        transit_lines = []

        for alloc in allocations:
            transit_lines.append({
                'voyage_id': self.id,
                'product_id': alloc.product_id.id,
                'product_uom_qty': alloc.quantity,
                'partner_id': alloc.partner_id.id,
                'order_id': alloc.sale_order_id.id,
                'allocation_id': alloc.id,
                'allocation_status': 'reserved',
                'container_number': 'PENDIENTE',
            })

        existing_stock_lines = self.line_ids.filtered(
            lambda l: not l.allocation_id and not l.partner_id and not l.order_id
        )
        existing_stock_by_product = {
            l.product_id.id: l for l in existing_stock_lines
        }

        for po_line in self.purchase_id.order_line:
            total_po_qty = po_line.product_qty
            total_allocated = sum(po_line.allocation_ids.mapped('quantity'))
            extra_for_stock = total_po_qty - total_allocated
            product_id = po_line.product_id.id

            if product_id in existing_stock_by_product:
                existing_line = existing_stock_by_product[product_id]

                if extra_for_stock > 0:
                    if existing_line.product_uom_qty != extra_for_stock:
                        existing_line.write({
                            'product_uom_qty': extra_for_stock,
                        })
                else:
                    existing_line.unlink()

            elif extra_for_stock > 0:
                transit_lines.append({
                    'voyage_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': extra_for_stock,
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'container_number': 'PENDIENTE',
                    'notes': 'Para Stock (cantidad extra en OC)',
                })

        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)

    def action_load_from_picking(self):
        """Carga/actualiza las líneas del viaje desde TODAS las recepciones
        que lo componen. En el flujo clásico hay una sola (picking_id); en
        multi-proforma cada recepción validada complementa el mismo embarque."""
        self.ensure_one()

        pickings = self._tc_component_pickings().sorted('id')

        if not pickings:
            return

        for picking in pickings:
            self._tc_load_lines_from_picking(picking)

    def _tc_load_lines_from_picking(self, picking):
        self.ensure_one()

        if not picking or not picking.exists():
            return

        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)

        if placeholder_lines:
            placeholder_lines.unlink()

        # Un lote puede tener VARIAS líneas (gemelas por parcialidad):
        # indexar TODAS, no solo la última, para repartir sin inflar.
        existing_by_lot = {}
        for line in self.line_ids:
            if line.lot_id:
                existing_by_lot.setdefault(
                    line.lot_id.id, self.env['stock.transit.line'])
                existing_by_lot[line.lot_id.id] |= line

        from .utils.transit_manager import TransitManager

        purchase = picking.purchase_id
        allocations_map = {}
        allocation_consumed = {}

        if purchase:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', purchase.id),
                ('state', 'not in', ['done', 'cancelled']),
            ], order='id asc')

            for alloc in allocations:
                allocations_map.setdefault(alloc.product_id.id, []).append(alloc)
                allocation_consumed[alloc.id] = 0.0

        lines_to_create = []
        hold_orders_map = {}

        for move_line in picking.move_line_ids:
            if not move_line.lot_id:
                continue

            lot_id = move_line.lot_id.id
            product_id = move_line.product_id.id

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id),
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id),
            ], limit=1)

            raw_qty_done = move_line.quantity
            qty_done = self._normalize_product_qty(
                move_line.product_id,
                found_quant.quantity if found_quant else raw_qty_done,
            )

            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False

            if product_id in allocations_map:
                for alloc in allocations_map[product_id]:
                    already_received = alloc.qty_received
                    consumed_this_load = allocation_consumed.get(alloc.id, 0.0)
                    remaining = alloc.quantity - (already_received + consumed_this_load)

                    if remaining > 0:
                        allocation_to_use = alloc
                        partner_to_assign = alloc.partner_id
                        order_to_assign = alloc.sale_order_id

                        if alloc.sale_line_id:
                            auto_assign = getattr(alloc.sale_line_id, 'auto_transit_assign', True)

                            if not auto_assign:
                                partner_to_assign = False
                                order_to_assign = False
                                allocation_to_use = False
                                continue

                        allocation_consumed[alloc.id] = consumed_this_load + qty_done
                        break

            lot_container = ''

            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref

            if lot_id in existing_by_lot:
                lot_lines = existing_by_lot[lot_id].exists()
                if not lot_lines:
                    continue

                existing_line = lot_lines.sorted('id')[0]
                update_vals = {}

                if len(lot_lines) == 1:
                    if self._qty_differs(move_line.product_id, existing_line.product_uom_qty, qty_done):
                        update_vals['product_uom_qty'] = qty_done
                else:
                    # GEMELAS (parcialidad): la cantidad física del lote se
                    # DISTRIBUYE — las líneas con orden conservan su
                    # parcialidad y la disponible absorbe el resto. Jamás se
                    # escribe el total del lote en cada gemela (eso duplicaba
                    # el lote con la cantidad completa en ambas).
                    reserved = lot_lines.filtered(lambda l: l.order_id)
                    free = (lot_lines - reserved).sorted('id')
                    reserved_qty = sum(reserved.mapped('product_uom_qty'))
                    rest = max(qty_done - reserved_qty, 0.0)

                    if free:
                        keeper = free[0]
                        extras = free - keeper
                        if extras:
                            extras.with_context(skip_reservation_logic=True).unlink()
                        if self._qty_differs(move_line.product_id, keeper.product_uom_qty, rest):
                            keeper.with_context(skip_reservation_logic=True).write({
                                'product_uom_qty': rest,
                            })
                        _logger.info(
                            "[TC_TWINS] lote=%s fisico=%.3f reservado=%.3f "
                            "saldo_libre=%.3f (gemelas normalizadas)",
                            move_line.lot_id.name, qty_done, reserved_qty, rest,
                        )

                    existing_line = (reserved.sorted('id')[:1] or free[:1])[0]

                if found_quant and existing_line.quant_id.id != found_quant.id:
                    update_vals['quant_id'] = found_quant.id

                if lot_container and existing_line.container_number != lot_container:
                    update_vals['container_number'] = lot_container

                if allocation_to_use and not existing_line.allocation_id:
                    update_vals['allocation_id'] = allocation_to_use.id

                if not existing_line.partner_id and partner_to_assign:
                    update_vals['partner_id'] = partner_to_assign.id
                    update_vals['order_id'] = order_to_assign.id if order_to_assign else False
                    update_vals['allocation_status'] = 'reserved'

                if update_vals:
                    existing_line.with_context(skip_reservation_logic=True).write(update_vals)

                continue

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container,
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }

            lines_to_create.append(line_vals)

            if partner_to_assign and order_to_assign:
                key = (partner_to_assign.id, order_to_assign.id)

                hold_orders_map.setdefault(key, {
                    'partner': partner_to_assign,
                    'order': order_to_assign,
                    'line_vals_indices': [],
                })
                hold_orders_map[key]['line_vals_indices'].append(len(lines_to_create) - 1)

        created_lines = self.env['stock.transit.line']

        if lines_to_create:
            created_lines = self.env['stock.transit.line'].create(lines_to_create)

        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)
                new_received = alloc.qty_received + qty_consumed
                alloc.write({
                    'qty_received': min(new_received, alloc.quantity),
                    'state': 'in_transit',
                })

        for key, data in hold_orders_map.items():
            partner = data['partner']
            order = data['order']
            indices = data['line_vals_indices']
            relevant_lines = [
                created_lines[i]
                for i in indices
                if i < len(created_lines)
            ]

            if not relevant_lines:
                continue

            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'fecha_orden': fields_module.Datetime.now(),
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            })

            for line in relevant_lines:
                TransitManager.reassign_lot(
                    self.env,
                    line,
                    partner,
                    order,
                    notes=False,
                    hold_order_obj=hold_order,
                )

            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()

    # =========================================================================
    # RECEPCIÓN FÍSICA
    # =========================================================================

    def _get_reception_candidate_lines(self):
        self.ensure_one()

        candidate_lines = self.line_ids.filtered(
            lambda l: l.lot_id and l.product_id and l.product_uom_qty > 0
        )

        if not candidate_lines:
            raise UserError(_("No hay líneas con lote y cantidad positiva para recibir."))

        Quant = self.env['stock.quant'].sudo()
        resolved_lines = []
        missing_lots = []
        source_location_ids = set()
        pending_entries = []

        for line in candidate_lines:
            quant = line.quant_id

            quant_is_valid = bool(
                quant
                and quant.exists()
                and quant.product_id.id == line.product_id.id
                and quant.lot_id.id == line.lot_id.id
                and quant.quantity > 0
                and quant.location_id._som_is_transit()
                and quant.company_id.id == self.company_id.id
            )

            if not quant_is_valid:
                quant = Quant.search([
                    ('company_id', '=', self.company_id.id),
                    ('lot_id', '=', line.lot_id.id),
                    ('product_id', '=', line.product_id.id),
                    ('quantity', '>', 0),
                ] + self.env['stock.location']._som_transit_quant_leaf(),
                    order='id desc', limit=1)

                if quant:
                    line.with_context(skip_reservation_logic=True).write({
                        'quant_id': quant.id,
                    })

            if not quant:
                missing_lots.append(
                    "%s (%.3f)" % (line.lot_id.display_name, line.product_uom_qty)
                )
                continue

            pending_entries.append((line, quant))

        # GEMELAS: el físico del quant se DISTRIBUYE entre las líneas que
        # comparten lote (parcialidades). Antes se escribía quant.quantity
        # completo en CADA gemela y ambas sumaban a la demanda de recepción
        # (20 m² para 10 físicos), pisando además la parcialidad reservada.
        by_quant = {}
        for line, quant in pending_entries:
            by_quant.setdefault(quant.id, {'quant': quant, 'lines': []})
            by_quant[quant.id]['lines'].append(line)

        # La CANTIDAD TEÓRICA es la del Packing List (línea del viaje) y es
        # inviolable: el quant NUNCA la infla. Si el quant trae MÁS que el
        # PL (quants duplicados por dobles validaciones/ajustes: caso
        # S3-01..S3-11 con 45.40 en vez de 22.70), se recibe SOLO el PL y
        # el excedente se denuncia en el chatter. Si trae MENOS, se recibe
        # el físico real (faltante legítimo).
        excess_alerts = []

        for data in by_quant.values():
            quant = data['quant']
            quant_lines = data['lines']
            product = quant_lines[0].product_id
            physical = self._normalize_product_qty(product, quant.quantity)

            if float_is_zero(
                physical,
                precision_rounding=self._get_qty_rounding(product),
            ):
                missing_lots.append(
                    "%s (quant cero efectivo)" % (quant.lot_id.display_name,)
                )
                continue

            source_location_ids.add(quant.location_id.id)

            if len(quant_lines) == 1:
                line = quant_lines[0]
                pl_qty = self._normalize_product_qty(
                    product, line.product_uom_qty)

                if pl_qty > 0 and physical > pl_qty and self._qty_differs(
                        product, physical, pl_qty):
                    excess_alerts.append(
                        "%s: quant en tránsito %.3f > PL %.3f" % (
                            quant.lot_id.display_name, physical, pl_qty))
                    take = pl_qty
                else:
                    take = min(physical, pl_qty) if pl_qty > 0 else physical
                    # Solo se REDUCE la teórica por faltante físico real;
                    # jamás se aumenta desde el quant.
                    if self._qty_differs(product, line.product_uom_qty, take):
                        line.with_context(skip_reservation_logic=True).write({
                            'product_uom_qty': take,
                        })

                resolved_lines.append({
                    'line': line,
                    'quant': quant,
                    'qty_to_receive': take,
                })
                continue

            reserved_twins = [l for l in quant_lines if l.order_id]
            free_twins = [l for l in quant_lines if not l.order_id]
            remaining = physical

            for line in reserved_twins:
                take = min(line.product_uom_qty or 0.0, remaining)
                remaining = max(remaining - take, 0.0)
                if take > 0:
                    resolved_lines.append({
                        'line': line,
                        'quant': quant,
                        'qty_to_receive': take,
                    })

            for index, line in enumerate(free_twins):
                if index == 0 and remaining > 0:
                    pl_qty = self._normalize_product_qty(
                        product, line.product_uom_qty)
                    if pl_qty > 0 and remaining > pl_qty and self._qty_differs(
                            product, remaining, pl_qty):
                        excess_alerts.append(
                            "%s: quant en tránsito %.3f > PL %.3f" % (
                                quant.lot_id.display_name, remaining, pl_qty))
                        take = pl_qty
                    else:
                        take = min(remaining, pl_qty) if pl_qty > 0 else remaining
                        if self._qty_differs(product, line.product_uom_qty, take):
                            line.with_context(skip_reservation_logic=True).write({
                                'product_uom_qty': take,
                            })
                    resolved_lines.append({
                        'line': line,
                        'quant': quant,
                        'qty_to_receive': take,
                    })
                    remaining = 0.0

        if excess_alerts:
            _logger.warning(
                "[TC_RECEPTION_GUARD] Viaje %s: quants en tránsito EXCEDEN el "
                "Packing List (posible duplicación de inventario): %s",
                self.name or self.id, "; ".join(excess_alerts),
            )
            self.message_post(body=_(
                "⚠️ <b>Inventario en tránsito excede el Packing List</b> — la "
                "recepción se preparó con las cantidades del PL (correctas) y "
                "el excedente quedó en tránsito. Revisar y ajustar los quants "
                "de estos lotes:<br/><pre>%s</pre>"
            ) % "\n".join(excess_alerts[:40]))

        if missing_lots:
            raise UserError(_(
                "No se puede preparar la recepción porque estos lotes no tienen quant positivo en una ubicación de tránsito:\n%s"
            ) % "\n".join(missing_lots[:50]))

        if len(source_location_ids) != 1:
            locations = self.env['stock.location'].browse(
                list(source_location_ids)
            ).mapped('complete_name')

            raise UserError(_(
                "Las líneas del viaje apuntan a múltiples ubicaciones de tránsito. "
                "La recepción física debe salir de una sola ubicación origen.\n%s"
            ) % "\n".join(locations))

        source_location = self.env['stock.location'].browse(
            next(iter(source_location_ids))
        )

        return resolved_lines, source_location

    def _get_reception_operation_defaults(self, source_location):
        self.ensure_one()

        picking_types = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id),
        ], order='sequence, id')

        if not picking_types:
            raise UserError(_("No se encontró un tipo de operación de traslado interno."))

        picking_type = False

        for pt in picking_types:
            if (
                pt.default_location_dest_id
                and pt.default_location_dest_id.usage == 'internal'
                and pt.default_location_dest_id.id != source_location.id
            ):
                picking_type = pt
                break

        if not picking_type:
            picking_type = picking_types[0]

        dest_location = False

        if (
            picking_type.default_location_dest_id
            and picking_type.default_location_dest_id.usage == 'internal'
            and picking_type.default_location_dest_id.id != source_location.id
        ):
            dest_location = picking_type.default_location_dest_id

        if (
            not dest_location
            and getattr(picking_type, 'warehouse_id', False)
            and picking_type.warehouse_id.lot_stock_id
            and picking_type.warehouse_id.lot_stock_id.id != source_location.id
        ):
            dest_location = picking_type.warehouse_id.lot_stock_id

        if not dest_location:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id),
            ], order='id', limit=1)

            if (
                warehouse
                and warehouse.lot_stock_id
                and warehouse.lot_stock_id.id != source_location.id
            ):
                dest_location = warehouse.lot_stock_id

        if not dest_location:
            dest_location = self.env['stock.location'].search([
                ('company_id', '=', self.company_id.id),
                ('usage', '=', 'internal'),
                ('id', '!=', source_location.id),
            ], order='id', limit=1)

        if not dest_location:
            raise UserError(_(
                "No se pudo determinar una ubicación destino interna para la recepción física."
            ))

        return picking_type, dest_location

    def _tc_reception_safe_context(self):
        # CRÍTICO: NO heredar llaves default_* del contexto del cliente.
        # Este contexto se usa en create() de stock.picking y stock.move; un
        # default_* arrastrado del navegador (vista/acción previa, breadcrumbs
        # corruptos) puede hacer nacer el documento en un estado indebido
        # (p.ej. default_state => recepción en HECHO sin pasar por ningún
        # guard de _action_done/button_validate). Solo se conservan las llaves
        # no-default y se inyectan los skips explícitos.
        ctx = {
            key: value
            for key, value in (self.env.context or {}).items()
            if not key.startswith('default_')
        }
        ctx.update({
            'skip_procurement': True,
            'tracking_disable': True,
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'skip_transit_reception_sync': True,
            'tc_physical_reception_prepare': True,
            'tc_no_auto_validate': True,
            'skip_immediate_transfer': True,
            'skip_backorder': True,

            # Defensas para módulos de reserva/validación automática. El botón
            # Recibir/Abrir Recepción nunca debe reservar por estrategia de
            # remoción ni ejecutar transferencias.
            'skip_action_assign': True,
            'skip_stock_reservation': True,
            'skip_stock_whole_lot_removal': True,
            'skip_whole_lot': True,
            'skip_whole_lot_removal': True,
            'skip_whole_lot_reservation': True,
            'skip_whole_lot_strategy': True,
            'skip_auto_assign': True,
            'skip_auto_reserve': True,
        })
        return ctx

    def _tc_get_allowed_reception_open_states(self):
        return {
            'draft',
            'waiting',
            'confirmed',
            'assigned',
            'partially_available',
        }

    def _tc_assert_reception_can_stay_open(self, picking, operation_label=False):
        """Hard guard: Recibir/Abrir Recepción nunca debe entregar el picking."""
        self.ensure_one()

        if not picking or not picking.exists():
            raise UserError(_("No se encontró la recepción física vinculada al embarque."))

        label = operation_label or _("preparar la recepción física")

        if picking.state == 'done':
            # Diagnóstico: deja rastro de QUÉ dejó la recepción en HECHO sin
            # pasar por los guards de validación (estados de moves y llaves de
            # contexto, donde un default_* heredado del cliente es el sospechoso
            # típico).
            _logger.error(
                "[TC_RECEPTION_GUARD] Recepción %s en HECHO durante '%s'. "
                "moves=%s estados_moves=%s move_lines=%s ctx_keys=%s",
                picking.name or picking.id,
                label,
                picking.move_ids.ids,
                picking.move_ids.mapped('state'),
                picking.move_line_ids.ids,
                sorted(self.env.context.keys()),
            )
            raise UserError(_(
                "Control Tower detuvo el flujo porque la operación de recepción física %(picking)s "
                "quedó en estado HECHO durante %(operation)s.\n\n"
                "El botón Recibir/Abrir Recepción solo puede preparar y abrir la recepción; "
                "no puede validarla. Debe procesar primero el Packing List físico y el Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
                'operation': label,
            })

        if picking.state == 'cancel':
            raise UserError(_(
                "La recepción física %(picking)s está cancelada. Genere una nueva recepción."
            ) % {
                'picking': picking.name or picking.display_name,
            })

        allowed_states = self._tc_get_allowed_reception_open_states()
        if picking.state and picking.state not in allowed_states:
            raise UserError(_(
                "La recepción física %(picking)s quedó en un estado no esperado: %(state)s.\n"
                "Estados permitidos antes de validar: borrador, en espera, listo o parcialmente disponible."
            ) % {
                'picking': picking.name or picking.display_name,
                'state': picking.state,
            })

        return True

    def _tc_assert_reception_can_be_rebuilt(self, picking):
        self.ensure_one()
        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("sincronizar la recepción física"),
        )

        locked_flags = []

        if 'packing_list_imported' in picking._fields and picking.packing_list_imported:
            locked_flags.append(_("Packing List físico ya procesado"))

        if 'worksheet_imported' in picking._fields and picking.worksheet_imported:
            locked_flags.append(_("Worksheet físico ya procesado"))

        if locked_flags and not self.env.context.get('force_tc_reception_resync'):
            raise UserError(_(
                "No se puede reconstruir la recepción física %(picking)s porque ya inició el flujo operativo:\n"
                "- %(flags)s\n\n"
                "Use Abrir Recepción para continuar trabajando. No se deben borrar líneas ni reiniciar PL/Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
                'flags': '\n- '.join(locked_flags),
            })

        return True

    def _tc_reception_has_locked_physical_work(self, picking):
        self.ensure_one()
        picking.ensure_one()

        return bool(
            ('packing_list_imported' in picking._fields and picking.packing_list_imported)
            or ('worksheet_imported' in picking._fields and picking.worksheet_imported)
        )

    def _tc_open_reception_action(self, picking):
        self.ensure_one()
        picking.ensure_one()
        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("abrir la recepción física"),
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _tc_prepare_reception_move_vals(self, picking, product, total_qty):
        self.ensure_one()

        Move = self.env['stock.move']
        move_fields = Move._fields

        vals = {}

        if 'name' in move_fields:
            vals['name'] = product.display_name

        if 'description_picking' in move_fields:
            vals['description_picking'] = product.display_name

        vals.update({
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': total_qty,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'company_id': picking.company_id.id or self.company_id.id,
        })

        if 'product_uom' in move_fields:
            vals['product_uom'] = product.uom_id.id
        elif 'product_uom_id' in move_fields:
            vals['product_uom_id'] = product.uom_id.id

        if 'picking_type_id' in move_fields:
            vals['picking_type_id'] = picking.picking_type_id.id

        if 'date' in move_fields:
            vals['date'] = fields_module.Datetime.now()

        if 'procure_method' in move_fields:
            vals['procure_method'] = 'make_to_stock'

        # La demanda de recepción SIEMPRE nace en borrador. Explícito para que
        # ningún default heredado de contexto pueda crear el move ya hecho.
        if 'state' in move_fields:
            vals['state'] = 'draft'

        if 'picked' in move_fields:
            vals['picked'] = False

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_fields
        }

        return vals

    def _tc_prepare_reception_move_line_vals(self, picking, move, line, quant, qty_to_receive):
        """
        Helper conservado para el flujo posterior de confirmación / procesamiento físico.
        No se usa durante action_generate_reception.
        """
        self.ensure_one()

        MoveLine = self.env['stock.move.line']

        vals = {
            'picking_id': picking.id,
            'move_id': move.id,
            'company_id': picking.company_id.id or self.company_id.id,
            'product_id': line.product_id.id,
            'lot_id': line.lot_id.id,
            'location_id': quant.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        }

        if 'product_uom_id' in MoveLine._fields:
            vals['product_uom_id'] = line.product_id.uom_id.id
        elif 'product_uom' in MoveLine._fields:
            vals['product_uom'] = line.product_id.uom_id.id

        if 'quantity' in MoveLine._fields:
            vals['quantity'] = qty_to_receive
        elif 'qty_done' in MoveLine._fields:
            vals['qty_done'] = 0.0

        if 'picked' in MoveLine._fields:
            vals['picked'] = False

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in MoveLine._fields
        }

        return vals

    def _sync_reception_picking_lines(self, picking, resolved_lines=None):
        """
        Prepara la recepción física desde el viaje sin validar inventario.

        Regla funcional:
        - Recibir solo crea/actualiza la demanda del traslado Transit -> Stock.
        - No se crean cantidades hechas en la preparación inicial.
        - La operación puede quedar en borrador/en espera/listo, pero nunca en hecho.
        - Si otro módulo intenta validarla durante esta etapa, se lanza excepción.
        """
        self.ensure_one()
        picking.ensure_one()

        ctx = self._tc_reception_safe_context()
        self._tc_assert_reception_can_be_rebuilt(picking)

        if resolved_lines is None:
            resolved_lines, source_location = self._get_reception_candidate_lines()
        else:
            if not resolved_lines:
                raise UserError(_("No hay líneas válidas para sincronizar."))
            source_location = resolved_lines[0]['quant'].location_id

        picking_type, dest_location = self._get_reception_operation_defaults(source_location)

        picking_vals = {}

        if picking.picking_type_id.id != picking_type.id:
            picking_vals['picking_type_id'] = picking_type.id

        if picking.location_id.id != source_location.id:
            picking_vals['location_id'] = source_location.id

        if picking.location_dest_id.id != dest_location.id:
            picking_vals['location_dest_id'] = dest_location.id

        if picking_vals:
            picking.with_context(ctx).write(picking_vals)

        reset_vals = {}

        if 'packing_list_imported' in picking._fields and picking.packing_list_imported:
            reset_vals['packing_list_imported'] = False

        if 'worksheet_imported' in picking._fields and picking.worksheet_imported:
            reset_vals['worksheet_imported'] = False

        if reset_vals:
            picking.with_context(ctx).write(reset_vals)

        product_totals = {}

        for item in resolved_lines:
            line = item['line']
            qty_to_receive = item.get('qty_to_receive', line.product_uom_qty)

            product_totals.setdefault(line.product_id.id, 0.0)
            product_totals[line.product_id.id] += qty_to_receive

        # CRÍTICO: en preparación inicial no deben existir move lines.
        # Si ya existían por intentos anteriores, se eliminan para reconstruir
        # la recepción física de forma limpia.
        if picking.move_line_ids:
            picking.move_line_ids.with_context(ctx).unlink()

        existing_moves = picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))

        for move in existing_moves:
            try:
                if move.state in ('assigned', 'partially_available') and hasattr(move, '_do_unreserve'):
                    move.with_context(ctx)._do_unreserve()
            except Exception as e:
                _logger.warning(
                    "[TC_RECEPTION_WARNING] No se pudo desreservar move %s antes de limpiar recepción física: %s",
                    move.id,
                    e,
                )

            move.with_context(ctx).unlink()

        moves_created = 0
        created_moves = self.env['stock.move']

        for product_id, total_qty in product_totals.items():
            product = self.env['product.product'].browse(product_id)

            move_vals = self._tc_prepare_reception_move_vals(
                picking=picking,
                product=product,
                total_qty=total_qty,
            )

            try:
                created_moves |= self.env['stock.move'].with_context(ctx).create(move_vals)
            except Exception as e:
                _logger.exception(
                    "[TC_RECEPTION_ERROR][MOVE_CREATE] "
                    "No se pudo crear stock.move | voyage=%s | picking=%s | "
                    "product=%s | qty=%s | vals=%s | error=%s",
                    self.name,
                    picking.name,
                    product.display_name,
                    total_qty,
                    move_vals,
                    str(e),
                )
                raise UserError(_(
                    "No se pudo crear la demanda de recepción para el producto:\n\n"
                    "%(product)s\n\n"
                    "Cantidad: %(qty)s\n"
                    "Origen: %(src)s\n"
                    "Destino: %(dest)s\n\n"
                    "Error técnico: %(error)s"
                ) % {
                    'product': product.display_name,
                    'qty': total_qty,
                    'src': picking.location_id.complete_name,
                    'dest': picking.location_dest_id.complete_name,
                    'error': str(e),
                })

            moves_created += 1

        # CRÍTICO:
        # No se confirma la demanda aquí. Confirmar el stock.move dispara
        # _action_assign() y, en esta instancia, stock_whole_lot_removal puede
        # reservar lotes automáticamente desde SOM/Transit. Ese intento de
        # reserva fue el origen del flujo que terminó dejando la recepción en
        # HECHO. El botón Recibir/Abrir Recepción debe crear demanda en borrador
        # y abrir el documento; el Packing List físico y el Worksheet son los
        # únicos pasos que deben construir las líneas operativas reales.
        if picking.move_line_ids:
            raise UserError(_(
                "Control Tower detuvo el flujo porque la recepción física %(picking)s "
                "generó líneas operativas durante la preparación.\n\n"
                "El botón Recibir/Abrir Recepción no debe reservar, asignar ni validar stock. "
                "Debe dejar la recepción abierta para procesar Packing List físico y Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
            })

        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("preparar la recepción física"),
        )

        total_qty = sum(product_totals.values())

        picking.message_post(
            body=_(
                "📦 Recepción física preparada desde Viaje %s.<br/>"
                "<b>Productos:</b> %s<br/>"
                "<b>Total esperado:</b> %.3f<br/>"
                "<b>Estado:</b> %s<br/><br/>"
                "La recepción quedó abierta sin confirmar, sin reservar y sin validar. "
                "Las líneas físicas se construirán únicamente al procesar el Packing List físico "
                "y el Worksheet."
            ) % (self.name, moves_created, total_qty, picking.state)
        )

        return picking

    def action_generate_reception(self):
        self.ensure_one()

        picking = self.reception_picking_id
        origin = f"{self.name} (Recepción Física)"

        # Recepción YA VALIDADA: solo se abre para consulta. El guard de
        # "no validar desde este botón" aplica a recepciones vivas; una hecha
        # es historia legítima, no un flujo forzado.
        if picking and picking.state == 'done':
            return self._tc_open_reception_action(picking)

        # Embarque multi-proforma: la recepción física se genera hasta que
        # TODAS las recepciones a tránsito que lo componen estén validadas
        # (su material debe existir en tránsito para poder recibirse junto).
        self._tc_assert_components_complete(_('generar la recepción física'))

        # Si la recepción ya existe y todavía no se procesó PL/Worksheet,
        # se puede sanear de forma segura. Esto corrige recepciones creadas por
        # versiones anteriores que quedaron confirmadas/asignadas o con líneas
        # automáticas al presionar Recibir. Si ya hay trabajo físico, solo se abre.
        if picking and picking.state != 'cancel':
            self._tc_assert_reception_can_stay_open(
                picking,
                operation_label=_("abrir la recepción física"),
            )

            if not self._tc_reception_has_locked_physical_work(picking):
                needs_rebuild = bool(
                    not picking.move_ids
                    or picking.move_line_ids
                    or picking.state != 'draft'
                )
                if needs_rebuild:
                    resolved_lines, _source_location = self._get_reception_candidate_lines()
                    self._sync_reception_picking_lines(
                        picking,
                        resolved_lines=resolved_lines,
                    )

            if self.custom_status != 'reception_pending':
                self.write({'custom_status': 'reception_pending'})
            return self._tc_open_reception_action(picking)

        resolved_lines, source_location = self._get_reception_candidate_lines()
        picking_type, dest_location = self._get_reception_operation_defaults(source_location)

        picking = self.env['stock.picking'].search([
            ('origin', '=', origin),
            ('company_id', '=', self.company_id.id),
            ('state', 'not in', ('done', 'cancel')),
            ('picking_type_code', '=', 'internal'),
        ], order='id desc', limit=1)

        if picking:
            self.write({
                'reception_picking_id': picking.id,
                'custom_status': 'reception_pending',
            })

            if not self._tc_reception_has_locked_physical_work(picking):
                needs_rebuild = bool(
                    not picking.move_ids
                    or picking.move_line_ids
                    or picking.state != 'draft'
                )
                if needs_rebuild:
                    self._sync_reception_picking_lines(
                        picking,
                        resolved_lines=resolved_lines,
                    )

            return self._tc_open_reception_action(picking)

        vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': origin,
            'company_id': self.company_id.id,
            'move_type': 'direct',
        }

        if 'supplier_bl_number' in self.env['stock.picking']._fields:
            vals['supplier_bl_number'] = self.bl_number

        if 'supplier_container_no' in self.env['stock.picking']._fields:
            vals['supplier_container_no'] = self.container_number

        if 'supplier_origin' in self.env['stock.picking']._fields:
            vals['supplier_origin'] = 'TRÁNSITO'

        picking = self.env['stock.picking'].with_context(
            self._tc_reception_safe_context()
        ).create(vals)

        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending',
        })

        self._sync_reception_picking_lines(
            picking,
            resolved_lines=resolved_lines,
        )

        return self._tc_open_reception_action(picking)

    def action_sync_reception_from_voyage(self):
        self.ensure_one()

        if not self.reception_picking_id:
            raise UserError(_("Primero debe generar la Recepción Física."))

        picking = self.reception_picking_id
        self._sync_reception_picking_lines(picking)

        return self._tc_open_reception_action(picking)

    def action_print_reception_labels(self):
        self.ensure_one()
        if not self.reception_picking_id and not self.line_ids:
            raise UserError(_("No hay recepción física ni lotes para imprimir."))
        return {
            'name': _('Imprimir Etiquetas'),
            'type': 'ir.actions.act_window',
            'res_model': 'transit.label.print.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_picking_id': self.reception_picking_id.id if self.reception_picking_id else False,
            }
        }

    def tc_register_label_print(self, label_format, label_count):
        """Bitácora de impresión de etiquetas del embarque.

        La llama el wizard de impresión con cada generación. NADIE pone
        'En Impresión' a mano: la primera impresión lo estampa sola. Un
        viaje ya Etiquetado no se degrada por reimprimir."""
        now = fields_module.Datetime.now()
        for rec in self:
            vals = {
                'tc_label_print_count': (rec.tc_label_print_count or 0) + 1,
                'tc_label_last_print_at': now,
            }
            if not rec.tc_label_first_print_at:
                vals['tc_label_first_print_at'] = now
            if rec.tc_labeling_status == 'none':
                vals['tc_labeling_status'] = 'printing'
            rec.write(vals)
            rec.message_post(body=Markup(
                '🏷️ <b>Impresión de etiquetas</b> #%s: %s etiqueta(s) '
                'formato <b>%s</b>.') % (
                    vals['tc_label_print_count'], label_count, label_format))

    def action_mark_labeled(self):
        """Check MANUAL de etiquetado terminado (verde). Exige que exista
        al menos una impresión: sin impresión no hay nada que pegar."""
        for rec in self:
            if not rec.tc_label_print_count:
                raise UserError(_(
                    'El embarque %s no tiene ninguna impresión de '
                    'etiquetas registrada. Imprime las etiquetas primero: '
                    'el paso En Impresión se marca solo.') % rec.name)
            rec.write({
                'tc_labeling_status': 'labeled',
                'tc_labeled_at': fields_module.Datetime.now(),
                'tc_labeled_by': self.env.user.id,
            })
            rec.message_post(body=Markup(
                '✅ <b>Etiquetado terminado</b>, confirmado por %s.')
                % self.env.user.name)

    def action_unmark_labeled(self):
        """Deshacer el check (vuelve a En Impresión, no borra la bitácora)."""
        for rec in self.filtered(lambda r: r.tc_labeling_status == 'labeled'):
            rec.write({
                'tc_labeling_status': 'printing',
                'tc_labeled_at': False,
                'tc_labeled_by': False,
            })
            rec.message_post(body=Markup(
                '↩️ Etiquetado desmarcado por %s.') % self.env.user.name)

    def _auto_finalize_after_reception(self):
        for rec in self:
            if rec.custom_status in ('delivered', 'cancel'):
                continue

            if not rec.reception_picking_id or rec.reception_picking_id.state != 'done':
                continue

            picking = rec.reception_picking_id
            if 'worksheet_imported' in picking._fields and not picking.worksheet_imported:
                raise UserError(_(
                    "No se puede cerrar automáticamente el embarque %(voyage)s porque la recepción física %(picking)s "
                    "está en HECHO sin Worksheet procesado. Revise la automatización que validó la recepción."
                ) % {
                    'voyage': rec.name,
                    'picking': picking.name,
                })

            write_vals = {
                'arrival_date': fields_module.Date.today(),
                'custom_status': 'delivered',
            }

            if not rec.arrival_date_bodega:
                write_vals['arrival_date_bodega'] = fields_module.Date.today()

            rec.write(write_vals)

            for line in rec.line_ids.filtered(lambda l: l.allocation_id):
                qty_received = rec._normalize_product_qty(
                    line.product_id,
                    line.quant_id.quantity if line.quant_id and line.quant_id.exists() else line.product_uom_qty,
                )

                if line.allocation_id.state != 'done' and not float_is_zero(
                    qty_received,
                    precision_rounding=rec._get_qty_rounding(line.product_id),
                ):
                    line.allocation_id.action_mark_received(qty_received)

            rec.message_post(
                body=_("✅ Viaje cerrado automáticamente al validar la recepción física %s.")
                % (rec.reception_picking_id.name,)
            )

    def action_arrive(self):
        self.ensure_one()

        if self.reception_picking_id and self.reception_picking_id.state != 'done':
            raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))

        if self.reception_picking_id:
            self._auto_finalize_after_reception()
            return

        # Sin recepción física NO se marca Entregado ni se dan por recibidas
        # las allocations: se crea la recepción y el viaje queda LISTO PARA
        # RECIBIR. El cierre real llega al VALIDAR la recepción.
        self.action_generate_reception()

    def action_cancel(self):
        """Cancelar el viaje LIBERA todo lo comprometido.

        Antes solo escribía el estado: las líneas reservadas conservaban
        pedido/cliente, las allocations seguían 'pending' reservando material
        futuro, y los quants publicados seguían visibles en Inventario Visual
        — material fantasma comprometido a pedidos que jamás llegaría.
        """
        for voyage in self:
            reserved_lines = voyage.line_ids.filtered(
                lambda l: l.order_id or l.partner_id or l.allocation_id
            )
            allocations = reserved_lines.mapped('allocation_id')

            for line in reserved_lines:
                try:
                    line._execute_release_logic()
                except Exception:
                    _logger.exception(
                        '[TC_VOYAGE_CANCEL] Fallo liberando la línea %s del viaje %s.',
                        line.id, voyage.name,
                    )

            if reserved_lines:
                reserved_lines.with_context(
                    skip_reservation_logic=True,
                    skip_transit_publication_sync=True,
                ).write({
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'notes': 'Liberado por cancelación del viaje %s' % voyage.name,
                })

            pending_allocations = allocations.filtered(
                lambda a: a.state in ('pending', 'in_transit')
            )
            if pending_allocations:
                pending_allocations.write({'state': 'cancelled'})

            # Despublicar: el material de un viaje cancelado no puede seguir
            # apareciendo como inventario en tránsito disponible/committed.
            published_quants = self.env['stock.quant'].sudo().search([
                ('transit_voyage_id', '=', voyage.id),
                ('transit_inventory_published', '=', True),
            ])
            if published_quants:
                published_quants.write({
                    'transit_inventory_published': False,
                    'transit_inventory_state': False,
                    'transit_voyage_id': False,
                    'transit_line_id': False,
                })
            voyage.line_ids.filtered('inventory_published').with_context(
                skip_reservation_logic=True,
                skip_transit_publication_sync=True,
            ).write({'inventory_published': False})

        self.write({'custom_status': 'cancel'})

    def _has_valid_container(self):
        self.ensure_one()

        has_container = self.env['supplier.shipment.container'].search_count([
            ('shipment_id.voyage_id', '=', self.id),
            ('container_number', '!=', False),
        ])

        if not has_container:
            has_container = any(
                line.container_number
                and line.container_number not in ('PENDIENTE', 'SN', 'False', '')
                for line in self.line_ids
            )

        return bool(has_container)

    def _needs_shipsgo_sync(self):
        self.ensure_one()

        if self.custom_status in ('arrived_port', 'reception_pending', 'delivered', 'cancel'):
            return False

        if not self.shipsgo_last_sync:
            return True

        delta = fields_module.Datetime.now() - self.shipsgo_last_sync
        return delta.total_seconds() > 7200

    @api.model
    def action_cron_sync_shipsgo(self):
        voyages = self.search([
            ('custom_status', 'not in', ['arrived_port', 'reception_pending', 'delivered', 'cancel']),
        ])

        for voyage in voyages:
            if not voyage._has_valid_container():
                continue

            try:
                voyage.action_sync_shipsgo()
            except Exception as e:
                _logger.warning(
                    "[ShipsGo CRON] Error sincronizando viaje %s: %s",
                    voyage.name,
                    str(e),
                )

    def web_read(self, specification):
        result = super().web_read(specification)

        if len(self) != 1:
            return result

        if self.env.context.get('no_auto_shipsgo_sync'):
            return result

        voyage = self

        if not voyage._needs_shipsgo_sync():
            return result

        if not voyage._has_valid_container():
            return result

        try:
            voyage.with_context(no_auto_shipsgo_sync=True).action_sync_shipsgo()
            result = super(StockTransitVoyage, voyage).web_read(specification)
        except Exception as e:
            _logger.warning(
                "[ShipsGo AUTO] Error en auto-sync al abrir viaje %s: %s",
                voyage.name,
                str(e),
            )

        return result