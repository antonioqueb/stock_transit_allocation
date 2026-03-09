# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.9.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v9.0 (Portal Embarques):
        - Nuevos modelos: supplier.proforma.header, supplier.shipment, 
          supplier.shipment.invoice, supplier.shipment.packing, supplier.shipment.container
        - Estructura jerárquica: Proforma → N Embarques → Invoices/PL/Contenedores
        - Integración con Torre de Control (voyage_id en shipment)
        - Retrocompatible con datos existentes

        Novedades v8.0 (Folium Map):
        - Mapa generado server-side con Folium (Python), sin dependencia de CDN JS.

        Novedades v7.0 (ShipsGo Integration):
        - Integración con API ShipsGo v2.
        - Sincronización automática de progreso y ubicación.

        Novedades v6.0:
        - FORMULARIO REDISEÑADO: Hero header con métricas KPI, panel de datos 3 columnas.
        - LISTA AGRUPADA POR PRODUCTO: Vista de lotes agrupada, con campos Bloque y Atado.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': [
        'stock', 'sale_management', 'purchase', 'web',
        'stock_lot_dimensions', 'sale_stock',
        'inventory_shopping_cart', 'sale_stone_selection',
        'stock_lot_packing_import',
    ],
    'external_dependencies': {
        'python': ['folium'],
    },
    'data': [
        'security/transit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_config_parameter_data.xml',
        'views/stock_transit_sheet_action.xml',
        'views/stock_transit_voyage_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/to_be_purchased_views.xml',
        'views/supplier_proforma_views.xml',
        'views/supplier_shipment_views.xml',
        'views/purchase_order_proforma_views.xml',
        'wizard/transit_reassign_wizard_views.xml',
        'wizard/sale_order_consolidate_purchase_views.xml',
        'wizard/transit_status_change_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stock_transit_allocation/static/src/css/transit_style.css',
            'stock_transit_allocation/static/src/css/transit_voyage_lines.css',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.scss',
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.scss',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form_odoo.scss',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.scss',
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