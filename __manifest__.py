# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Gestión visual y reasignación de mercancía en tránsito marítimo',
    'description': """
        Módulo profesional para la gestión de contenedores y asignación de stock en tránsito.
        
        Características:
        - Vinculación con Recepciones (Pickings) creados desde BL.
        - Gestión de "Viajes" (Voyages) y Contenedores.
        - Interfaz visual con seguimiento de ETA (Widget de Barco).
        - Reasignación dinámica de lotes a clientes (integra con stock_lot_hold).
        - Trazabilidad completa en el chatter.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': ['stock', 'web', 'stock_lot_dimensions'],
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
