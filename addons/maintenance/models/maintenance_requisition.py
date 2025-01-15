# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
import logging

_logger = logging.getLogger(__name__)

class MaintenanceRequisition(models.Model):
    _name = 'maintenance.requisition'
    _description = 'Equipment Requisition'
    _inherit = ['mail.thread']

    name = fields.Char('Equipment Name', required=True)
    description = fields.Text('Description')
    quantity = fields.Integer('Quantity', default=1)
    expected_cost = fields.Float('Expected Cost')
    priority = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High')
    ], string='Priority', default='medium')
    notes = fields.Text('Additional Notes')
    requester_name = fields.Char('Requester Name', required=True)
    department = fields.Char('Department')
    request_date = fields.Date('Request Date', default=fields.Date.today, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted')
    ], default='draft', string='Status', readonly=True)

    def action_submit_request(self):
        self.ensure_one()
        template = self.env.ref('maintenance.mail_template_equipment_requisition')
        
        # Prepare email values
        email_values = {
            'email_from': 'ppothepalli@srisaibiopharma.com',
            'email_to': 'parichay2406@gmail.com',
            'subject': f'New Equipment Requisition: {self.name}',
            'body_html': f'''
                <div style="margin: 0px; padding: 0px;">
                    <p>Hello,</p>
                    <p>A new equipment requisition has been submitted with the following details:</p>
                    <ul>
                        <li>Equipment: {self.name}</li>
                        <li>Requester: {self.requester_name}</li>
                        <li>Department: {self.department}</li>
                        <li>Quantity: {self.quantity}</li>
                        <li>Expected Cost: {self.expected_cost}</li>
                        <li>Priority: {self.priority}</li>
                        <li>Request Date: {self.request_date}</li>
                    </ul>
                    <p>Description:</p>
                    <p>{self.description or 'N/A'}</p>
                    <p>Additional Notes:</p>
                    <p>{self.notes or 'N/A'}</p>
                    <br/>
                    <p>Best regards</p>
                </div>
            '''
        }

        # Send email
        self.env['mail.mail'].create(email_values).send()
        
        # Update state
        self.write({'state': 'submitted'})
        
        return True