# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Asignación en Tránsito (Control Tower)',
    'version': '19.0.13.3.0',
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
        'security/ir.model.access_transit_label.csv',

        'data/ir_sequence_data.xml',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron_data.xml',

        'reports/transit_packing_list_report.xml',

        'views/stock_transit_sheet_action.xml',
        'views/stock_transit_voyage_views.xml',
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
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}

./wizard/init.py

# -*- coding: utf-8 -*-
from . import transit_reassign_wizard
from . import sale_order_consolidate_purchase
from . import transit_status_change_wizard
from . import packing_list_import_wizard
from . import worksheet_import_wizard
from . import transit_label_print_wizard

./wizard/transit_label_print_wizard.py

# -*- coding: utf-8 -*-
import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class TransitLabelPrintWizard(models.TransientModel):
    _name = 'transit.label.print.wizard'
    _description = 'Impresión de Etiquetas de Recepción'

    voyage_id = fields.Many2one('stock.transit.voyage', string='Viaje')
    picking_id = fields.Many2one('stock.picking', string='Recepción Física')
    
    label_format = fields.Selection([
        ('10x5', 'Estándar (10x5 cm)'),
        ('17.5x1', 'Canto/Lomo (17.5x1 cm)'),
        ('20x10', 'Grande (20x10 cm)'),
    ], string='Formato de Etiqueta', default='17.5x1', required=True)

    def action_print(self):
        self.ensure_one()
        
        quant_ids = []
        
        # Recopilar quants desde la Recepción (Picking) o desde el Viaje
        if self.picking_id:
            for ml in self.picking_id.move_line_ids:
                if not ml.lot_id:
                    continue
                loc_id = ml.location_dest_id.id if self.picking_id.state == 'done' else ml.location_id.id
                quant = self.env['stock.quant'].search([
                    ('lot_id', '=', ml.lot_id.id),
                    ('location_id', '=', loc_id),
                    ('quantity', '>', 0)
                ], limit=1)
                if not quant:
                    quant = self.env['stock.quant'].search([
                        ('lot_id', '=', ml.lot_id.id),
                        ('quantity', '>', 0)
                    ], limit=1)
                if quant and quant.id not in quant_ids:
                    quant_ids.append(quant.id)
        elif self.voyage_id:
            for line in self.voyage_id.line_ids:
                if line.quant_id and line.quant_id.id not in quant_ids:
                    quant_ids.append(line.quant_id.id)
                elif line.lot_id:
                    quant = self.env['stock.quant'].search([('lot_id', '=', line.lot_id.id), ('quantity', '>', 0)], limit=1)
                    if quant and quant.id not in quant_ids:
                        quant_ids.append(quant.id)
        
        if not quant_ids:
            raise UserError(_("No se encontraron lotes físicos con cantidad positiva para imprimir."))

        if not hasattr(self.env['stock.quant'], 'generate_zpl_labels'):
            raise UserError(_("El módulo de impresión de etiquetas (generate_zpl_labels) no está disponible."))

        # Llamar a la función existente en el sistema
        res = self.env['stock.quant'].generate_zpl_labels(quant_ids, self.label_format)
        
        if not res.get('success'):
            raise UserError(res.get('message', _('Error al generar etiquetas.')))

        zpl_data = res.get('zpl_data', '')
        filename = res.get('filename', 'etiquetas.zpl')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(zpl_data.encode('utf-8')),
            'mimetype': 'text/plain',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

./wizard/transit_label_print_wizard_views.xml

<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_transit_label_print_wizard_form" model="ir.ui.view">
        <field name="name">transit.label.print.wizard.form</field>
        <field name="model">transit.label.print.wizard</field>
        <field name="arch" type="xml">
            <form string="Imprimir Etiquetas">
                <group>
                    <div class="alert alert-info" role="alert" style="margin-bottom: 0px;">
                        Seleccione el formato para descargar el archivo ZPL con las etiquetas de los lotes recepcionados.
                    </div>
                </group>
                <group>
                    <field name="label_format" widget="radio"/>
                    <field name="voyage_id" invisible="1"/>
                    <field name="picking_id" invisible="1"/>
                </group>
                <footer>
                    <button name="action_print" string="Descargar ZPL" type="object" class="btn-primary" icon="fa-download"/>
                    <button string="Cancelar" class="btn-secondary" special="cancel"/>
                </footer>
            </form>
        </field>
    </record>
</odoo>

./security/ir.model.access_transit_label.csv

id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_transit_label_print_wizard,transit.label.print.wizard,model_transit_label_print_wizard,stock_transit_allocation.group_transit_user,1,1,1,1

./models/stock_picking.py

# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    transit_voyage_ids = fields.One2many(
        'stock.transit.voyage',
        'picking_id',
        string='Viajes de Tránsito',
    )

    transit_count = fields.Integer(
        compute='_compute_transit_count',
    )

    supplier_shipment_id = fields.Many2one(
        'supplier.shipment',
        string='Embarque proveedor',
        copy=False,
        index=True,
        ondelete='set null',
        help='Relaciona esta recepción con un embarque específico capturado en el portal del proveedor.',
    )

    transit_sale_order_ids = fields.Many2many(
        'sale.order',
        string='Pedidos Consolidados',
        compute='_compute_transit_sale_orders',
        store=True,
    )

    @api.depends('move_ids.sale_line_id')
    def _compute_transit_sale_orders(self):
        for picking in self:
            picking.transit_sale_order_ids = picking.move_ids.sale_line_id.order_id

    def _compute_transit_count(self):
        for pick in self:
            pick.transit_count = len(pick.transit_voyage_ids)

    # -------------------------------------------------------------------------
    # HELPERS GENERALES
    # -------------------------------------------------------------------------

    def _tc_move_line_qty(self, move_line):
        """
        Compatibilidad Odoo 17/18/19:
        - En Odoo 18/19 suele usarse quantity.
        - En versiones previas o algunos flujos legacy puede existir qty_done.
        """
        if 'quantity' in move_line._fields:
            return move_line.quantity or 0.0

        return move_line.qty_done or 0.0

    def _tc_assignment_context(self):
        ctx = dict(self.env.context or {})
        ctx.update({
            'skip_stone_sync_so': True,
            'skip_stone_sync_picking': True,
            'skip_hold_validation': True,
            'skip_duplicate_lot_validation': True,
            'skip_picking_clean': True,
            'skip_transit_sale_sync': True,
            'skip_procurement': True,
            'skip_tc_allocation_recovery': True,
        })
        return ctx

    def _tc_prepare_stock_move_vals(
        self,
        picking,
        product,
        qty,
        sale_line=False,
        description=False,
    ):
        """
        Crea valores seguros para stock.move en Odoo 18/19.

        En Odoo 19 ya se detectó que stock.move puede no aceptar 'name',
        por eso todos los campos se validan contra _fields antes de crear.
        """
        self.ensure_one()

        Move = self.env['stock.move']
        move_fields = Move._fields

        vals = {
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': qty or 0.0,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'company_id': picking.company_id.id or self.company_id.id,
        }

        label = description or product.display_name

        if 'name' in move_fields:
            vals['name'] = label

        if 'description_picking' in move_fields:
            vals['description_picking'] = label

        if 'product_uom' in move_fields:
            vals['product_uom'] = product.uom_id.id
        elif 'product_uom_id' in move_fields:
            vals['product_uom_id'] = product.uom_id.id

        if 'picking_type_id' in move_fields:
            vals['picking_type_id'] = picking.picking_type_id.id

        if 'partner_id' in move_fields:
            vals['partner_id'] = picking.partner_id.id if picking.partner_id else False

        if 'group_id' in move_fields and picking.group_id:
            vals['group_id'] = picking.group_id.id

        if sale_line and 'sale_line_id' in move_fields:
            vals['sale_line_id'] = sale_line.id

        if 'date' in move_fields:
            vals['date'] = fields.Datetime.now()

        if 'procure_method' in move_fields:
            vals['procure_method'] = 'make_to_stock'

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_fields
        }

        return vals

    def _tc_prepare_stock_move_line_vals(
        self,
        picking,
        move,
        product,
        lot,
        qty,
        location_id=False,
        location_dest_id=False,
    ):
        """
        Crea valores seguros para stock.move.line en Odoo 18/19.
        """
        self.ensure_one()

        MoveLine = self.env['stock.move.line']
        move_line_fields = MoveLine._fields

        vals = {
            'picking_id': picking.id,
            'move_id': move.id,
            'company_id': picking.company_id.id or self.company_id.id,
            'product_id': product.id,
            'lot_id': lot.id if lot else False,
            'location_id': location_id or move.location_id.id,
            'location_dest_id': location_dest_id or move.location_dest_id.id,
        }

        if 'product_uom_id' in move_line_fields:
            vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in move_line_fields:
            vals['product_uom'] = product.uom_id.id

        if 'quantity' in move_line_fields:
            vals['quantity'] = qty or 0.0
        elif 'qty_done' in move_line_fields:
            vals['qty_done'] = qty or 0.0

        # Evita que Odoo 19 trate la línea como físicamente "picked"
        # antes de que el usuario valide la operación de salida.
        if 'picked' in move_line_fields:
            vals['picked'] = False

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_line_fields
        }

        return vals

    def _get_linked_reception_voyage(self):
        self.ensure_one()

        voyage = self.env['stock.transit.voyage'].search([
            ('reception_picking_id', '=', self.id),
        ], limit=1)

        if not voyage and self.origin:
            origin_ref = self.origin.split(' ')[0]
            voyage = self.env['stock.transit.voyage'].search([
                ('name', 'ilike', origin_ref),
            ], limit=1)

        return voyage

    def _auto_close_linked_transit_voyage(self):
        self.ensure_one()

        voyage = self._get_linked_reception_voyage()
        if not voyage:
            _logger.info(
                "[TC_DEBUG] Picking %s no tiene viaje de recepción vinculado para cierre automático.",
                self.name,
            )
            return

        try:
            voyage._auto_finalize_after_reception()
            _logger.info(
                "[TC_DEBUG] Viaje %s auto-cerrado tras validar %s.",
                voyage.name,
                self.name,
            )
        except Exception as e:
            _logger.error(
                "[TC_ERROR] Falló el cierre automático del viaje %s desde %s: %s",
                voyage.name,
                self.name,
                e,
                exc_info=True,
            )
            raise

    # -------------------------------------------------------------------------
    # ACCIONES
    # -------------------------------------------------------------------------

    def action_sync_from_voyage(self):
        self.ensure_one()
        _logger.info("[TC_DEBUG] Sincronizando Picking %s con Viaje...", self.name)

        voyage = self._get_linked_reception_voyage()

        if not voyage:
            raise UserError(_(
                "No se encontró un Viaje de Tránsito vinculado a esta recepción para sincronizar."
            ))

        if (
            self.picking_type_code == 'internal'
            and voyage.reception_picking_id
            and voyage.reception_picking_id.id == self.id
        ):
            voyage._sync_reception_picking_lines(self)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Recepción física saneada'),
                    'message': _(
                        'La recepción quedó abierta sin reservar ni validar. '
                        'Procese el Packing List físico y el Worksheet antes de validar.'
                    ),
                    'type': 'success',
                    'sticky': False,
                },
            }

        if self.state == 'done':
            raise UserError(_("No puede sincronizar una recepción ya validada."))

        if self.state == 'cancel':
            raise UserError(_("No puede sincronizar una recepción cancelada."))

        ctx = self._tc_assignment_context()

        if self.move_line_ids:
            self.move_line_ids.with_context(ctx).unlink()

        lines_created = 0

        for line in voyage.line_ids:
            if not line.lot_id or line.product_uom_qty <= 0:
                continue

            move = self.move_ids.filtered(
                lambda m: m.product_id.id == line.product_id.id
                and m.state not in ['done', 'cancel']
            )

            target_move = False

            if move:
                target_move = move[0]
            else:
                _logger.info(
                    "[TC_FIX] Creando demanda faltante para %s en %s",
                    line.product_id.name,
                    self.name,
                )

                try:
                    move_vals = self._tc_prepare_stock_move_vals(
                        picking=self,
                        product=line.product_id,
                        qty=line.product_uom_qty,
                        description=line.product_id.display_name,
                    )
                    target_move = self.env['stock.move'].with_context(ctx).create(move_vals)

                    if target_move.state == 'draft' and hasattr(target_move, '_action_confirm'):
                        target_move.with_context(ctx)._action_confirm()

                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] No se pudo crear demanda para %s: %s",
                        line.product_id.name,
                        e,
                        exc_info=True,
                    )
                    continue

            try:
                move_line_vals = self._tc_prepare_stock_move_line_vals(
                    picking=self,
                    move=target_move,
                    product=line.product_id,
                    lot=line.lot_id,
                    qty=line.product_uom_qty,
                    location_id=target_move.location_id.id,
                    location_dest_id=target_move.location_dest_id.id,
                )

                self.env['stock.move.line'].with_context(ctx).create(move_line_vals)
                lines_created += 1

            except Exception as e:
                _logger.error(
                    "[TC_ERROR] Error creando línea de sincronización para lote %s: %s",
                    line.lot_id.name,
                    e,
                    exc_info=True,
                )

        if lines_created > 0:
            msg = _(
                "Sincronización completada. %s líneas de lotes cargadas desde el Viaje %s."
            ) % (lines_created, voyage.name)

            self.message_post(body=msg)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sincronización Exitosa'),
                    'message': _(
                        'Los lotes han sido cargados. Verifique las cantidades y presione Validar.'
                    ),
                    'type': 'success',
                    'sticky': False,
                },
            }

        raise UserError(_(
            "No se encontraron líneas válidas con lote y cantidad mayor a cero en el viaje para sincronizar."
        ))

    def _tc_get_physical_reception_voyage_for_validation(self):
        self.ensure_one()

        if self.picking_type_code != 'internal':
            return False

        try:
            return self._get_linked_reception_voyage()
        except Exception as e:
            _logger.warning(
                "[TC_RECEPTION_GUARD] No se pudo resolver viaje de recepción para picking %s: %s",
                self.id,
                e,
                exc_info=True,
            )
            return False

    def _tc_context_allows_physical_reception_done(self):
        return bool(self.env.context.get('tc_allow_physical_reception_done'))

    def _tc_block_physical_reception_auto_done(self, operation_label=False):
        if self._tc_context_allows_physical_reception_done():
            return True

        blocked = []
        for pick in self:
            voyage = pick._tc_get_physical_reception_voyage_for_validation()
            if voyage:
                blocked.append((pick, voyage))

        if not blocked:
            return True

        detail = []
        for pick, voyage in blocked:
            detail.append('%s / %s' % (pick.name or pick.display_name, voyage.name or voyage.display_name))

        raise UserError(_(
            "Control Tower bloqueó una validación automática de recepción física.\n\n"
            "Operación detectada: %(operation)s\n"
            "Recepción(es):\n- %(pickings)s\n\n"
            "Estas recepciones solo pueden quedar en HECHO mediante el botón Validar, "
            "después de procesar Packing List físico y Worksheet."
        ) % {
            'operation': operation_label or _('marcar como hecho'),
            'pickings': '\n- '.join(detail),
        })

    def _action_done(self, *args, **kwargs):
        self._tc_block_physical_reception_auto_done(
            operation_label=_("_action_done"),
        )
        return super(StockPicking, self)._action_done(*args, **kwargs)

    def _tc_assert_physical_reception_can_validate(self, voyage=False):
        self.ensure_one()

        voyage = voyage or self._tc_get_physical_reception_voyage_for_validation()
        if not voyage:
            return True

        if self.env.context.get('tc_physical_reception_prepare') or self.env.context.get('tc_no_auto_validate'):
            raise UserError(_(
                "La recepción física %(picking)s está en preparación desde Torre de Control.\n\n"
                "El botón Recibir/Abrir Recepción no puede validar ni marcar la operación como hecha. "
                "Primero debe trabajar el Packing List físico y el Worksheet."
            ) % {
                'picking': self.name or self.display_name,
            })

        if self.state == 'cancel':
            raise UserError(_("No puede validar una recepción física cancelada."))

        missing_steps = []

        if 'packing_list_imported' not in self._fields:
            missing_steps.append(_("El módulo no expone el control packing_list_imported."))
        elif not self.packing_list_imported:
            missing_steps.append(_("Procesar o reprocesar el Packing List físico."))

        if 'worksheet_imported' not in self._fields:
            missing_steps.append(_("El módulo no expone el control worksheet_imported."))
        elif not self.worksheet_imported:
            missing_steps.append(_("Procesar el Worksheet físico."))

        positive_physical_lines = self.move_line_ids.filtered(
            lambda move_line: move_line.product_id
            and move_line.lot_id
            and self._tc_move_line_qty(move_line) > 0
        )

        if not self.move_line_ids:
            missing_steps.append(_("Cargar líneas físicas con lote y cantidad real."))
        elif not positive_physical_lines:
            missing_steps.append(_("Cargar al menos una línea física con lote y cantidad real positiva."))
        else:
            lines_without_lot = self.move_line_ids.filtered(
                lambda move_line: move_line.product_id
                and self._tc_move_line_qty(move_line) > 0
                and not move_line.lot_id
            )
            if lines_without_lot:
                missing_steps.append(_("Todas las líneas físicas con cantidad positiva deben tener lote/placa."))

        if missing_steps:
            raise UserError(_(
                "No puede validar la recepción física %(picking)s del embarque %(voyage)s todavía.\n\n"
                "Pendiente:\n- %(steps)s\n\n"
                "Flujo requerido:\n"
                "1. Abrir la recepción.\n"
                "2. Corregir o confirmar el Packing List físico.\n"
                "3. Procesar/reprocesar el Packing List físico.\n"
                "4. Generar y procesar el Worksheet.\n"
                "5. Validar manualmente la recepción física."
            ) % {
                'picking': self.name or self.display_name,
                'voyage': voyage.name if voyage else '',
                'steps': '\n- '.join(missing_steps),
            })

        return True

    def _tc_assert_no_forced_done_during_physical_reception(self, operation_label=False):
        """
        Guarda dura para cualquier camino que intente cerrar la recepción física.

        Solo se permite cerrar con el contexto tc_allow_physical_reception_done,
        que se inyecta exclusivamente desde button_validate() después de validar
        Packing List físico, Worksheet y líneas reales. Aunque PL/Worksheet ya
        estén procesados, una automatización externa no debe marcar HECHO.
        """
        if self._tc_context_allows_physical_reception_done():
            return True

        self._tc_block_physical_reception_auto_done(
            operation_label=operation_label or _('_action_done/write(state=done)'),
        )
        return True

    def write(self, vals):
        vals = dict(vals or {})

        if vals.get('state') == 'done' and not self._tc_context_allows_physical_reception_done():
            self._tc_assert_no_forced_done_during_physical_reception(
                operation_label=_("write(state=done)"),
            )

        return super(StockPicking, self).write(vals)

    def button_validate(self):
        """
        Validación protegida para recepción física.

        Regla:
        Si el picking interno está vinculado como recepción física de un viaje,
        NO se puede validar hasta que el Worksheet físico haya sido procesado.

        Además, cuando la recepción física se valida, se pasan los lotes
        preasignados desde Torre de Control hacia la entrega del pedido.
        """
        physical_voyage_by_pick = {}

        for pick in self:
            voyage = pick._tc_get_physical_reception_voyage_for_validation()
            if voyage:
                physical_voyage_by_pick[pick.id] = voyage
                if pick.state != 'done':
                    pick._tc_assert_physical_reception_can_validate(voyage=voyage)

        _logger.info(
            "=== [TC_DEBUG] VALIDATE BUTTON CLICKED - Picking IDs: %s ===",
            self.ids,
        )

        res = super(StockPicking, self.with_context(tc_allow_physical_reception_done=True)).button_validate()

        for pick in self:
            voyage = physical_voyage_by_pick.get(pick.id)
            if voyage and pick.state == 'done':
                pick._tc_assert_physical_reception_can_validate(voyage=voyage)

        for pick in self:
            is_transit_loc = False
            dest_loc = pick.location_dest_id

            if dest_loc and (
                dest_loc.id == 128
                or any(x in (dest_loc.name or '') for x in ['Transit', 'Tránsito', 'Trancit'])
            ):
                is_transit_loc = True

            if is_transit_loc and pick.picking_type_code == 'incoming':
                _logger.info(
                    "[TC_DEBUG] Picking %s detectado como Entrada a Tránsito. Creando Viaje independiente...",
                    pick.name,
                )
                pick._create_automatic_transit_voyage()

            if pick.picking_type_code == 'internal' and pick.state == 'done':
                _logger.info(
                    "[TC_DEBUG] Picking %s validado Internal/Done. Iniciando lógica de asignación a Ventas...",
                    pick.name,
                )

                try:
                    pick._assign_lots_to_delivery_orders()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] Falló la asignación automática en %s: %s",
                        pick.name,
                        str(e),
                        exc_info=True,
                    )
                    raise

                try:
                    pick._auto_close_linked_transit_voyage()
                except Exception as e:
                    _logger.error(
                        "[TC_ERROR] Falló el cierre automático del viaje en %s: %s",
                        pick.name,
                        str(e),
                        exc_info=True,
                    )
                    raise

        _logger.info(
            "=== [TC_DEBUG] VALIDATION FINISHED - Picking IDs: %s ===",
            self.ids,
        )
        return res

    def action_print_reception_labels(self):
        self.ensure_one()
        return {
            'name': _('Imprimir Etiquetas'),
            'type': 'ir.actions.act_window',
            'res_model': 'transit.label.print.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
            }
        }

    # -------------------------------------------------------------------------
    # HELPERS: PASAR ASIGNACIÓN DE TRÁNSITO A ENTREGA
    # -------------------------------------------------------------------------

    def _tc_get_sale_line_for_assignment(self, order, product):
        SaleLine = self.env['sale.order.line']

        if not order or not product:
            return SaleLine

        lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.id == product.id
        )

        if not lines:
            return SaleLine

        pending_lines = lines.filtered(
            lambda l: (l.product_uom_qty or 0.0) > (l.qty_delivered or 0.0)
        )

        return (pending_lines or lines)[:1]

    def _tc_build_lot_breakdown_from_transit_lines(self, transit_lines):
        breakdown = {}

        for transit_line in transit_lines:
            lot = transit_line.lot_id
            if not lot:
                continue

            lot_type = ''
            if 'x_tipo' in lot._fields and lot.x_tipo:
                lot_type = str(lot.x_tipo).lower()

            if lot_type in ('formato', 'pieza'):
                breakdown[str(lot.id)] = transit_line.product_uom_qty or 0.0

        return breakdown

    def _tc_prepare_breakdown_value_for_sale_line(self, sale_line, breakdown):
        if not breakdown:
            return False

        field = sale_line._fields.get('x_lot_breakdown_json')
        if field and field.type in ('char', 'text'):
            return json.dumps(breakdown)

        return breakdown

    def _tc_sync_sale_line_lots_after_reception(self, sale_line, transit_lines):
        """
        Fusiona en la SO los lotes recibidos/asignados desde tránsito.

        Flujo mixto:
        - Conserva placas ya seleccionadas manualmente.
        - Agrega lotes comprados/recibidos desde tránsito.
        - Solo limpia Mandar a pedir cuando ya no queda pendiente real.
        """
        if not sale_line or 'lot_ids' not in sale_line._fields:
            return False

        transit_lot_ids = transit_lines.mapped('lot_id').ids
        current_lot_ids = sale_line.lot_ids.ids if sale_line.lot_ids else []

        merged_lot_ids = list(current_lot_ids)
        for lot_id in transit_lot_ids:
            if lot_id not in merged_lot_ids:
                merged_lot_ids.append(lot_id)

        vals = {
            'lot_ids': [(6, 0, merged_lot_ids)],
        }

        if 'x_lot_breakdown_json' in sale_line._fields:
            breakdown = sale_line._tc_read_lot_breakdown() if hasattr(sale_line, '_tc_read_lot_breakdown') else {}
            transit_breakdown = self._tc_build_lot_breakdown_from_transit_lines(transit_lines)
            breakdown.update(transit_breakdown)

            if hasattr(sale_line, '_tc_prepare_breakdown_value_for_line'):
                vals['x_lot_breakdown_json'] = sale_line._tc_prepare_breakdown_value_for_line(breakdown)
            else:
                vals['x_lot_breakdown_json'] = self._tc_prepare_breakdown_value_for_sale_line(
                    sale_line,
                    breakdown,
                )

        sale_line.with_context(
            skip_stone_sync_picking=True,
            skip_stone_sync_so=True,
            skip_hold_validation=True,
            skip_duplicate_lot_validation=True,
            skip_picking_clean=True,
            skip_transit_sale_sync=True,
            skip_tc_allocation_recovery=True,
        ).write(vals)

        if hasattr(sale_line, '_tc_get_pending_allocation_qty'):
            pending_qty = sale_line._tc_get_pending_allocation_qty()
            if sale_line._tc_float_le_zero(pending_qty):
                clear_vals = {}
                if 'auto_transit_assign' in sale_line._fields:
                    clear_vals['auto_transit_assign'] = False
                if 'tc_stock_rejected' in sale_line._fields:
                    clear_vals.update({
                        'tc_stock_rejected': False,
                        'tc_stock_rejected_reason': False,
                        'tc_stock_rejected_by': False,
                        'tc_stock_rejected_at': False,
                    })
                if clear_vals:
                    sale_line.with_context(
                        skip_tc_allocation_recovery=True,
                        skip_duplicate_lot_validation=True,
                    ).write(clear_vals)

        _logger.info(
            "[TC_ASSIGN] SO line %s lotes fusionados: actuales=%s tránsito=%s final=%s",
            sale_line.id,
            current_lot_ids,
            transit_lot_ids,
            merged_lot_ids,
        )

        return True

    def _tc_find_delivery_for_order(self, order, delivery_cache, product=False, sale_line=False):
        """
        Busca la operación de venta pendiente asociada a una orden.

        Odoo 19 / rutas multi-step:
        - En almacenes con Pick/Pack/Ship, la primera operación vinculada a la SO
          puede ser internal, por ejemplo SOM/PICK/00039.
        - No se debe limitar la búsqueda a picking_type_code = outgoing.
        - La clave real observada es stock.move.sale_line_id.
        """
        self.ensure_one()

        if not order or not order.exists():
            return False

        cache_key = (
            order.id,
            sale_line.id if sale_line else 0,
            product.id if product else 0,
        )

        if cache_key in delivery_cache:
            return delivery_cache[cache_key]

        Picking = self.env['stock.picking']
        Move = self.env['stock.move']

        pending_states = [
            'draft',
            'waiting',
            'confirmed',
            'assigned',
            'partially_available',
        ]

        # CRÍTICO:
        # outgoing = entrega directa / salida final
        # internal = picking/preparación en rutas multi-step, ej. SOM/PICK/00039.
        sale_operation_codes = ['outgoing', 'internal']

        base_domain = [
            ('id', '!=', self.id),
            ('picking_type_code', 'in', sale_operation_codes),
            ('state', 'in', pending_states),
            ('company_id', 'in', [False, self.company_id.id]),
        ]

        delivery = Picking
        has_sale_line_id = 'sale_line_id' in Move._fields

        def _search(domain, order_by='id asc'):
            return Picking.search(domain, order=order_by, limit=1)

        # 1) Búsqueda más precisa: operación con move de la sale_line exacta.
        if not delivery and sale_line and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id', '=', sale_line.id),
                ]
            )

        # 2) Operación con movimientos de la orden y producto.
        if not delivery and product and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id.order_id', '=', order.id),
                    ('move_ids.product_id', '=', product.id),
                ]
            )

        # 3) Cualquier operación pendiente ligada a la orden.
        if not delivery and has_sale_line_id:
            delivery = _search(
                base_domain + [
                    ('move_ids.sale_line_id.order_id', '=', order.id),
                ]
            )

        # 4) Procurement group defensivo.
        procurement_group = False

        if 'procurement_group_id' in order._fields:
            procurement_group = order.procurement_group_id
        elif 'group_id' in order._fields:
            procurement_group = order.group_id

        if (
            not delivery
            and procurement_group
            and procurement_group.exists()
            and 'group_id' in Picking._fields
        ):
            delivery = _search(
                base_domain + [
                    ('group_id', '=', procurement_group.id),
                ]
            )

        # 5) Búsqueda legacy por sale_id si existe en stock.picking.
        if not delivery and 'sale_id' in Picking._fields:
            delivery = _search(
                base_domain + [
                    ('sale_id', '=', order.id),
                ]
            )

        # 6) Origin exacto.
        if not delivery:
            delivery = _search(
                base_domain + [
                    ('origin', '=', order.name),
                ]
            )

        # 7) Origin flexible.
        if not delivery:
            delivery = _search(
                base_domain + [
                    ('origin', 'ilike', order.name),
                ]
            )

        if delivery:
            _logger.info(
                "[TC_ASSIGN] Operación de venta encontrada para pedido %s | picking=%s | type=%s | sale_line=%s | product=%s",
                order.name,
                delivery.name,
                delivery.picking_type_code,
                sale_line.id if sale_line else False,
                product.display_name if product else False,
            )
        else:
            _logger.warning(
                "[TC_ASSIGN] No se encontró operación de venta pendiente para pedido %s | sale_line=%s | product=%s",
                order.name,
                sale_line.id if sale_line else False,
                product.display_name if product else False,
            )

        delivery_cache[cache_key] = delivery or False
        return delivery_cache[cache_key]

    def _tc_get_or_create_delivery_move(self, delivery, sale_line, product, qty):
        self.ensure_one()

        ctx = self._tc_assignment_context()
        Move = self.env['stock.move']
        has_sale_line_id = 'sale_line_id' in Move._fields

        if has_sale_line_id:
            move = delivery.move_ids.filtered(
                lambda m: m.sale_line_id.id == sale_line.id
                and m.product_id.id == product.id
                and m.state not in ['done', 'cancel']
            )[:1]
        else:
            move = delivery.move_ids.filtered(
                lambda m: m.product_id.id == product.id
                and m.state not in ['done', 'cancel']
            )[:1]

        if move:
            if move.product_uom_qty < qty:
                move.with_context(ctx).write({'product_uom_qty': qty})
            return move

        move_vals = self._tc_prepare_stock_move_vals(
            picking=delivery,
            product=product,
            qty=qty,
            sale_line=sale_line,
            description=sale_line.name or product.display_name,
        )

        move = self.env['stock.move'].with_context(ctx).create(move_vals)

        if move.state == 'draft' and hasattr(move, '_action_confirm'):
            move.with_context(ctx)._action_confirm()

        return move

    def _tc_get_sale_order_from_move_line(self, move_line):
        """
        Resuelve la venta de una stock.move.line de forma defensiva.

        Orden:
        1. move_id.sale_line_id.order_id
        2. picking.sale_id, si existe
        3. picking.origin exacto contra sale.order.name
        """
        SaleOrder = self.env['sale.order'].sudo()

        if (
            move_line.move_id
            and move_line.move_id.sale_line_id
            and move_line.move_id.sale_line_id.order_id
        ):
            return move_line.move_id.sale_line_id.order_id.sudo()

        picking = move_line.picking_id

        if picking and 'sale_id' in picking._fields and picking.sale_id:
            return picking.sale_id.sudo()

        if picking and picking.origin:
            sale_order = SaleOrder.search([('name', '=', picking.origin)], limit=1)
            if sale_order:
                return sale_order

        return SaleOrder.browse()

    def _tc_release_conflicting_auto_assignments(
        self,
        lot,
        product,
        current_order,
        source_location_id,
    ):
        """
        Libera autoasignaciones conflictivas generadas en la misma transacción.

        Caso cubierto:
        - Al validar Transit -> Stock, Odoo/sale_stone_selection puede intentar
          reservar automáticamente el lote para otra venta pendiente.
        - Control Tower es la fuente de verdad para lotes reservados en tránsito.
        - Antes de crear la línea exacta para la venta destino, se eliminan solo
          las move lines pendientes de otra venta para el mismo lote/producto/origen.

        No toca operaciones done/cancel.
        No libera líneas del pedido actual.
        No borra demanda comercial; solo quita la línea detallada de lote incorrecta.
        """
        self.ensure_one()

        if not (
            lot
            and lot.exists()
            and product
            and product.exists()
            and current_order
            and current_order.exists()
            and source_location_id
        ):
            return self.env['stock.move.line']

        MoveLine = self.env['stock.move.line'].sudo()
        qty_field = 'quantity' if 'quantity' in MoveLine._fields else 'qty_done'

        blockers = MoveLine.search([
            ('product_id', '=', product.id),
            ('lot_id', '=', lot.id),
            ('location_id', '=', source_location_id),
            ('picking_id', '!=', False),
            ('picking_id.state', 'not in', ['done', 'cancel']),
            ('state', 'not in', ['done', 'cancel']),
            (qty_field, '>', 0),
        ])

        if not blockers:
            return blockers

        blockers = blockers.filtered(
            lambda ml:
                self._tc_get_sale_order_from_move_line(ml)
                and self._tc_get_sale_order_from_move_line(ml).id != current_order.id
        )

        if not blockers:
            return blockers

        ctx = self._tc_assignment_context()

        _logger.warning(
            "[TC_ASSIGN_RELEASE] Liberando autoasignaciones conflictivas | "
            "Recepción=%s | Lote=%s | Producto=%s | Venta destino=%s | Líneas=%s",
            self.name,
            lot.name,
            product.display_name,
            current_order.name,
            blockers.ids,
        )

        for move_line in blockers:
            sale_order = self._tc_get_sale_order_from_move_line(move_line)
            sale_line = move_line.move_id.sale_line_id if move_line.move_id else False

            _logger.warning(
                "[TC_ASSIGN_RELEASE] Línea conflictiva removida | "
                "ML=%s | Picking=%s | Venta=%s | Lote=%s | Qty=%.4f",
                move_line.id,
                move_line.picking_id.name if move_line.picking_id else False,
                sale_order.name if sale_order else False,
                lot.name,
                float(getattr(move_line, qty_field, 0.0) or 0.0),
            )

            if move_line.exists():
                move_line.with_context(ctx).unlink()

            if sale_line and 'lot_ids' in sale_line._fields and lot in sale_line.lot_ids:
                sale_line.with_context(ctx).write({
                    'lot_ids': [(3, lot.id)],
                })

        return blockers

    def _assign_lots_to_delivery_orders(self):
        """
        Al validar la recepción física Transit → Stock:

        1. Lee las preasignaciones del viaje.
        2. Solo procesa líneas allocation_status = reserved.
        3. Solo procesa lotes realmente recibidos en este picking.
        4. Reemplaza en la SO únicamente los lotes del mismo producto.
        5. Libera autoasignaciones conflictivas de otras ventas.
        6. Crea/actualiza la entrega con sale_line_id y lotes exactos.
        """
        self.ensure_one()
        _logger.info("[TC_ASSIGN] START recepción física %s", self.name)

        voyage = self._get_linked_reception_voyage()

        if not voyage:
            _logger.info(
                "[TC_ASSIGN] Picking %s sin viaje vinculado. Se omite asignación a ventas.",
                self.name,
            )
            return

        Quant = self.env['stock.quant'].sudo()

        received_by_lot = {}

        for move_line in self.move_line_ids:
            if not move_line.lot_id:
                continue

            qty = self._tc_move_line_qty(move_line)
            if qty <= 0:
                continue

            lot_id = move_line.lot_id.id

            if lot_id not in received_by_lot:
                received_by_lot[lot_id] = {
                    'qty': 0.0,
                    'move_line': move_line,
                    'location_dest_id': move_line.location_dest_id.id,
                }

            received_by_lot[lot_id]['qty'] += qty

        if not received_by_lot:
            _logger.info("[TC_ASSIGN] Picking %s no tiene lotes recibidos.", self.name)
            return

        assignments_by_key = {}

        reserved_lines = voyage.line_ids.filtered(
            lambda l: l.lot_id
            and l.order_id
            and l.product_id
            and l.allocation_status == 'reserved'
            and l.lot_id.id in received_by_lot
        )

        for line in reserved_lines:
            key = (line.order_id.id, line.product_id.id)
            assignments_by_key.setdefault(key, self.env['stock.transit.line'])
            assignments_by_key[key] |= line

        if not assignments_by_key:
            _logger.info(
                "[TC_ASSIGN] Viaje %s no tiene lotes reservados recibidos en %s. Todo queda libre.",
                voyage.name,
                self.name,
            )
            return

        delivery_cache = {}
        total_assigned_lots = 0

        ctx = self._tc_assignment_context()

        for (order_id, product_id), transit_lines in assignments_by_key.items():
            order = self.env['sale.order'].browse(order_id)
            product = self.env['product.product'].browse(product_id)

            if not order.exists() or not product.exists():
                continue

            if order.state not in ('sale', 'done'):
                raise UserError(_(
                    "El pedido %s tiene lotes preasignados desde tránsito, pero no está confirmado."
                ) % order.name)

            sale_line = self._tc_get_sale_line_for_assignment(order, product)

            if not sale_line:
                raise UserError(_(
                    "El pedido %(order)s no tiene una línea vigente para el producto %(product)s."
                ) % {
                    'order': order.name,
                    'product': product.display_name,
                })

            delivery = self._tc_find_delivery_for_order(
                order=order,
                delivery_cache=delivery_cache,
                product=product,
                sale_line=sale_line,
            )

            if not delivery:
                raise UserError(_(
                    "No se encontró una operación de venta pendiente para el pedido %s.\n\n"
                    "Confirme que el pedido tenga un picking activo vinculado a la línea de venta."
                ) % order.name)

            total_qty = 0.0

            for transit_line in transit_lines:
                received_data = received_by_lot.get(transit_line.lot_id.id)
                if not received_data:
                    continue
                total_qty += received_data['qty']

            if total_qty <= 0:
                continue

            # 1) Fusionar selección oficial en la línea de venta.
            #    No reemplaza placas manuales; agrega las compradas/recibidas.
            self._tc_sync_sale_line_lots_after_reception(sale_line, transit_lines)

            assigned_qty = total_qty
            if hasattr(sale_line, '_tc_get_assigned_lot_qty'):
                assigned_qty = sale_line._tc_get_assigned_lot_qty()

            requested_qty = sale_line.product_uom_qty or assigned_qty or total_qty
            target_qty = min(requested_qty, assigned_qty) if assigned_qty else total_qty

            # 2) Obtener/crear movimiento de entrega vinculado a la sale_line.
            target_move = self._tc_get_or_create_delivery_move(
                delivery=delivery,
                sale_line=sale_line,
                product=product,
                qty=target_qty,
            )

            if target_move.product_uom_qty != target_qty:
                target_move.with_context(ctx).write({'product_uom_qty': target_qty})

            # 3) No se llama _do_unreserve() sobre todo el move porque eso puede
            #    soltar placas manuales ya asignadas. Solo reconstruimos los lotes
            #    que llegaron desde tránsito para evitar duplicados.
            transit_lot_ids = transit_lines.mapped('lot_id').ids
            existing_transit_move_lines = target_move.move_line_ids.filtered(
                lambda ml: ml.lot_id and ml.lot_id.id in transit_lot_ids
            )
            if existing_transit_move_lines:
                existing_transit_move_lines.with_context(ctx).unlink()

            # 4) Crear move lines exactas desde ubicación interna real para los lotes recibidos.
            for transit_line in transit_lines:
                lot = transit_line.lot_id
                received_data = received_by_lot.get(lot.id)

                if not received_data:
                    continue

                qty_to_assign = received_data['qty']
                source_location_id = received_data['location_dest_id']

                quant = Quant.search([
                    ('company_id', '=', self.company_id.id),
                    ('product_id', '=', product.id),
                    ('lot_id', '=', lot.id),
                    ('quantity', '>', 0),
                    ('location_id.usage', '=', 'internal'),
                ], order='id desc', limit=1)

                if quant:
                    source_location_id = quant.location_id.id
                    qty_to_assign = min(qty_to_assign, quant.quantity)

                if qty_to_assign <= 0:
                    continue

                # Corrección crítica:
                # Antes de crear la línea exacta para la venta definida por Torre
                # de Control, se liberan autoasignaciones transaccionales del mismo
                # lote hechas por Odoo u otros módulos a ventas distintas.
                self._tc_release_conflicting_auto_assignments(
                    lot=lot,
                    product=product,
                    current_order=order,
                    source_location_id=source_location_id,
                )

                move_line_vals = self._tc_prepare_stock_move_line_vals(
                    picking=delivery,
                    move=target_move,
                    product=product,
                    lot=lot,
                    qty=qty_to_assign,
                    location_id=source_location_id,
                    location_dest_id=target_move.location_dest_id.id,
                )

                self.env['stock.move.line'].with_context(ctx).create(move_line_vals)

                total_assigned_lots += 1

                _logger.info(
                    "[TC_ASSIGN] Pedido %s | Operación %s | Producto %s | Lote %s | Qty %.4f",
                    order.name,
                    delivery.name,
                    product.display_name,
                    lot.name,
                    qty_to_assign,
                )

            delivery.message_post(body=_(
                "🚚 Material recibido desde tránsito asignado automáticamente.<br/>"
                "<b>Viaje:</b> %(voyage)s<br/>"
                "<b>Pedido:</b> %(order)s<br/>"
                "<b>Producto:</b> %(product)s<br/>"
                "<b>Lotes:</b> %(lots)s"
            ) % {
                'voyage': voyage.name,
                'order': order.name,
                'product': product.display_name,
                'lots': ', '.join(transit_lines.mapped('lot_id.name')),
            })

        _logger.info(
            "[TC_ASSIGN] FIN recepción %s. Lotes asignados a entregas: %s",
            self.name,
            total_assigned_lots,
        )

    def _create_automatic_transit_voyage(self):
        """
        Crea SIEMPRE un nuevo voyage por picking.
        No reutiliza voyages existentes de la misma OC.
        """
        self.ensure_one()
        Voyage = self.env['stock.transit.voyage']

        existing = Voyage.search([
            ('picking_id', '=', self.id),
            ('custom_status', '!=', 'cancel'),
        ], limit=1)

        if existing:
            _logger.info(
                "[TC] Picking %s ya tiene voyage %s, actualizando lotes.",
                self.name,
                existing.name,
            )
            existing.action_load_from_picking()
            return

        bl = (
            getattr(self, 'supplier_bl_number', None)
            or self.origin
            or self.name
        )

        voyage = Voyage.create({
            'picking_id': self.id,
            'purchase_id': self.purchase_id.id if self.purchase_id else False,
            'bl_number': bl,
            'etd': fields.Date.today(),
            'custom_status': 'on_sea',
        })

        _logger.info(
            "[TC] Voyage %s creado para picking %s OC: %s",
            voyage.name,
            self.name,
            self.purchase_id.name if self.purchase_id else 'N/A',
        )

        voyage.action_load_from_picking()

    def action_view_transit_voyage(self):
        self.ensure_one()
        return {
            'name': 'Gestión de Tránsito',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.transit.voyage',
            'view_mode': 'list,form',
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id},
        }


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _tc_guarded_physical_reception_pickings(self):
        return self.mapped('picking_id').filtered(
            lambda picking: picking
            and picking.exists()
            and hasattr(picking, '_tc_get_physical_reception_voyage_for_validation')
            and picking._tc_get_physical_reception_voyage_for_validation()
        )

    def _tc_assert_physical_reception_moves_can_be_done(self):
        """Bloquea _action_done() sobre recepciones físicas no autorizadas."""
        pickings = self._tc_guarded_physical_reception_pickings()

        for picking in pickings:
            if hasattr(picking, '_tc_assert_no_forced_done_during_physical_reception'):
                picking._tc_assert_no_forced_done_during_physical_reception(
                    operation_label=_("stock.move._action_done"),
                )

        return True

    def _action_assign(self, *args, **kwargs):
        if self.env.context.get('tc_physical_reception_prepare') or self.env.context.get('tc_no_auto_validate'):
            guarded_moves = self.filtered(
                lambda move: move.picking_id in self._tc_guarded_physical_reception_pickings()
            )
            other_moves = self - guarded_moves

            if guarded_moves:
                _logger.info(
                    "[TC_RECEPTION_GUARD] Se omitió _action_assign durante preparación de recepción física. moves=%s pickings=%s",
                    guarded_moves.ids,
                    guarded_moves.mapped('picking_id.name'),
                )

            if other_moves:
                return super(StockMove, other_moves)._action_assign(*args, **kwargs)

            return True

        return super(StockMove, self)._action_assign(*args, **kwargs)

    def _action_done(self, *args, **kwargs):
        self._tc_assert_physical_reception_moves_can_be_done()
        return super(StockMove, self)._action_done(*args, **kwargs)

./views/stock_picking_views.xml

<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!--
        Extensión propia de Torre de Control.

        Este archivo NO crea botones nuevos de Packing List / Worksheet.
        Los botones originales ya existen en stock_lot_packing_import.

        Aquí solo agregamos:
        - Campo técnico tc_is_physical_reception
        - Botón Sincronizar con Embarque
        - Smart button de tránsito
        - Pedidos consolidados

        Y después reactivamos un parche de visibilidad sobre los botones originales.
    -->
    <record id="view_picking_form_transit_inherit" model="ir.ui.view">
        <field name="name">stock.picking.form.transit.inherit</field>
        <field name="model">stock.picking</field>
        <field name="inherit_id" ref="stock.view_picking_form"/>
        <field name="priority">40</field>
        <field name="arch" type="xml">

            <field name="partner_id" position="after">
                <field name="tc_is_physical_reception" invisible="1"/>
            </field>

            <xpath expr="//header" position="inside">
                <button name="action_sync_from_voyage"
                        string="📲 Sincronizar con Embarque"
                        type="object"
                        class="btn-primary"
                        groups="stock.group_stock_user"
                        invisible="not tc_is_physical_reception or state in ('done', 'cancel')"/>

                <button name="action_print_reception_labels"
                        string="🏷️ Imprimir Etiquetas"
                        type="object"
                        class="btn-secondary"
                        invisible="not tc_is_physical_reception"/>
            </xpath>

            <div name="button_box" position="inside">
                <button name="action_view_transit_voyage"
                        type="object"
                        class="oe_stat_button"
                        icon="fa-ship"
                        invisible="transit_count == 0">
                    <field name="transit_count"
                           widget="statinfo"
                           string="En Tránsito"/>
                </button>
            </div>

            <xpath expr="//field[@name='origin']" position="after">
                <field name="transit_sale_order_ids"
                       widget="many2many_tags"
                       invisible="picking_type_code != 'incoming'"
                       string="Pedidos (Consolidado)"/>
            </xpath>

        </field>
    </record>


    <!--
        Parche sobre los botones ORIGINALES del módulo principal:
        stock_lot_packing_import.view_picking_form_inherit_packing_import

        No se crea ningún botón nuevo.
        Solo se ajusta la visibilidad de los botones existentes para que también
        aparezcan en recepciones físicas internas de Torre de Control.

        Importante Odoo 19:
        - No usar @string como selector XPath.
        - No usar atributos invisible multilínea.
    -->
    <record id="view_picking_form_transit_patch_packing_import_buttons" model="ir.ui.view">
        <field name="name">stock.picking.form.transit.patch.packing.import.buttons</field>
        <field name="model">stock.picking</field>
        <field name="inherit_id" ref="stock_lot_packing_import.view_picking_form_inherit_packing_import"/>
        <field name="priority">99</field>
        <field name="active" eval="True"/>
        <field name="arch" type="xml">

            <!-- Campo técnico disponible dentro de la rama heredada del módulo principal. -->
            <xpath expr="//field[@name='supplier_access_ids']" position="after">
                <field name="tc_is_physical_reception" invisible="1"/>
            </xpath>

            <!-- Abrir PL: primer botón action_open_packing_list_spreadsheet del módulo principal. -->
            <xpath expr="(//header/button[@name='action_open_packing_list_spreadsheet'])[1]" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or packing_list_imported or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Procesar PL: primer botón action_import_packing_list del módulo principal. -->
            <xpath expr="(//header/button[@name='action_import_packing_list'])[1]" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or packing_list_imported or not spreadsheet_id or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Corregir PL: segundo botón action_open_packing_list_spreadsheet del módulo principal. -->
            <xpath expr="(//header/button[@name='action_open_packing_list_spreadsheet'])[2]" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or not packing_list_imported or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Reprocesar PL: segundo botón action_import_packing_list del módulo principal. -->
            <xpath expr="(//header/button[@name='action_import_packing_list'])[2]" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or not packing_list_imported or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Imprimir Worksheet original. -->
            <xpath expr="//header/button[@name='action_print_worksheet_pdf']" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or not packing_list_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Abrir Worksheet original. -->
            <xpath expr="//header/button[@name='action_open_worksheet_spreadsheet']" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or not packing_list_imported or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

            <!-- Procesar Worksheet original. -->
            <xpath expr="//header/button[@name='action_import_worksheet']" position="attributes">
                <attribute name="invisible">state in ('done', 'cancel', 'draft') or not packing_list_imported or not ws_spreadsheet_id or worksheet_imported or (picking_type_code != 'incoming' and not tc_is_physical_reception)</attribute>
            </xpath>

        </field>
    </record>

</odoo>

./models/stock_transit_voyage.py

# -*- coding: utf-8 -*-
import json
import logging
import re

import requests

from markupsafe import Markup

from odoo import models, api, _
from odoo import fields as fields_module
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_compare, float_is_zero

fields = fields_module

_logger = logging.getLogger(__name__)

try:
    import folium
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False
    _logger.warning("Folium no está instalado. pip install folium --break-system-packages")


ETA_DRAMATIC_CHANGE_DAYS = 5
ETA_WARNING_DAYS_BEFORE = 1
ETA_OVERDUE_DAYS_AFTER = 1


class StockTransitVoyage(models.Model):
    _name = 'stock.transit.voyage'
    _description = 'Viaje / Contenedor en Tránsito'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'eta asc'

    name = fields_module.Char(
        string='Referencia Viaje',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )

    custom_status = fields_module.Selection(
        [
            ('solicitud', 'Solicitud Enviada'),
            ('production', 'Producción'),
            ('booking', 'Booking'),
            ('puerto_origen', 'Puerto Origen'),
            ('on_sea', 'En Altamar / Mar'),
            ('puerto_destino', 'Puerto Destino'),
            ('arrived_port', 'Arribo a Puerto (Trámite)'),
            ('reception_pending', 'En Recepción Física'),
            ('delivered', 'Entregado en Almacén'),
            ('cancel', 'Cancelado'),
        ],
        string='Estado',
        default='solicitud',
        tracking=True,
    )

    shipping_line = fields_module.Char(
        string='Naviera',
        tracking=True,
    )
    transit_days_expected = fields_module.Integer(
        string='Tiempo Tránsito (Días)',
    )
    vessel_name = fields_module.Char(
        string='Buque / Barco',
        tracking=True,
    )
    voyage_number = fields_module.Char(
        string='No. Viaje',
        tracking=True,
    )

    container_number = fields_module.Char(
        string='Contenedores',
        compute='_compute_container_number',
        store=True,
        tracking=True,
        help="Resumen automático de contenedores presentes en las líneas del viaje",
    )

    bl_number = fields_module.Char(
        string='Folio Compra / BL',
        tracking=True,
    )

    etd = fields_module.Date(
        string='ETD (Salida Estimada)',
    )
    eta = fields_module.Date(
        string='ETA (Llegada Estimada)',
        required=False,
        tracking=True,
    )
    eta_original = fields_module.Date(
        string='ETA Original',
        readonly=True,
        copy=False,
        tracking=True,
    )

    delay_days = fields_module.Integer(
        string='Días de Retraso',
        compute='_compute_delay_days',
        store=True,
    )

    eta_alert_level = fields_module.Selection(
        [
            ('ok', 'En Tiempo'),
            ('warning', 'Próximo a Vencer'),
            ('danger', 'Vencido'),
            ('done', 'Entregado'),
        ],
        string='Alerta ETA',
        compute='_compute_eta_alert',
        store=True,
    )

    eta_warning_notified = fields_module.Boolean(
        string='Notificación "Próximo a Vencer" enviada',
        default=False,
        copy=False,
    )
    eta_overdue_notified = fields_module.Boolean(
        string='Notificación "Vencido" enviada',
        default=False,
        copy=False,
    )

    arrival_date = fields_module.Date(
        string='Llegada Real',
        tracking=True,
    )
    arrival_date_bodega = fields_module.Date(
        string='Entregado en Bodega',
        tracking=True,
    )

    picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción (Tránsito)',
        domain=[('picking_type_code', '=', 'incoming')],
    )

    reception_picking_id = fields_module.Many2one(
        'stock.picking',
        string='Recepción Física (Bodega)',
        domain=[('picking_type_code', '=', 'internal')],
        readonly=True,
    )

    purchase_id = fields_module.Many2one(
        'purchase.order',
        string='Orden de Compra Origen',
        readonly=True,
    )

    company_id = fields_module.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
    )

    line_ids = fields_module.One2many(
        'stock.transit.line',
        'voyage_id',
        string='Contenido (Lotes)',
    )

    total_m2 = fields_module.Float(
        string='Total m²',
        compute='_compute_totals',
        store=True,
        compute_sudo=True,
    )

    allocated_m2 = fields_module.Float(
        string='Asignado m²',
        compute='_compute_totals',
        store=True,
        compute_sudo=True,
    )

    allocation_percent = fields_module.Float(
        string='% Asignación',
        compute='_compute_allocation_percent',
        store=False,
        compute_sudo=False,
    )

    shipsgo_last_sync = fields_module.Datetime(
        string="Última Sincronización API",
        readonly=True,
    )

    shipsgo_payload = fields_module.Text(
        string="Datos Geoespaciales (JSON)",
        readonly=True,
    )

    shipsgo_map_html = fields_module.Html(
        string="Mapa de Seguimiento",
        sanitize=False,
        readonly=True,
    )

    transit_progress = fields_module.Integer(
        string='Progreso Viaje',
        compute='_compute_transit_progress',
        store=True,
        readonly=False,
    )

    # =========================================================================
    # SHIPSGO
    # =========================================================================

    def _shipsgo_get_config(self):
        Config = self.env['ir.config_parameter'].sudo()
        api_url = Config.get_param('stock_transit.shipsgo_api_url', 'https://api.shipsgo.com/v2')
        api_token = Config.get_param('stock_transit.shipsgo_api_token', '')

        if not api_token:
            raise UserError(_("No se ha configurado el Token de ShipsGo en Parámetros del Sistema."))

        return api_url, api_token

    def _shipsgo_headers(self, json_body=False):
        api_url, api_token = self._shipsgo_get_config()

        headers = {
            "Accept": "application/json",
            "User-Agent": "OdooControlTower/1.0",
            "X-Shipsgo-User-Token": api_token,
        }

        if json_body:
            headers["Content-Type"] = "application/json"

        return api_url, headers

    def _normalize_container_number(self, value):
        return (value or '').strip().upper()

    def _validate_container_number(self, value):
        if not re.fullmatch(r'^[A-Z]{4}[0-9]{7}$', value or ''):
            raise UserError(
                _("El contenedor '%s' no cumple el formato esperado AAAA9999999.") % (value or '')
            )

    def _extract_shipment_from_response(self, payload):
        if not isinstance(payload, dict):
            return {}

        if isinstance(payload.get('shipment'), dict):
            return payload['shipment']

        if isinstance(payload.get('data'), dict):
            return payload['data']

        return payload

    def _make_shipsgo_reference(self, container_ref, shipment_container=False):
        self.ensure_one()

        parts = []

        if shipment_container and shipment_container.shipment_id:
            parts.append(shipment_container.shipment_id.name or '')

        if self.name:
            parts.append(self.name)

        if self.purchase_id:
            parts.append(self.purchase_id.name or '')

        parts.append(container_ref)

        reference = " | ".join([p for p in parts if p]).strip()

        if len(reference) < 5:
            reference = f"{self.name or 'VOYAGE'}-{container_ref}"

        return reference[:128]

    def _find_shipsgo_shipment_by_container(self, container_ref):
        self.ensure_one()

        api_url, headers = self._shipsgo_headers()

        try:
            r = requests.get(
                f"{api_url}/ocean/shipments",
                headers=headers,
                params={"filters[container_number]": f"eq:{container_ref}"},
                timeout=20,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise UserError(_("Error buscando shipment existente en ShipsGo: %s") % str(e))

        shipments = payload.get('shipments') or payload.get('data') or []
        return shipments[0] if shipments else False

    def _create_or_link_shipsgo_tracking_for_container(self, container_ref, shipment_container=False):
        self.ensure_one()

        container_ref = self._normalize_container_number(container_ref)
        if not container_ref:
            raise UserError(_("No se recibió un número de contenedor válido para crear tracking."))

        self._validate_container_number(container_ref)

        if shipment_container and shipment_container.shipsgo_shipment_id:
            return {
                'id': shipment_container.shipsgo_shipment_id,
                'reference': shipment_container.shipsgo_reference,
                'container_number': container_ref,
            }

        existing = self._find_shipsgo_shipment_by_container(container_ref)

        if existing:
            shipment_id = existing.get('id')
            reference = existing.get('reference') or self._make_shipsgo_reference(
                container_ref,
                shipment_container=shipment_container,
            )

            if shipment_container and shipment_id:
                shipment_container.with_context(skip_auto_shipsgo=True).write({
                    'shipsgo_shipment_id': shipment_id,
                    'shipsgo_reference': reference,
                    'shipsgo_last_create': fields_module.Datetime.now(),
                    'shipsgo_last_error': False,
                })

            self.message_post(body=Markup(
                "🔁 <b>ShipsGo ya existente</b><br/>"
                "Contenedor: {container}<br/>"
                "Shipment ID: {shipment_id}"
            ).format(
                container=container_ref,
                shipment_id=shipment_id or 'N/A',
            ))

            return existing

        api_url, headers = self._shipsgo_headers(json_body=True)
        reference = self._make_shipsgo_reference(
            container_ref,
            shipment_container=shipment_container,
        )

        payload = {
            "reference": reference,
            "container_number": container_ref,
        }

        carrier_candidate = False

        if shipment_container and shipment_container.shipment_id and shipment_container.shipment_id.shipping_line:
            carrier_candidate = shipment_container.shipment_id.shipping_line.strip().upper()
        elif self.shipping_line:
            carrier_candidate = self.shipping_line.strip().upper()

        if carrier_candidate and re.fullmatch(r'^(SG_)?[A-Z0-9]{4}$', carrier_candidate):
            payload["carrier"] = carrier_candidate

        try:
            r = requests.post(
                f"{api_url}/ocean/shipments",
                headers=headers,
                json=payload,
                timeout=20,
            )

            try:
                response_payload = r.json() if r.content else {}
            except Exception:
                response_payload = {}

        except Exception as e:
            raise UserError(_("Error creando shipment en ShipsGo: %s") % str(e))

        if r.status_code in (200, 201):
            shipment = self._extract_shipment_from_response(response_payload)
        elif r.status_code == 409:
            shipment = self._extract_shipment_from_response(response_payload)
            if not shipment:
                shipment = self._find_shipsgo_shipment_by_container(container_ref) or {}
        elif r.status_code == 402:
            raise UserError(_("ShipsGo reportó que no hay créditos suficientes para crear el tracking."))
        elif r.status_code == 429:
            raise UserError(_("ShipsGo rechazó la creación por demasiadas solicitudes concurrentes. Intente de nuevo."))
        else:
            message = response_payload.get('message') if isinstance(response_payload, dict) else False
            raise UserError(
                _("ShipsGo devolvió un error al crear el tracking (%s): %s")
                % (r.status_code, message or r.text)
            )

        shipment_id = shipment.get('id')
        resolved_reference = shipment.get('reference') or reference

        if shipment_container:
            shipment_container.with_context(skip_auto_shipsgo=True).write({
                'shipsgo_shipment_id': shipment_id or 0,
                'shipsgo_reference': resolved_reference,
                'shipsgo_last_create': fields_module.Datetime.now(),
                'shipsgo_last_error': False,
            })

        self.message_post(body=Markup(
            "🆕 <b>Tracking ShipsGo creado</b><br/>"
            "Contenedor: {container}<br/>"
            "Shipment ID: {shipment_id}<br/>"
            "Reference: {reference}"
        ).format(
            container=container_ref,
            shipment_id=shipment_id or 'N/A',
            reference=resolved_reference,
        ))

        return shipment

    def _clean_coord(self, lat, lng):
        try:
            if lat is None or lng is None:
                return None

            f_lat = float(lat)
            f_lng = float(lng)

            if f_lat == 0.0 and f_lng == 0.0:
                return None

            return [f_lat, f_lng]

        except (ValueError, TypeError):
            return None

    def _generate_folium_map(self, map_data):
        if not HAS_FOLIUM:
            return self._generate_fallback_map_html(map_data)

        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')

        all_points = []

        if origin_loc and len(origin_loc) == 2:
            all_points.append(origin_loc)
        if dest_loc and len(dest_loc) == 2:
            all_points.append(dest_loc)
        if current_loc and len(current_loc) == 2:
            all_points.append(current_loc)

        if current_loc and len(current_loc) == 2:
            center = current_loc
            zoom = 6
        elif all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lng = sum(p[1] for p in all_points) / len(all_points)
            center = [avg_lat, avg_lng]
            zoom = 4
        else:
            center = [20, -40]
            zoom = 2

        m = folium.Map(
            location=center,
            zoom_start=zoom,
            tiles='cartodbpositron',
            width='100%',
            height='600px',
            scrollWheelZoom=False,
        )

        if origin_loc and len(origin_loc) == 2:
            origin_name = map_data.get('origin', {}).get('name', 'Puerto Origen')
            origin_country = map_data.get('origin', {}).get('country', '')
            origin_date = map_data.get('origin', {}).get('date', '')

            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b>⚓ Origen</b><br/>"
                f"<b>{origin_name}</b>"
                f"{'<br/>' + origin_country if origin_country else ''}"
                f"{'<br/>Salida: ' + origin_date if origin_date else ''}"
                f"</div>"
            )

            folium.Marker(
                location=origin_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"Origen: {origin_name}",
                icon=folium.Icon(color='green', icon='anchor', prefix='fa'),
            ).add_to(m)

        if dest_loc and len(dest_loc) == 2:
            dest_name = map_data.get('destination', {}).get('name', 'Puerto Destino')
            dest_country = map_data.get('destination', {}).get('country', '')
            dest_date = map_data.get('destination', {}).get('date', '')

            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b>🏁 Destino</b><br/>"
                f"<b>{dest_name}</b>"
                f"{'<br/>' + dest_country if dest_country else ''}"
                f"{'<br/>Llegada est.: ' + dest_date if dest_date else ''}"
                f"</div>"
            )

            folium.Marker(
                location=dest_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"Destino: {dest_name}",
                icon=folium.Icon(color='red', icon='flag', prefix='fa'),
            ).add_to(m)

        if current_loc and len(current_loc) == 2:
            container = map_data.get('container', 'N/A')
            vessel = map_data.get('vessel', 'N/A')
            status = map_data.get('status', 'En tránsito')
            pct = map_data.get('transit_pct', 0)

            popup_html = (
                f"<div style='min-width:180px;text-align:center'>"
                f"<b>🚢 {container}</b><br/>"
                f"<span style='background:#2563eb;color:#fff;padding:2px 8px;"
                f"border-radius:12px;font-size:11px'>{status}</span><br/>"
                f"<small>Buque: {vessel}</small><br/>"
                f"<small>Progreso: {pct}%</small>"
                f"</div>"
            )

            ship_icon = folium.DivIcon(
                html='<div style="font-size:28px;text-align:center;'
                     'filter:drop-shadow(0 2px 3px rgba(0,0,0,0.3))">🚢</div>',
                icon_size=(32, 32),
                icon_anchor=(16, 16),
            )

            folium.Marker(
                location=current_loc,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{container} - {status}",
                icon=ship_icon,
            ).add_to(m)

        route = map_data.get('route', {})

        for line_coords in route.get('past', []):
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#6b7280',
                    weight=3,
                    opacity=0.7,
                ).add_to(m)

        current_past = route.get('current_past', [])
        if len(current_past) >= 2:
            folium.PolyLine(
                locations=current_past,
                color='#2563eb',
                weight=4,
                opacity=0.85,
            ).add_to(m)

        current_future = route.get('current_future', [])
        if len(current_future) >= 2:
            folium.PolyLine(
                locations=current_future,
                color='#2563eb',
                weight=3,
                opacity=0.5,
                dash_array='8 10',
            ).add_to(m)

        for line_coords in route.get('future', []):
            if len(line_coords) >= 2:
                folium.PolyLine(
                    locations=line_coords,
                    color='#9ca3af',
                    weight=3,
                    opacity=0.5,
                    dash_array='8 10',
                ).add_to(m)

        return m._repr_html_()

    def _generate_fallback_map_html(self, map_data):
        origin_loc = map_data.get('origin', {}).get('loc')
        dest_loc = map_data.get('destination', {}).get('loc')
        current_loc = map_data.get('current_loc')

        container = map_data.get('container', 'N/A')
        vessel = map_data.get('vessel', 'N/A')
        status = map_data.get('status', 'En tránsito')
        pct = map_data.get('transit_pct', 0)
        origin_name = map_data.get('origin', {}).get('name', 'Origen')
        dest_name = map_data.get('destination', {}).get('name', 'Destino')

        markers_js = ""
        bounds_js = "var bounds = [];\n"

        if origin_loc:
            markers_js += f"""
            L.marker([{origin_loc[0]}, {origin_loc[1]}], {{
                icon: L.divIcon({{html:'⚓', className:'', iconSize:[22,22], iconAnchor:[11,11]}})
            }}).addTo(map).bindPopup('<b>Origen:</b> {origin_name}');
            bounds.push([{origin_loc[0]}, {origin_loc[1]}]);
            """

        if dest_loc:
            markers_js += f"""
            L.marker([{dest_loc[0]}, {dest_loc[1]}], {{
                icon: L.divIcon({{html:'🏁', className:'', iconSize:[22,22], iconAnchor:[11,11]}})
            }}).addTo(map).bindPopup('<b>Destino:</b> {dest_name}');
            bounds.push([{dest_loc[0]}, {dest_loc[1]}]);
            """

        if current_loc:
            markers_js += f"""
            L.marker([{current_loc[0]}, {current_loc[1]}], {{
                icon: L.divIcon({{html:'🚢', className:'', iconSize:[28,28], iconAnchor:[14,14]}})
            }}).addTo(map).bindPopup('<b>{container}</b><br/>{status}<br/>Buque: {vessel}<br/>Progreso: {pct}%').openPopup();
            bounds.push([{current_loc[0]}, {current_loc[1]}]);
            """

        bounds_js += """
        if(bounds.length > 1) map.fitBounds(bounds, {padding:[50,50], maxZoom:8});
        else if(bounds.length === 1) map.setView(bounds[0], 5);
        """

        return f"""
        <div style="width:100%;height:1200px;position:relative;">
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <div id="fallback_map" style="width:100%;height:100%;"></div>
            <script>
                (function() {{
                    var map = L.map('fallback_map', {{scrollWheelZoom: false}}).setView([20, -40], 2);
                    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                        attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19
                    }}).addTo(map);
                    {markers_js}
                    {bounds_js}
                }})();
            </script>
        </div>
        """

    def action_sync_shipsgo(self):
        self.ensure_one()

        api_url, headers = self._shipsgo_headers()

        def safe_get(d, keys, default=None):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return default
            return d if d is not None else default

        Container = self.env['supplier.shipment.container']

        linked_container = Container.search([
            ('shipment_id.voyage_id', '=', self.id),
            ('container_number', '!=', False),
        ], order='shipsgo_shipment_id desc, id asc', limit=1)

        container_ref = False
        shipment_id = False
        shipment_data = {}

        if linked_container:
            container_ref = self._normalize_container_number(linked_container.container_number)

            if linked_container.shipsgo_shipment_id:
                shipment_id = linked_container.shipsgo_shipment_id
            else:
                shipment_data = self._create_or_link_shipsgo_tracking_for_container(
                    container_ref=container_ref,
                    shipment_container=linked_container,
                )
                shipment_id = shipment_data.get('id')
        else:
            for line in self.line_ids:
                candidate = self._normalize_container_number(line.container_number)
                if candidate and candidate not in ('PENDIENTE', 'SN', 'FALSE'):
                    container_ref = candidate
                    break

            if not container_ref and self.container_number and 'PENDIENTE' not in (self.container_number or ''):
                container_ref = self._normalize_container_number(
                    str(self.container_number).split(',')[0].strip()
                )

            if not container_ref:
                raise UserError(_("No se encontró un número de contenedor válido en las líneas o en el embarque vinculado."))

            shipment_data = self._find_shipsgo_shipment_by_container(container_ref) or {}
            shipment_id = shipment_data.get('id')

            if not shipment_id:
                shipment_data = self._create_or_link_shipsgo_tracking_for_container(container_ref)
                shipment_id = shipment_data.get('id')

        if shipment_id:
            try:
                sr = requests.get(
                    f"{api_url}/ocean/shipments/{shipment_id}",
                    headers=headers,
                    timeout=20,
                )
                sr.raise_for_status()

                try:
                    shipment_detail_payload = sr.json()
                except Exception:
                    shipment_detail_payload = {}

                shipment_data = self._extract_shipment_from_response(shipment_detail_payload)

            except Exception as e:
                _logger.warning("[ShipsGo] No se pudo obtener detalle de shipment %s: %s", shipment_id, e)

        if not shipment_data and container_ref:
            shipment_data = self._find_shipsgo_shipment_by_container(container_ref) or {}

        if not shipment_data:
            self.message_post(body=_("⚠️ ShipsGo no devolvió datos para %s.") % container_ref)
            self.write({'shipsgo_last_sync': fields_module.Datetime.now()})
            return

        shipment_id = shipment_data.get('id') or shipment_id

        geojson_data = {}
        current_location = None
        vessel_name = ''
        voyage_number = ''
        past_lines = []
        current_lines = []
        future_lines = []
        pol_coordinates = None
        pod_coordinates = None
        all_pol_candidates = []
        all_pod_candidates = []

        if shipment_id:
            try:
                gr = requests.get(
                    f"{api_url}/ocean/shipments/{shipment_id}/geojson",
                    headers=headers,
                    timeout=20,
                )
                gr.raise_for_status()

                try:
                    geojson_data = gr.json()
                except Exception:
                    geojson_data = {}

            except Exception as e:
                _logger.warning("[ShipsGo] No se pudo obtener GeoJSON para %s: %s", shipment_id, e)

        route_info = safe_get(shipment_data, ['route'], {})
        transit_pct = route_info.get('transit_percentage', 0) or 0
        status_text = shipment_data.get('status', 'N/A')
        checked_at = shipment_data.get('checked_at', '')
        carrier_name = safe_get(shipment_data, ['carrier', 'name'], '')

        pol_name = safe_get(route_info, ['port_of_loading', 'location', 'name'], '')
        pod_name = safe_get(route_info, ['port_of_discharge', 'location', 'name'], '')
        date_loading = safe_get(route_info, ['port_of_loading', 'date_of_loading'], '')
        date_discharge = safe_get(route_info, ['port_of_discharge', 'date_of_discharge'], '')
        pol_country = safe_get(route_info, ['port_of_loading', 'location', 'country', 'code'], '')
        pod_country = safe_get(route_info, ['port_of_discharge', 'location', 'country', 'code'], '')

        features = safe_get(geojson_data, ['geojson', 'features'], [])

        for feature in features:
            geom_type = feature.get('geometry', {}).get('type')
            props = feature.get('properties', {})
            status = props.get('status')
            coords_raw = feature.get('geometry', {}).get('coordinates', [])

            if current_location is None and props.get('current') is not None:
                cur = props['current']
                lon, lat = cur['coordinates'][0], cur['coordinates'][1]
                current_location = [lat, lon]
                vessel_name = safe_get(props, ['vessel', 'name'], '')
                voyage_number = props.get('voyage', '')

            if geom_type == 'Point':
                loc_name = safe_get(props, ['location', 'name'], '')
                lat_lon = (coords_raw[1], coords_raw[0])

                if status == 'PAST':
                    all_pol_candidates.append({'coords': lat_lon, 'name': loc_name})
                elif status == 'FUTURE':
                    all_pod_candidates.append({'coords': lat_lon, 'name': loc_name})

            elif geom_type == 'LineString':
                line_coords = [(c[1], c[0]) for c in coords_raw]

                if status == 'PAST':
                    past_lines.append(line_coords)
                elif status == 'CURRENT':
                    current_lines.append({'coords': line_coords, 'props': props})
                elif status == 'FUTURE':
                    future_lines.append(line_coords)

        if all_pol_candidates:
            pol_coordinates = list(all_pol_candidates[0]['coords'])
            if not pol_name:
                pol_name = all_pol_candidates[0]['name']

        if all_pod_candidates:
            pod_coordinates = list(all_pod_candidates[-1]['coords'])
            if not pod_name:
                pod_name = all_pod_candidates[-1]['name']

        current_past_coords = []
        current_future_coords = []

        for seg in current_lines:
            cur_prop = seg['props'].get('current')
            if cur_prop:
                idx = cur_prop.get('index', -1)
                all_c = seg['coords']
                if idx >= 0:
                    current_past_coords = all_c[:idx + 1]
                    current_future_coords = all_c[idx:]
                else:
                    current_future_coords = all_c
            else:
                current_future_coords = seg['coords']

        map_data = {
            'container': container_ref,
            'shipment_id': shipment_id,
            'current_loc': current_location,
            'vessel': vessel_name or shipment_data.get('vessel_name', ''),
            'voyage': voyage_number,
            'status': status_text,
            'transit_pct': int(transit_pct),
            'checked_at': checked_at,
            'carrier': carrier_name,
            'origin': {
                'name': pol_name,
                'loc': pol_coordinates,
                'country': pol_country,
                'date': date_loading,
            },
            'destination': {
                'name': pod_name,
                'loc': pod_coordinates,
                'country': pod_country,
                'date': date_discharge,
            },
            'route': {
                'past': past_lines,
                'current_past': current_past_coords,
                'current_future': current_future_coords,
                'future': future_lines,
            },
        }

        try:
            map_html = self._generate_folium_map(map_data)
        except Exception as e:
            _logger.error("[ShipsGo] Error generando mapa Folium: %s", e)
            map_html = False

        old_eta = self.eta
        new_eta_from_api = False

        if date_discharge:
            try:
                new_eta_from_api = fields_module.Date.from_string(date_discharge[:10])
            except Exception:
                new_eta_from_api = False

        eta_changed_dramatically = False
        days_diff = 0

        if old_eta and new_eta_from_api and old_eta != new_eta_from_api:
            days_diff = abs((new_eta_from_api - old_eta).days)
            if days_diff >= ETA_DRAMATIC_CHANGE_DAYS:
                eta_changed_dramatically = True

        vals = {
            'shipsgo_last_sync': fields_module.Datetime.now(),
            'shipsgo_payload': json.dumps(map_data),
            'shipsgo_map_html': map_html,
            'transit_progress': int(transit_pct),
        }

        if vessel_name:
            vals['vessel_name'] = vessel_name
        if carrier_name:
            vals['shipping_line'] = carrier_name
        if date_discharge:
            vals['eta'] = date_discharge

        no_more_coordinates = current_location is None
        is_completed = int(transit_pct) >= 100

        if (
            (is_completed or no_more_coordinates)
            and self.custom_status not in ('arrived_port', 'reception_pending', 'delivered', 'cancel')
        ):
            vals['custom_status'] = 'arrived_port'
            self.message_post(body=Markup(
                "🏁 <b>Cambio automático de estado:</b> El tracking de ShipsGo "
                "indica que el contenedor llegó (Progreso: {pct}%, Coordenadas: {coords}). "
                "Estado actualizado a <b>Arribo a Puerto</b>. La sincronización automática se detiene."
            ).format(
                pct=int(transit_pct),
                coords='Sí' if current_location else 'No',
            ))

        self.with_context(
            shipsgo_api_update=True,
            eta_dramatic_change=eta_changed_dramatically,
            eta_dramatic_diff=days_diff,
        ).write(vals)

        if linked_container:
            linked_container.write({
                'shipsgo_last_sync': fields_module.Datetime.now(),
                'shipsgo_last_error': False,
            })

        self.message_post(body=Markup(
            "📡 <b>Sincronización ShipsGo</b><br/>"
            "Contenedor: {container} | Shipment ID: {shipment_id} | Estado: {status}<br/>"
            "Progreso: {pct}% | Buque: {vessel}<br/>"
            "POL: {pol} → POD: {pod}<br/>"
            "Pos. actual: {loc}"
        ).format(
            container=container_ref,
            shipment_id=shipment_id or 'N/A',
            status=status_text,
            pct=int(transit_pct),
            vessel=vessel_name or 'N/A',
            pol=pol_name or 'N/A',
            pod=pod_name or 'N/A',
            loc=str(current_location) if current_location else '⚠️ sin coordenadas',
        ))

    # =========================================================================
    # CÓMPUTOS
    # =========================================================================

    @api.depends('line_ids.container_number')
    def _compute_container_number(self):
        for rec in self:
            containers = set()

            for line in rec.line_ids:
                if line.container_number and line.container_number not in ('', 'PENDIENTE', 'SN', 'False'):
                    containers.add(line.container_number)

            rec.container_number = ', '.join(sorted(containers)) if containers else 'PENDIENTE'

    @api.depends('eta', 'eta_original', 'arrival_date_bodega')
    def _compute_delay_days(self):
        for rec in self:
            if not rec.eta_original:
                rec.delay_days = 0
                continue

            reference_end = rec.arrival_date_bodega or rec.eta

            if reference_end:
                rec.delay_days = (reference_end - rec.eta_original).days
            else:
                rec.delay_days = 0

    @api.depends('eta', 'custom_status')
    def _compute_eta_alert(self):
        today = fields_module.Date.today()

        for rec in self:
            if rec.custom_status == 'delivered':
                rec.eta_alert_level = 'done'
            elif not rec.eta:
                rec.eta_alert_level = 'ok'
            elif today > rec.eta:
                rec.eta_alert_level = 'danger'
            elif (rec.eta - today).days <= ETA_WARNING_DAYS_BEFORE:
                rec.eta_alert_level = 'warning'
            else:
                rec.eta_alert_level = 'ok'

    @api.depends('line_ids.product_uom_qty', 'line_ids.allocation_status')
    def _compute_totals(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('product_uom_qty'))
            allocated = sum(
                rec.line_ids.filtered(
                    lambda l: l.allocation_status == 'reserved'
                ).mapped('product_uom_qty')
            )

            rec.total_m2 = total
            rec.allocated_m2 = allocated

    @api.depends('total_m2', 'allocated_m2')
    def _compute_allocation_percent(self):
        for rec in self:
            rec.allocation_percent = (
                (rec.allocated_m2 / rec.total_m2) * 100
                if rec.total_m2 > 0 else 0
            )

    @api.depends('etd', 'eta', 'custom_status', 'create_date', 'shipsgo_payload')
    def _compute_transit_progress(self):
        today = fields_module.Date.today()

        status_floor = {
            'solicitud': 5,
            'production': 15,
            'booking': 25,
            'puerto_origen': 40,
            'on_sea': 60,
            'puerto_destino': 85,
            'arrived_port': 100,
            'reception_pending': 100,
            'delivered': 100,
            'cancel': 0,
        }

        for rec in self:
            if rec.custom_status == 'cancel':
                rec.transit_progress = 0
                continue

            if rec.custom_status in ('arrived_port', 'reception_pending', 'delivered'):
                rec.transit_progress = 100
                continue

            payload_progress = None

            if rec.shipsgo_payload:
                try:
                    payload = json.loads(rec.shipsgo_payload)
                    if isinstance(payload, dict) and payload.get('transit_pct') is not None:
                        payload_progress = int(float(payload.get('transit_pct') or 0))
                except Exception:
                    payload_progress = None

            if payload_progress is not None:
                rec.transit_progress = max(0, min(100, payload_progress))
                continue

            start_date = rec.etd or (rec.create_date.date() if rec.create_date else False)

            if not start_date or not rec.eta:
                rec.transit_progress = status_floor.get(rec.custom_status, 0)
                continue

            if today < start_date:
                date_progress = 0
            elif today > rec.eta:
                date_progress = 95
            else:
                total_days = (rec.eta - start_date).days
                elapsed = (today - start_date).days

                if total_days > 0:
                    date_progress = int((elapsed / total_days) * 100)
                    date_progress = max(0, min(95, date_progress))
                else:
                    date_progress = status_floor.get(rec.custom_status, 0)

            rec.transit_progress = max(
                status_floor.get(rec.custom_status, 0),
                date_progress,
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.transit.voyage') or _('Nuevo')

            vals.pop('container_number', None)

            if vals.get('eta') and not vals.get('eta_original'):
                vals['eta_original'] = vals['eta']

        return super(StockTransitVoyage, self).create(vals_list)

    def write(self, vals):
        if 'eta' in vals:
            for rec in self:
                if not rec.eta_original and vals.get('eta'):
                    super(StockTransitVoyage, rec).write({
                        'eta_original': vals['eta'],
                    })

        is_api_update = self.env.context.get('shipsgo_api_update', False)

        if 'eta' in vals and not is_api_update:
            for rec in self:
                if rec.eta != vals.get('eta'):
                    super(StockTransitVoyage, rec).write({
                        'eta_warning_notified': False,
                        'eta_overdue_notified': False,
                    })

        res = super().write(vals)

        if 'custom_status' in vals or 'eta' in vals:
            transit_lines = self.mapped('line_ids')
            order_ids = transit_lines.mapped('order_id')

            if order_ids:
                sol = self.env['sale.order.line'].search([
                    ('order_id', 'in', order_ids.ids),
                    ('auto_transit_assign', '=', True),
                ])
                sol._compute_transit_info()

        if is_api_update and self.env.context.get('eta_dramatic_change'):
            for rec in self:
                rec._notify_dramatic_eta_change(
                    self.env.context.get('eta_dramatic_diff', 0)
                )

        if 'eta' in vals or 'custom_status' in vals:
            self._check_eta_alerts()

        return res

    # =========================================================================
    # NOTIFICACIONES
    # =========================================================================

    def _get_notification_recipient(self):
        self.ensure_one()

        if self.purchase_id and self.purchase_id.user_id:
            return self.purchase_id.user_id

        return False

    def _notify_dramatic_eta_change(self, days_diff):
        self.ensure_one()

        responsible = self._get_notification_recipient()

        if not responsible:
            return

        if self.custom_status in ('delivered', 'cancel'):
            return

        eta_str = self.eta.strftime('%d/%m/%Y') if self.eta else '—'

        body = Markup(
            "📅 <b>Cambio importante de ETA detectado</b><br/>"
            "El embarque <b>%s</b> tuvo un ajuste de <b>%s días</b> en su fecha de llegada según ShipsGo.<br/>"
            "Nuevo ETA: <b>%s</b>"
        ) % (self.name, days_diff, eta_str)

        self.message_post(
            body=body,
            partner_ids=responsible.partner_id.ids,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _check_eta_alerts(self):
        today = fields_module.Date.today()

        for rec in self:
            if rec.custom_status in ('delivered', 'cancel'):
                continue

            if not rec.eta:
                continue

            responsible = rec._get_notification_recipient()

            if not responsible:
                continue

            days_to_eta = (rec.eta - today).days

            if days_to_eta == ETA_WARNING_DAYS_BEFORE and not rec.eta_warning_notified:
                eta_str = rec.eta.strftime('%d/%m/%Y')
                body = Markup(
                    "⚠️ <b>Embarque próximo a llegar</b><br/>"
                    "El embarque <b>%s</b> tiene ETA <b>mañana (%s)</b> y está en estado <b>%s</b>."
                ) % (
                    rec.name,
                    eta_str,
                    dict(rec._fields['custom_status'].selection).get(rec.custom_status, rec.custom_status),
                )

                rec.message_post(
                    body=body,
                    partner_ids=responsible.partner_id.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )

                super(StockTransitVoyage, rec).write({
                    'eta_warning_notified': True,
                })

            days_overdue = (today - rec.eta).days

            if days_overdue == ETA_OVERDUE_DAYS_AFTER and not rec.eta_overdue_notified:
                eta_str = rec.eta.strftime('%d/%m/%Y')
                body = Markup(
                    "🚨 <b>Embarque vencido</b><br/>"
                    "El embarque <b>%s</b> tenía ETA <b>%s</b> y aún no ha llegado. "
                    "Estado actual: <b>%s</b>."
                ) % (
                    rec.name,
                    eta_str,
                    dict(rec._fields['custom_status'].selection).get(rec.custom_status, rec.custom_status),
                )

                rec.message_post(
                    body=body,
                    partner_ids=responsible.partner_id.ids,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )

                super(StockTransitVoyage, rec).write({
                    'eta_overdue_notified': True,
                })

    @api.model
    def _cron_check_eta_alerts(self):
        voyages = self.search([
            ('custom_status', 'not in', ['delivered', 'cancel']),
            ('eta', '!=', False),
        ])
        voyages._check_eta_alerts()

    # =========================================================================
    # ESTADOS
    # =========================================================================

    STATUS_SEQUENCE = [
        'solicitud',
        'production',
        'booking',
        'puerto_origen',
        'on_sea',
        'puerto_destino',
        'arrived_port',
        'reception_pending',
        'delivered',
    ]

    STATUS_LABELS = {
        'solicitud': 'Solicitud Enviada',
        'production': 'Producción',
        'booking': 'Booking',
        'puerto_origen': 'Puerto Origen',
        'on_sea': 'En Altamar',
        'puerto_destino': 'Puerto Destino',
        'arrived_port': 'Arribo a Puerto',
        'reception_pending': 'En Recepción',
        'delivered': 'Entregado en Almacén',
        'cancel': 'Cancelado',
    }

    def action_advance_status(self):
        self.ensure_one()

        if self.custom_status in ('delivered', 'cancel'):
            return

        self._do_advance_status(notes=False)

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_retreat_status(self):
        self.ensure_one()

        if self.custom_status in ('solicitud', 'delivered', 'cancel'):
            return

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'transit.status.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_direction': 'retreat',
            },
        }

    def _do_advance_status(self, notes=None):
        self.ensure_one()

        current = self.custom_status

        if current in ('cancel', 'delivered'):
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        next_idx = idx + 1

        if next_idx >= len(self.STATUS_SEQUENCE):
            return

        next_status = self.STATUS_SEQUENCE[next_idx]

        if next_status == 'delivered':
            if self.reception_picking_id and self.reception_picking_id.state != 'done':
                raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))

            if self.reception_picking_id:
                self._auto_finalize_after_reception()
            else:
                write_vals = {
                    'arrival_date': fields_module.Date.today(),
                    'custom_status': 'delivered',
                }

                if not self.arrival_date_bodega:
                    write_vals['arrival_date_bodega'] = fields_module.Date.today()

                self.write(write_vals)

                for line in self.line_ids:
                    if line.allocation_id and line.allocation_id.state != 'done':
                        line.allocation_id.action_mark_received(line.product_uom_qty)
        else:
            if next_status == 'on_sea':
                if self.picking_id and self.picking_id.purchase_id:
                    allocations = self.env['purchase.order.line.allocation'].search([
                        ('purchase_order_id', '=', self.picking_id.purchase_id.id),
                        ('state', '=', 'pending'),
                    ])
                    allocations.action_mark_in_transit()

            self.write({'custom_status': next_status})

        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(self.custom_status, self.custom_status)

        msg_parts = [
            Markup("⏩ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]

        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)

        self.message_post(body=Markup('').join(msg_parts))

    def _do_retreat_status(self, notes=None):
        self.ensure_one()

        current = self.custom_status

        if current == 'cancel':
            return

        try:
            idx = self.STATUS_SEQUENCE.index(current)
        except ValueError:
            return

        if idx <= 0:
            return

        prev_status = self.STATUS_SEQUENCE[idx - 1]

        old_label = self.STATUS_LABELS.get(current, current)
        new_label = self.STATUS_LABELS.get(prev_status, prev_status)

        self.write({'custom_status': prev_status})

        msg_parts = [
            Markup("⏪ <b>Cambio de Estado:</b> %s → %s") % (old_label, new_label)
        ]

        if notes:
            msg_parts.append(Markup("<br/>📝 <b>Nota:</b> %s") % notes)

        self.message_post(body=Markup('').join(msg_parts))

    # =========================================================================
    # CARGA Y RECEPCIÓN
    # =========================================================================

    def _get_qty_rounding(self, product):
        self.ensure_one()

        rounding = 0.0001

        if product and getattr(product, 'uom_id', False) and product.uom_id.rounding:
            rounding = product.uom_id.rounding

        return rounding

    def _normalize_product_qty(self, product, qty):
        rounding = self._get_qty_rounding(product)
        return float_round(qty or 0.0, precision_rounding=rounding)

    def _qty_differs(self, product, qty_a, qty_b):
        rounding = self._get_qty_rounding(product)
        return float_compare(
            qty_a or 0.0,
            qty_b or 0.0,
            precision_rounding=rounding,
        ) != 0

    def action_load_from_purchase(self):
        self.ensure_one()

        if not self.purchase_id:
            return

        existing_alloc_ids = self.line_ids.mapped('allocation_id.id')

        allocations = self.env['purchase.order.line.allocation'].search([
            ('purchase_order_id', '=', self.purchase_id.id),
            ('id', 'not in', existing_alloc_ids),
        ])

        transit_lines = []

        for alloc in allocations:
            transit_lines.append({
                'voyage_id': self.id,
                'product_id': alloc.product_id.id,
                'product_uom_qty': alloc.quantity,
                'partner_id': alloc.partner_id.id,
                'order_id': alloc.sale_order_id.id,
                'allocation_id': alloc.id,
                'allocation_status': 'reserved',
                'container_number': 'PENDIENTE',
            })

        existing_stock_lines = self.line_ids.filtered(
            lambda l: not l.allocation_id and not l.partner_id and not l.order_id
        )
        existing_stock_by_product = {
            l.product_id.id: l for l in existing_stock_lines
        }

        for po_line in self.purchase_id.order_line:
            total_po_qty = po_line.product_qty
            total_allocated = sum(po_line.allocation_ids.mapped('quantity'))
            extra_for_stock = total_po_qty - total_allocated
            product_id = po_line.product_id.id

            if product_id in existing_stock_by_product:
                existing_line = existing_stock_by_product[product_id]

                if extra_for_stock > 0:
                    if existing_line.product_uom_qty != extra_for_stock:
                        existing_line.write({
                            'product_uom_qty': extra_for_stock,
                        })
                else:
                    existing_line.unlink()

            elif extra_for_stock > 0:
                transit_lines.append({
                    'voyage_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': extra_for_stock,
                    'partner_id': False,
                    'order_id': False,
                    'allocation_id': False,
                    'allocation_status': 'available',
                    'container_number': 'PENDIENTE',
                    'notes': 'Para Stock (cantidad extra en OC)',
                })

        if transit_lines:
            self.env['stock.transit.line'].create(transit_lines)

    def action_load_from_picking(self):
        self.ensure_one()

        if not self.picking_id:
            return

        placeholder_lines = self.line_ids.filtered(lambda l: not l.lot_id)

        if placeholder_lines:
            placeholder_lines.unlink()

        existing_by_lot = {
            line.lot_id.id: line
            for line in self.line_ids
            if line.lot_id
        }

        from .utils.transit_manager import TransitManager

        purchase = self.picking_id.purchase_id
        allocations_map = {}
        allocation_consumed = {}

        if purchase:
            allocations = self.env['purchase.order.line.allocation'].search([
                ('purchase_order_id', '=', purchase.id),
                ('state', 'not in', ['done', 'cancelled']),
            ], order='id asc')

            for alloc in allocations:
                allocations_map.setdefault(alloc.product_id.id, []).append(alloc)
                allocation_consumed[alloc.id] = 0.0

        lines_to_create = []
        hold_orders_map = {}

        for move_line in self.picking_id.move_line_ids:
            if not move_line.lot_id:
                continue

            lot_id = move_line.lot_id.id
            product_id = move_line.product_id.id

            found_quant = self.env['stock.quant'].search([
                ('lot_id', '=', move_line.lot_id.id),
                ('product_id', '=', move_line.product_id.id),
                ('quantity', '>', 0),
                ('location_id', '=', move_line.location_dest_id.id),
            ], limit=1)

            raw_qty_done = move_line.quantity
            qty_done = self._normalize_product_qty(
                move_line.product_id,
                found_quant.quantity if found_quant else raw_qty_done,
            )

            partner_to_assign = False
            order_to_assign = False
            allocation_to_use = False

            if product_id in allocations_map:
                for alloc in allocations_map[product_id]:
                    already_received = alloc.qty_received
                    consumed_this_load = allocation_consumed.get(alloc.id, 0.0)
                    remaining = alloc.quantity - (already_received + consumed_this_load)

                    if remaining > 0:
                        allocation_to_use = alloc
                        partner_to_assign = alloc.partner_id
                        order_to_assign = alloc.sale_order_id

                        if alloc.sale_line_id:
                            auto_assign = getattr(alloc.sale_line_id, 'auto_transit_assign', True)

                            if not auto_assign:
                                partner_to_assign = False
                                order_to_assign = False
                                allocation_to_use = False
                                continue

                        allocation_consumed[alloc.id] = consumed_this_load + qty_done
                        break

            lot_container = ''

            if hasattr(move_line.lot_id, 'x_contenedor') and move_line.lot_id.x_contenedor:
                lot_container = move_line.lot_id.x_contenedor
            elif move_line.lot_id.ref:
                lot_container = move_line.lot_id.ref

            if lot_id in existing_by_lot:
                existing_line = existing_by_lot[lot_id]
                update_vals = {}

                if self._qty_differs(move_line.product_id, existing_line.product_uom_qty, qty_done):
                    update_vals['product_uom_qty'] = qty_done

                if found_quant and existing_line.quant_id.id != found_quant.id:
                    update_vals['quant_id'] = found_quant.id

                if lot_container and existing_line.container_number != lot_container:
                    update_vals['container_number'] = lot_container

                if allocation_to_use and not existing_line.allocation_id:
                    update_vals['allocation_id'] = allocation_to_use.id

                if not existing_line.partner_id and partner_to_assign:
                    update_vals['partner_id'] = partner_to_assign.id
                    update_vals['order_id'] = order_to_assign.id if order_to_assign else False
                    update_vals['allocation_status'] = 'reserved'

                if update_vals:
                    existing_line.with_context(skip_reservation_logic=True).write(update_vals)

                continue

            line_vals = {
                'voyage_id': self.id,
                'product_id': move_line.product_id.id,
                'lot_id': move_line.lot_id.id,
                'quant_id': found_quant.id if found_quant else False,
                'product_uom_qty': qty_done,
                'partner_id': partner_to_assign.id if partner_to_assign else False,
                'order_id': order_to_assign.id if order_to_assign else False,
                'allocation_status': 'reserved' if partner_to_assign else 'available',
                'container_number': lot_container,
                'allocation_id': allocation_to_use.id if allocation_to_use else False,
            }

            lines_to_create.append(line_vals)

            if partner_to_assign and order_to_assign:
                key = (partner_to_assign.id, order_to_assign.id)

                hold_orders_map.setdefault(key, {
                    'partner': partner_to_assign,
                    'order': order_to_assign,
                    'line_vals_indices': [],
                })
                hold_orders_map[key]['line_vals_indices'].append(len(lines_to_create) - 1)

        created_lines = self.env['stock.transit.line']

        if lines_to_create:
            created_lines = self.env['stock.transit.line'].create(lines_to_create)

        for alloc_id, qty_consumed in allocation_consumed.items():
            if qty_consumed > 0:
                alloc = self.env['purchase.order.line.allocation'].browse(alloc_id)
                new_received = alloc.qty_received + qty_consumed
                alloc.write({
                    'qty_received': min(new_received, alloc.quantity),
                    'state': 'in_transit',
                })

        for key, data in hold_orders_map.items():
            partner = data['partner']
            order = data['order']
            indices = data['line_vals_indices']
            relevant_lines = [
                created_lines[i]
                for i in indices
                if i < len(created_lines)
            ]

            if not relevant_lines:
                continue

            hold_order = self.env['stock.lot.hold.order'].create({
                'partner_id': partner.id,
                'user_id': self.env.user.id,
                'company_id': self.env.company.id,
                'fecha_orden': fields_module.Datetime.now(),
                'notas': f"Asignación Automática - Pedido {order.name} (Desde Tránsito)",
            })

            for line in relevant_lines:
                TransitManager.reassign_lot(
                    self.env,
                    line,
                    partner,
                    order,
                    notes=False,
                    hold_order_obj=hold_order,
                )

            if hold_order.hold_line_ids:
                hold_order.action_confirm()
            else:
                hold_order.unlink()

    # =========================================================================
    # RECEPCIÓN FÍSICA
    # =========================================================================

    def _get_reception_candidate_lines(self):
        self.ensure_one()

        candidate_lines = self.line_ids.filtered(
            lambda l: l.lot_id and l.product_id and l.product_uom_qty > 0
        )

        if not candidate_lines:
            raise UserError(_("No hay líneas con lote y cantidad positiva para recibir."))

        Quant = self.env['stock.quant'].sudo()
        resolved_lines = []
        missing_lots = []
        source_location_ids = set()

        for line in candidate_lines:
            quant = line.quant_id

            quant_is_valid = bool(
                quant
                and quant.exists()
                and quant.product_id.id == line.product_id.id
                and quant.lot_id.id == line.lot_id.id
                and quant.quantity > 0
                and quant.location_id.usage == 'transit'
                and quant.company_id.id == self.company_id.id
            )

            if not quant_is_valid:
                quant = Quant.search([
                    ('company_id', '=', self.company_id.id),
                    ('lot_id', '=', line.lot_id.id),
                    ('product_id', '=', line.product_id.id),
                    ('quantity', '>', 0),
                    ('location_id.usage', '=', 'transit'),
                ], order='id desc', limit=1)

                if quant:
                    line.with_context(skip_reservation_logic=True).write({
                        'quant_id': quant.id,
                    })

            if not quant:
                missing_lots.append(
                    "%s (%.3f)" % (line.lot_id.display_name, line.product_uom_qty)
                )
                continue

            qty_to_receive = self._normalize_product_qty(line.product_id, quant.quantity)

            if float_is_zero(
                qty_to_receive,
                precision_rounding=self._get_qty_rounding(line.product_id),
            ):
                missing_lots.append(
                    "%s (quant cero efectivo)" % (line.lot_id.display_name,)
                )
                continue

            if self._qty_differs(line.product_id, line.product_uom_qty, qty_to_receive):
                line.with_context(skip_reservation_logic=True).write({
                    'product_uom_qty': qty_to_receive,
                })

            source_location_ids.add(quant.location_id.id)

            resolved_lines.append({
                'line': line,
                'quant': quant,
                'qty_to_receive': qty_to_receive,
            })

        if missing_lots:
            raise UserError(_(
                "No se puede preparar la recepción porque estos lotes no tienen quant positivo en una ubicación de tránsito:\n%s"
            ) % "\n".join(missing_lots[:50]))

        if len(source_location_ids) != 1:
            locations = self.env['stock.location'].browse(
                list(source_location_ids)
            ).mapped('complete_name')

            raise UserError(_(
                "Las líneas del viaje apuntan a múltiples ubicaciones de tránsito. "
                "La recepción física debe salir de una sola ubicación origen.\n%s"
            ) % "\n".join(locations))

        source_location = self.env['stock.location'].browse(
            next(iter(source_location_ids))
        )

        return resolved_lines, source_location

    def _get_reception_operation_defaults(self, source_location):
        self.ensure_one()

        picking_types = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.company_id.id),
        ], order='sequence, id')

        if not picking_types:
            raise UserError(_("No se encontró un tipo de operación de traslado interno."))

        picking_type = False

        for pt in picking_types:
            if (
                pt.default_location_dest_id
                and pt.default_location_dest_id.usage == 'internal'
                and pt.default_location_dest_id.id != source_location.id
            ):
                picking_type = pt
                break

        if not picking_type:
            picking_type = picking_types[0]

        dest_location = False

        if (
            picking_type.default_location_dest_id
            and picking_type.default_location_dest_id.usage == 'internal'
            and picking_type.default_location_dest_id.id != source_location.id
        ):
            dest_location = picking_type.default_location_dest_id

        if (
            not dest_location
            and getattr(picking_type, 'warehouse_id', False)
            and picking_type.warehouse_id.lot_stock_id
            and picking_type.warehouse_id.lot_stock_id.id != source_location.id
        ):
            dest_location = picking_type.warehouse_id.lot_stock_id

        if not dest_location:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id),
            ], order='id', limit=1)

            if (
                warehouse
                and warehouse.lot_stock_id
                and warehouse.lot_stock_id.id != source_location.id
            ):
                dest_location = warehouse.lot_stock_id

        if not dest_location:
            dest_location = self.env['stock.location'].search([
                ('company_id', '=', self.company_id.id),
                ('usage', '=', 'internal'),
                ('id', '!=', source_location.id),
            ], order='id', limit=1)

        if not dest_location:
            raise UserError(_(
                "No se pudo determinar una ubicación destino interna para la recepción física."
            ))

        return picking_type, dest_location

    def _tc_reception_safe_context(self):
        ctx = dict(self.env.context or {})
        ctx.update({
            'skip_procurement': True,
            'tracking_disable': True,
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'skip_transit_reception_sync': True,
            'tc_physical_reception_prepare': True,
            'tc_no_auto_validate': True,
            'skip_immediate_transfer': True,
            'skip_backorder': True,

            # Defensas para módulos de reserva/validación automática. El botón
            # Recibir/Abrir Recepción nunca debe reservar por estrategia de
            # remoción ni ejecutar transferencias.
            'skip_action_assign': True,
            'skip_stock_reservation': True,
            'skip_stock_whole_lot_removal': True,
            'skip_whole_lot': True,
            'skip_whole_lot_removal': True,
            'skip_whole_lot_reservation': True,
            'skip_whole_lot_strategy': True,
            'skip_auto_assign': True,
            'skip_auto_reserve': True,
        })
        return ctx

    def _tc_get_allowed_reception_open_states(self):
        return {
            'draft',
            'waiting',
            'confirmed',
            'assigned',
            'partially_available',
        }

    def _tc_assert_reception_can_stay_open(self, picking, operation_label=False):
        """Hard guard: Recibir/Abrir Recepción nunca debe entregar el picking."""
        self.ensure_one()

        if not picking or not picking.exists():
            raise UserError(_("No se encontró la recepción física vinculada al embarque."))

        label = operation_label or _("preparar la recepción física")

        if picking.state == 'done':
            raise UserError(_(
                "Control Tower detuvo el flujo porque la operación de recepción física %(picking)s "
                "quedó en estado HECHO durante %(operation)s.\n\n"
                "El botón Recibir/Abrir Recepción solo puede preparar y abrir la recepción; "
                "no puede validarla. Debe procesar primero el Packing List físico y el Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
                'operation': label,
            })

        if picking.state == 'cancel':
            raise UserError(_(
                "La recepción física %(picking)s está cancelada. Genere una nueva recepción."
            ) % {
                'picking': picking.name or picking.display_name,
            })

        allowed_states = self._tc_get_allowed_reception_open_states()
        if picking.state and picking.state not in allowed_states:
            raise UserError(_(
                "La recepción física %(picking)s quedó en un estado no esperado: %(state)s.\n"
                "Estados permitidos antes de validar: borrador, en espera, listo o parcialmente disponible."
            ) % {
                'picking': picking.name or picking.display_name,
                'state': picking.state,
            })

        return True

    def _tc_assert_reception_can_be_rebuilt(self, picking):
        self.ensure_one()
        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("sincronizar la recepción física"),
        )

        locked_flags = []

        if 'packing_list_imported' in picking._fields and picking.packing_list_imported:
            locked_flags.append(_("Packing List físico ya procesado"))

        if 'worksheet_imported' in picking._fields and picking.worksheet_imported:
            locked_flags.append(_("Worksheet físico ya procesado"))

        if locked_flags and not self.env.context.get('force_tc_reception_resync'):
            raise UserError(_(
                "No se puede reconstruir la recepción física %(picking)s porque ya inició el flujo operativo:\n"
                "- %(flags)s\n\n"
                "Use Abrir Recepción para continuar trabajando. No se deben borrar líneas ni reiniciar PL/Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
                'flags': '\n- '.join(locked_flags),
            })

        return True

    def _tc_reception_has_locked_physical_work(self, picking):
        self.ensure_one()
        picking.ensure_one()

        return bool(
            ('packing_list_imported' in picking._fields and picking.packing_list_imported)
            or ('worksheet_imported' in picking._fields and picking.worksheet_imported)
        )

    def _tc_open_reception_action(self, picking):
        self.ensure_one()
        picking.ensure_one()
        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("abrir la recepción física"),
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': picking.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _tc_prepare_reception_move_vals(self, picking, product, total_qty):
        self.ensure_one()

        Move = self.env['stock.move']
        move_fields = Move._fields

        vals = {}

        if 'name' in move_fields:
            vals['name'] = product.display_name

        if 'description_picking' in move_fields:
            vals['description_picking'] = product.display_name

        vals.update({
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': total_qty,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'company_id': picking.company_id.id or self.company_id.id,
        })

        if 'product_uom' in move_fields:
            vals['product_uom'] = product.uom_id.id
        elif 'product_uom_id' in move_fields:
            vals['product_uom_id'] = product.uom_id.id

        if 'picking_type_id' in move_fields:
            vals['picking_type_id'] = picking.picking_type_id.id

        if 'date' in move_fields:
            vals['date'] = fields_module.Datetime.now()

        if 'procure_method' in move_fields:
            vals['procure_method'] = 'make_to_stock'

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in move_fields
        }

        return vals

    def _tc_prepare_reception_move_line_vals(self, picking, move, line, quant, qty_to_receive):
        """
        Helper conservado para el flujo posterior de confirmación / procesamiento físico.
        No se usa durante action_generate_reception.
        """
        self.ensure_one()

        MoveLine = self.env['stock.move.line']

        vals = {
            'picking_id': picking.id,
            'move_id': move.id,
            'company_id': picking.company_id.id or self.company_id.id,
            'product_id': line.product_id.id,
            'lot_id': line.lot_id.id,
            'location_id': quant.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        }

        if 'product_uom_id' in MoveLine._fields:
            vals['product_uom_id'] = line.product_id.uom_id.id
        elif 'product_uom' in MoveLine._fields:
            vals['product_uom'] = line.product_id.uom_id.id

        if 'quantity' in MoveLine._fields:
            vals['quantity'] = qty_to_receive
        elif 'qty_done' in MoveLine._fields:
            vals['qty_done'] = 0.0

        if 'picked' in MoveLine._fields:
            vals['picked'] = False

        vals = {
            field_name: field_value
            for field_name, field_value in vals.items()
            if field_name in MoveLine._fields
        }

        return vals

    def _sync_reception_picking_lines(self, picking, resolved_lines=None):
        """
        Prepara la recepción física desde el viaje sin validar inventario.

        Regla funcional:
        - Recibir solo crea/actualiza la demanda del traslado Transit -> Stock.
        - No se crean cantidades hechas en la preparación inicial.
        - La operación puede quedar en borrador/en espera/listo, pero nunca en hecho.
        - Si otro módulo intenta validarla durante esta etapa, se lanza excepción.
        """
        self.ensure_one()
        picking.ensure_one()

        ctx = self._tc_reception_safe_context()
        self._tc_assert_reception_can_be_rebuilt(picking)

        if resolved_lines is None:
            resolved_lines, source_location = self._get_reception_candidate_lines()
        else:
            if not resolved_lines:
                raise UserError(_("No hay líneas válidas para sincronizar."))
            source_location = resolved_lines[0]['quant'].location_id

        picking_type, dest_location = self._get_reception_operation_defaults(source_location)

        picking_vals = {}

        if picking.picking_type_id.id != picking_type.id:
            picking_vals['picking_type_id'] = picking_type.id

        if picking.location_id.id != source_location.id:
            picking_vals['location_id'] = source_location.id

        if picking.location_dest_id.id != dest_location.id:
            picking_vals['location_dest_id'] = dest_location.id

        if picking_vals:
            picking.with_context(ctx).write(picking_vals)

        reset_vals = {}

        if 'packing_list_imported' in picking._fields and picking.packing_list_imported:
            reset_vals['packing_list_imported'] = False

        if 'worksheet_imported' in picking._fields and picking.worksheet_imported:
            reset_vals['worksheet_imported'] = False

        if reset_vals:
            picking.with_context(ctx).write(reset_vals)

        product_totals = {}

        for item in resolved_lines:
            line = item['line']
            qty_to_receive = item.get('qty_to_receive', line.product_uom_qty)

            product_totals.setdefault(line.product_id.id, 0.0)
            product_totals[line.product_id.id] += qty_to_receive

        # CRÍTICO: en preparación inicial no deben existir move lines.
        # Si ya existían por intentos anteriores, se eliminan para reconstruir
        # la recepción física de forma limpia.
        if picking.move_line_ids:
            picking.move_line_ids.with_context(ctx).unlink()

        existing_moves = picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))

        for move in existing_moves:
            try:
                if move.state in ('assigned', 'partially_available') and hasattr(move, '_do_unreserve'):
                    move.with_context(ctx)._do_unreserve()
            except Exception as e:
                _logger.warning(
                    "[TC_RECEPTION_WARNING] No se pudo desreservar move %s antes de limpiar recepción física: %s",
                    move.id,
                    e,
                )

            move.with_context(ctx).unlink()

        moves_created = 0
        created_moves = self.env['stock.move']

        for product_id, total_qty in product_totals.items():
            product = self.env['product.product'].browse(product_id)

            move_vals = self._tc_prepare_reception_move_vals(
                picking=picking,
                product=product,
                total_qty=total_qty,
            )

            try:
                created_moves |= self.env['stock.move'].with_context(ctx).create(move_vals)
            except Exception as e:
                _logger.exception(
                    "[TC_RECEPTION_ERROR][MOVE_CREATE] "
                    "No se pudo crear stock.move | voyage=%s | picking=%s | "
                    "product=%s | qty=%s | vals=%s | error=%s",
                    self.name,
                    picking.name,
                    product.display_name,
                    total_qty,
                    move_vals,
                    str(e),
                )
                raise UserError(_(
                    "No se pudo crear la demanda de recepción para el producto:\n\n"
                    "%(product)s\n\n"
                    "Cantidad: %(qty)s\n"
                    "Origen: %(src)s\n"
                    "Destino: %(dest)s\n\n"
                    "Error técnico: %(error)s"
                ) % {
                    'product': product.display_name,
                    'qty': total_qty,
                    'src': picking.location_id.complete_name,
                    'dest': picking.location_dest_id.complete_name,
                    'error': str(e),
                })

            moves_created += 1

        # CRÍTICO:
        # No se confirma la demanda aquí. Confirmar el stock.move dispara
        # _action_assign() y, en esta instancia, stock_whole_lot_removal puede
        # reservar lotes automáticamente desde SOM/Transit. Ese intento de
        # reserva fue el origen del flujo que terminó dejando la recepción en
        # HECHO. El botón Recibir/Abrir Recepción debe crear demanda en borrador
        # y abrir el documento; el Packing List físico y el Worksheet son los
        # únicos pasos que deben construir las líneas operativas reales.
        if picking.move_line_ids:
            raise UserError(_(
                "Control Tower detuvo el flujo porque la recepción física %(picking)s "
                "generó líneas operativas durante la preparación.\n\n"
                "El botón Recibir/Abrir Recepción no debe reservar, asignar ni validar stock. "
                "Debe dejar la recepción abierta para procesar Packing List físico y Worksheet."
            ) % {
                'picking': picking.name or picking.display_name,
            })

        self._tc_assert_reception_can_stay_open(
            picking,
            operation_label=_("preparar la recepción física"),
        )

        total_qty = sum(product_totals.values())

        picking.message_post(
            body=_(
                "📦 Recepción física preparada desde Viaje %s.<br/>"
                "<b>Productos:</b> %s<br/>"
                "<b>Total esperado:</b> %.3f<br/>"
                "<b>Estado:</b> %s<br/><br/>"
                "La recepción quedó abierta sin confirmar, sin reservar y sin validar. "
                "Las líneas físicas se construirán únicamente al procesar el Packing List físico "
                "y el Worksheet."
            ) % (self.name, moves_created, total_qty, picking.state)
        )

        return picking

    def action_generate_reception(self):
        self.ensure_one()

        picking = self.reception_picking_id
        origin = f"{self.name} (Recepción Física)"

        # Si la recepción ya existe y todavía no se procesó PL/Worksheet,
        # se puede sanear de forma segura. Esto corrige recepciones creadas por
        # versiones anteriores que quedaron confirmadas/asignadas o con líneas
        # automáticas al presionar Recibir. Si ya hay trabajo físico, solo se abre.
        if picking and picking.state != 'cancel':
            self._tc_assert_reception_can_stay_open(
                picking,
                operation_label=_("abrir la recepción física"),
            )

            if not self._tc_reception_has_locked_physical_work(picking):
                needs_rebuild = bool(
                    not picking.move_ids
                    or picking.move_line_ids
                    or picking.state != 'draft'
                )
                if needs_rebuild:
                    resolved_lines, _source_location = self._get_reception_candidate_lines()
                    self._sync_reception_picking_lines(
                        picking,
                        resolved_lines=resolved_lines,
                    )

            if self.custom_status != 'reception_pending':
                self.write({'custom_status': 'reception_pending'})
            return self._tc_open_reception_action(picking)

        resolved_lines, source_location = self._get_reception_candidate_lines()
        picking_type, dest_location = self._get_reception_operation_defaults(source_location)

        picking = self.env['stock.picking'].search([
            ('origin', '=', origin),
            ('company_id', '=', self.company_id.id),
            ('state', 'not in', ('done', 'cancel')),
            ('picking_type_code', '=', 'internal'),
        ], order='id desc', limit=1)

        if picking:
            self.write({
                'reception_picking_id': picking.id,
                'custom_status': 'reception_pending',
            })

            if not self._tc_reception_has_locked_physical_work(picking):
                needs_rebuild = bool(
                    not picking.move_ids
                    or picking.move_line_ids
                    or picking.state != 'draft'
                )
                if needs_rebuild:
                    self._sync_reception_picking_lines(
                        picking,
                        resolved_lines=resolved_lines,
                    )

            return self._tc_open_reception_action(picking)

        vals = {
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': origin,
            'company_id': self.company_id.id,
            'move_type': 'direct',
        }

        if 'supplier_bl_number' in self.env['stock.picking']._fields:
            vals['supplier_bl_number'] = self.bl_number

        if 'supplier_container_no' in self.env['stock.picking']._fields:
            vals['supplier_container_no'] = self.container_number

        if 'supplier_origin' in self.env['stock.picking']._fields:
            vals['supplier_origin'] = 'TRÁNSITO'

        picking = self.env['stock.picking'].with_context(
            self._tc_reception_safe_context()
        ).create(vals)

        self.write({
            'reception_picking_id': picking.id,
            'custom_status': 'reception_pending',
        })

        self._sync_reception_picking_lines(
            picking,
            resolved_lines=resolved_lines,
        )

        return self._tc_open_reception_action(picking)

    def action_sync_reception_from_voyage(self):
        self.ensure_one()

        if not self.reception_picking_id:
            raise UserError(_("Primero debe generar la Recepción Física."))

        picking = self.reception_picking_id
        self._sync_reception_picking_lines(picking)

        return self._tc_open_reception_action(picking)

    def action_print_reception_labels(self):
        self.ensure_one()
        if not self.reception_picking_id and not self.line_ids:
            raise UserError(_("No hay recepción física ni lotes para imprimir."))
        return {
            'name': _('Imprimir Etiquetas'),
            'type': 'ir.actions.act_window',
            'res_model': 'transit.label.print.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_voyage_id': self.id,
                'default_picking_id': self.reception_picking_id.id if self.reception_picking_id else False,
            }
        }

    def _auto_finalize_after_reception(self):
        for rec in self:
            if rec.custom_status in ('delivered', 'cancel'):
                continue

            if not rec.reception_picking_id or rec.reception_picking_id.state != 'done':
                continue

            picking = rec.reception_picking_id
            if 'worksheet_imported' in picking._fields and not picking.worksheet_imported:
                raise UserError(_(
                    "No se puede cerrar automáticamente el embarque %(voyage)s porque la recepción física %(picking)s "
                    "está en HECHO sin Worksheet procesado. Revise la automatización que validó la recepción."
                ) % {
                    'voyage': rec.name,
                    'picking': picking.name,
                })

            write_vals = {
                'arrival_date': fields_module.Date.today(),
                'custom_status': 'delivered',
            }

            if not rec.arrival_date_bodega:
                write_vals['arrival_date_bodega'] = fields_module.Date.today()

            rec.write(write_vals)

            for line in rec.line_ids.filtered(lambda l: l.allocation_id):
                qty_received = rec._normalize_product_qty(
                    line.product_id,
                    line.quant_id.quantity if line.quant_id and line.quant_id.exists() else line.product_uom_qty,
                )

                if line.allocation_id.state != 'done' and not float_is_zero(
                    qty_received,
                    precision_rounding=rec._get_qty_rounding(line.product_id),
                ):
                    line.allocation_id.action_mark_received(qty_received)

            rec.message_post(
                body=_("✅ Viaje cerrado automáticamente al validar la recepción física %s.")
                % (rec.reception_picking_id.name,)
            )

    def action_arrive(self):
        self.ensure_one()

        if self.reception_picking_id and self.reception_picking_id.state != 'done':
            raise UserError(_("No puede cerrar el viaje hasta que la Recepción Física haya sido validada."))

        if self.reception_picking_id:
            self._auto_finalize_after_reception()
            return

        write_vals = {
            'arrival_date': fields_module.Date.today(),
            'custom_status': 'delivered',
        }

        if not self.arrival_date_bodega:
            write_vals['arrival_date_bodega'] = fields_module.Date.today()

        self.write(write_vals)

        for line in self.line_ids:
            if line.allocation_id and line.allocation_id.state != 'done':
                line.allocation_id.action_mark_received(line.product_uom_qty)

    def action_cancel(self):
        self.write({'custom_status': 'cancel'})

    def _has_valid_container(self):
        self.ensure_one()

        has_container = self.env['supplier.shipment.container'].search_count([
            ('shipment_id.voyage_id', '=', self.id),
            ('container_number', '!=', False),
        ])

        if not has_container:
            has_container = any(
                line.container_number
                and line.container_number not in ('PENDIENTE', 'SN', 'False', '')
                for line in self.line_ids
            )

        return bool(has_container)

    def _needs_shipsgo_sync(self):
        self.ensure_one()

        if self.custom_status in ('arrived_port', 'reception_pending', 'delivered', 'cancel'):
            return False

        if not self.shipsgo_last_sync:
            return True

        delta = fields_module.Datetime.now() - self.shipsgo_last_sync
        return delta.total_seconds() > 7200

    @api.model
    def action_cron_sync_shipsgo(self):
        voyages = self.search([
            ('custom_status', 'not in', ['arrived_port', 'reception_pending', 'delivered', 'cancel']),
        ])

        for voyage in voyages:
            if not voyage._has_valid_container():
                continue

            try:
                voyage.action_sync_shipsgo()
            except Exception as e:
                _logger.warning(
                    "[ShipsGo CRON] Error sincronizando viaje %s: %s",
                    voyage.name,
                    str(e),
                )

    def web_read(self, specification):
        result = super().web_read(specification)

        if len(self) != 1:
            return result

        if self.env.context.get('no_auto_shipsgo_sync'):
            return result

        voyage = self

        if not voyage._needs_shipsgo_sync():
            return result

        if not voyage._has_valid_container():
            return result

        try:
            voyage.with_context(no_auto_shipsgo_sync=True).action_sync_shipsgo()
            result = super(StockTransitVoyage, voyage).web_read(specification)
        except Exception as e:
            _logger.warning(
                "[ShipsGo AUTO] Error en auto-sync al abrir viaje %s: %s",
                voyage.name,
                str(e),
            )

        return result
