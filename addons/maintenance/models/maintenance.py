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

    # Add department field
    department = fields.Selection([
        ('01', 'Maintenance (01)'),
        ('02', 'Validations (02)'),
        ('03', 'IT (03)'),
        ('04', 'Quality (04)')
    ], string='Department', required=True, default='01', tracking=True)

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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If category exists but subcategory doesn't, find or create the NAN subcategory
            if 'category_id' in vals and not vals.get('subcategory_id'):
                nan_subcategory = self.env['maintenance.equipment.subcategory'].search([
                    ('name', '=', 'Not Available'),
                    ('category_id', '=', vals['category_id'])
                ], limit=1)
                
                if not nan_subcategory:
                    # Create it if it doesn't exist
                    nan_subcategory = self.env['maintenance.equipment.subcategory'].create({
                        'name': 'Not Available',
                        'category_id': vals['category_id']
                    })
                
                vals['subcategory_id'] = nan_subcategory.id
                
            # ... rest of the existing create method ...
        return super().create(vals_list)


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
    _rec_name = 'equipment_identifier'

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

    name = fields.Char('Label', tracking=True)
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
    department_readonly = fields.Boolean(compute='_compute_readonly_fields')

    # Add new fields
    registration_number = fields.Char('Registration Number', readonly=True, copy=False, tracking=True)
    registration_sequence = fields.Integer('Registration Sequence', readonly=True, copy=False)
    is_registered = fields.Boolean('Is Registered', default=False, tracking=True)
    registration_line_id = fields.One2many('equipment.registration.line', 'equipment_id', string='Registration Line')

    # Add registration_date field
    registration_date = fields.Date('Registration Date', readonly=True, copy=False, tracking=True)

    # Add department field with the same selection as in requisition
    department = fields.Selection([
        ('01', 'Maintenance (01)'),
        ('02', 'Validations (02)'),
        ('03', 'IT (03)'),
        ('04', 'Quality (04)')
    ], string='Department', tracking=True)

    # Add equipment identifier field
    equipment_identifier = fields.Char(
        'Equipment Identifier', 
        compute='_compute_equipment_identifier',
        store=True,
        readonly=True,
        copy=False, 
        tracking=True,
        help="Unique identifier for equipment in format YY-DEPT-CAT-SUB-Number"
    )

    @api.depends('maintenance_ids.close_date', 'maintenance_ids.stage_id.done')
    def _compute_mtbf(self):
        for equipment in self:
            maintenance_done = equipment.maintenance_ids.filtered(lambda x: x.stage_id.done)
            if len(maintenance_done) > 1:
                # Filter out False/None values and then sort
                valid_dates = [date for date in maintenance_done.mapped('close_date') if date]
                if valid_dates:
                    dates = sorted(valid_dates)
                    delta_days = (dates[-1] - dates[0]).days
                    equipment.mtbf = delta_days / len(maintenance_done)
                else:
                    equipment.mtbf = 0
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
        if self.requisition_id and not self.is_manual_overr:
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
                'department': requisition.department,  # Add department field
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
        """Open registration wizard instead of directly registering equipment"""
        self.ensure_one()
        if not self.registration_number:
            raise UserError(_("This equipment doesn't have a registration number."))
        
        return {
            'name': _('Register Equipment'),
            'type': 'ir.actions.act_window',
            'res_model': 'equipment.registration.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'maintenance.equipment',
                'active_id': self.id,
            },
        }

    def _track_subtype(self, init_values):
        """Override to handle message subtypes for equipment"""
        self.ensure_one()
        if 'owner_user_id' in init_values and self.owner_user_id:
            # Changed to use a more generic message subtype since mt_mat_assign doesn't exist
            return self.env.ref('mail.mt_note')
        return super(MaintenanceEquipment, self)._track_subtype(init_values)

    @api.depends('equipment_identifier', 'serial_no', 'name')
    def _compute_display_name(self):
        for record in self:
            if record.equipment_identifier:
                record.display_name = record.equipment_identifier
            elif record.serial_no and record.name:
                record.display_name = record.name + '/' + record.serial_no
            elif record.name:
                record.display_name = record.name
            else:
                record.display_name = _("Equipment")

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

    @api.onchange('equipment_id')
    def _onchange_equipment_id(self):
        """Update department when equipment changes, safely applying checklists"""
        result = {}
        if self.equipment_id:
            # Update department from equipment
            self.department = self.equipment_id.department
            
            # We're not automatically creating checklists here
            # This will be done when submitting the request
        return result

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
                        'department': requisition.department,  # Add department field
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
            equipment.department_readonly = is_readonly

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
            'is_registered': False,
            'department': requisition.department,  # Add department field
        }
        return self.create(vals)

    def _default_department(self):
        if self.env.user.has_group('maintenance.group_department_maintenance'):
            return '01'
        elif self.env.user.has_group('maintenance.group_department_validations'):
            return '02'
        elif self.env.user.has_group('maintenance.group_department_it'):
            return '03'
        return '01'  # Default to maintenance

    def _onchange_department(self):
        if self.department:
            self.category_id = self.env['maintenance.equipment.category'].search([('department', '=', self.department)], limit=1)

    @api.depends('department', 'category_id', 'subcategory_id', 'registration_sequence', 'is_registered')
    def _compute_equipment_identifier(self):
        for equipment in self:
            # Only set the identifier when equipment is registered
            if equipment.is_registered and equipment.equipment_identifier:
                # Keep existing identifier if already set
                continue
            elif equipment.is_registered:
                # Generate identifier only for registered equipment 
                # This should typically be set by the wizard, not computed
                if not equipment.equipment_identifier:
                    # If somehow we got here without an identifier, generate a temporary one
                    equipment._generate_equipment_identifier()
            else:
                # For unregistered equipment, keep identifier empty
                equipment.equipment_identifier = False

    def _generate_equipment_identifier(self):
        """Generate equipment identifier when registering equipment"""
        # This should not be called directly - only through the wizard
        year = fields.Date.today().strftime('%y')
        department = self.department or '01'  # Default to '01' if not set
        
        # Get category code (first 3 letters)
        category_code = 'UNK'
        if self.category_id and self.category_id.name:
            category_code = self.category_id.name[:3].upper()
        
        # Get subcategory code (first 3 letters or NAN if missing)
        subcategory_code = 'NAN'
        if self.subcategory_id and self.subcategory_id.name:
            subcategory_code = self.subcategory_id.name[:3].upper()
        
        # Start sequence from 1403
        starting_sequence = 1403
        
        # Find the highest sequence number currently in use
        last_equipment = self.env['maintenance.equipment'].search([
            ('equipment_identifier', '!=', False),
            ('equipment_identifier', 'like', f"{year}-{department}-{category_code}-{subcategory_code}-%")
        ], order='equipment_identifier desc', limit=1)
        
        next_sequence = starting_sequence
        if last_equipment and last_equipment.equipment_identifier:
            parts = last_equipment.equipment_identifier.split('-')
            if len(parts) >= 5 and parts[4].isdigit():
                next_sequence = int(parts[4]) + 1
        
        self.equipment_identifier = f"{year}-{department}-{category_code}-{subcategory_code}-{next_sequence}"

    def name_get(self):
        result = []
        for record in self:
            if record.equipment_identifier:
                name = record.equipment_identifier
                if record.name:
                    name = f"{name} - {record.name}"
            elif record.name:
                name = record.name
            else:
                name = _("Unnamed Equipment")
            result.append((record.id, name))
        return result


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
        # Updated to specifically return the 'Draft' stage
        return self.env['maintenance.stage'].search([('name', '=', 'Draft')], limit=1)

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

    name = fields.Char('Identifier', readonly=True, required=True, 
                       default=lambda self: _('New Request'))
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
    maintenance_type = fields.Selection([('corrective', 'Corrective'), ('preventive', 'Preventive')], string='Maintenance Type')
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
        default=fields.Date.to_string(fields.Date.today() + relativedelta(year=2024, month=12, day=30)))
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

    # Add the department field with the same selection as in requisition
    department = fields.Selection([
        ('01', 'Maintenance (01)'),
        ('02', 'Validations (02)'),
        ('03', 'IT (03)'),
        ('04', 'Quality (04)')
    ], string='Department', tracking=True)

    is_recurring_locked = fields.Boolean('Recurring Locked', default=False)

    # Add these fields
    end_date = fields.Datetime('End Date', readonly=True, copy=False)
    can_finish_maintenance = fields.Boolean(compute='_compute_can_finish_maintenance')
    start_date = fields.Datetime('Start Date', readonly=True, copy=False)
    can_start_maintenance = fields.Boolean(compute='_compute_can_start_maintenance')

    qa_rejection_comments = fields.Text('QA Rejection Comments')
    show_rejection_comments = fields.Boolean('Show Rejection Comments', default=False)

    # Add a field to track re-addressed comments after rejection
    qa_re_addressed_comments = fields.Text('Re-addressed Comments', 
        help="Comments from maintenance team explaining how they addressed QA rejection issues")
    show_re_addressed_comments = fields.Boolean('Show Re-addressed Comments Section', default=False)
    re_addressed_completed = fields.Boolean('Re-addressed Completed', default=False)

    # Add a new computed field for QA visibility
    def _compute_can_perform_qa_review(self):
        """Determine if user can see and perform QA review actions"""
        for record in self:
            # Only show QA review buttons if:
            # 1. User is in quality department 
            # 2. Maintenance has been finished (end_date is set)
            # 3. Request has never been approved before
            is_quality_user = self.env.user.has_group('maintenance.group_quality_department')
            record.can_perform_qa_review = (
                is_quality_user and 
                record.end_date and 
                not record.has_been_approved  # Add this condition
            )

    # Add this field to the class
    can_perform_qa_review = fields.Boolean(
        string='Can Perform QA Review', 
        compute='_compute_can_perform_qa_review',
        help="Technical field to control visibility of QA review buttons"
    )

    @api.depends('stage_id')
    def _compute_can_finish_maintenance(self):
        """Show Finish Maintenance button only in In Progress stage for maintenance team"""
        for record in self:
            # Only show Finish button in In Progress stage and for maintenance team members
            is_maintenance_team = self.env.user.has_group('maintenance.group_maintenance_team')
            record.can_finish_maintenance = record.stage_id.name == 'In Progress' and is_maintenance_team

    @api.depends('stage_id', 'maintenance_type')
    def _compute_can_start_maintenance(self):
        """Show Start Maintenance button only in New Request stage for all maintenance types"""
        for record in self:
            record.can_start_maintenance = record.stage_id and record.stage_id.name == 'New Request'

    def action_finish_maintenance(self):
        """Complete maintenance and move to Ready for QA Review stage"""
        self.ensure_one()
        
        if self.maintenance_type == 'corrective':
            # For corrective maintenance, check work description instead of checklist
            if not self.corrective_work_description or len(self.corrective_work_description) < 100:
                raise UserError(_("Please provide a detailed work description of at least 100 characters "
                                 "explaining the issue, repair work performed, and outcomes."))
        else:
            # For preventive maintenance, check all checklist items
            unchecked_items = self.checklist_item_ids.filtered(lambda x: not x.is_checked)
            if unchecked_items:
                # Get the names of unchecked items
                unchecked_names = '\n- '.join(unchecked_items.mapped('name'))
                raise UserError(_("Cannot finish maintenance. The following items are not checked:\n- %s") % unchecked_names)
        
        # If this is a re-submitted maintenance after QA rejection, check if re-addressed comments were provided
        if self.show_re_addressed_comments and not self.re_addressed_completed:
            raise UserError(_("Please fill in the 'Re-addressed Comments' section explaining how you addressed the QA rejection issues."))
        
        # Find the Ready for QA Review stage
        qa_review_stage = self.env['maintenance.stage'].search([('name', '=', 'Ready for QA Review')], limit=1)
        if not qa_review_stage:
            raise UserError(_("Stage 'Ready for QA Review' not found."))
        
        # Move to Ready for QA Review stage and set end date
        result = self.write({
            'stage_id': qa_review_stage.id,
            'end_date': fields.Datetime.now()
        })
        
        # Trigger computation of the can_perform_qa_review field
        self._compute_can_perform_qa_review()
        
        return result

    def action_start_maintenance(self):
        """
        Move request to In Progress stage and set start date.
        For corrective maintenance, assign the next counter number.
        """
        in_progress_stage = self.env['maintenance.stage'].search([('name', '=', 'In Progress')], limit=1)
        if not in_progress_stage:
            raise UserError(_("Stage 'In Progress' not found."))
        
        # If this is a corrective maintenance, get and increment the counter for this equipment
        if self.maintenance_type == 'corrective' and self.equipment_id:
            # Find the highest counter for this equipment
            highest_counter = 0
            last_request = self.env['maintenance.request'].search([
                ('equipment_id', '=', self.equipment_id.id),
                ('maintenance_type', '=', 'corrective'),
                ('corrective_counter', '>', 0)
            ], order='corrective_counter desc', limit=1)
            
            if last_request:
                highest_counter = last_request.corrective_counter
            
            # Increment counter
            new_counter = highest_counter + 1
            
            # Get equipment ID
            eq_id = "0000"
            if self.equipment_id and self.equipment_id.equipment_identifier:
                eq_parts = self.equipment_id.equipment_identifier.split('-')
                if eq_parts:
                    last_part = eq_parts[-1]
                    eq_id = last_part[-4:].zfill(4)
            
            # Generate the new name with proper counter
            new_name = f"CR-{eq_id}-{new_counter:02d}"
            
            # Set the counter and new name but don't prefill the work description
            result = self.write({
                'stage_id': in_progress_stage.id,
                'start_date': fields.Datetime.now(),
                'corrective_counter': new_counter,
                'name': new_name
            })
        else:
            # For preventive maintenance
            result = self.write({
                'stage_id': in_progress_stage.id,
                'start_date': fields.Datetime.now()
            })
        
        return result

    def _create_recurring_requests(self):
        """Modified to ensure child requests are created with correct settings"""
        self.ensure_one()
        if not self.recurring_maintenance or self.version != 'main':
            return

        # Get the New Request stage
        new_request_stage = self.env['maintenance.stage'].search([('name', '=', 'New Request')], limit=1)
        if not new_request_stage:
            raise UserError(_("Stage 'New Request' not found."))

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

            # Create child request with basic values first
            child_vals = {
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
                'category_id': self.category_id.id,
                'description': self.description,
                'company_id': self.company_id.id,
                'owner_user_id': self.owner_user_id.id,
                'instruction_type': self.instruction_type,
                'instruction_text': self.instruction_text,
                'instruction_pdf': self.instruction_pdf,
                'instruction_google_slide': self.instruction_google_slide,
                'is_recurring_locked': True,  # Ensure child requests are locked
                'recurring_maintenance': False,  # Ensure child requests aren't recurring
                'stage_id': new_request_stage.id,  # Start in New Request stage
                'repeat_unit': self.repeat_unit,  # Copy frequency info for name generation
                'repeat_interval': self.repeat_interval
            }
            
            child_request = self.env['maintenance.request'].create(child_vals)

            # Copy the checklist items from the main request to ensure they match
            self._copy_checklist_items_to_child(child_request)

            next_sequence += 1

            # If repeat type is forever, limit to 52 occurrences (1 year) for safety
            if self.repeat_type == 'forever' and next_sequence > 52:
                break

    def action_submit_request(self):
        """Submit the request by moving it to the 'New Request' stage."""
        # Get the New Request stage
        new_request_stage = self.env['maintenance.stage'].search([('name', '=', 'New Request')], limit=1)
        if not new_request_stage:
            raise UserError(_("Stage 'New Request' not found. Please ensure it exists."))
        
        for request in self:
            # Only process requests in Draft stage
            if request.stage_id and request.stage_id.name == 'Draft':
                # Only apply checklists for preventive maintenance
                if request.maintenance_type == 'preventive' and not request.checklist_item_ids:
                    request._apply_hardcoded_checklists()
                
                # Update the stage
                request.write({'stage_id': new_request_stage.id})
        
        return True

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
            # Generate new name based on the pattern
            new_name = request._generate_maintenance_request_name()
            request.write({'name': new_name})
            
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
        """Consolidated write method handling all maintenance request features."""
        # 1. Handle kanban state resets for stage changes
        if vals and 'kanban_state' not in vals and 'stage_id' in vals:
            vals['kanban_state'] = 'normal'
        
        # 2. Check stage sequence restrictions
        if 'stage_id' in vals:
            new_stage = self.env['maintenance.stage'].browse(vals['stage_id'])
            for request in self:
                if (request.stage_id.sequence > new_stage.sequence or
                    (request.stage_id.name == 'In Progress' and new_stage.name == 'New Request')):
                    raise UserError(_("Cannot move maintenance request backwards in stages."))
        
        # 3. Handle schedule date updates for child requests
        if 'schedule_date' in vals:
            for request in self.filtered(lambda r: r.version == 'main'):
                base_date = fields.Datetime.from_string(vals['schedule_date'])
                for child in request.child_ids:
                    new_date = base_date + relativedelta(**{
                        f"{request.repeat_unit}s": request.repeat_interval * child.child_sequence
                    })
                    super(MaintenanceRequest, child).write({'schedule_date': new_date})
        
        # 4. Check restrictions for child request updates
        for request in self:
            if request.version == 'child' and any(f in vals for f in [
                'maintenance_type', 'recurring_maintenance', 'repeat_interval',
                'repeat_unit', 'repeat_type', 'repeat_until', 'version'
            ]):
                raise UserError(_("Cannot modify maintenance type or recurring settings in child requests. Please modify the main request instead."))
        
        # 5. Call super to update the record
        res = super(MaintenanceRequest, self).write(vals)
        
        # 6. Create recurring requests when marked as done or recurring settings change
        recurring_field_changed = any(f in vals for f in [
            'recurring_maintenance', 'repeat_interval', 'repeat_unit', 
            'repeat_type', 'repeat_until'
        ])
        
        stage_done = False
        if 'stage_id' in vals:
            stage = self.env['maintenance.stage'].browse(vals['stage_id'])
            stage_done = stage.done
        
        if (recurring_field_changed or ('stage_id' in vals and stage_done)) and self.filtered(
            lambda r: r.recurring_maintenance and r.maintenance_type == 'preventive' 
                      and r.version == 'main'
        ):
            for request in self.filtered(lambda r: r.recurring_maintenance and 
                                       r.maintenance_type == 'preventive' and 
                                       r.version == 'main'):
                # Clear existing child requests
                request.child_ids.unlink()
                # Create new child requests
                request._create_recurring_requests()
        
        # 7. Handle follower updates and activity updates
        if vals.get('owner_user_id') or vals.get('user_id'):
            self._add_followers()
        if 'stage_id' in vals:
            self.filtered(lambda m: m.stage_id.done).write({'close_date': fields.Date.today()})
            self.filtered(lambda m: not m.stage_id.done).write({'close_date': False})
            self.activity_feedback(['maintenance.mail_act_maintenance_request'])
            self.activity_update()
        if vals.get('user_id') or vals.get('schedule_date'):
            self.activity_update()
        if vals.get('equipment_id'):
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
        """Update department when equipment changes, safely applying checklists"""
        result = {}
        if self.equipment_id:
            # Update department from equipment
            self.department = self.equipment_id.department
            
            # We're not automatically creating checklists here
            # This will be done when submitting the request
        return result

    def _copy_checklist_items_to_child(self, child_request):
        """Copy checklist items from main request to a child request"""
        if not self.checklist_item_ids:
            return
        
        # Clear any existing checklist items to prevent duplicates
        child_request.checklist_item_ids.unlink()
        
        for item in self.checklist_item_ids:
            self.env['maintenance.checklist.item'].create({
                'name': item.name,
                'sequence': item.sequence,
                'request_id': child_request.id,
                'is_checked': False,
                'observation': False,
            })

    def _is_in_draft_stage(self):
        """Check if the maintenance request is in 'Draft' stage."""
        for record in self:
            record.is_in_draft_stage = record.stage_id.name == 'Draft'

    # Add this field to the MaintenanceRequest model
    is_in_draft_stage = fields.Boolean(string='Is Draft Stage', compute='_is_in_draft_stage')

    def _compute_field_readonly(self):
        """Compute if fields should be readonly based on stage"""
        for record in self:
            # Make fields readonly if not in Draft stage
            is_readonly = record.stage_id.name != 'Draft'
            record.field_readonly = is_readonly

    # Add this field to the MaintenanceRequest model
    field_readonly = fields.Boolean(string='Fields Readonly', compute='_compute_field_readonly')

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

    @api.onchange('repeat_type')
    def _onchange_repeat_type(self):
        if self.repeat_type == 'until' and not self.repeat_until:
            self.repeat_until = fields.Date.to_string(fields.Date.today() + relativedelta(year=2024, month=12, day=30))

    @api.constrains('stage_id')
    def _check_stage_sequence(self):
        for request in self:
            if request.stage_id and request.stage_id.sequence < request._origin.stage_id.sequence:
                raise ValidationError(_("You cannot move a maintenance request to a previous stage. Forward progression only."))

    def _apply_hardcoded_checklists(self):
        """Apply hardcoded checklists for preventive maintenance only"""
        self.ensure_one()
        
        # First, clear existing checklist items if any
        if self.checklist_item_ids:
            self.checklist_item_ids.unlink()
        
        # Only create checklists for preventive maintenance
        if self.maintenance_type == 'preventive':
            # For preventive, find category-specific checklists
            category_code = self.category_id.name[:3].upper() if self.category_id and self.category_id.name else ''
            subcategory_code = self.subcategory_id.name[:3].upper() if self.subcategory_id and self.subcategory_id.name else ''
            
            # Composite key for checklist lookup
            checklist_key = f"{category_code}+{subcategory_code}"
            
            # Get hardcoded checklist items for this category+subcategory combo
            checklist_items = self._get_hardcoded_checklist_items(checklist_key)
        
        if not checklist_items:
            _logger.info(f"No checklist found for preventive maintenance with key {checklist_key}")
            # Use default preventive checklist if none found for this equipment type
            checklist_items = [
                'Inspect equipment for visible wear and damage',
                'Check and clean equipment surface',
                'Verify proper operation of all components',
                'Check electrical connections',
                'Lubricate moving parts as needed',
                'Test safety features',
                'Verify equipment performance'
            ]
        
        # Create new checklist items
        for sequence, item_name in enumerate(checklist_items, 1):
            self.env['maintenance.checklist.item'].create({
                'name': item_name,
                'sequence': sequence,
                'request_id': self.id,
                'is_checked': False,
                'observation': False,
            })
        
        _logger.info(f"Applied {len(checklist_items)} checklist items for preventive maintenance")

    def _get_hardcoded_checklist_items(self, key):
        """Return hardcoded checklist items based on category+subcategory key for preventive maintenance"""
        # Standard checklists for preventive maintenance
        checklists = {
            # HVC+RFT (HVAC + Roof Top) example with 5 items
            'HVC+RFT': [
                'Check refrigerant levels and pressure',
                'Inspect condenser and evaporator coils for damage',
                'Test temperature differential across supply/return',
                'Clean or replace air filters',
                'Check electrical connections and components'
            ],
            # Other equipment checklists
            'HVC+AIR': [
                'Check air handler operation',
                'Inspect ductwork for leaks or damage',
                'Measure airflow at registers',
                'Test thermostat operation',
                'Check blower motor and belt condition'
            ],
            'ELE+LIG': [
                'Inspect all fixtures for damage',
                'Test emergency lighting systems',
                'Check for proper illumination levels',
                'Verify switches and controls function correctly',
                'Inspect wiring connections'
            ],
            'PLU+WAT': [
                'Check for water leaks in pipes and fixtures',
                'Test water pressure and flow',
                'Inspect drain lines for clogs',
                'Check water heater operation',
                'Test shut-off valves'
            ]
        }
        
        # Return the matching checklist or an empty list if none found
        return checklists.get(key, [])

    def action_qa_approve(self):
        """Approve the maintenance request and move to Approved stage"""
        self.ensure_one()
        approved_stage = self.env['maintenance.stage'].search([('name', '=', 'Approved')], limit=1)
        if not approved_stage:
            raise UserError(_("Stage 'Approved' not found."))
        
        # Set has_been_approved to True and clear all QA-related fields
        return self.write({
            'stage_id': approved_stage.id,
            'has_been_approved': True,  # Set this flag when approving
            'show_rejection_comments': False,
            'qa_rejection_comments': False,
            'show_re_addressed_comments': False,
            'qa_re_addressed_comments': False,
            're_addressed_completed': False
        })

    def action_qa_reject(self):
        """Show rejection comments field"""
        self.ensure_one()
        return self.write({
            'show_rejection_comments': True
        })

    def action_qa_submit_rejection(self):
        """Submit rejection and move back to In Progress stage"""
        self.ensure_one()
        
        if not self.qa_rejection_comments:
            raise UserError(_("Please provide rejection comments."))
        
        in_progress_stage = self.env['maintenance.stage'].search([('name', '=', 'In Progress')], limit=1)
        if not in_progress_stage:
            raise UserError(_("Stage 'In Progress' not found."))
        
        # Add a message in the chatter about the rejection
        self.message_post(
            body=_("Maintenance request rejected and moved back to In Progress.\nRejection Comments: %s") % self.qa_rejection_comments,
            message_type='comment'
        )
        
        # Temporarily disable the constraint check for this specific action
        # Use direct SQL to bypass ORM constraints
        self.env.cr.execute("""
            UPDATE maintenance_request 
            SET stage_id = %s, 
                show_rejection_comments = FALSE, 
                show_re_addressed_comments = TRUE,
                re_addressed_completed = FALSE,
                end_date = NULL
            WHERE id = %s
        """, (in_progress_stage.id, self.id))
        
        # Use _invalidate_cache instead of invalidate_cache
        self.env['maintenance.request']._invalidate_cache(['stage_id', 'show_rejection_comments', 
                                                          'show_re_addressed_comments', 're_addressed_completed',
                                                          'end_date'])
        
        return True

    def action_submit_re_addressed_comments(self):
        """Submit re-addressed comments after addressing QA rejection issues"""
        self.ensure_one()
        
        if not self.qa_re_addressed_comments:
            raise UserError(_("Please explain how you addressed the QA rejection issues."))
        
        # Add a message in the chatter about the re-addressed comments
        self.message_post(
            body=_("Maintenance team has addressed QA rejection issues.\nRe-addressed Comments: %s") % self.qa_re_addressed_comments,
            message_type='comment'
        )
        
        # Mark as completed
        self.write({
            're_addressed_completed': True
        })
        
        return True

    def _compute_can_re_address_qa_rejection(self):
        """Determine if user can see and fill re-addressed comments section"""
        for record in self:
            # Only show re-address section if:
            # 1. User is in maintenance team
            # 2. Request shows re-addressed comments section
            is_maintenance_team = self.env.user.has_group('maintenance.group_maintenance_team')
            record.can_re_address_qa_rejection = is_maintenance_team and record.show_re_addressed_comments

    can_re_address_qa_rejection = fields.Boolean(
        string='Can Re-address QA Rejection',
        compute='_compute_can_re_address_qa_rejection',
        help="Technical field to control visibility of re-addressed comments section"
    )

    # Add this field to the MaintenanceRequest class
    has_been_approved = fields.Boolean('Has Been Approved', default=False, copy=False)

    def _generate_maintenance_request_name(self):
        """Generate name with pattern based on maintenance type:
        - Preventive: PM-NNNN-Freq-Version
        - Corrective: CR-NNNN-XX (where XX is the counter)
        """
        self.ensure_one()
        
        # Maintenance type prefix
        type_prefix = "PM" if self.maintenance_type == 'preventive' else "CR"
        
        # Equipment identifier (last 4 digits)
        eq_id = "0000"
        if self.equipment_id and self.equipment_id.equipment_identifier:
            # Extract last 4 digits or pad with zeros if shorter
            eq_parts = self.equipment_id.equipment_identifier.split('-')
            if eq_parts:
                last_part = eq_parts[-1]
                eq_id = last_part[-4:].zfill(4)
        
        # For corrective maintenance, use counter format (CR-NNNN-XX)
        if self.maintenance_type == 'corrective':
            # The counter will be assigned when clicking "Start Maintenance"
            # For now, placeholder value until "Start Maintenance" is clicked
            return f"{type_prefix}-{eq_id}-00"
        
        # For preventive maintenance, use frequency-based format
        frequency = "Reg"  # Default
        if self.maintenance_type == 'preventive':
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
        
        # Version suffix (Main or C01, C02, etc.)
        version_suffix = "Main"
        if self.version == 'child' and self.child_sequence:
            version_suffix = f"C{self.child_sequence:02d}"
        
        # Construct the full name for preventive maintenance
        return f"{type_prefix}-{eq_id}-{frequency}-{version_suffix}"

    # Add a new field to track the counter for corrective maintenance per equipment
    corrective_counter = fields.Integer('Corrective Counter', default=0, copy=False)

    # Add a new compute method to determine if a "Submit" button should be visible
    @api.depends('stage_id')
    def _compute_can_submit_request(self):
        """Determine if the Submit button should be visible in Draft stage"""
        for record in self:
            record.can_submit_request = record.stage_id and record.stage_id.name == 'Draft'

    # Add this field to the MaintenanceRequest model
    can_submit_request = fields.Boolean(
        string='Can Submit Request', 
        compute='_compute_can_submit_request',
        help="Technical field to control visibility of Submit button"
    )

    # Add a new field for corrective maintenance work description
    corrective_work_description = fields.Text(
        'Work Description', 
        help="Detailed description of the issue, repair work performed, and outcomes. "
             "Minimum 100 characters required for corrective maintenance."
    )

    # Add this to the MaintenanceRequest class
    corrective_work_description_length = fields.Integer(
        string='Work Description Length',
        compute='_compute_corrective_work_description_length',
        store=False
    )

    @api.depends('corrective_work_description')
    def _compute_corrective_work_description_length(self):
        """Compute the length of the corrective work description"""
        for record in self:
            if record.corrective_work_description:
                record.corrective_work_description_length = len(record.corrective_work_description)
            else:
                record.corrective_work_description_length = 0


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
        """Only allow editing checklist items in 'In Progress' stage"""
        for item in self:
            # Make editable only when in "In Progress" stage
            item.readonly_state = item.request_id.stage_id.name != 'In Progress'

    readonly_state = fields.Boolean(
        string='Readonly State', 
        compute='_compute_readonly_state', 
        store=True
    )

    def write(self, vals):
        """Prevent modifications if not in In Progress stage"""
        for record in self:
            if record.request_id.stage_id.name != 'In Progress' and 'is_checked' in vals:
                raise UserError(_("Checklist items can only be modified when maintenance is in progress."))
            
            # Update checked_by and checked_at when checkbox is checked
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
                [('maintenance_team_id', '=', team.id), ('stage_id.done', '=', False)],
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


