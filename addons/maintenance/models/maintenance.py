# -*- coding: utf-8 -*-

import ast
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError
from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.osv import expression
import logging

_logger = logging.getLogger(__name__)


class MaintenanceStage(models.Model):
    """ Model for case stages. This models the main stages of a Maintenance Request management flow. """

    _name = 'maintenance.stage'
    _description = 'Maintenance Stage'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('Folded in Maintenance Pipe')
    done = fields.Boolean('Request Done')
    stage_sequence = fields.Integer('Stage Sequence', default=1)


class MaintenanceEquipmentCategory(models.Model):
    _name = 'maintenance.equipment.category'
    _inherit = ['mail.alias.mixin', 'mail.thread']
    _description = 'Maintenance Equipment Category'

    @api.depends('equipment_ids')
    def _compute_fold(self):
        # fix mutual dependency: 'fold' depends on 'equipment_count', which is
        # computed with a read_group(), which retrieves 'fold'!
        self.fold = False
        for category in self:
            category.fold = False if category.equipment_count else True

    name = fields.Char('Category Name', required=True, translate=True)
    company_id = fields.Many2one('res.company', string='Company',
        default=lambda self: self.env.company)
    technician_user_id = fields.Many2one('res.users', 'Responsible', tracking=True, default=lambda self: self.env.uid)
    color = fields.Integer('Color Index')
    note = fields.Html('Comments', translate=True)
    equipment_ids = fields.One2many('maintenance.equipment', 'category_id', string='Equipment', copy=False)
    equipment_count = fields.Integer(string="Equipment Count", compute='_compute_equipment_count')
    maintenance_ids = fields.One2many('maintenance.request', 'category_id', copy=False)
    maintenance_count = fields.Integer(string="Maintenance Count", compute='_compute_maintenance_count')
    maintenance_open_count = fields.Integer(string="Current Maintenance", compute='_compute_maintenance_count')
    alias_id = fields.Many2one(help="Email alias for this equipment category. New emails will automatically "
        "create a new equipment under this category.")
    fold = fields.Boolean(string='Folded in Maintenance Pipe', compute='_compute_fold', store=True)
    subcategories = fields.One2many('maintenance.equipment.subcategory', 'category_id', string='Subcategories')

    def _compute_equipment_count(self):
        equipment_data = self.env['maintenance.equipment']._read_group([('category_id', 'in', self.ids)], ['category_id'], ['__count'])
        mapped_data = {category.id: count for category, count in equipment_data}
        for category in self:
            category.equipment_count = mapped_data.get(category.id, 0)

    def _compute_maintenance_count(self):
        maintenance_data = self.env['maintenance.request']._read_group([('category_id', 'in', self.ids)], ['category_id', 'archive'], ['__count'])
        mapped_data = {(category.id, archive): count for category, archive, count in maintenance_data}
        for category in self:
            category.maintenance_open_count = mapped_data.get((category.id, False), 0)
            category.maintenance_count = category.maintenance_open_count + mapped_data.get((category.id, True), 0)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_contains_maintenance_requests(self):
        for category in self:
            if category.equipment_ids or category.maintenance_ids:
                raise UserError(_("You cannot delete an equipment category containing equipment or maintenance requests."))

    def _alias_get_creation_values(self):
        values = super(MaintenanceEquipmentCategory, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('maintenance.request').id
        if self.id:
            values['alias_defaults'] = defaults = ast.literal_eval(self.alias_defaults or "{}")
            defaults['category_id'] = self.id
        return values


class MaintenanceEquipmentSubcategory(models.Model):
    _name = 'maintenance.equipment.subcategory'
    _description = 'Maintenance Equipment Subcategory'
    _order = 'name'

    name = fields.Char('Subcategory Name', required=True)
    category_id = fields.Many2one('maintenance.equipment.category', string='Category', required=True)


class MaintenanceMixin(models.AbstractModel):
    _name = 'maintenance.mixin'
    _check_company_auto = True
    _description = 'Maintenance Maintained Item'

    company_id = fields.Many2one('res.company', string='Company',
        default=lambda self: self.env.company)
    maintenance_team_id = fields.Many2one('maintenance.team', string='Maintenance Team', compute='_compute_maintenance_team_id', store=True, readonly=False, check_company=True)
    technician_user_id = fields.Many2one('res.users', string='Technician', tracking=True)
    maintenance_ids = fields.One2many('maintenance.request')  # needs to be extended in order to specify inverse_name !
    maintenance_count = fields.Integer(compute='_compute_maintenance_count', string="Maintenance Count", store=True)
    maintenance_open_count = fields.Integer(compute='_compute_maintenance_count', string="Current Maintenance", store=True)
    expected_mtbf = fields.Integer(string='Expected MTBF', help='Expected Mean Time Between Failure')
    mtbf = fields.Integer(compute='_compute_maintenance_request', string='MTBF', help='Mean Time Between Failure, computed based on done corrective maintenances.')
    mttr = fields.Integer(compute='_compute_maintenance_request', string='MTTR', help='Mean Time To Repair')
    estimated_next_failure = fields.Date(compute='_compute_maintenance_request', string='Estimated time before next failure (in days)', help='Computed as Latest Failure Date + MTBF')
    latest_failure_date = fields.Date(compute='_compute_maintenance_request', string='Latest Failure Date')

    @api.depends('company_id')
    def _compute_maintenance_team_id(self):
        for record in self:
            if record.maintenance_team_id.company_id and record.maintenance_team_id.company_id.id != record.company_id.id:
                record.maintenance_team_id = False

    @api.depends('maintenance_ids.stage_id', 'maintenance_ids.close_date', 'maintenance_ids.request_date')
    def _compute_maintenance_request(self):
        for record in self:
            maintenance_requests = record.maintenance_ids.filtered(lambda mr: mr.maintenance_type == 'corrective' and mr.stage_id.done)
            record.mttr = len(maintenance_requests) and (sum(int((request.close_date - request.request_date).days) for request in maintenance_requests) / len(maintenance_requests)) or 0
            record.latest_failure_date = max((request.request_date for request in maintenance_requests), default=False)
            record.mtbf = record.latest_failure_date and (record.latest_failure_date - record.effective_date).days / len(maintenance_requests) or 0
            record.estimated_next_failure = record.mtbf and record.latest_failure_date + relativedelta(days=record.mtbf) or False

    @api.depends('maintenance_ids.stage_id.done', 'maintenance_ids.archive')
    def _compute_maintenance_count(self):
        for record in self:
            record.maintenance_count = len(record.maintenance_ids)
            record.maintenance_open_count = len(record.maintenance_ids.filtered(lambda mr: not mr.stage_id.done and not mr.archive))


class MaintenanceEquipment(models.Model):
    _name = 'maintenance.equipment'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Maintenance Equipment'
    _check_company_auto = True

    SUBCATEGORY_SELECTION = [
        ('mechanical', 'Mechanical'),
        ('electrical', 'Electrical'),
        ('electronic', 'Electronic'),
        ('hydraulic', 'Hydraulic'),
        ('pneumatic', 'Pneumatic'),
        ('it_equipment', 'IT Equipment'),
        ('office_equipment', 'Office Equipment'),
        ('other', 'Other')
    ]

    name = fields.Char('Equipment Name', required=True, tracking=True)
    active = fields.Boolean(default=True)
    category_id = fields.Many2one('maintenance.equipment.category', string='Equipment Category', required=True, tracking=True)
    subcategory_id = fields.Many2one('maintenance.equipment.subcategory', string='Subcategory', required=True, tracking=True)
    owner_user_id = fields.Many2one('res.users', string='Owner', tracking=True)
    technician_user_id = fields.Many2one('res.users', string='Technician', tracking=True)
    maintenance_team_id = fields.Many2one('maintenance.team', string='Maintenance Team', check_company=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', tracking=True)  # Main vendor field
    partner_ref = fields.Char('Vendor Reference', copy=False, tracking=True)
    location = fields.Char('Location')
    model = fields.Char('Model', tracking=True)
    serial_no = fields.Char('Serial Number', copy=False, tracking=True)
    warranty_date = fields.Date('Warranty Expiration', tracking=True)
    cost = fields.Float('Cost', tracking=True)
    note = fields.Html('Note')
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    
    # Requisition related fields
    requisition_id = fields.Many2one('maintenance.requisition', string='Requisition Number', tracking=True)
    is_manual_override = fields.Boolean('Manual Override', help="Check this to manually edit auto-populated fields")
    
    # MTBF related fields
    expected_mtbf = fields.Integer('Expected MTBF', help='Expected Mean Time Between Failure', tracking=True)
    mtbf = fields.Integer('MTBF', help='Mean Time Between Failure', compute='_compute_mtbf', store=True)
    
    # Failure tracking fields
    estimated_next_failure = fields.Date('Estimated Next Failure', compute='_compute_estimated_next_failure', store=True)
    latest_failure_date = fields.Date('Latest Failure Date', tracking=True)
    mttr = fields.Float('MTTR', help='Mean Time To Repair (hours)', compute='_compute_mttr', store=True)
    maintenance_ids = fields.One2many('maintenance.request', 'equipment_id', string='Maintenance Records')
    
    # Maintenance count fields
    maintenance_open_count = fields.Integer(compute='_compute_maintenance_count', string="Number of open maintenance")
    maintenance_count = fields.Integer(compute='_compute_maintenance_count', string="Total number of maintenance")

    stage_id = fields.Many2one('maintenance.stage', string='Stage', tracking=True)

    # Add fields to track which fields were auto-filled
    is_autofilled = fields.Boolean(string='Is Autofilled', default=False)
    autofilled_fields = fields.Text(string='Autofilled Fields', readonly=True)

    # Add readonly flag fields
    name_readonly = fields.Boolean(compute='_compute_readonly_fields')
    category_readonly = fields.Boolean(compute='_compute_readonly_fields')
    subcategory_readonly = fields.Boolean(compute='_compute_readonly_fields')
    owner_readonly = fields.Boolean(compute='_compute_readonly_fields')
    technician_readonly = fields.Boolean(compute='_compute_readonly_fields')
    team_readonly = fields.Boolean(compute='_compute_readonly_fields')
    vendor_readonly = fields.Boolean(compute='_compute_readonly_fields')
    vendor_ref_readonly = fields.Boolean(compute='_compute_readonly_fields')
    serial_readonly = fields.Boolean(compute='_compute_readonly_fields')
    model_readonly = fields.Boolean(compute='_compute_readonly_fields')
    cost_readonly = fields.Boolean(compute='_compute_readonly_fields')
    warranty_readonly = fields.Boolean(compute='_compute_readonly_fields')

    # Add new fields
    registration_number = fields.Char('Registration Number', readonly=True, copy=False, tracking=True)
    registration_sequence = fields.Integer('Registration Sequence', readonly=True, copy=False)
    is_registered = fields.Boolean('Is Registered', default=False, tracking=True)
    registration_line_id = fields.One2many('equipment.registration.line', 'equipment_id', string='Registration Line')

    # Add registration_date field
    registration_date = fields.Date('Registration Date', readonly=True, copy=False, tracking=True)

    @api.depends('maintenance_ids.close_date', 'maintenance_ids.stage_id.done')
    def _compute_mtbf(self):
        for equipment in self:
            maintenance_done = equipment.maintenance_ids.filtered(lambda x: x.stage_id.done)
            if len(maintenance_done) > 1:
                dates = sorted(maintenance_done.mapped('close_date'))
                if dates:
                    delta_days = (dates[-1] - dates[0]).days
                    equipment.mtbf = delta_days / len(maintenance_done)
            else:
                equipment.mtbf = 0

    @api.depends('latest_failure_date', 'expected_mtbf')
    def _compute_estimated_next_failure(self):
        for equipment in self:
            if equipment.latest_failure_date and equipment.expected_mtbf:
                equipment.estimated_next_failure = fields.Date.add(
                    equipment.latest_failure_date,
                    days=equipment.expected_mtbf
                )
            else:
                equipment.estimated_next_failure = False

    @api.depends('maintenance_ids.duration', 'maintenance_ids.stage_id.done')
    def _compute_mttr(self):
        for equipment in self:
            maintenance_done = equipment.maintenance_ids.filtered(lambda x: x.stage_id.done)
            if maintenance_done:
                total_duration = sum(maintenance_done.mapped('duration'))
                equipment.mttr = total_duration / len(maintenance_done)
            else:
                equipment.mttr = 0.0

    @api.onchange('requisition_id')
    def _onchange_requisition_id(self):
        """Autopopulate fields from requisition when selected"""
        if self.requisition_id and not self.is_manual_override:
            requisition = self.requisition_id
            # Map only requisition fields
            autofilled = {
                'name': requisition.name,
                'category_id': requisition.category_id.id,
                'subcategory_id': requisition.subcategory_id.id,
                'cost': requisition.purchase_cost,
                'partner_id': requisition.vendor.id,  # Updated to use partner_id
                'partner_ref': requisition.vendor_reference,  # Updated to use partner_ref
                'serial_no': requisition.serial_number,
                'model': requisition.model,
                'warranty_date': requisition.warranty_expiration_date,
                'owner_user_id': requisition.requester_id.id,
                'technician_user_id': requisition.technician_id.id,
                'maintenance_team_id': requisition.maintenance_team_id.id,
            }
            self.update(autofilled)
            self.is_autofilled = True
            self.autofilled_fields = ','.join(autofilled.keys())

    @api.onchange('category_id')
    def _onchange_category_id(self):
        """Clear and filter subcategory based on selected category"""
        if not self.is_manual_override:
            self.subcategory_id = False
        return {
            'domain': {
                'subcategory_id': [('category_id', '=', self.category_id.id)] if self.category_id else []
            }
        }

    @api.onchange('is_manual_override')
    def _onchange_manual_override(self):
        """Reset autofilled status when manual override is enabled"""
        if self.is_manual_override:
            self.is_autofilled = False
            self.autofilled_fields = False

    def action_register_equipment(self):
        """Register the equipment"""
        for equipment in self:
            if equipment.registration_number:
                equipment.write({
                    'is_registered': True,
                    'registration_number': equipment.registration_number.replace('TEMP/', '')
                })
                
                # Get the registration record and check if all equipment is registered
                registration = self.env['equipment.registration'].browse(
                    equipment.registration_line_id.registration_id.id
                )
                if registration:
                    registered_count = len(registration.equipment_ids.filtered('is_registered'))
                    total_count = len(registration.equipment_ids)
                    
                    if registered_count == total_count and registration.registration_number.startswith('TEMP/'):
                        registration.write({
                            'registration_number': registration.registration_number.replace('TEMP/', '')
                        })
        return True

    def _track_subtype(self, init_values):
        """Override to handle message subtypes for equipment"""
        self.ensure_one()
        if 'owner_user_id' in init_values and self.owner_user_id:
            # Changed to use a more generic message subtype since mt_mat_assign doesn't exist
            return self.env.ref('mail.mt_note')
        return super(MaintenanceEquipment, self)._track_subtype(init_values)

    @api.depends('serial_no')
    def _compute_display_name(self):
        for record in self:
            if record.serial_no:
                record.display_name = record.name + '/' + record.serial_no
            else:
                record.display_name = record.name

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        domain = domain or []
        query = None
        if name and operator not in expression.NEGATIVE_TERM_OPERATORS and operator != '=':
            query = self._search([('name', '=', name)] + domain, limit=limit, order=order)
        return query or super()._name_search(name, domain, operator, limit, order)

    assign_date = fields.Date('Assigned Date', tracking=True)
    color = fields.Integer('Color Index')
    scrap_date = fields.Date('Scrap Date')
    maintenance_ids = fields.One2many('maintenance.request', 'equipment_id')

    # Add this field to the existing fields
    maintenance_schedule_ids = fields.One2many(
        'maintenance.request', 
        'equipment_id',
        domain=[('maintenance_type', '=', 'preventive')],
        string='Maintenance Schedule'
    )

    @api.onchange('category_id')
    def _onchange_category_id(self):
        self.technician_user_id = self.category_id.technician_user_id

    _sql_constraints = [
        ('serial_no', 'unique(serial_no)', "Another asset already exists with this serial number!"),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to ensure required fields are set"""
        for vals in vals_list:
            if 'requisition_id' in vals and not vals.get('is_manual_override'):
                requisition = self.env['maintenance.requisition'].browse(vals['requisition_id'])
                if requisition:
                    autofilled = {
                        'name': requisition.name,
                        'category_id': requisition.category_id.id,
                        'subcategory_id': requisition.subcategory_id.id,
                        'cost': requisition.purchase_cost,
                        'partner_id': requisition.vendor.id,  # Updated to use partner_id
                        'partner_ref': requisition.vendor_reference,  # Updated to use partner_ref
                        'serial_no': requisition.serial_number,
                        'model': requisition.model,
                        'warranty_date': requisition.warranty_expiration_date,
                        'owner_user_id': requisition.requester_id.id,
                        'technician_user_id': requisition.technician_id.id,
                        'maintenance_team_id': requisition.maintenance_team_id.id,
                    }
                    vals.update(autofilled)
                    vals['is_autofilled'] = True
                    vals['autofilled_fields'] = ','.join(autofilled.keys())
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('owner_user_id'):
            self.message_subscribe(partner_ids=self.env['res.users'].browse(vals['owner_user_id']).partner_id.ids)
        return super(MaintenanceEquipment, self).write(vals)

    @api.model
    def _read_group_category_ids(self, categories, domain, order):
        """ Read group customization in order to display all the categories in
            the kanban view, even if they are empty.
        """
        category_ids = categories._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return categories.browse(category_ids)

    @api.depends('maintenance_ids', 'maintenance_ids.stage_id.done')
    def _compute_maintenance_count(self):
        """Compute the number of maintenance requests"""
        for equipment in self:
            maintenance_data = self.env['maintenance.request'].read_group([
                ('equipment_id', '=', equipment.id)
            ], ['equipment_id'], ['equipment_id'])
            equipment.maintenance_count = maintenance_data[0]['equipment_id_count'] if maintenance_data else 0

    def _compute_maintenance_count(self):
        """Compute maintenance counts"""
        for equipment in self:
            maintenance_data = self.env['maintenance.request'].read_group([
                ('equipment_id', '=', equipment.id)
            ], ['equipment_id', 'stage_id'], ['equipment_id', 'stage_id'])
            
            # Initialize counters
            equipment.maintenance_count = 0
            equipment.maintenance_open_count = 0
            
            for data in maintenance_data:
                equipment.maintenance_count += data['__count']
                # Check if stage is not done
                stage = self.env['maintenance.stage'].browse(data['stage_id'][0])
                if not stage.done:
                    equipment.maintenance_open_count += data['__count']

    @api.depends('requisition_id', 'is_manual_override', 'is_autofilled')
    def _compute_readonly_fields(self):
        """Compute whether fields should be readonly based on requisition and override status"""
        for equipment in self:
            # Fields should be readonly if there's a requisition and no manual override
            is_readonly = equipment.requisition_id and not equipment.is_manual_override and equipment.is_autofilled
            equipment.name_readonly = is_readonly
            equipment.category_readonly = is_readonly
            equipment.subcategory_readonly = is_readonly
            equipment.owner_readonly = is_readonly
            equipment.technician_readonly = is_readonly
            equipment.team_readonly = is_readonly
            equipment.vendor_readonly = is_readonly
            equipment.vendor_ref_readonly = is_readonly
            equipment.serial_readonly = is_readonly
            equipment.model_readonly = is_readonly
            equipment.cost_readonly = is_readonly
            equipment.warranty_readonly = is_readonly

    @api.model
    def create_from_requisition(self, requisition_id, sequence):
        """Create equipment record from requisition with sequence number"""
        requisition = self.env['maintenance.requisition'].browse(requisition_id)
        
        registration_number = f'TEMP/REG/{requisition.requisition_number}/{sequence}'
        
        vals = {
            'name': requisition.name,
            'category_id': requisition.category_id.id,
            'subcategory_id': requisition.subcategory_id.id,
            'cost': requisition.purchase_cost,
            'partner_id': requisition.vendor.id,
            'partner_ref': requisition.vendor_reference,
            'serial_no': requisition.serial_number,
            'model': requisition.model,
            'warranty_date': requisition.warranty_expiration_date,
            'requisition_id': requisition.id,
            'registration_number': registration_number,
            'registration_sequence': sequence,
            'is_registered': False
        }
        return self.create(vals)


class MaintenanceChecklistTemplate(models.Model):
    _name = 'maintenance.checklist.template'
    _description = 'Maintenance Checklist Template'

    name = fields.Char('Name', required=True)
    category_id = fields.Many2one('maintenance.equipment.category', string='Category')
    subcategory = fields.Selection([
        ('forklift', 'Forklift'),
        ('crane', 'Crane'),
        ('conveyor', 'Conveyor'),
        # Match with equipment subcategories
    ], string='Subcategory')
    item_ids = fields.One2many('maintenance.checklist.template.item', 'template_id', string='Checklist Items')

class MaintenanceChecklistTemplateItem(models.Model):
    _name = 'maintenance.checklist.template.item'
    _description = 'Maintenance Checklist Template Item'

    template_id = fields.Many2one('maintenance.checklist.template', string='Template')
    name = fields.Char('Item Name', required=True)
    sequence = fields.Integer('Sequence', default=10)

class MaintenanceRequest(models.Model):
    _name = 'maintenance.request'
    _inherit = ['mail.thread.cc', 'mail.activity.mixin']
    _description = 'Maintenance Request'
    _order = "id desc"
    _check_company_auto = True

    @api.returns('self')
    def _default_stage(self):
        return self.env['maintenance.stage'].search([], limit=1)

    def _creation_subtype(self):
        return self.env.ref('maintenance.mt_req_created')

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'stage_id' in init_values:
            return self.env.ref('maintenance.mt_req_status')
        return super(MaintenanceRequest, self)._track_subtype(init_values)

    def _get_default_team_id(self):
        MT = self.env['maintenance.team']
        team = MT.search([('company_id', '=', self.env.company.id)], limit=1)
        if not team:
            team = MT.search([], limit=1)
        return team.id

    name = fields.Char('Subjects', required=True)
    company_id = fields.Many2one('res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    description = fields.Html('Description')
    request_date = fields.Date('Request Date', tracking=True, default=fields.Date.context_today,
                               help="Date requested for the maintenance to happen")
    owner_user_id = fields.Many2one('res.users', string='Created by User', default=lambda s: s.env.uid)
    category_id = fields.Many2one('maintenance.equipment.category', related='equipment_id.category_id', string='Category', store=True, readonly=True)
    equipment_id = fields.Many2one('maintenance.equipment', string='Equipment', tracking=True, check_company=True)
    user_id = fields.Many2one('res.users', string='Technician', compute='_compute_user_id', store=True, readonly=False, tracking=True)
    stage_id = fields.Many2one('maintenance.stage', string='Stage', ondelete='restrict', tracking=True,
                               group_expand='_read_group_stage_ids', default=_default_stage, copy=False)
    priority = fields.Selection([('0', 'Very Low'), ('1', 'Low'), ('2', 'Normal'), ('3', 'High')], string='Priority')
    color = fields.Integer('Color Index')
    close_date = fields.Date('Close Date', help="Date the maintenance was finished. ")
    kanban_state = fields.Selection([('normal', 'In Progress'), ('blocked', 'Blocked'), ('done', 'Ready for next stage')],
                                    string='Kanban State', required=True, default='normal', tracking=True)
    # active = fields.Boolean(default=True, help="Set active to false to hide the maintenance request without deleting it.")
    archive = fields.Boolean(default=False, help="Set archive to true to hide the maintenance request without deleting it.")
    maintenance_type = fields.Selection([('corrective', 'Corrective'), ('preventive', 'Preventive')], string='Maintenance Type', default="corrective")
    schedule_date = fields.Datetime('Scheduled Date', help="Date the maintenance team plans the maintenance.  It should not differ much from the Request Date. ")
    maintenance_team_id = fields.Many2one('maintenance.team', string='Team', required=True, default=_get_default_team_id,
                                          compute='_compute_maintenance_team_id', store=True, readonly=False, check_company=True)
    duration = fields.Float(help="Duration in hours.")
    done = fields.Boolean(related='stage_id.done')
    instruction_type = fields.Selection([
        ('pdf', 'PDF'), ('google_slide', 'Google Slide'), ('text', 'Text')],
        string="Instruction", default="text"
    )
    instruction_pdf = fields.Binary('PDF', attachment=True, copy=False)
    instruction_pdf_filename = fields.Char('PDF Filename')
    instruction_google_slide = fields.Char('Google Slide', help="Paste the url of your Google Slide. Make sure the access to the document is public.")
    instruction_text = fields.Html('Text')
    recurring_maintenance = fields.Boolean(string="Recurrent", compute='_compute_recurring_maintenance', store=True, readonly=False)
    repeat_interval = fields.Integer(string='Repeat Every', default=1)
    repeat_unit = fields.Selection([
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
        ('year', 'Years'),
    ], default='week')
    repeat_type = fields.Selection([
        ('forever', 'Forever'),
        ('until', 'Until'),
    ], default="forever", string="Until")
    repeat_until = fields.Date(
        string='Repeat Until', 
        default=fields.Date.to_string(fields.Date.today() + relativedelta(year=2024, month=12, day=30))
    )
    checklist_item_ids = fields.One2many('maintenance.checklist.item', 'request_id', string='Checklist Items')

    # Add these new fields
    version = fields.Selection([
        ('main', 'Main'),
        ('child', 'Child')
    ], string='Version', default='main', required=True, tracking=True)
    parent_id = fields.Many2one('maintenance.request', string='Parent Request', 
                               ondelete='cascade', readonly=True)
    child_ids = fields.One2many('maintenance.request', 'parent_id', 
                               string='Child Requests', copy=False)
    child_sequence = fields.Integer(string='Child Sequence', copy=False)

    subcategory_id = fields.Many2one('maintenance.equipment.subcategory', 
                                    string='Subcategory',
                                    related='equipment_id.subcategory_id',
                                    store=True)

    def archive_equipment_request(self):
        self.write({'archive': True, 'recurring_maintenance': False})

    def reset_equipment_request(self):
        """ Reinsert the maintenance request into the maintenance pipe in the first stage"""
        first_stage_obj = self.env['maintenance.stage'].search([], order="sequence asc", limit=1)
        # self.write({'active': True, 'stage_id': first_stage_obj.id})
        self.write({'archive': False, 'stage_id': first_stage_obj.id})

    @api.constrains('repeat_interval', 'recurring_maintenance')
    def _check_repeat_interval(self):
        for record in self:
            if record.recurring_maintenance and record.repeat_interval < 1:
                raise ValidationError(_("Repeat Interval cannot be less than 1."))

    @api.depends('company_id', 'equipment_id')
    def _compute_maintenance_team_id(self):
        for request in self:
            if request.equipment_id and request.equipment_id.maintenance_team_id:
                request.maintenance_team_id = request.equipment_id.maintenance_team_id.id
            if request.maintenance_team_id.company_id and request.maintenance_team_id.company_id.id != request.company_id.id:
                request.maintenance_team_id = False

    @api.depends('company_id', 'equipment_id')
    def _compute_user_id(self):
        for request in self:
            if request.equipment_id:
                request.user_id = request.equipment_id.technician_user_id or request.equipment_id.category_id.technician_user_id
            if request.user_id and request.company_id.id not in request.user_id.company_ids.ids:
                request.user_id = False

    @api.depends('maintenance_type')
    def _compute_recurring_maintenance(self):
        for request in self:
            if request.maintenance_type != 'preventive':
                request.recurring_maintenance = False

    @api.model_create_multi
    def create(self, vals_list):
        maintenance_requests = super().create(vals_list)
        
        for request in maintenance_requests:
            # If this is a child request, copy checklist items from parent
            if request.parent_id and request.version == 'child':
                checklist_vals = []
                for parent_item in request.parent_id.checklist_item_ids:
                    checklist_vals.append({
                        'name': parent_item.name,
                        'sequence': parent_item.sequence,
                        'request_id': request.id,
                        'is_checked': False,  # Start unchecked
                        'observation': '',    # Start with empty observation
                    })
                if checklist_vals:
                    self.env['maintenance.checklist.item'].create(checklist_vals)
        
        return maintenance_requests

    def write(self, vals):
        # Overridden to reset the kanban_state to normal whenever
        # the stage (stage_id) of the Maintenance Request changes.
        if vals and 'kanban_state' not in vals and 'stage_id' in vals:
            vals['kanban_state'] = 'normal'
        
        # Create recurring requests when main request is marked as done
        if ('stage_id' in vals or 'recurring_maintenance' in vals) and self.maintenance_type == 'preventive':
            stage = self.env['maintenance.stage'].browse(vals.get('stage_id', self.stage_id.id))
            if stage.done and (self.recurring_maintenance or vals.get('recurring_maintenance')):
                if self.version == 'main':
                    self._create_recurring_requests()
                
        res = super(MaintenanceRequest, self).write(vals)
        if vals.get('owner_user_id') or vals.get('user_id'):
            self._add_followers()
        if 'stage_id' in vals:
            self.filtered(lambda m: m.stage_id.done).write({'close_date': fields.Date.today()})
            self.filtered(lambda m: not m.stage_id.done).write({'close_date': False})
            self.activity_feedback(['maintenance.mail_act_maintenance_request'])
            self.activity_update()
        if vals.get('user_id') or vals.get('schedule_date'):
            self.activity_update()
        if self._need_new_activity(vals):
            # need to change description of activity also so unlink old and create new activity
            self.activity_unlink(['maintenance.mail_act_maintenance_request'])
            self.activity_update()
        return res

    def _need_new_activity(self, vals):
        return vals.get('equipment_id')

    def _get_activity_note(self):
        self.ensure_one()
        if self.equipment_id:
            return _('Request planned for %s', self.equipment_id._get_html_link())
        return False

    def activity_update(self):
        """ Update maintenance activities based on current record set state.
        It reschedule, unlink or create maintenance request activities. """
        self.filtered(lambda request: not request.schedule_date).activity_unlink(['maintenance.mail_act_maintenance_request'])
        for request in self.filtered(lambda request: request.schedule_date):
            date_dl = fields.Datetime.from_string(request.schedule_date).date()
            updated = request.activity_reschedule(
                ['maintenance.mail_act_maintenance_request'],
                date_deadline=date_dl,
                new_user_id=request.user_id.id or request.owner_user_id.id or self.env.uid)
            if not updated:
                note = request._get_activity_note()
                request.activity_schedule(
                    'maintenance.mail_act_maintenance_request',
                    fields.Datetime.from_string(request.schedule_date).date(),
                    note=note, user_id=request.user_id.id or request.owner_user_id.id or self.env.uid)

    def _add_followers(self):
        for request in self:
            partner_ids = (request.owner_user_id.partner_id + request.user_id.partner_id).ids
            request.message_subscribe(partner_ids=partner_ids)

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        """ Read group customization in order to display all the stages in the
            kanban view, even if they are empty
        """
        stage_ids = stages._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return stages.browse(stage_ids)

    @api.onchange('equipment_id')
    def _onchange_equipment_id(self):
        if self.equipment_id:
            category = self.equipment_id.category_id
            subcategory = self.equipment_id.subcategory_id
            
            _logger.info(f"Selected Equipment: {self.equipment_id.name}")
            _logger.info(f"Category: {category.name}, Subcategory: {subcategory}")
            
            # Clear existing checklist items
            self.checklist_item_ids = [(5, 0, 0)]

            checklist_key = (category.name.lower(), subcategory.lower())
            if checklist_key in self.get_checklist_items():
                checklist_vals = []
                for sequence, item_name in enumerate(self.get_checklist_items()[checklist_key], 1):
                    _logger.debug(f"Creating checklist item: {item_name} with sequence: {sequence}")
                    checklist_vals.append((0, 0, {
                        'name': item_name,
                        'sequence': sequence,
                        'is_checked': False,
                        'observation': False,
                    }))
                self.checklist_item_ids = checklist_vals
            else:
                _logger.warning(f"No checklist found for Category: {category.name}, Subcategory: {subcategory}")

    def get_checklist_items(self):
        return {
            ('machinery', 'forklift'): [
                'Check hydraulic fluid levels',
                'Inspect fork condition and wear',
                'Test brake system functionality',
                'Check tire condition and pressure',
                'Inspect safety features (lights, horn, backup alarm)'
            ],
            ('machinery', 'crane'): [
                'Inspect wire ropes and chains',
                'Check hook and safety latch',
                'Test limit switches',
                'Check hydraulic system for leaks',
                'Verify load capacity indicators'
            ],
            ('machinery', 'conveyor'): [
                'Check belt tension and alignment',
                'Inspect rollers for wear',
                'Test emergency stop system',
                'Check motor and gearbox condition',
                'Inspect belt surface condition'
            ],
        }

    def _create_recurring_requests(self):
        self.ensure_one()
        if not self.recurring_maintenance or self.version != 'main':
            return

        # Get the last child sequence number
        last_child = self.child_ids.sorted('child_sequence', reverse=True)[:1]
        next_sequence = (last_child.child_sequence or 0) + 1

        # Calculate next schedule date
        schedule_date = self.schedule_date or fields.Datetime.now()
        
        # Generate child requests until repeat_until or indefinitely
        while True:
            schedule_date += relativedelta(**{f"{self.repeat_unit}s": self.repeat_interval})
            
            # Check if we should stop generating requests
            if self.repeat_type == 'until' and schedule_date.date() > self.repeat_until:
                break

            # Create child request
            child_vals = {
                'name': f"{self.name} - Child {next_sequence}",
                'equipment_id': self.equipment_id.id,
                'maintenance_type': self.maintenance_type,
                'schedule_date': schedule_date,
                'parent_id': self.id,
                'child_sequence': next_sequence,
                'maintenance_team_id': self.maintenance_team_id.id,
                'user_id': self.user_id.id,
                'duration': self.duration,
                'priority': self.priority,
                'version': 'child',
                # Add these additional fields
                'category_id': self.category_id.id,
                'description': self.description,
                'company_id': self.company_id.id,
                'owner_user_id': self.owner_user_id.id,
                'instruction_type': self.instruction_type,
                'instruction_text': self.instruction_text,
                'instruction_pdf': self.instruction_pdf,
                'instruction_google_slide': self.instruction_google_slide,
            }
            child_request = self.env['maintenance.request'].create(child_vals)

            # Create checklist items for the child request
            for item in self.checklist_item_ids:
                self.env['maintenance.checklist.item'].create({
                    'name': item.name,
                    'sequence': item.sequence,
                    'request_id': child_request.id,
                    'is_checked': False,  # Start unchecked
                    'observation': '',    # Start with empty observation
                })

            next_sequence += 1

            # If repeat type is forever, limit to 52 occurrences (1 year) for safety
            if self.repeat_type == 'forever' and next_sequence > 52:
                break

    def write(self, vals):
        res = super().write(vals)
        
        # Generate child requests when recurring maintenance is enabled
        if 'recurring_maintenance' in vals or any(f in vals for f in ['repeat_interval', 'repeat_unit', 'repeat_type', 'repeat_until']):
            for request in self:
                if request.recurring_maintenance and request.maintenance_type == 'preventive' and request.version == 'main':
                    # Clear existing child requests
                    request.child_ids.unlink()
                    # Create new child requests
                    request._create_recurring_requests()
        
        return res

    def action_open_child_request(self):
        """Opens the child maintenance request in form view."""
        self.ensure_one()
        return {
            'name': _('Child Maintenance Request'),
            'view_mode': 'form',
            'res_model': 'maintenance.request',
            'res_id': self.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def action_confirm_recurring(self):
        """Opens confirmation wizard for recurring maintenance."""
        self.ensure_one()
        return {
            'name': _('Confirm Recurring Maintenance'),
            'type': 'ir.actions.act_window',
            'res_model': 'maintenance.recurring.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_maintenance_request_id': self.id,
                'default_repeat_interval': self.repeat_interval,
                'default_repeat_unit': self.repeat_unit,
                'default_repeat_type': self.repeat_type,
                'default_repeat_until': self.repeat_until,
            }
        }

    def confirm_and_create_recurring(self):
        """Called after confirmation to create recurring requests."""
        self.ensure_one()
        if self.recurring_maintenance and self.maintenance_type == 'preventive' and self.version == 'main':
            # Clear existing child requests
            self.child_ids.unlink()
            # Create new child requests
            self._create_recurring_requests()
        return True

    def write(self, vals):
        """Consolidated write method with proper handling of schedule dates"""
        # Handle schedule date updates first
        if 'schedule_date' in vals:
            # If this is a main request, update child request schedule dates
            for request in self.filtered(lambda r: r.version == 'main'):
                base_date = fields.Datetime.from_string(vals['schedule_date'])
                for child in request.child_ids:
                    # Calculate new schedule date based on sequence and repeat settings
                    new_date = base_date + relativedelta(**{
                        f"{request.repeat_unit}s": request.repeat_interval * child.child_sequence
                    })
                    # Use super().write to bypass the child schedule date restriction
                    super(MaintenanceRequest, child).write({'schedule_date': new_date})
        
        # Check other restrictions for child requests
        for request in self:
            if request.version == 'child':
                restricted_fields = [
                    'maintenance_type', 
                    'recurring_maintenance',
                    'repeat_interval',
                    'repeat_unit',
                    'repeat_type',
                    'repeat_until',
                    'version'
                ]
                # Only check restrictions for fields other than schedule_date
                restricted_updates = set(restricted_fields) & set(vals.keys())
                if restricted_updates:
                    raise UserError(_("Cannot modify maintenance type or "
                                    "recurring settings in child requests."))
        
        # Proceed with the standard write
        return super().write(vals)

    @api.onchange('repeat_type')
    def _onchange_repeat_type(self):
        if self.repeat_type == 'until' and not self.repeat_until:
            self.repeat_until = fields.Date.to_string(fields.Date.today() + relativedelta(year=2024, month=12, day=30))

    @api.constrains('stage_id')
    def _check_stage_sequence(self):
        for request in self:
            if request.stage_id and request.stage_id.sequence < request._origin.stage_id.sequence:
                raise ValidationError(_("You cannot move a maintenance request to a previous stage. Forward progression only."))

    def write(self, vals):
        # If trying to change stage
        if 'stage_id' in vals:
            new_stage = self.env['maintenance.stage'].browse(vals['stage_id'])
            for request in self:
                # Prevent moving back to "New Request" if already "In Progress"
                if (request.stage_id.sequence > new_stage.sequence or
                    (request.stage_id.name == 'In Progress' and new_stage.name == 'New Request')):
                    raise UserError(_("Cannot move maintenance request backwards in stages."))

        return super().write(vals)

    @api.depends('equipment_id', 'equipment_id.subcategory_id')
    def _compute_subcategory(self):
        for request in self:
            request.subcategory_id = request.equipment_id.subcategory_id if request.equipment_id else False

class MaintenanceChecklistItem(models.Model):
    _name = 'maintenance.checklist.item'
    _description = 'Maintenance Checklist Item'
    _order = 'sequence'

    name = fields.Char('Item Name', required=True, index=True)
    request_id = fields.Many2one('maintenance.request', string='Maintenance Request', ondelete='cascade')
    sequence = fields.Integer('Sequence', default=10)
    is_checked = fields.Boolean('Checked', default=False)
    observation = fields.Text('Observations')
    checked_by = fields.Many2one('res.users', string='Checked By', readonly=True)
    checked_at = fields.Datetime('Checked At', readonly=True)

    @api.depends('request_id.stage_id')
    def _compute_readonly_state(self):
        for item in self:
            item.readonly_state = (
                item.request_id.stage_id.name == 'New Request' or 
                item.request_id.stage_id.done
            )

    readonly_state = fields.Boolean(
        string='Readonly State', 
        compute='_compute_readonly_state', 
        store=True
    )

    def write(self, vals):
        """Override write to handle checkbox state changes"""
        if 'is_checked' in vals:
            if vals['is_checked']:
                vals.update({
                    'checked_by': self.env.user.id,
                    'checked_at': fields.Datetime.now()
                })
            else:
                vals.update({
                    'checked_by': False,
                    'checked_at': False
                })
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to handle initial checkbox state"""
        for vals in vals_list:
            if vals.get('is_checked'):
                vals.update({
                    'checked_by': self.env.user.id,
                    'checked_at': fields.Datetime.now()
                })
        return super().create(vals_list)


class MaintenanceTeam(models.Model):
    _name = 'maintenance.team'
    _description = 'Maintenance Teams'

    name = fields.Char('Team Name', required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company',
        default=lambda self: self.env.company)
    member_ids = fields.Many2many(
        'res.users', 'maintenance_team_users_rel', string="Team Members",
        domain="[('company_ids', 'in', company_id)]")
    color = fields.Integer("Color Index", default=0)
    request_ids = fields.One2many('maintenance.request', 'maintenance_team_id', copy=False)
    equipment_ids = fields.One2many('maintenance.equipment', 'maintenance_team_id', copy=False)

    # For the dashboard only
    todo_request_ids = fields.One2many('maintenance.request', string="Requests", copy=False, compute='_compute_todo_requests')
    todo_request_count = fields.Integer(string="Number of Requests", compute='_compute_todo_requests')
    todo_request_count_date = fields.Integer(string="Number of Requests Scheduled", compute='_compute_todo_requests')
    todo_request_count_high_priority = fields.Integer(string="Number of Requests in High Priority", compute='_compute_todo_requests')
    todo_request_count_block = fields.Integer(string="Number of Requests Blocked", compute='_compute_todo_requests')
    todo_request_count_unscheduled = fields.Integer(string="Number of Requests Unscheduled", compute='_compute_todo_requests')

    @api.depends('request_ids.stage_id.done')
    def _compute_todo_requests(self):
        for team in self:
            team.todo_request_ids = self.env['maintenance.request'].search([('maintenance_team_id', '=', team.id), ('stage_id.done', '=', False), ('archive', '=', False)])
            data = self.env['maintenance.request']._read_group(
                [('maintenance_team_id', '=', team.id), ('stage_id.done', '=', False), ('archive', '=', False)],
                ['schedule_date:year', 'priority', 'kanban_state'],
                ['__count']
            )
            team.todo_request_count = sum(count for (_, _, _, count) in data)
            team.todo_request_count_date = sum(count for (schedule_date, _, _, count) in data if schedule_date)
            team.todo_request_count_high_priority = sum(count for (_, priority, _, count) in data if priority == 3)
            team.todo_request_count_block = sum(count for (_, _, kanban_state, count) in data if kanban_state == 'blocked')
            team.todo_request_count_unscheduled = team.todo_request_count - team.todo_request_count_date

    @api.depends('equipment_ids')
    def _compute_equipment(self):
        for team in self:
            team.equipment_count = len(team.equipment_ids)


