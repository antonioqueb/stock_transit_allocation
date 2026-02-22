# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.7.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v7.0 (ShipsGo Integration):
        - Integración con API ShipsGo v2.
        - Mapa interactivo Leaflet en el formulario de viaje.
        - Sincronización automática de progreso y ubicación.

        Novedades v6.0:
        - FORMULARIO REDISEÑADO: Hero header con métricas KPI, panel de datos 3 columnas.
        - LISTA AGRUPADA POR PRODUCTO: Vista de lotes agrupada, con campos Bloque y Atado.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['stock', 'sale_management', 'purchase', 'web', 'stock_lot_dimensions', 'sale_stock', 'inventory_shopping_cart', 'sale_stone_selection'],
    'data': [
        'security/transit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_config_parameter_data.xml',  # NUEVO
        'views/stock_transit_sheet_action.xml',
        'views/stock_transit_voyage_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/to_be_purchased_views.xml',
        'wizard/transit_reassign_wizard_views.xml',
        'wizard/sale_order_consolidate_purchase_views.xml',
        'wizard/transit_status_change_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Librerías Externas (Leaflet CDN)
            'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
            'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',

            # CSS existentes
            'stock_transit_allocation/static/src/css/transit_style.css',
            'stock_transit_allocation/static/src/css/transit_voyage_lines.css',
            
            # NUEVO: Widget del Mapa
            'stock_transit_allocation/static/src/components/transit_map/transit_map.scss',
            'stock_transit_allocation/static/src/components/transit_map/transit_map.js',
            'stock_transit_allocation/static/src/components/transit_map/transit_map.xml',

            # Cronograma
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.scss',
            # Kanban
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.scss',
            # Formulario del viaje
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form_odoo.scss',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.scss',
            
            # JS existentes
            'stock_transit_allocation/static/src/js/transit_progress_widget.js',
            'stock_transit_allocation/static/src/xml/transit_progress_widget.xml',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.js',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.xml',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.js',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.xml',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.js',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.xml',
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.js',
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.xml',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.js',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}