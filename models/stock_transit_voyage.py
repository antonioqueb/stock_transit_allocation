# -*- coding: utf-8 -*-
import logging
import requests
import json
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError

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

    name = fields.Char(string='Referencia Viaje', required=True, copy=False, readonly=True, default=lambda self: _('Nuevo'))
    
    custom_status = fields.Selection([
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
    
    shipping_line = fields.Char(string='Naviera', tracking=True)
    transit_days_expected = fields.Integer(string='Tiempo Tránsito (Días)')
    vessel_name = fields.Char(string='Buque / Barco', tracking=True)
    voyage_number = fields.Char(string='No. Viaje', tracking=True)
    
    container_number = fields.Char(
        string='Contenedores', 
        compute='_compute_container_number',
        store=True,
        tracking=True,
        help="Resumen automático de contenedores presentes en las líneas del viaje"
    )
    
    bl_number = fields.Char(string='Folio Compra / BL', tracking=True)
    
    etd = fields.Date(string='ETD (Salida Estimada)')
    eta = fields.Date(string='ETA (Llegada Estimada)', required=False, tracking=True)
    eta_original = fields.Date(string='ETA Original', readonly=True, copy=False, tracking=True)
    
    delay_days = fields.Integer(
        string='Días de Retraso',
        compute='_compute_delay_days',
        store=True
    )
    
    eta_alert_level = fields.Selection([
        ('ok',      'En Tiempo'),
        ('warning', 'Próximo a Vencer'),
        ('danger',  'Vencido'),
        ('done',    'Entregado'),
    ], string='Alerta ETA', compute='_compute_eta_alert', store=True)

    arrival_date = fields.Date(string='Llegada Real', tracking=True)
    arrival_date_bodega = fields.Date(string='Entregado en Bodega', tracking=True)

    picking_id = fields.Many2one('stock.picking', string='Recepción (Tránsito)', 
        domain=[('picking_type_code', '=', 'incoming')])
    
    reception_picking_id = fields.Many2one('stock.picking', string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')], readonly=True)

    purchase_id = fields.Many2one('purchase.order', string='Orden de Compra Origen', readonly=True)
    
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    line_ids = fields.One2many('stock.transit.line', 'voyage_id', string='Contenido (Lotes)')
    
    total_m2 = fields.Float(string='Total m²', compute='_compute_totals', store=True)
    allocated_m2 = fields.Float(string='Asignado m²', compute='_compute_totals', store=True)
    allocation_percent = fields.Float(string='% Asignación', compute='_compute_totals')
    
    # =========================================================================
    # SHIPSGO & TRACKING FIELDS
    # =========================================================================
    shipsgo_last_sync = fields.Datetime(string="Última Sincronización API", readonly=True)
    shipsgo_payload = fields.Text(string="Datos Geoespaciales (JSON)", readonly=True)
    
    # NUEVO: Campo HTML generado por Folium (reemplaza el widget JS de Leaflet)
    shipsgo_map_html = fields.Html(
        string="Mapa de Seguimiento",
        sanitize=False,
        readonly=True,
        help="Mapa interactivo generado por Folium con la ruta del contenedor."
    )
    
    transit_progress = fields.Integer(
        string='Progreso Viaje', 
        compute='_compute_transit_progress', 
        store=True, 
        readonly=False
    )

    # =========================================================================
    # HELPER: Limpieza de Coordenadas
    # =========================================================================
    def _clean_coord(self, lat, lng):
        """Convierte coordenadas a float y valida que no sean 0 o None"""
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
        """
        Genera un mapa HTML interactivo usando Folium a partir de los datos de ShipsGo.
        Retorna el HTML como string listo para guardar en shipsgo_map_html.
        """
        if not HAS_FOLIUM:
            return self._generate_fallback_map_html(map_data)

        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')
        
        # Determinar centro y zoom
        all_points = []
        if origin_loc and len(origin_loc) == 2:
            all_points.append(origin_loc)
        if dest_loc and len(dest_loc) == 2:
            all_points.append(dest_loc)
        if current_loc and len(current_loc) == 2:
            all_points.append(current_loc)
        
        if not all_points:
            center = [20, -40]
            zoom = 2
        elif len(all_points) == 1:
            center = all_points[0]
            zoom = 5
        else:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lng = sum(p[1] for p in all_points) / len(all_points)
            center = [avg_lat, avg_lng]
            zoom = 3

        # Crear mapa
        m = folium.Map(
            location=center,
            zoom_start=zoom,
            tiles='cartodbpositron',
            width='100%',
            height='100%',
        )

        # Marcador de origen
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

        # Marcador de destino
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

        # Marcador de posición actual del barco
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
            # Icono personalizado del barco
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

        # Líneas de ruta
        route = map_data.get('route', {})

        # Rutas pasadas (gris sólido)
        past_lines = route.get('past', [])
        for line_coords in past_lines:
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#6b7280',
                    weight=3,
                    opacity=0.7,
                ).add_to(m)

        # Ruta current pasada (azul sólido)
        current_past = route.get('current_past', [])
        if len(current_past) >= 2:
            folium.PolyLine(
                locations=current_past,
                color='#2563eb',
                weight=4,
                opacity=0.85,
            ).add_to(m)

        # Ruta current futura (azul punteado)
        current_future = route.get('current_future', [])
        if len(current_future) >= 2:
            folium.PolyLine(
                locations=current_future,
                color='#2563eb',
                weight=3,
                opacity=0.5,
                dash_array='8 10',
            ).add_to(m)

        # Rutas futuras (gris punteado)
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

        # Fallback: líneas directas si no hay rutas detalladas
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

        # Ajustar bounds
        if len(all_points) > 1:
            m.fit_bounds(all_points, padding=(50, 50))

        # Generar HTML
        map_html = m._repr_html_()
        return map_html

    def _generate_fallback_map_html(self, map_data):
        """
        Genera HTML de mapa básico sin Folium (usando Leaflet CDN directamente en el HTML).
        Usado como fallback si folium no está instalado.
        """
        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')
        container = map_data.get('container', 'N/A')
        vessel = map_data.get('vessel', 'N/A')
        status = map_data.get('status', 'En tránsito')
        pct = map_data.get('transit_pct', 0)
        origin_name = map_data.get('origin', {}).get('name', 'Origen')
        dest_name = map_data.get('destination', {}).get('name', 'Destino')

        # Construir JS para marcadores
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
        
        # Líneas
        if origin_loc and current_loc:
            markers_js += f"""
            L.polyline([[{origin_loc[0]},{origin_loc[1]}],[{current_loc[0]},{current_loc[1]}]], 
                {{color:'#2563eb',weight:4,opacity:0.85}}).addTo(map);
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
        <div style="width:100%;height:400px;position:relative;">
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <div id="fallback_map" style="width:100%;height:100%;"></div>
            <script>
                (function() {{
                    var map = L.map('fallback_map').setView([20, -40], 2);
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
        """Sincroniza datos de ShipsGo v2 usando shipments + geojson endpoints."""
        self.ensure_one()

        Config = self.env['ir.config_parameter'].sudo()
        api_url   = Config.get_param('stock_transit.shipsgo_api_url', 'https://api.shipsgo.com/v2')
        api_token = Config.get_param('stock_transit.shipsgo_api_token', '')

        if not api_token:
            raise UserError(_("No se ha configurado el Token de ShipsGo en Parámetros del Sistema."))

        # ── Número de contenedor ───────────────────────────────────────────────
        container_ref = False
        for line in self.line_ids:
            if line.container_number and line.container_number not in ('PENDIENTE', 'SN', False, ''):
                container_ref = line.container_number
                break
        if not container_ref and self.container_number and 'PENDIENTE' not in (self.container_number or ''):
            container_ref = str(self.container_number).split(',')[0].strip()
        if container_ref:
            container_ref = str(container_ref).strip().upper()
        if not container_ref:
            raise UserError(_("No se encontró un número de contenedor válido en las líneas."))

        headers = {
            "Accept": "application/json",
            "User-Agent": "OdooControlTower/1.0",
            "X-Shipsgo-User-Token": api_token,
        }

        # ── 1. GET /ocean/shipments ────────────────────────────────────────────
        try:
            r = requests.get(
                f"{api_url}/ocean/shipments",
                headers=headers,
                params={"filters[container_number]": f"eq:{container_ref}"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise UserError(_(f"Error al conectar con ShipsGo: {e}"))

        shipments = data.get('shipments') or data.get('data') or []
        if not shipments:
            self.message_post(body=_(f"⚠️ ShipsGo no devolvió datos para {container_ref}."))
            self.write({'shipsgo_last_sync': fields.Datetime.now()})
            return

        shipment = shipments[0]
        shipment_id = shipment.get('id')

        # ── Helper para navegar dicts anidados ────────────────────────────────
        def safe_get(d, keys, default=None):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return default
            return d if d is not None else default

        # ── Datos del shipment ─────────────────────────────────────────────────
        route_info      = safe_get(shipment, ['route'], {})
        transit_pct     = route_info.get('transit_percentage', 0) or 0
        status_text     = shipment.get('status', 'N/A')
        checked_at      = shipment.get('checked_at', '')
        carrier_name    = safe_get(shipment, ['carrier', 'name'], '')

        pol_name        = safe_get(route_info, ['port_of_loading',  'location', 'name'], '')
        pod_name        = safe_get(route_info, ['port_of_discharge', 'location', 'name'], '')
        date_loading    = safe_get(route_info, ['port_of_loading',  'date_of_loading'], '')
        date_discharge  = safe_get(route_info, ['port_of_discharge', 'date_of_discharge'], '')
        pol_country     = safe_get(route_info, ['port_of_loading',  'location', 'country', 'code'], '')
        pod_country     = safe_get(route_info, ['port_of_discharge', 'location', 'country', 'code'], '')

        # ── 2. GET /ocean/shipments/{id}/geojson ──────────────────────────────
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
                geojson_data = gr.json()
            except Exception as e:
                _logger.warning(f"[ShipsGo] No se pudo obtener GeoJSON para {shipment_id}: {e}")

        # ── Parsear GeoJSON ────────────────────────────────────────────────────
        features = safe_get(geojson_data, ['geojson', 'features'], [])

        for feature in features:
            geom_type = feature.get('geometry', {}).get('type')
            props     = feature.get('properties', {})
            status    = props.get('status')
            coords_raw = feature.get('geometry', {}).get('coordinates', [])

            if current_location is None and props.get('current') is not None:
                cur = props['current']
                lon, lat = cur['coordinates'][0], cur['coordinates'][1]
                current_location = [lat, lon]
                vessel_name  = safe_get(props, ['vessel', 'name'], '')
                voyage_number = props.get('voyage', '')

            if geom_type == 'Point':
                loc_name = safe_get(props, ['location', 'name'], '')
                lat_lon  = (coords_raw[1], coords_raw[0])
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

        # ── Construir línea CURRENT dividida ──────────────────────────────────
        current_past_coords  = []
        current_future_coords = []
        for seg in current_lines:
            cur_prop = seg['props'].get('current')
            if cur_prop:
                idx = cur_prop.get('index', -1)
                all_c = seg['coords']
                if idx >= 0:
                    current_past_coords  = all_c[:idx + 1]
                    current_future_coords = all_c[idx:]
                else:
                    current_future_coords = all_c
            else:
                current_future_coords = seg['coords']

        # ── Payload para referencia (se mantiene para compatibilidad) ──────────
        map_data = {
            'container':    container_ref,
            'current_loc':  current_location,
            'vessel':       vessel_name or shipment.get('vessel_name', ''),
            'voyage':       voyage_number,
            'status':       status_text,
            'transit_pct':  int(transit_pct),
            'checked_at':   checked_at,
            'carrier':      carrier_name,
            'origin': {
                'name':    pol_name,
                'loc':     pol_coordinates,
                'country': pol_country,
                'date':    date_loading,
            },
            'destination': {
                'name':    pod_name,
                'loc':     pod_coordinates,
                'country': pod_country,
                'date':    date_discharge,
            },
            'route': {
                'past':           past_lines,
                'current_past':   current_past_coords,
                'current_future': current_future_coords,
                'future':         future_lines,
            },
        }

        # ── Generar mapa HTML con Folium ──────────────────────────────────────
        try:
            map_html = self._generate_folium_map(map_data)
        except Exception as e:
            _logger.error(f"[ShipsGo] Error generando mapa Folium: {e}")
            map_html = False

        vals = {
            'shipsgo_last_sync': fields.Datetime.now(),
            'shipsgo_payload':   json.dumps(map_data),
            'shipsgo_map_html':  map_html,
            'transit_progress':  int(transit_pct),
        }
        if vessel_name:
            vals['vessel_name'] = vessel_name
        if carrier_name:
            vals['shipping_line'] = carrier_name
        if date_discharge:
            vals['eta'] = date_discharge

        self.write(vals)

        self.message_post(body=Markup(
            "📡 <b>Sincronización ShipsGo</b><br/>"
            "Contenedor: {container} | Estado: {status}<br/>"
            "Progreso: {pct}% | Buque: {vessel}<br/>"
            "POL: {pol} → POD: {pod}<br/>"
            "Pos. actual: {loc}"
        ).format(
            container = container_ref,
            status    = status_text,
            pct       = int(transit_pct),
            vessel    = vessel_name or 'N/A',
            pol       = pol_name or 'N/A',
            pod       = pod_name or 'N/A',
            loc       = str(current_location) if current_location else '⚠️ sin coordenadas',
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
        today = fields.Date.today()
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
            allocated = sum(rec.line_ids.filtered(lambda l: l.allocation_status == 'reserved').mapped('product_uom_qty'))
            rec.total_m2 = total
            rec.allocated_m2 = allocated
            rec.allocation_percent = (allocated / total) * 100 if total > 0 else 0

    @api.depends('etd', 'eta', 'custom_status', 'create_date', 'shipsgo_payload')
    def _compute_transit_progress(self):
        today = fields.Date.today()
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
    # CAMBIO #4: Notificaciones de alerta ETA
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
                rec.message_post(body=body, partner_ids=followers.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
                continue

            level_label = 'VENCIDO' if rec.eta_alert_level == 'danger' else 'PRÓXIMO A VENCER'
            eta_str = rec.eta.strftime('%d/%m/%Y') if rec.eta else '—'
            body = Markup(
                "⚠️ <b>Alerta ETA %s</b><br/>"
                "El embarque <b>%s</b> tiene ETA %s y está en estado <b>%s</b>."
            ) % (level_label, rec.name, eta_str, rec.custom_status)
            rec.message_post(body=body, partner_ids=responsible.partner_id.ids, message_type='comment', subtype_xmlid='mail.mt_comment')

    # =========================================================================
    # MÉTODOS DE ESTADO (WIZARD)
    # =========================================================================
    
    STATUS_SEQUENCE = [
        'solicitud', 'production', 'booking', 'puerto_origen',
        'on_sea', 'puerto_destino', 'arrived_port', 'reception_pending', 'delivered',
    ]

    STATUS_LABELS = {
        'solicitud':         'Solicitud Enviada',
        'production':        'Producción',
        'booking':           'Booking',
        'puerto_origen':     'Puerto Origen',
        'on_sea':            'En Altamar',
        'puerto_destino':    'Puerto Destino',
        'arrived_port':      'Arribo a Puerto',
        'reception_pending': 'En Recepción',
        'delivered':         'Entregado en Almacén',
        'cancel':            'Cancelado',
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
            write_vals = {
                'arrival_date': fields.Date.today(),
                'custom_status': 'delivered',
            }
            if not self.arrival_date_bodega:
                write_vals['arrival_date_bodega'] = fields.Date.today()
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
            qty_done = move_line.quantity

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

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id), 
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id)
            ], limit=1)

            lot_container = ''
            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref

            if lot_id in existing_by_lot:
                existing_line = existing_by_lot[lot_id]
                update_vals = {}
                if existing_line.product_uom_qty != qty_done:
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
                    hold_orders_map[key] = {'partner': partner_to_assign, 'order': order_to_assign, 'line_vals_indices': []}
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
            if not relevant_lines: continue
            
            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'fecha_orden': fields.Datetime.now(),
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            })
            
            for line in relevant_lines:
                TransitManager.reassign_lot(self.env, line, partner, order, notes=False, hold_order_obj=hold_order)
            
            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()

    def action_generate_reception(self):
        self.ensure_one()
        if self.reception_picking_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.reception_picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        if not picking_type:
            raise UserError(_("No se encontró un tipo de operación 'Internal Transfer'."))

        valid_lines = self.line_ids.filtered(lambda l: l.lot_id and l.quant_id)
        if not valid_lines:
            raise UserError(_("No hay líneas válidas para mover."))
            
        source_location = valid_lines[0].quant_id.location_id
        
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': f"{self.name} (Recepción Física)",
            'company_id': self.company_id.id,
            'move_type': 'direct',
            'supplier_bl_number': self.bl_number if hasattr(self.env['stock.picking'], 'supplier_bl_number') else False,
            'supplier_container_no': self.container_number if hasattr(self.env['stock.picking'], 'supplier_container_no') else False,
            'supplier_origin': 'TRÁNSITO' if hasattr(self.env['stock.picking'], 'supplier_origin') else False,
        })

        products_map = {}
        for line in valid_lines:
            if line.product_uom_qty <= 0: continue
            if line.product_id not in products_map: products_map[line.product_id] = 0.0
            products_map[line.product_id] += line.product_uom_qty

        for product, qty in products_map.items():
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty,
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': source_location.id,
                'location_dest_id': picking.location_dest_id.id,
                'company_id': self.company_id.id,
                'state': 'draft',
            })
        
        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending'
        })
        
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
        if picking.state == 'done':
            raise UserError(_("La recepción ya fue validada."))
        if picking.state == 'draft':
            picking.action_confirm()

        picking.move_line_ids.unlink()
        lines_created = 0
        for line in self.line_ids:
            if not line.lot_id or line.product_uom_qty <= 0: continue
            move = picking.move_ids.filtered(lambda m: m.product_id.id == line.product_id.id and m.state not in ['done', 'cancel'])
            if not move:
                move = self.env['stock.move'].create({
                    'picking_id': picking.id,
                    'product_id': line.product_id.id,
                    'product_uom': line.product_id.uom_id.id,
                    'product_uom_qty': line.product_uom_qty,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'company_id': self.company_id.id,
                })
                move._action_confirm()
            else:
                move = move[0]

            self.env['stock.move.line'].create({
                'picking_id': picking.id,
                'move_id': move.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_id.uom_id.id,
                'lot_id': line.lot_id.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'quantity': line.product_uom_qty,
            })
            lines_created += 1

        if lines_created == 0:
            raise UserError(_("No hay líneas con lote asignado para sincronizar."))

        picking.message_post(body=f"🔄 {lines_created} lotes sincronizados desde Viaje {self.name}.")
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_arrive(self):
        self.ensure_one()
        if self.reception_picking_id and self.reception_picking_id.state != 'done':
            raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))
        
        write_vals = {
            'arrival_date': fields.Date.today(),
            'custom_status': 'delivered'
        }
        if not self.arrival_date_bodega:
            write_vals['arrival_date_bodega'] = fields.Date.today()
        self.write(write_vals)
        
        for line in self.line_ids:
            if line.allocation_id and line.allocation_id.state != 'done':
                line.allocation_id.action_mark_received(line.product_uom_qty)

    def action_cancel(self):
        self.write({'custom_status': 'cancel'})