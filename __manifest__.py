# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.6.0.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.
        
        Novedades v6.0:
        - FORMULARIO REDISEÑADO: Hero header con métricas KPI, panel de datos 3 columnas.
        - LISTA AGRUPADA POR PRODUCTO: Vista de lotes agrupada, con campos Bloque y Atado.
        - Campos x_bloque y x_atado expuestos desde stock.lot en las líneas de tránsito.
        - Propagación rápida de cliente/orden hacia todos los lotes del mismo producto.
        - Asignación masiva multi-selección con panel flotante.

        Novedades v5.1:
        - SÁBANA DE SEGUIMIENTO: Vista completamente personalizada con componente OWL.
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
        'views/stock_transit_sheet_action.xml',
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
            # CSS existentes
            'stock_transit_allocation/static/src/css/transit_style.css',
            'stock_transit_allocation/static/src/css/transit_voyage_lines.css',
            # Sábana de seguimiento
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.scss',
            # Kanban
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.scss',
            # Formulario del viaje (hero header + panel datos)
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form_odoo.scss',
            # Lista agrupada por producto (widget field)
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.scss',
            # JS existentes
            'stock_transit_allocation/static/src/js/transit_progress_widget.js',
            'stock_transit_allocation/static/src/xml/transit_progress_widget.xml',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.js',
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.xml',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.js',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.xml',
            # Sábana
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.js',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.xml',
            # Kanban
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.js',
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.xml',
            # Widget agrupado por producto en el formulario del viaje
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.js',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}