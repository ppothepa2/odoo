# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

class MaintenanceRequisition(models.Model):
    _name = 'maintenance.requisition'
    _description = 'Equipment Requisition'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'is_summary desc, card_color_order desc, requisition_number desc'
    _rec_name = 'requisition_number'

    requisition_number = fields.Char('Requisition Number', readonly=True, copy=False, tracking=True)
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
        ('registered_with_equipment', 'Registered with Equipment'),
        ('rejected', 'Rejected')
    ], default='draft', string='Status', tracking=True)

    # Product Information fields
    vendor = fields.Char('Vendor', tracking=True)
    vendor_reference = fields.Char('Vendor Reference', tracking=True)
    model = fields.Char('Model', tracking=True)
    cost = fields.Float('Cost', tracking=True)
    warranty_expiration_date = fields.Date('Warranty Expiration Date', tracking=True)

    is_temporary = fields.Boolean(
        string='Is Temporary',
        compute='_compute_is_temporary',
        store=True,
        help="Technical field to identify temporary requisitions"
    )

    submission_date = fields.Datetime(
        string='Submission Date',
        readonly=True,
        copy=False
    )
    
    approval_due_date = fields.Datetime(
        string='Approval Due Date',
        compute='_compute_approval_due_date',
        store=True,
        help='7 days from submission date'
    )
    
    days_remaining = fields.Integer(
        string='Days Remaining',
        compute='_compute_days_remaining',
        help='Days remaining for approval'
    )

    card_color = fields.Selection([
        ('red', 'Red'),
        ('yellow', 'Yellow'),
        ('green', 'Green')
    ], compute='_compute_card_color', store=True)

    card_color_order = fields.Integer(
        string='Card Color Order',
        compute='_compute_card_color',
        store=True,
        help='Technical field for ordering: Red (3), Yellow (2), Green (1)'
    )

    is_summary = fields.Boolean(
        string='Is Summary Card',
        default=False,
        help="Technical field to identify summary cards"
    )

    stats_count = fields.Integer(
        string='Statistics Count',
        compute='_compute_stats',
        store=False
    )
    
    stats_cost = fields.Float(
        string='Statistics Cost',
        compute='_compute_stats',
        store=False
    )

    purchase_cost = fields.Float(
        'Purchase Cost', 
        tracking=True,
        copy=False
    )
    
    stats_purchase_cost = fields.Float(
        string='Statistics Purchase Cost',
        compute='_compute_stats',
        store=False
    )

    # Add these new fields
    category_id = fields.Many2one(
        'maintenance.equipment.category', 
        string='Equipment Category',
        tracking=True
    )
    
    subcategory = fields.Selection([
        ('forklift', 'Forklift'),
        ('crane', 'Crane'),
        ('conveyor', 'Conveyor'),
        # Add more subcategories as needed
    ], string='Subcategory', tracking=True)

    @api.depends('requisition_number')
    def _compute_is_temporary(self):
        for record in self:
            record.is_temporary = record.requisition_number and 'TEMP/' in record.requisition_number

    @api.depends('submission_date')
    def _compute_approval_due_date(self):
        for record in self:
            if record.submission_date:
                record.approval_due_date = record.submission_date + timedelta(days=7)
            else:
                record.approval_due_date = False

    @api.depends('approval_due_date', 'state')
    def _compute_days_remaining(self):
        now = fields.Datetime.now()
        for record in self:
            if record.approval_due_date and record.state == 'submitted':
                delta = record.approval_due_date - now
                record.days_remaining = max(0, delta.days)
            else:
                record.days_remaining = 0

    @api.depends('state', 'days_remaining')
    def _compute_card_color(self):
        for record in self:
            if record.state == 'done':
                record.card_color = 'green'
                record.card_color_order = 1
            elif record.state == 'submitted':
                record.card_color = 'red'
                record.card_color_order = 3
            elif record.state in ['po_pending', 'inspection']:
                record.card_color = 'yellow'
                record.card_color_order = 2
            else:
                record.card_color = False
                record.card_color_order = 0

    def action_submit(self):
        for record in self:
            if not record.requisition_number:
                sequence = self.env['ir.sequence'].next_by_code('maintenance.requisition')
                record.requisition_number = sequence
        
        self.write({
            'state': 'submitted',
            'submission_date': fields.Datetime.now(),
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
        
        # Update requisition number to remove TEMP/
        permanent_number = self.requisition_number.replace('TEMP/', '')
        
        self.write({
            'state': 'done',
            'inspected_by': self.env.user.id,
            'inspected_date': fields.Datetime.now(),
            'requisition_number': permanent_number
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
            'subject': f'{subject_map.get(action, "Update")}: {self.requisition_number} - {self.name}',
            'body_html': self._prepare_notification_body(action)
        }
        self.env['mail.mail'].create(email_values).send()

    def _prepare_notification_body(self, action):
        return f'''
            <div style="margin: 0px; padding: 0px;">
                <p>Hello,</p>
                <p>Equipment requisition {self.requisition_number} - {self.name} has been {action}.</p>
                <ul>
                    <li>Requisition Number: {self.requisition_number}</li>
                    <li>Equipment: {self.name}</li>
                    <li>Requester: {self.requester_name}</li>
                    <li>Department: {self.department}</li>
                    <li>Status: {dict(self._fields['state'].selection).get(self.state)}</li>
                    <li>Vendor: {self.vendor or ''}</li>
                    <li>Model: {self.model or ''}</li>
                    <li>Cost: {self.cost or 0.0}</li>
                    <li>Warranty Expiration: {self.warranty_expiration_date or ''}</li>
                    {f"<li>Rejection Reason: {self.rejection_reason}</li>" if action == 'rejected' else ""}
                    {f"<li>PO Number: {self.po_number}</li>" if self.po_number else ""}
                </ul>
                <p>Best regards</p>
            </div>
        '''

    @api.model
    def create_summary_cards(self):
        """Create summary cards if they don't exist"""
        if not self.search([('is_summary', '=', True)]):
            self.create([
                {
                    'name': 'Quarterly Summary',
                    'is_summary': True,
                    'requester_name': 'System',
                    'state': 'draft',
                    'priority': 'low',
                    'quantity': 1,
                },
                {
                    'name': 'Yearly Summary',
                    'is_summary': True,
                    'requester_name': 'System',
                    'state': 'draft',
                    'priority': 'low',
                    'quantity': 1,
                }
            ])

    @api.depends('name', 'request_date')
    def _compute_stats(self):
        today = fields.Date.today()
        for record in self:
            if not record.is_summary:
                record.stats_count = 0
                record.stats_cost = 0
                record.stats_purchase_cost = 0
                continue

            if 'Quarter' in record.name:
                # Quarterly stats
                quarter_start = today + relativedelta(months=-((today.month - 1) % 3), day=1)
                domain = [
                    ('request_date', '>=', quarter_start),
                    ('request_date', '<=', today),
                    ('is_summary', '=', False)
                ]
            else:
                # Yearly stats
                year_start = today.replace(month=1, day=1)
                domain = [
                    ('request_date', '>=', year_start),
                    ('request_date', '<=', today),
                    ('is_summary', '=', False)
                ]

            records = self.search(domain)
            record.stats_count = len(records)
            record.stats_cost = sum(records.mapped('expected_cost'))
            record.stats_purchase_cost = sum(r.purchase_cost for r in records if r.purchase_cost)