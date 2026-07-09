# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.13.25.0',
    'category': 'Inventory/Logistics',
    'summary': 'Torre de control para gestión de contenedores y asignación de pedidos',
    'description': """
        Módulo optimizado para la gestión de contenedores y asignación de stock en tránsito.

        Novedades v13.3:
        - Botón para imprimir etiquetas ZPL de los lotes recepcionados.
        - Reporte PDF Packing List de Embarque.
        - Botón de impresión en embarque.
        - Botón de impresión en orden de compra, visible solo si hay embarque con Packing List cargado.
        - Detalle logístico en 9px con todos los campos relevantes del embarque/packing.

        Novedades v13.2:
        - Protección estricta para que Recibir/Abrir Recepción prepare la recepción física sin validarla.
        - Bloqueo de auto-validación en recepción física hasta procesar Packing List y Worksheet.
        - Reabrir recepción existente ya no reconstruye ni borra PL/Worksheet trabajados.

        Novedades v13.1:
        - Hotfix de assets para registrar correctamente action_transit_allocation.
        - Manifest limpio sin contenido pegado accidentalmente.
        - __init__.py limpio importando transit_allocation.

        Novedades v13.0:
        - Nuevo hub raíz Transit Allocation para asignar inventario ubicado en tránsito.
        - Integración directa con stock.transit.line y stock.transit.voyage sin duplicar reservas.
        - Asignación desde tránsito hacia pedidos confirmados con demanda pendiente.

        Novedades v12.0:
        - Nuevo hub To Be Allocated para pedidos con requerimiento pendiente y stock disponible.
        - Integración To Be Allocated → To Be Purchased mediante botón Mandar pedido.
        - Cálculo de pendiente comercial por placas asignadas, no por cantidad entregada.
        - Soporte de flujo mixto: parcialmente asignado + restante por asignar/comprar.
        - Rechazo explícito de stock por vendedor sin bloquear inventario disponible.
        - Protección para que material de OC no se reasigne a pedidos ya cubiertos.

        Novedades v11.0:
        - Publicación controlada de inventario en tránsito.
        - El material recibido en tránsito no aparece como disponible hasta publicar inventario.
        - El inventario en tránsito se clasifica como Disponible o Committed desde Torre de Control.
        - On Hold ya no aplica para material en tránsito.
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'depends': [
        'stock',
        'sale_management',
        'purchase',
        'web',
        'stock_lot_dimensions',
        'sale_stock',
        'inventory_shopping_cart',
        'sale_stone_selection',
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
        'data/ir_cron_data.xml',

        'reports/transit_packing_list_report.xml',

        'views/stock_transit_sheet_action.xml',
        'views/stock_transit_voyage_views.xml',
        'views/supplier_links_tracking_views.xml',
        'views/stock_transit_publication_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/to_be_purchased_views.xml',
        'views/transit_allocation_views.xml',
        'views/supplier_proforma_views.xml',
        'views/supplier_shipment_views.xml',
        'views/purchase_order_proforma_views.xml',
        'views/transit_packing_list_buttons.xml',

        'wizard/transit_reassign_wizard_views.xml',
        'wizard/transit_unassign_wizard_views.xml',
        'wizard/sale_order_consolidate_purchase_views.xml',
        'wizard/transit_status_change_wizard_views.xml',
        'wizard/transit_label_print_wizard_views.xml',
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
            'stock_transit_allocation/static/src/components/to_be_purchased/to_be_purchased.scss',

            'stock_transit_allocation/static/src/components/transit_allocation/transit_allocation.js',
            'stock_transit_allocation/static/src/components/transit_allocation/transit_allocation.xml',
            'stock_transit_allocation/static/src/components/transit_allocation/transit_allocation.scss',

            'stock_transit_allocation/static/src/components/to_be_allocated/to_be_allocated.js',
            'stock_transit_allocation/static/src/components/to_be_allocated/to_be_allocated.xml',
            'stock_transit_allocation/static/src/components/to_be_allocated/to_be_allocated.scss',

            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.js',
            'stock_transit_allocation/static/src/components/transit_voyage_lines/transit_line_propagate.xml',

            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.js',
            'stock_transit_allocation/static/src/components/transit_sheet/transit_sheet.xml',

            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.js',
            'stock_transit_allocation/static/src/components/transit_kanban/transit_kanban.xml',

            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.js',
            'stock_transit_allocation/static/src/components/transit_voyage_form/transit_voyage_form.xml',

            'stock_transit_allocation/static/src/components/supplier_access_tracking/supplier_access_tracking.scss',
            'stock_transit_allocation/static/src/components/supplier_access_tracking/supplier_access_tracking.js',
            'stock_transit_allocation/static/src/components/supplier_access_tracking/supplier_access_tracking.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}