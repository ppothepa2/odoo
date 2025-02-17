from odoo import api, fields, models, _

class EquipmentRegistration(models.Model):
    _name = 'equipment.registration'
    _description = 'Equipment Registration'
    _rec_name = 'registration_number'

    registration_number = fields.Char('Registration Number', compute='_compute_registration_number', store=True)
    requisition_id = fields.Many2one('maintenance.requisition', string='Requisition',
        domain=[('state', '=', 'done')], required=True)
    equipment_ids = fields.One2many('equipment.registration.line', 'registration_id', string='Equipment')
    progress = fields.Char(string='Progress (%)', compute='_compute_progress', store=True)
    all_registered = fields.Boolean(compute='_compute_progress', store=True)
    
    @api.depends('equipment_ids.is_registered')
    def _compute_progress(self):
        for rec in self:
            registered = len(rec.equipment_ids.filtered('is_registered'))
            total = len(rec.equipment_ids)
            percentage = (registered / total * 100) if total > 0 else 0
            rec.progress = f"{percentage:.0f}%"
            rec.all_registered = total > 0 and registered == total
            # If all registered, update main registration number
            if rec.all_registered:
                rec.registration_number = f'REG/{rec.requisition_id.requisition_number}'

    @api.depends('requisition_id', 'all_registered')
    def _compute_registration_number(self):
        for rec in self:
            if rec.requisition_id:
                prefix = '' if rec.all_registered else 'TEMP/'
                rec.registration_number = f'{prefix}REG/{rec.requisition_id.requisition_number}'
            else:
                rec.registration_number = False

    @api.model
    def create(self, vals):
        """Create registration lines when creating the registration"""
        res = super().create(vals)
        if res.requisition_id and not res.equipment_ids:
            self._create_equipment_lines(res)
        return res

    def _create_equipment_lines(self, registration):
        """Create equipment lines for the registration"""
        lines = []
        for seq in range(1, registration.requisition_id.quantity + 1):
            equipment = self.env['maintenance.equipment'].search([
                ('requisition_id', '=', registration.requisition_id.id),
                ('registration_sequence', '=', seq)
            ], limit=1)
            
            if not equipment:
                equipment = self.env['maintenance.equipment'].create_from_requisition(
                    registration.requisition_id.id, seq)
            
            self.env['equipment.registration.line'].create({
                'registration_id': registration.id,
                'equipment_id': equipment.id,
            })

class EquipmentRegistrationLine(models.Model):
    _name = 'equipment.registration.line'
    _description = 'Equipment Registration Line'

    registration_id = fields.Many2one('equipment.registration', string='Registration')
    equipment_id = fields.Many2one('maintenance.equipment', string='Equipment')
    registration_number = fields.Char(related='equipment_id.registration_number', readonly=True, store=True)
    is_registered = fields.Boolean(related='equipment_id.is_registered', readonly=True, store=True)

    def action_open_equipment(self):
        return {
            'name': _('Register Equipment'),
            'type': 'ir.actions.act_window',
            'res_model': 'maintenance.equipment',
            'res_id': self.equipment_id.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_registration_number': self.registration_number},
        } 