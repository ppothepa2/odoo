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
        
        # Instead of generating a custom name, use the same naming function as for child requests
        # but update it with "M" for main
        
        # Get equipment ID (last 4 digits)
        eq_id = "0000"
        if request.equipment_id and request.equipment_id.equipment_identifier:
            eq_parts = request.equipment_id.equipment_identifier.split('-')
            if eq_parts:
                last_part = eq_parts[-1]
                eq_id = last_part[-4:].zfill(4)
        
        # Type prefix
        type_prefix = "PM" if request.maintenance_type == 'preventive' else "CR"
        
        # Generate frequency code consistent with _generate_maintenance_request_name method
        frequency = "Reg"  # Default
        if request.maintenance_type == 'preventive':
            if self.repeat_unit == 'day':
                frequency = "Dly"
            elif self.repeat_unit == 'week':
                if self.repeat_interval == 1:
                    frequency = "Wkly"
                elif self.repeat_interval == 2:
                    frequency = "Bi-W"
                else:
                    frequency = f"{self.repeat_interval}W"
            elif self.repeat_unit == 'month':
                if self.repeat_interval == 1:
                    frequency = "Mon"
                elif self.repeat_interval == 2:
                    frequency = "Bi-M"
                elif self.repeat_interval == 6:
                    frequency = "Bi-A"  # Bi-Annual (6 months)
                else:
                    frequency = f"{self.repeat_interval}M"
            elif self.repeat_unit == 'year':
                if self.repeat_interval == 1:
                    frequency = "Yrly"
                else:
                    frequency = f"{self.repeat_interval}Y"
        
        # Use "M" for Main instead of "Main"
        main_suffix = "M"
        
        # Generate the name with the same pattern as child requests
        name = f"{type_prefix}-{eq_id}-{frequency}-{main_suffix}"
        
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