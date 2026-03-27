# -*- coding: utf-8 -*-
import logging
import requests
import json
import re
from markupsafe import Markup
from odoo import models, api, _
from odoo import fields as fields_module   # alias para evitar colisión con param 'fields' en read()
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_compare, float_is_zero

# Re-exportar fields para que el resto del código del módulo funcione igual
fields = fields_module

_logger = logging.getLogger(__name__)

# Intentar importar folium; si no está, se genera HTML básico
try:
    import folium
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False
    _logger.warning("Folium no está instalado. pip install folium --break-system-packages")


class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields_module.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))

    custom_status = fields_module.Selection([
        ('solicitud', 'Solicitud Enviada'),
        ('production', 'Producción'),
        ('booking', 'Booking'),
        ('puerto_origen', 'Puerto Origen'),
        ('on_sea', 'En Altamar / Mar'),
        ('puerto_destino', 'Puerto Destino'),
        ('arrived_port', 'Arribo a Puerto (Trámite)'),
        ('reception_pending', 'En Recepción Física'),
        ('delivered', 'Entregado en Almacén'),
        ('cancel', 'Cancelado'),
    ], string='Estado', default='solicitud', tracking=True)

    shipping_line = fields_module.Char(string='Naviera', tracking=True)
    transit_days_expected = fields_module.Integer(string='Tiempo Tránsito (Días)')
    vessel_name = fields_module.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields_module.Char(string='No. Viaje', tracking=True)

    container_number = fields_module.Char(
        string='Contenedores',
        compute='_compute_container_number',
        store=True,
        tracking=True,
        help="Resumen automático de contenedores presentes en las líneas del viaje"
    )

    bl_number = fields_module.Char(string='Folio Compra / BL', tracking=True)

    etd = fields_module.Date(string='ETD (Salida Estimada)')
    eta = fields_module.Date(string='ETA (Llegada Estimada)', required=False, tracking=True)
    eta_original = fields_module.Date(string='ETA Original', readonly=True, copy=False, tracking=True)

    delay_days = fields_module.Integer(
        string='Días de Retraso',
        compute='_compute_delay_days',
        store=True
    )

    eta_alert_level = fields_module.Selection([
        ('ok', 'En Tiempo'),
        ('warning', 'Próximo a Vencer'),
        ('danger', 'Vencido'),
        ('done', 'Entregado'),
    ], string='Alerta ETA', compute='_compute_eta_alert', store=True)

    arrival_date = fields_module.Date(string='Llegada Real', tracking=True)
    arrival_date_bodega = fields_module.Date(string='Entregado en Bodega', tracking=True)

    picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción (Tránsito)',
        domain=[('picking_type_code', '=', 'incoming')]
    )

    reception_picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')],
        readonly=True
    )

    purchase_id = fields_module.Many2one('purchase.order', string='Orden de Compra Origen', readonly=True)

    company_id = fields_module.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    line_ids = fields_module.One2many('stock.transit.line', 'voyage_id', string='Contenido (Lotes)')

    total_m2 = fields_module.Float(string='Total m²', compute='_compute_totals', store=True, compute_sudo=True)
    allocated_m2 = fields_module.Float(string='Asignado m²', compute='_compute_totals', store=True, compute_sudo=True)
    allocation_percent = fields_module.Float(string='% Asignación', compute='_compute_allocation_percent', store=False, compute_sudo=False)

    # =========================================================================
    # SHIPSGO & TRACKING FIELDS
    # =========================================================================
    shipsgo_last_sync = fields_module.Datetime(string="Última Sincronización API", readonly=True)
    shipsgo_payload = fields_module.Text(string="Datos Geoespaciales (JSON)", readonly=True)

    shipsgo_map_html = fields_module.Html(
        string="Mapa de Seguimiento",
        sanitize=False,
        readonly=True,
        help="Mapa interactivo generado por Folium con la ruta del contenedor."
    )

    transit_progress = fields_module.Integer(
        string='Progreso Viaje',
        compute='_compute_transit_progress',
        store=True,
        readonly=False
    )

    # =========================================================================
    # HELPERS SHIPSGO
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
                shipment_container=shipment_container
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
        reference = self._make_shipsgo_reference(container_ref, shipment_container=shipment_container)

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
            raise UserError(_("ShipsGo devolvió un error al crear el tracking (%s): %s") % (r.status_code, message or r.text))

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

    # =========================================================================
    # HELPER: Limpieza de Coordenadas
    # =========================================================================
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

    # =========================================================================
    # GENERADOR DE MAPA CON FOLIUM
    # =========================================================================
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

        past_lines = route.get('past', [])
        for line_coords in past_lines:
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

        future_lines = route.get('future', [])
        for line_coords in future_lines:
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#9ca3af',
                    weight=3,
                    opacity=0.5,
                    dash_array='8 10',
                ).add_to(m)

        if not past_lines and not current_past and not current_future and not future_lines:
            if origin_loc and current_loc:
                folium.PolyLine(
                    locations=[origin_loc, current_loc],
                    color='#2563eb',
                    weight=4,
                    opacity=0.85,
                ).add_to(m)
            if current_loc and dest_loc:
                folium.PolyLine(
                    locations=[current_loc, dest_loc],
                    color='#6b7280',
                    weight=3,
                    dash_array='8 10',
                    opacity=0.65,
                ).add_to(m)
            elif origin_loc and dest_loc and not current_loc:
                folium.PolyLine(
                    locations=[origin_loc, dest_loc],
                    color='#9ca3af',
                    weight=3,
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

        if origin_loc and current_loc:
            markers_js += f"""
            L.polyline([[{origin_loc[0]},{origin_loc[1]}],[{current_loc[0]},{current_loc[1]}]],
                {{color:'#2563eb',weight:4,opacity:0.85}}).add_to(map);
            """
        if current_loc and dest_loc:
            markers_js += f"""
            L.polyline([[{current_loc[0]},{current_loc[1]}],[{dest_loc[0]},{dest_loc[1]}]],
                {{color:'#6b7280',weight:3,dashArray:'8,10',opacity:0.65}}).addTo(map);
            """

        bounds_js += """
        if(bounds.length > 1) map.fitBounds(bounds, {padding:[50,50], maxZoom:8});
        else if(bounds.length === 1) map.setView(bounds[0], 5);
        """

        html = f"""
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
        return html

    # =========================================================================
    # ACCIÓN: SINCRONIZAR SHIPSGO API
    # =========================================================================

    def action_sync_shipsgo(self):
        """
        Sincroniza datos de ShipsGo para este viaje.
        Llamado por: cron jobs, web_read (auto-sync al abrir formulario).
        """
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

        # ============================================================
        # 1) Resolver contenedor principal del mapa
        # ============================================================
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
                container_ref = self._normalize_container_number(str(self.container_number).split(',')[0].strip())

            if not container_ref:
                raise UserError(_("No se encontró un número de contenedor válido en las líneas o en el embarque vinculado."))

            shipment_data = self._find_shipsgo_shipment_by_container(container_ref) or {}
            shipment_id = shipment_data.get('id')
            if not shipment_id:
                shipment_data = self._create_or_link_shipsgo_tracking_for_container(container_ref)
                shipment_id = shipment_data.get('id')

        # ============================================================
        # 2) Obtener detalle del shipment por ID
        # ============================================================
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
            if linked_container:
                linked_container.write({
                    'shipsgo_last_sync': fields_module.Datetime.now(),
                    'shipsgo_last_error': False,
                })
            return

        shipment_id = shipment_data.get('id') or shipment_id

        # ============================================================
        # 3) GeoJSON del shipment
        # ============================================================
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

        # ============================================================
        # 4) Datos base del shipment
        # ============================================================
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

        # ============================================================
        # 5) Parsear GeoJSON
        # ============================================================
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

        self.write(vals)

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
        warning_days = 7
        for rec in self:
            if rec.custom_status == 'delivered':
                rec.eta_alert_level = 'done'
            elif not rec.eta:
                rec.eta_alert_level = 'ok'
            elif today > rec.eta:
                rec.eta_alert_level = 'danger'
            elif (rec.eta - today).days <= warning_days:
                rec.eta_alert_level = 'warning'
            else:
                rec.eta_alert_level = 'ok'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')
            vals.pop('container_number', None)
            if vals.get('eta') and not vals.get('eta_original'):
                vals['eta_original'] = vals['eta']
        return super(StockTransitVoyage, self).create(vals_list)

    def write(self, vals):
        if 'eta' in vals:
            for rec in self:
                if not rec.eta_original and vals.get('eta'):
                    super(StockTransitVoyage, rec).write({'eta_original': vals['eta']})

        res = super().write(vals)

        if 'custom_status' in vals or 'eta' in vals:
            transit_lines = self.mapped('line_ids')
            order_ids = transit_lines.mapped('order_id')
            if order_ids:
                sol = self.env['sale.order.line'].search([
                    ('order_id', 'in', order_ids.ids),
                    ('auto_transit_assign', '=', True),
                ])
                sol._compute_transit_info()

        if 'eta' in vals or 'custom_status' in vals:
            self._check_eta_alerts()

        return res

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status')
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('product_uom_qty'))
            allocated = sum(
                rec.line_ids.filtered(lambda l: l.allocation_status == 'reserved').mapped('product_uom_qty')
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

    @api.depends('etd', 'eta', 'custom_status', 'create_date', 'shipsgo_payload')
    def _compute_transit_progress(self):
        today = fields_module.Date.today()
        for rec in self:
            if rec.shipsgo_payload:
                continue

            if rec.custom_status == 'delivered':
                rec.transit_progress = 100
                continue
            if rec.custom_status == 'cancel':
                rec.transit_progress = 0
                continue

            start_date = rec.etd or (rec.create_date.date() if rec.create_date else False)
            if not start_date or not rec.eta:
                rec.transit_progress = 0
                continue

            if today < start_date:
                rec.transit_progress = 0
            elif today > rec.eta:
                rec.transit_progress = 95
            else:
                total_days = (rec.eta - start_date).days
                elapsed = (today - start_date).days
                if total_days > 0:
                    progress = int((elapsed / total_days) * 100)
                    rec.transit_progress = max(0, min(95, progress))
                else:
                    rec.transit_progress = 0

    # =========================================================================
    # NOTIFICACIONES DE ALERTA ETA
    # =========================================================================

    def _check_eta_alerts(self):
        for rec in self:
            if rec.eta_alert_level not in ('warning', 'danger'):
                continue
            if rec.custom_status == 'delivered':
                continue

            responsible = False
            if rec.purchase_id and rec.purchase_id.user_id:
                responsible = rec.purchase_id.user_id

            if not responsible:
                followers = rec.message_partner_ids
                if not followers:
                    continue
                level_label = 'VENCIDO' if rec.eta_alert_level == 'danger' else 'PRÓXIMO A VENCER'
                eta_str = rec.eta.strftime('%d/%m/%Y') if rec.eta else '—'
                body = Markup(
                    "⚠️ <b>Alerta ETA %s</b><br/>"
                    "El embarque <b>%s</b> tiene ETA %s y está en estado <b>%s</b>."
                ) % (level_label, rec.name, eta_str, rec.custom_status)
                rec.message_post(
                    body=body,
                    partner_ids=followers.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
                continue

            level_label = 'VENCIDO' if rec.eta_alert_level == 'danger' else 'PRÓXIMO A VENCER'
            eta_str = rec.eta.strftime('%d/%m/%Y') if rec.eta else '—'
            body = Markup(
                "⚠️ <b>Alerta ETA %s</b><br/>"
                "El embarque <b>%s</b> tiene ETA %s y está en estado <b>%s</b>."
            ) % (level_label, rec.name, eta_str, rec.custom_status)
            rec.message_post(
                body=body,
                partner_ids=responsible.partner_id.ids,
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )

    # =========================================================================
    # MÉTODOS DE ESTADO (WIZARD)
    # =========================================================================

    STATUS_SEQUENCE = [
        'solicitud', 'production', 'booking', 'puerto_origen',
        'on_sea', 'puerto_destino', 'arrived_port', 'reception_pending', 'delivered',
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

    def action_advance_status(self):
        self.ensure_one()
        if self.custom_status in ('delivered', 'cancel'):
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'transit.status.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_direction': 'advance',
            },
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

        if next_status == 'delivered':
            if self.reception_picking_id and self.reception_picking_id.state != 'done':
                raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))
            if self.reception_picking_id:
                self._auto_finalize_after_reception()
            else:
                write_vals = {
                    'arrival_date': fields_module.Date.today(),
                    'custom_status': 'delivered',
                }
                if not self.arrival_date_bodega:
                    write_vals['arrival_date_bodega'] = fields_module.Date.today()
                self.write(write_vals)
                for line in self.line_ids:
                    if line.allocation_id and line.allocation_id.state != 'done':
                        line.allocation_id.action_mark_received(line.product_uom_qty)
        else:
            if next_status == 'on_sea':
                if self.picking_id and self.picking_id.purchase_id:
                    allocations = self.env['purchase.order.line.allocation'].search([
                        ('purchase_order_id', '=', self.picking_id.purchase_id.id),
                        ('state', '=', 'pending')
                    ])
                    allocations.action_mark_in_transit()
            self.write({'custom_status': next_status})

        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(self.custom_status, self.custom_status)
        msg_parts = [Markup("⏩ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)]
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

        msg_parts = [Markup("⏪ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)]
        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)
        self.message_post(body=Markup('').join(msg_parts))

    # =========================================================================
    # ACCIONES DE CARGA Y RECEPCIÓN
    # =========================================================================

    # =========================================================================
    # HELPERS DE CANTIDAD / PRECISIÓN
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
        return float_compare(qty_a or 0.0, qty_b or 0.0, precision_rounding=rounding) != 0

    def action_load_from_purchase(self):
        self.ensure_one()
        if not self.purchase_id:
            return

        existing_alloc_ids = self.line_ids.mapped('allocation_id.id')

        allocations = self.env['purchase.order.line.allocation'].search([
            ('purchase_order_id', '=', self.purchase_id.id),
            ('id', 'not in', existing_alloc_ids)
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

        existing_stock_lines = self.line_ids.filtered(lambda l: not l.allocation_id and not l.partner_id and not l.order_id)
        existing_stock_by_product = {l.product_id.id: l for l in existing_stock_lines}

        for po_line in self.purchase_id.order_line:
            total_po_qty = po_line.product_qty
            total_allocated = sum(po_line.allocation_ids.mapped('quantity'))
            extra_for_stock = total_po_qty - total_allocated
            product_id = po_line.product_id.id

            if product_id in existing_stock_by_product:
                existing_line = existing_stock_by_product[product_id]
                if extra_for_stock > 0:
                    if existing_line.product_uom_qty != extra_for_stock:
                        existing_line.write({'product_uom_qty': extra_for_stock})
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
        self.ensure_one()
        if not self.picking_id:
            return

        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)
        if placeholder_lines:
            placeholder_lines.unlink()

        existing_by_lot = {line.lot_id.id: line for line in self.line_ids if line.lot_id}

        from .utils.transit_manager import TransitManager
        purchase = self.picking_id.purchase_id
        allocations_map = {}
        allocation_consumed = {}

        if purchase:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', purchase.id),
                ('state', 'not in', ['done', 'cancelled'])
            ], order='id asc')
            for alloc in allocations:
                if alloc.product_id.id not in allocations_map:
                    allocations_map[alloc.product_id.id] = []
                allocations_map[alloc.product_id.id].append(alloc)
                allocation_consumed[alloc.id] = 0.0

        lines_to_create = []
        hold_orders_map = {}

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue

            lot_id = move_line.lot_id.id
            product_id = move_line.product_id.id

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id),
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id)
            ], limit=1)

            raw_qty_done = move_line.quantity
            qty_done = self._normalize_product_qty(
                move_line.product_id,
                found_quant.quantity if found_quant else raw_qty_done
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
                existing_line = existing_by_lot[lot_id]
                update_vals = {}
                if self._qty_differs(move_line.product_id, existing_line.product_uom_qty, qty_done):
                    update_vals['product_uom_qty'] = qty_done
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
                    self.env['stock.transit.line'].browse(existing_line.id).with_context(skip_reservation_logic=True).write(update_vals)
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
                if key not in hold_orders_map:
                    hold_orders_map[key] = {
                        'partner': partner_to_assign,
                        'order': order_to_assign,
                        'line_vals_indices': []
                    }
                hold_orders_map[key]['line_vals_indices'].append(len(lines_to_create) - 1)

        created_lines = self.env['stock.transit.line']
        if lines_to_create:
            created_lines = self.env['stock.transit.line'].create(lines_to_create)

        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)
                new_received = alloc.qty_received + qty_consumed
                alloc.write({'qty_received': min(new_received, alloc.quantity), 'state': 'in_transit'})

        for key, data in hold_orders_map.items():
            partner = data['partner']
            order = data['order']
            indices = data['line_vals_indices']
            relevant_lines = [created_lines[i] for i in indices if i < len(created_lines)]
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
                TransitManager.reassign_lot(self.env, line, partner, order, notes=False, hold_order_obj=hold_order)

            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()


    # =========================================================================
    # HELPERS RECEPCIÓN FÍSICA
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

        for line in candidate_lines:
            quant = line.quant_id

            quant_is_valid = bool(
                quant
                and quant.exists()
                and quant.product_id.id == line.product_id.id
                and quant.lot_id.id == line.lot_id.id
                and quant.quantity > 0
                and quant.location_id.usage == 'transit'
                and quant.company_id.id == self.company_id.id
            )

            if not quant_is_valid:
                quant = Quant.search([
                    ('company_id', '=', self.company_id.id),
                    ('lot_id', '=', line.lot_id.id),
                    ('product_id', '=', line.product_id.id),
                    ('quantity', '>', 0),
                    ('location_id.usage', '=', 'transit'),
                ], order='id desc', limit=1)

                if quant:
                    line.with_context(skip_reservation_logic=True).write({
                        'quant_id': quant.id,
                    })

            if not quant:
                missing_lots.append(
                    "%s (%.3f)" % (line.lot_id.display_name, line.product_uom_qty)
                )
                continue

            qty_to_receive = self._normalize_product_qty(line.product_id, quant.quantity)
            if float_is_zero(qty_to_receive, precision_rounding=self._get_qty_rounding(line.product_id)):
                missing_lots.append(
                    "%s (quant cero efectivo)" % (line.lot_id.display_name,)
                )
                continue

            if self._qty_differs(line.product_id, line.product_uom_qty, qty_to_receive):
                line.with_context(skip_reservation_logic=True).write({
                    'product_uom_qty': qty_to_receive,
                })

            source_location_ids.add(quant.location_id.id)
            resolved_lines.append({
                'line': line,
                'quant': quant,
                'qty_to_receive': qty_to_receive,
            })

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

    def _sync_reception_picking_lines(self, picking, resolved_lines=None):
        self.ensure_one()
        picking.ensure_one()

        if picking.state == 'done':
            raise UserError(_("La recepción ya fue validada."))
        if picking.state == 'cancel':
            raise UserError(_("La recepción está cancelada. Debe generar una nueva."))

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
            picking.write(picking_vals)

        product_totals = {}
        for item in resolved_lines:
            line = item['line']
            qty_to_receive = item.get('qty_to_receive', line.product_uom_qty)
            product_totals.setdefault(line.product_id.id, 0.0)
            product_totals[line.product_id.id] += qty_to_receive

        move_map = {}
        existing_moves = picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))

        for move in existing_moves:
            total_qty = product_totals.get(move.product_id.id, 0.0)

            if total_qty <= 0:
                move.unlink()
                continue

            move_vals = {}
            if self._qty_differs(move.product_id, move.product_uom_qty, total_qty):
                move_vals['product_uom_qty'] = total_qty
            if move.location_id.id != picking.location_id.id:
                move_vals['location_id'] = picking.location_id.id
            if move.location_dest_id.id != picking.location_dest_id.id:
                move_vals['location_dest_id'] = picking.location_dest_id.id
            if move_vals:
                move.write(move_vals)

            move_map[move.product_id.id] = move

        for product_id, total_qty in product_totals.items():
            if product_id in move_map:
                continue

            product = self.env['product.product'].browse(product_id)
            move = self.env['stock.move'].create({
                'picking_id': picking.id,
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'product_uom_qty': total_qty,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'company_id': self.company_id.id,
            })
            move_map[product_id] = move

        draft_moves = picking.move_ids.filtered(lambda m: m.state == 'draft')
        if draft_moves:
            draft_moves._action_confirm()

        picking.move_line_ids.unlink()

        lines_created = 0
        for item in resolved_lines:
            line = item['line']
            quant = item['quant']
            move = move_map.get(line.product_id.id)

            if not move:
                raise UserError(_(
                    "No se encontró movimiento para el producto %s."
                ) % line.product_id.display_name)

            qty_to_receive = item.get('qty_to_receive', line.product_uom_qty)
            self.env['stock.move.line'].create({
                'picking_id': picking.id,
                'move_id': move.id,
                'company_id': self.company_id.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_id.uom_id.id,
                'lot_id': line.lot_id.id,
                'location_id': quant.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'quantity': qty_to_receive,
            })
            lines_created += 1

        expected_lines = len(resolved_lines)
        if lines_created != expected_lines:
            raise UserError(_(
                "Se intentaron sincronizar %s lotes pero solo se crearon %s move lines."
            ) % (expected_lines, lines_created))

        total_qty = sum(product_totals.values())
        picking.message_post(
            body=_("🔄 %s lotes sincronizados desde Viaje %s. Total: %.3f")
            % (lines_created, self.name, total_qty)
        )

        if hasattr(picking, 'packing_list_imported') and not picking.packing_list_imported:
            picking.write({'packing_list_imported': True})

        return picking

    def action_generate_reception(self):
        self.ensure_one()

        if self.reception_picking_id and self.reception_picking_id.state == 'done':
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.reception_picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

        resolved_lines, source_location = self._get_reception_candidate_lines()
        picking_type, dest_location = self._get_reception_operation_defaults(source_location)

        picking = self.reception_picking_id
        if picking and picking.state == 'cancel':
            picking = False

        if not picking:
            picking = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': source_location.id,
                'location_dest_id': dest_location.id,
                'origin': f"{self.name} (Recepción Física)",
                'company_id': self.company_id.id,
                'move_type': 'direct',
                'supplier_bl_number': self.bl_number if hasattr(self.env['stock.picking'], 'supplier_bl_number') else False,
                'supplier_container_no': self.container_number if hasattr(self.env['stock.picking'], 'supplier_container_no') else False,
                'supplier_origin': 'TRÁNSITO' if hasattr(self.env['stock.picking'], 'supplier_origin') else False,
            })

        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending',
        })

        self._sync_reception_picking_lines(picking, resolved_lines=resolved_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_sync_reception_from_voyage(self):
        self.ensure_one()

        if not self.reception_picking_id:
            raise UserError(_("Primero debe generar la Recepción Física."))

        picking = self.reception_picking_id
        self._sync_reception_picking_lines(picking)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _auto_finalize_after_reception(self):
        for rec in self:
            if rec.custom_status in ('delivered', 'cancel'):
                continue

            if not rec.reception_picking_id or rec.reception_picking_id.state != 'done':
                continue

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
                    line.quant_id.quantity if line.quant_id and line.quant_id.exists() else line.product_uom_qty
                )
                if line.allocation_id.state != 'done' and not float_is_zero(
                    qty_received, precision_rounding=rec._get_qty_rounding(line.product_id)
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

        write_vals = {
            'arrival_date': fields_module.Date.today(),
            'custom_status': 'delivered'
        }
        if not self.arrival_date_bodega:
            write_vals['arrival_date_bodega'] = fields_module.Date.today()
        self.write(write_vals)

        for line in self.line_ids:
            if line.allocation_id and line.allocation_id.state != 'done':
                line.allocation_id.action_mark_received(line.product_uom_qty)

    def action_cancel(self):
        self.write({'custom_status': 'cancel'})

    # =========================================================================
    # HELPER INTERNO: verificar si el viaje tiene contenedor registrado
    # =========================================================================

    def _has_valid_container(self):
        """Retorna True si el viaje tiene al menos un contenedor válido registrado."""
        self.ensure_one()
        has_container = self.env['supplier.shipment.container'].search_count([
            ('shipment_id.voyage_id', '=', self.id),
            ('container_number', '!=', False),
        ])
        if not has_container:
            has_container = any(
                line.container_number and line.container_number not in ('PENDIENTE', 'SN', 'False', '')
                for line in self.line_ids
            )
        return bool(has_container)

    def _needs_shipsgo_sync(self):
        """Retorna True si el viaje requiere sincronización con ShipsGo."""
        self.ensure_one()
        if self.custom_status in ('delivered', 'cancel'):
            return False
        if not self.shipsgo_last_sync:
            return True
        delta = fields_module.Datetime.now() - self.shipsgo_last_sync
        return delta.total_seconds() > 7200  # 2 horas

    # =========================================================================
    # CRON & AUTO-SYNC
    # =========================================================================

    @api.model
    def action_cron_sync_shipsgo(self):
        """
        Ejecutado por los cron jobs (cada 2h y diario a las 5am).
        Sincroniza todos los viajes activos que tengan contenedores registrados.
        """
        voyages = self.search([
            ('custom_status', 'not in', ['delivered', 'cancel']),
        ])
        for voyage in voyages:
            if not voyage._has_valid_container():
                continue
            try:
                voyage.action_sync_shipsgo()
            except Exception as e:
                _logger.warning(
                    "[ShipsGo CRON] Error sincronizando viaje %s: %s",
                    voyage.name, str(e)
                )

    # =========================================================================
    # AUTO-SYNC AL ABRIR EL FORMULARIO — Odoo 18/19
    # web_read es el método que realmente llama el cliente OWL al abrir un form.
    # read() ya no se dispara de forma confiable en v18/v19.
    # =========================================================================

    def web_read(self, specification):
        """
        Override de web_read para disparar auto-sync de ShipsGo al abrir el formulario.
        En Odoo 18/19 el cliente OWL llama web_read, no read().
        Solo sincroniza si:
        - Es exactamente 1 registro (apertura de formulario individual)
        - El viaje no está en estado terminal (delivered/cancel)
        - Tiene contenedor registrado
        - No se sincronizó en las últimas 2 horas
        """
        result = super().web_read(specification)

        # Solo procesar apertura de un formulario individual
        if len(self) != 1:
            return result

        # Evitar recursión
        if self.env.context.get('no_auto_shipsgo_sync'):
            return result

        voyage = self

        if not voyage._needs_shipsgo_sync():
            return result

        if not voyage._has_valid_container():
            return result

        try:
            voyage.with_context(no_auto_shipsgo_sync=True).action_sync_shipsgo()
            # Re-leer para que el cliente reciba el mapa y datos frescos
            result = super(StockTransitVoyage, voyage).web_read(specification)
        except Exception as e:
            _logger.warning(
                "[ShipsGo AUTO] Error en auto-sync al abrir viaje %s: %s",
                voyage.name, str(e)
            )

        return result