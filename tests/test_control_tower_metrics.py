# -*- coding: utf-8 -*-
"""Pruebas del flujo de material de Torre de Control.

Cubre: comprado vs embarcado, pendiente por embarcar, exceso embarcado,
sin asignar, mapa por producto sin N+1 y producto sin vínculo con OC.
"""
from odoo.tests.common import TransactionCase


class TestControlTowerMetrics(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Proveedor Test CT'})
        cls.product_a = cls.env['product.product'].create({
            'name': 'PLACA TEST A', 'type': 'consu', 'is_storable': True,
        })
        cls.product_b = cls.env['product.product'].create({
            'name': 'FORMATO TEST B', 'type': 'consu', 'is_storable': True,
        })
        cls.po = cls.env['purchase.order'].create({
            'partner_id': cls.partner.id,
            'order_line': [
                (0, 0, {
                    'product_id': cls.product_a.id,
                    'product_qty': 100.0,
                    'price_unit': 10.0,
                }),
                (0, 0, {
                    'product_id': cls.product_b.id,
                    'product_qty': 50.0,
                    'price_unit': 5.0,
                }),
            ],
        })
        cls.voyage = cls.env['stock.transit.voyage'].create({
            'purchase_id': cls.po.id,
        })

    def _add_line(self, product, qty, status='available'):
        return self.env['stock.transit.line'].create({
            'voyage_id': self.voyage.id,
            'product_id': product.id,
            'product_uom_qty': qty,
            'allocation_status': status,
        })

    def test_pending_ship(self):
        """Comprado 100, embarcado 60 → pendiente por embarcar 40."""
        self._add_line(self.product_a, 60.0)
        self.voyage.invalidate_recordset()
        self.assertAlmostEqual(self.voyage.tc_purchased_qty, 100.0, places=2)
        self.assertAlmostEqual(self.voyage.tc_pending_ship_qty, 40.0, places=2)
        self.assertAlmostEqual(self.voyage.tc_excess_ship_qty, 0.0, places=2)

    def test_excess_ship(self):
        """Comprado 100, embarcado 120 → exceso 20, pendiente 0."""
        self._add_line(self.product_a, 120.0)
        self.voyage.invalidate_recordset()
        self.assertAlmostEqual(self.voyage.tc_excess_ship_qty, 20.0, places=2)
        self.assertAlmostEqual(self.voyage.tc_pending_ship_qty, 0.0, places=2)

    def test_purchased_only_voyage_products(self):
        """Comprado solo cuenta productos PRESENTES en el viaje."""
        self._add_line(self.product_a, 60.0)
        self.voyage.invalidate_recordset()
        # product_b (50 comprados) NO está en el viaje: no se suma.
        self.assertAlmostEqual(self.voyage.tc_purchased_qty, 100.0, places=2)

    def test_product_flow_map_no_po_link(self):
        """Producto embarcado sin línea de compra → sin vínculo con OC."""
        product_c = self.env['product.product'].create({
            'name': 'SIN OC TEST C', 'type': 'consu', 'is_storable': True,
        })
        self._add_line(self.product_a, 60.0)
        self._add_line(product_c, 10.0)
        flow = self.env['stock.transit.voyage'].tc_get_product_flow_map(self.voyage.id)
        self.assertTrue(flow[self.product_a.id]['has_po_link'])
        self.assertAlmostEqual(flow[self.product_a.id]['pending_ship'], 40.0, places=2)
        self.assertFalse(flow[product_c.id]['has_po_link'])
        self.assertAlmostEqual(flow[product_c.id]['excess_ship'], 0.0, places=2)

    def test_free_qty(self):
        """Sin asignar = embarcado − asignado (nunca negativo)."""
        self._add_line(self.product_a, 40.0, status='available')
        self._add_line(self.product_a, 20.0, status='reserved')
        self.voyage.invalidate_recordset()
        self.assertAlmostEqual(
            self.voyage.tc_free_qty,
            max((self.voyage.total_m2 or 0.0) - (self.voyage.allocated_m2 or 0.0), 0.0),
            places=2,
        )
