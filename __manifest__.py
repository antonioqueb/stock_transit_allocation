# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.2.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v2.0:
        - Campo "Mandar Pedir" en Ventas.
        - Independencia de líneas manuales en Compras (No afectan la SO original).
        - Validación estricta: Cliente requiere Orden de Venta.
        - Ubicación ajustada a SOM/Transit.
        - Corrección en barras de progreso y referencias de Compra.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['stock', 'sale_management', 'purchase', 'web', 'stock_lot_dimensions'],
    'data': [
        'security/transit_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/stock_transit_voyage_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'wizard/transit_reassign_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stock_transit_allocation/static/src/css/transit_style.css',
            'stock_transit_allocation/static/src/js/transit_progress_widget.js',
            'stock_transit_allocation/static/src/xml/transit_progress_widget.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}