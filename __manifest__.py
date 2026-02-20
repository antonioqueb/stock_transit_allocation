# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.5.1.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v5.1:
        - SÁBANA DE SEGUIMIENTO: Vista completamente personalizada con componente OWL.
        - Todos los registros siempre visibles (sin expansión requerida).
        - Filtros rápidos por estado, búsqueda de texto, agrupación flexible.
        - Columnas configurables, totales fijos en pie de tabla.
        - BOTONES DE PROPAGACIÓN en la lista de líneas del viaje.
        - CONSOLIDACIÓN DE LÍNEAS: Una sola línea por producto en la OC.
        - ALLOCATIONS: Modelo intermedio para trackear qué cantidad va a cada cliente.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['stock', 'sale_management', 'purchase', 'web', 'stock_lot_dimensions', 'sale_stock', 'inventory_shopping_cart', 'sale_stone_selection'],
    'data': [
        'security/transit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/stock_transit_voyage_views.xml',
        'views/stock_transit_sheet_action.xml',          # <-- NUEVO: acción client para sábana
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/to_be_purchased_views.xml', 
        'wizard/transit_reassign_wizard_views.xml',
        'wizard/sale_order_consolidate_purchase_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # CSS existentes
            'stock_transit_allocation/static/src/css/transit_style.css',
            'stock_transit_allocation/static/src/css/transit_voyage_lines.css',
            # NUEVO: Sábana de seguimiento (SCSS)
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.scss',
            # JS existentes
            'stock_transit_allocation/static/src/js/transit_progress_widget.js',
            'stock_transit_allocation/static/src/xml/transit_progress_widget.xml',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.js',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.xml',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.js',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.xml',
            # NUEVO: Sábana personalizada (JS antes que XML)
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.js',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}