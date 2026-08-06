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

        # Bitácora de etiquetado (KPI): estampar la PRIMERA impresión del lote.
        lots = self.env['stock.quant'].browse(quant_ids).mapped('lot_id')
        lots.filtered(lambda l: not l.x_zpl_printed_at).sudo().write({
            'x_zpl_printed_at': fields.Datetime.now(),
        })

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