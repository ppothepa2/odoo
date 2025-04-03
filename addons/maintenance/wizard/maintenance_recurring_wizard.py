from odoo import api, fields, models, _

class MaintenanceRecurringWizard(models.TransientModel):
    _name = 'maintenance.recurring.wizard'
    _description = 'Maintenance Recurring Confirmation Wizard'

    maintenance_request_id = fields.Many2one('maintenance.request', string='Maintenance Request', required=True)
    repeat_interval = fields.Integer(string='Repeat Every', required=True)
    repeat_unit = fields.Selection([
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
        ('year', 'Years'),
    ], required=True)
    repeat_type = fields.Selection([
        ('forever', 'Forever'),
        ('until', 'Until'),
    ], required=True, string="Until")
    repeat_until = fields.Date(string="End Date")

    def action_confirm(self):
        self.ensure_one()
        request = self.maintenance_request_id
        
        # Generate compact identifier components
        category_code = request.category_id.name[:3].upper() if request.category_id and request.category_id.name else 'UNK'
        subcategory_code = request.subcategory_id.name[:3].upper() if request.subcategory_id and request.subcategory_id.name else 'NAN'
        
        # Get equipment last 4 digits
        equipment_suffix = '0000'
        if request.equipment_id and request.equipment_id.equipment_identifier:
            parts = request.equipment_id.equipment_identifier.split('-')
            if len(parts) >= 5:
                equipment_suffix = parts[4][-4:] if len(parts[4]) >= 4 else parts[4].zfill(4)
        
        # Single letter periodicity code
        period_code = {
            'day': 'D',
            'week': 'W',
            'month': 'M',
            'year': 'Y',
        }.get(self.repeat_unit, 'X')
        
        # Special cases for common intervals
        if self.repeat_unit == 'month':
            if self.repeat_interval == 3:
                period_code = 'Q'  # Quarterly
            elif self.repeat_interval == 6:
                period_code = 'B'  # Biannual
        
        # Generate the compact identifier with dashes
        name = f"{category_code}-{subcategory_code}-{equipment_suffix}-{period_code}"
        
        # Update request with readonly fields
        request.write({
            'name': name,
            'recurring_maintenance': True,
            'repeat_interval': self.repeat_interval,
            'repeat_unit': self.repeat_unit,
            'repeat_type': self.repeat_type,
            'repeat_until': self.repeat_until,
            'is_recurring_locked': True  # New field to control editability
        })
        
        # Apply checklists based on category and subcategory before creating child requests
        request._apply_hardcoded_checklists()
        
        return request.confirm_and_create_recurring()

    def action_retry(self):
        return {'type': 'ir.actions.act_window_close'} 