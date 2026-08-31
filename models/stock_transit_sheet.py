# -*- coding: utf-8 -*-
"""
Vista SQL resumida para Cronograma / Torre de Control.
"""
from odoo import models, fields
from odoo.tools import drop_view_if_exists


class StockTransitSheet(models.Model):
    _name = 'stock.transit.sheet'
    _description = 'Cronograma (Resumen)'
    _auto = False
    _order = 'eta asc, voyage_id desc'

    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje', readonly=True)
    # Multiempresa: la del viaje, para que la regla de registro aplique
    # sobre la vista SQL igual que sobre el viaje.
    company_id = fields.Many2one('res.company', string='Compañía', readonly=True)
    product_id = fields.Many2one('product.product', string='Descripción / Producto', readonly=True)
    order_id = fields.Many2one('sale.order', string='Sales Order', readonly=True)
    purchase_id = fields.Many2one('purchase.order', string='OC Sistema', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Cliente / Proyecto', readonly=True)
    container_number = fields.Char(string='Contenedor', readonly=True)
    date_order = fields.Datetime(string='Fecha OC', readonly=True)
    proforma_ref = fields.Char(string='Proforma / Ref Prov', readonly=True)
    vendor_id = fields.Many2one('res.partner', string='Proveedor', readonly=True)

    voyage_status = fields.Selection([
        ('solicitud', 'Solicitud Enviada'),
        ('production', 'Producción'),
        ('booking', 'Booking'),
        ('puerto_origen', 'Puerto Origen'),
        ('on_sea', 'En Altamar / Mar'),
        ('puerto_destino', 'Puerto Destino'),
        ('delivered', 'Entregado en Almacén'),
        ('cancel', 'Cancelado'),
    ], string='Status', readonly=True)

    eta_alert_level = fields.Selection([
        ('ok', 'En Tiempo'),
        ('warning', 'Próximo a Vencer'),
        ('danger', 'Vencido'),
        ('done', 'Entregado'),
    ], string='Alerta ETA', readonly=True)

    shipping_line = fields.Char(string='Naviera', readonly=True)
    bl_number = fields.Char(string='Factura de Carga / BL', readonly=True)
    etd = fields.Date(string='ETD', readonly=True)
    eta = fields.Date(string='ETA', readonly=True)
    eta_original = fields.Date(string='ETA Original', readonly=True)
    delay_days = fields.Integer(string='Días de Retraso', readonly=True)
    arrival_date = fields.Date(string='Llegada Real', readonly=True)
    arrival_date_bodega = fields.Date(string='Entregado en Bodega', readonly=True)
    invoice_number = fields.Char(string='No. Invoice', readonly=True)

    product_uom_qty = fields.Float(string='M2 Embarcados', readonly=True)
    qty_proforma = fields.Float(string='Metraje Proforma', readonly=True)
    qty_original_demand = fields.Float(string='Metraje Pedido Original', readonly=True)
    salesperson_id = fields.Many2one('res.users', string='Vendedor', readonly=True)
    product_categ_id = fields.Many2one('product.category', string='Categoría', readonly=True)

    def init(self):
        drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW stock_transit_sheet AS (
                SELECT
                    MIN(l.id) as id,
                    l.voyage_id,
                    v.company_id,
                    l.product_id,
                    l.order_id,
                    l.purchase_id,
                    l.partner_id,
                    l.container_number,
                    MAX(l.date_order) as date_order,
                    MAX(l.proforma_ref) as proforma_ref,
                    MAX(l.vendor_id) as vendor_id,
                    MAX(l.voyage_status) as voyage_status,
                    MAX(l.shipping_line) as shipping_line,
                    MAX(l.bl_number) as bl_number,
                    MAX(l.etd) as etd,
                    MAX(l.eta) as eta,
                    MAX(l.arrival_date) as arrival_date,
                    MAX(l.salesperson_id) as salesperson_id,
                    SUM(l.product_uom_qty) as product_uom_qty,
                    MAX(l.qty_proforma) as qty_proforma,
                    MAX(l.qty_original_demand) as qty_original_demand,
                    MAX(v.eta_original) as eta_original,
                    MAX(v.delay_days) as delay_days,
                    MAX(v.eta_alert_level) as eta_alert_level,
                    pt.categ_id as product_categ_id,
                    MAX(v.arrival_date_bodega) as arrival_date_bodega,
                    MAX(picking.supplier_invoice_number) as invoice_number
                FROM
                    stock_transit_line l
                    JOIN stock_transit_voyage v ON v.id = l.voyage_id
                    LEFT JOIN stock_picking picking ON picking.id = v.picking_id
                    LEFT JOIN product_product pp ON pp.id = l.product_id
                    LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
                GROUP BY
                    l.voyage_id,
                    v.company_id,
                    l.product_id,
                    l.order_id,
                    l.purchase_id,
                    l.partner_id,
                    l.container_number,
                    pt.categ_id
            )
        """)


class StockTransitSheetPublicationPending(models.Model):
    """Filtro "Pendiente de publicar" en el Cronograma: delega al viaje.

    Vive en este archivo (y no en stock_transit_publication.py) porque el
    _inherit exige que 'stock.transit.sheet' ya esté definido: el orden de
    import de models/__init__.py carga publication ANTES que sheet.
    """
    _inherit = "stock.transit.sheet"

    tc_publication_pending = fields.Boolean(
        string="Pendiente de publicar",
        compute="_compute_tc_publication_pending",
        search="_search_tc_publication_pending",
    )

    def _compute_tc_publication_pending(self):
        for rec in self:
            rec.tc_publication_pending = bool(
                rec.voyage_id and rec.voyage_id.tc_publication_pending)

    def _search_tc_publication_pending(self, operator, value):
        return [("voyage_id.tc_publication_pending", operator, value)]
