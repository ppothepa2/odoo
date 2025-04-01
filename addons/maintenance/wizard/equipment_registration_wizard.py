from odoo import api, fields, models, _
from odoo.exceptions import UserError

class EquipmentRegistrationWizard(models.TransientModel):
    _name = 'equipment.registration.wizard'
    _description = 'Equipment Registration Wizard'

    equipment_id = fields.Many2one('maintenance.equipment', string='Equipment', required=True)
    proposed_identifier = fields.Char('Proposed Identifier', readonly=True)
    
    @api.model
    def default_get(self, fields_list):
        res = super(EquipmentRegistrationWizard, self).default_get(fields_list)
        if self.env.context.get('active_model') == 'maintenance.equipment' and self.env.context.get('active_id'):
            equipment = self.env['maintenance.equipment'].browse(self.env.context.get('active_id'))
            res['equipment_id'] = equipment.id
            res['proposed_identifier'] = self._generate_equipment_identifier(equipment)
        return res
    
    def _generate_equipment_identifier(self, equipment):
        """Generate equipment identifier in format YY-DEPT-CAT-SUB-Number"""
        # Get current year (2 digits)
        import datetime
        current_year = str(datetime.datetime.now().year)[-2:]
        
        # Get department code
        dept_code = equipment.department or '01'  # Default to '01' if not set
        
        # Get category code (3 letters)
        category_code = 'UNK'
        if equipment.category_id and equipment.category_id.name:
            category_code = equipment.category_id.name[:3].upper()
        
        # Get subcategory code (3 letters)
        subcategory_code = 'NAN'
        if equipment.subcategory_id and equipment.subcategory_id.name:
            subcategory_code = equipment.subcategory_id.name[:3].upper()
        
        # Starting sequence
        starting_sequence = 1403
        
        # Find the highest sequence number across ALL equipment
        all_equipment = self.env['maintenance.equipment'].search([
            ('equipment_identifier', '!=', False),
            ('is_registered', '=', True)
        ])
        
        next_sequence = starting_sequence
        for equip in all_equipment:
            if equip.equipment_identifier:
                parts = equip.equipment_identifier.split('-')
                if len(parts) >= 5 and parts[4].isdigit():
                    seq_number = int(parts[4])
                    if seq_number >= next_sequence:
                        next_sequence = seq_number + 1
        
        # Combine all parts to form the identifier
        return f"{current_year}-{dept_code}-{category_code}-{subcategory_code}-{next_sequence}"
    
    def action_confirm(self):
        """Confirm equipment registration with the proposed identifier"""
        self.ensure_one()
        if not self.equipment_id:
            raise UserError(_("No equipment selected."))
        
        # Update the equipment with the identifier and mark as registered
        self.equipment_id.write({
            'is_registered': True,
            'equipment_identifier': self.proposed_identifier,
            'registration_date': fields.Date.today(),
            'registration_number': self.equipment_id.registration_number.replace('TEMP/', '') if self.equipment_id.registration_number else False
        })
        
        # Update the registration record if needed
        if self.equipment_id.registration_line_id:
            registration = self.env['equipment.registration'].browse(
                self.equipment_id.registration_line_id.registration_id.id
            )
            if registration:
                registered_count = len(registration.equipment_ids.filtered('is_registered'))
                total_count = len(registration.equipment_ids)
                
                if registered_count == total_count and registration.registration_number.startswith('TEMP/'):
                    registration.write({
                        'registration_number': registration.registration_number.replace('TEMP/', '')
                    })
        
        return {'type': 'ir.actions.act_window_close'}
    
    def action_cancel(self):
        """Cancel equipment registration"""
        return {'type': 'ir.actions.act_window_close'} 