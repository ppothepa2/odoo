from odoo import api, fields, models, _
from odoo.exceptions import UserError
from lxml import etree

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
    state = fields.Selection([
        ('draft', 'Draft'),
        ('partially_registered', 'Partially Registered'),
        ('fully_registered', 'Fully Registered')
    ], string='Status', default='draft', tracking=True, copy=False, required=True)
    
    @api.onchange('requisition_id')
    def _onchange_requisition_id(self):
        """Update state when requisition is selected"""
        if self.requisition_id:
            if self.requisition_id.state != 'done':
                raise UserError(_('Only requisitions in Done state can be selected.'))
            self.state = 'draft'
            # Update requisition state
            self.requisition_id.write({
                'state': 'partially_registered_with_equipment'
            })

    @api.depends('equipment_ids.is_registered')
    def _compute_progress(self):
        """Extend existing compute method to update states"""
        for rec in self:
            registered = len(rec.equipment_ids.filtered('is_registered'))
            total = len(rec.equipment_ids)
            percentage = (registered / total * 100) if total > 0 else 0
            rec.progress = f"{percentage:.0f}%"
            rec.all_registered = total > 0 and registered == total

            # Update states based on progress
            if total > 0:
                if registered == 0:
                    rec.state = 'draft'
                    # Update requisition state when no equipment is registered
                    if rec.requisition_id:
                        rec.requisition_id.write({
                            'state': 'partially_registered_with_equipment'
                        })
                elif registered == total:
                    rec.state = 'fully_registered'
                    # Update requisition state when all equipment is registered
                    if rec.requisition_id:
                        rec.requisition_id.write({
                            'state': 'fully_registered_with_equipment'
                        })
                else:
                    rec.state = 'partially_registered'
                    # Update requisition state when some equipment is registered
                    if rec.requisition_id:
                        rec.requisition_id.write({
                            'state': 'partially_registered_with_equipment'
                        })

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

    def _default_department(self):
        """Set default department based on user access rights"""
        if self.env.user.has_group('maintenance.group_department_maintenance'):
            return '01'
        elif self.env.user.has_group('maintenance.group_department_validations'):
            return '02'
        elif self.env.user.has_group('maintenance.group_department_it'):
            return '03'
        return '01'  # Default to maintenance

    department = fields.Selection([
        ('01', 'Maintenance (01)'),
        ('02', 'Validations (02)'),
        ('03', 'IT (03)')
    ], string='Department', default=_default_department, readonly=True)

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        # Override to dynamically modify domain based on user
        result = super(EquipmentRegistration, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        
        if view_type == 'form' and result.get('fields', {}).get('requisition_id'):
            doc = etree.XML(result['arch'])
            for node in doc.xpath("//field[@name='requisition_id']"):
                if self.env.user.has_group('maintenance.group_maintenance_super_admin'):
                    # Admin sees all done requisitions
                    node.set('domain', "[('state', '=', 'done')]")
                else:
                    # Others see only their department's
                    dept = self._default_department()
                    node.set('domain', f"[('state', '=', 'done'), ('department', '=', '{dept}')]")
            
            result['arch'] = etree.tostring(doc, encoding='unicode')
        return result

class EquipmentRegistrationLine(models.Model):
    _name = 'equipment.registration.line'
    _description = 'Equipment Registration Line'

    registration_id = fields.Many2one('equipment.registration', string='Registration')
    requisition_id = fields.Many2one(related='registration_id.requisition_id', 
        string='Requisition', store=True, readonly=True)
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