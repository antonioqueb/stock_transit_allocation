# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.5.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v5.0:
        - WIDGET PERSONALIZADO para líneas del viaje con propagación rápida.
        - BOTONES DE PROPAGACIÓN: ↓1 (propaga al siguiente) y ↓↓ (propaga a todos abajo).
        - Los botones aparecen al lado del campo Cliente cuando hay valor asignado.
        - CONSOLIDACIÓN DE LÍNEAS: Una sola línea por producto en la OC.
        - ALLOCATIONS: Modelo intermedio para trackear qué cantidad va a cada cliente.
        - Reasignación en tránsito funcionando con allocations.
        - Vista de asignaciones en la orden de compra.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['stock', 'sale_management', 'purchase', 'web', 'stock_lot_dimensions', 'sale_stock', 'inventory_shopping_cart', 'sale_stone_selection'],
    'data': [
        'security/transit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/stock_transit_voyage_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/to_be_purchased_views.xml', 
        'wizard/transit_reassign_wizard_views.xml',
        'wizard/sale_order_consolidate_purchase_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # CSS existente
            'stock_transit_allocation/static/src/css/transit_style.css',
            # NUEVO CSS para el widget de líneas del viaje
            'stock_transit_allocation/static/src/css/transit_voyage_lines.css',
            # JS existentes
            'stock_transit_allocation/static/src/js/transit_progress_widget.js',
            'stock_transit_allocation/static/src/xml/transit_progress_widget.xml',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.js',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.xml',
            # NUEVO widget de líneas del viaje con propagación (JS primero, XML después)
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.js',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}