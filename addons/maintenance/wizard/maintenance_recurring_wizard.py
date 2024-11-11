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
        request.write({
            'recurring_maintenance': True,
            'repeat_interval': self.repeat_interval,
            'repeat_unit': self.repeat_unit,
            'repeat_type': self.repeat_type,
            'repeat_until': self.repeat_until,
        })
        return request.confirm_and_create_recurring()

    def action_retry(self):
        return {'type': 'ir.actions.act_window_close'} 