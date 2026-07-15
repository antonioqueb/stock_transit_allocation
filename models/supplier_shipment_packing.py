# -*- coding: utf-8 -*-
from odoo import models, fields, api


class SupplierShipmentPacking(models.Model):
    _name = 'supplier.shipment.packing'
    _description = 'Packing List de Embarque'
    _order = 'shipment_id, packing_date, id'

    shipment_id = fields.Many2one(
        'supplier.shipment', string='Embarque',
        required=True, ondelete='cascade', index=True,
    )
    proforma_id = fields.Many2one(
        'supplier.proforma.header', string='Proforma',
        related='shipment_id.proforma_id', store=True,
    )
    purchase_id = fields.Many2one(
        'purchase.order', string='OC',
        related='shipment_id.purchase_id', store=True,
    )

    packing_number = fields.Char(string='Número de Packing List', required=True)
    packing_date = fields.Date(string='Fecha')
    file = fields.Binary(string='Archivo Packing List', attachment=True)
    filename = fields.Char(string='Nombre archivo')

    scope = fields.Selection([
        ('full_shipment', 'Embarque Completo'),
        ('specific_containers', 'Contenedores Específicos'),
    ], string='Alcance', default='full_shipment')

    container_ids = fields.Many2many(
        'supplier.shipment.container',
        'supplier_packing_container_rel',
        'packing_id', 'container_id',
        string='Contenedores que Cubre',
    )

    row_ids = fields.One2many(
        'supplier.shipment.packing.row', 'packing_id',
        string='Líneas de Producto',
    )
    row_count = fields.Integer(compute='_compute_row_count', store=True)

    @api.depends('row_ids')
    def _compute_row_count(self):
        for rec in self:
            rec.row_count = len(rec.row_ids)

    def name_get(self):
        return [(r.id, r.packing_number or f"PL-{r.id}") for r in self]


class SupplierShipmentPackingRow(models.Model):
    _name = 'supplier.shipment.packing.row'
    _description = 'Línea de Packing List (Detalle de Producto)'
    _order = 'packing_id, sequence, id'

    packing_id = fields.Many2one(
        'supplier.shipment.packing', string='Packing List',
        required=True, ondelete='cascade', index=True,
    )
    shipment_id = fields.Many2one(
        'supplier.shipment', string='Embarque',
        related='packing_id.shipment_id', store=True,
    )
    sequence = fields.Integer(default=10)

    product_id = fields.Many2one('product.product', string='Producto', required=True)
    container_id = fields.Many2one(
        'supplier.shipment.container', string='Contenedor',
        help='Contenedor específico de esta línea (opcional)',
    )

    # Trazabilidad PI/PO por fila. DUPLICADO INTENCIONAL de la definición en
    # stock_lot_packing_import: esta clase re-declara el modelo con _name (no
    # _inherit), y en Odoo 19 esa re-declaración REEMPLAZA a la definición del
    # módulo base — sin estos campos aquí, el registry no los conoce y
    # trace_pi_po revienta al resolver sus @depends.
    purchase_line_id = fields.Many2one(
        'purchase.order.line', string='Línea de compra (PO)',
        index=True, ondelete='set null', copy=False,
    )
    pi_header_id = fields.Many2one(
        'supplier.proforma.header', string='PI de la línea',
        index=True, ondelete='set null', copy=False,
    )

    # --- Dimensiones (para Placas) ---
    grosor = fields.Char(string='Grosor')
    alto = fields.Float(string='Alto (m)', digits=(12, 4))
    ancho = fields.Float(string='Ancho (m)', digits=(12, 4))
    peso = fields.Float(string='Peso (kg)', digits=(12, 3))

    # --- Cantidad (para Piezas/Formatos) ---
    quantity = fields.Float(string='Cantidad', digits='Product Unit of Measure')

    # --- Identificadores de lote ---
    bloque = fields.Char(string='Bloque')
    numero_placa = fields.Char(string='No. Placa')
    atado = fields.Char(string='Atado')
    color = fields.Char(string='Notas / Color')
    grupo_name = fields.Char(string='Grupo')
    pedimento = fields.Char(string='Pedimento')
    ref_proveedor = fields.Char(string='Ref. Proveedor')

    # --- Tipo de unidad ---
    tipo = fields.Selection([
        ('Placa', 'Placa'),
        ('Pieza', 'Pieza'),
        ('Formato', 'Formato'),
    ], string='Tipo', default='Placa')

    # ==================== FOTOGRAFÍA DE LA PLACA ====================
    image = fields.Binary(
        string='Fotografía',
        attachment=True,
        help='Fotografía de la placa/pieza capturada por el proveedor en el portal.',
    )
    image_filename = fields.Char(string='Nombre de imagen')

    # --- Computado ---
    area_m2 = fields.Float(
        string='Área (m²)', compute='_compute_area', store=True, digits=(12, 4),
    )

    @api.depends('alto', 'ancho', 'tipo', 'quantity')
    def _compute_area(self):
        for row in self:
            if row.tipo == 'Placa':
                row.area_m2 = round(row.alto * row.ancho, 4)
            else:
                row.area_m2 = row.quantity


class SupplierShipmentBlockImage(models.Model):
    _name = 'supplier.shipment.block.image'
    _description = 'Fotografía de Bloque (Portal Proveedor)'
    _order = 'shipment_id, block_name, sequence'

    shipment_id = fields.Many2one(
        'supplier.shipment', string='Embarque',
        required=True, ondelete='cascade', index=True,
    )
    proforma_id = fields.Many2one(
        'supplier.proforma.header', string='Proforma',
        related='shipment_id.proforma_id', store=True,
    )
    block_name = fields.Char(string='Nombre del Bloque', required=True, index=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True)
    sequence = fields.Integer(default=10)
    image = fields.Binary(string='Fotografía', required=True, attachment=True)
    image_filename = fields.Char(string='Nombre archivo')
    notes = fields.Text(string='Notas')
