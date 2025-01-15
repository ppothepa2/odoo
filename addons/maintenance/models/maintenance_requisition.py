# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class MaintenanceRequisition(models.Model):
    _name = 'maintenance.requisition'
    _description = 'Equipment Requisition'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Equipment Name', required=True, tracking=True)
    description = fields.Text('Description', tracking=True)
    quantity = fields.Integer('Quantity', default=1, tracking=True)
    expected_cost = fields.Float('Expected Cost', tracking=True)
    priority = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High')
    ], string='Priority', default='medium', tracking=True)
    notes = fields.Text('Additional Notes', tracking=True)
    requester_name = fields.Char('Requester Name', required=True)
    department = fields.Char('Department')
    request_date = fields.Date('Request Date', default=fields.Date.today, readonly=True)
    
    # Remove tracking from binary field
    po_attachment = fields.Binary('PO Attachment')
    po_attachment_name = fields.Char('PO Attachment Name')
    
    # Track only the PO number
    po_number = fields.Char('PO Number', tracking=True)
    rejection_reason = fields.Text('Rejection Reason', tracking=True)
    inspection_notes = fields.Text('Inspection Notes', tracking=True)
    
    # Stage tracking fields
    submitted_by = fields.Many2one('res.users', 'Submitted By', readonly=True)
    submitted_date = fields.Datetime('Submitted Date', readonly=True)
    reviewed_by = fields.Many2one('res.users', 'Reviewed By', readonly=True)
    reviewed_date = fields.Datetime('Reviewed Date', readonly=True)
    po_uploaded_by = fields.Many2one('res.users', 'PO Uploaded By', readonly=True)
    po_uploaded_date = fields.Datetime('PO Uploaded Date', readonly=True)
    inspected_by = fields.Many2one('res.users', 'Inspected By', readonly=True)
    inspected_date = fields.Datetime('Inspected Date', readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('accounting_review', 'Under Review'),
        ('po_pending', 'PO Pending'),
        ('inspection', 'Inspection'),
        ('done', 'Done'),
        ('rejected', 'Rejected')
    ], default='draft', string='Status', tracking=True)

    def action_submit(self):
        self.write({
            'state': 'submitted',
            'submitted_by': self.env.user.id,
            'submitted_date': fields.Datetime.now()
        })
        self._send_notification('submitted')

    def action_approve(self):
        self.write({
            'state': 'po_pending',
            'reviewed_by': self.env.user.id,
            'reviewed_date': fields.Datetime.now()
        })
        self._send_notification('approved')

    def action_reject(self):
        if not self.rejection_reason:
            raise UserError(_('Please provide a rejection reason'))
        self.write({
            'state': 'rejected',
            'reviewed_by': self.env.user.id,
            'reviewed_date': fields.Datetime.now()
        })
        self._send_notification('rejected')

    def action_upload_po(self):
        if not self.po_number or not self.po_attachment:
            raise UserError(_('Please provide both PO number and attachment'))
        self.write({
            'state': 'inspection',
            'po_uploaded_by': self.env.user.id,
            'po_uploaded_date': fields.Datetime.now()
        })
        self._send_notification('po_uploaded')

    def action_complete_inspection(self):
        if not self.inspection_notes:
            raise UserError(_('Please provide inspection notes'))
        self.write({
            'state': 'done',
            'inspected_by': self.env.user.id,
            'inspected_date': fields.Datetime.now()
        })
        self._send_notification('completed')

    def _send_notification(self, action):
        template = self.env.ref('maintenance.mail_template_equipment_requisition')
        subject_map = {
            'submitted': 'New Equipment Requisition Submitted',
            'approved': 'Equipment Requisition Approved',
            'rejected': 'Equipment Requisition Rejected',
            'po_uploaded': 'PO Uploaded for Equipment Requisition',
            'completed': 'Equipment Requisition Completed'
        }
        
        email_values = {
            'email_from': 'ppothepalli@srisaibiopharma.com',
            'email_to': 'parichay2406@gmail.com',
            'subject': f'{subject_map.get(action, "Update")}: {self.name}',
            'body_html': self._prepare_notification_body(action)
        }
        self.env['mail.mail'].create(email_values).send()

    def _prepare_notification_body(self, action):
        return f'''
            <div style="margin: 0px; padding: 0px;">
                <p>Hello,</p>
                <p>Equipment requisition {self.name} has been {action}.</p>
                <ul>
                    <li>Equipment: {self.name}</li>
                    <li>Requester: {self.requester_name}</li>
                    <li>Department: {self.department}</li>
                    <li>Status: {dict(self._fields['state'].selection).get(self.state)}</li>
                    {f"<li>Rejection Reason: {self.rejection_reason}</li>" if action == 'rejected' else ""}
                    {f"<li>PO Number: {self.po_number}</li>" if self.po_number else ""}
                </ul>
                <p>Best regards</p>
            </div>
        '''