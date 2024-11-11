# -*- coding: utf-8 -*-

{
    'name': 'Maintenance',
    'version': '1.0',
    'sequence': 100,
    'category': 'Manufacturing/Maintenance',
    'description': """
Track equipment and maintenance requests""",
    'depends': ['mail'],
    'summary': 'Track equipment and manage maintenance requests',
    'website': 'https://www.odoo.com/app/maintenance',
    'data': [
        'security/maintenance.xml',
        'security/ir.model.access.csv',
        'wizard/maintenance_recurring_wizard_views.xml',
        'views/maintenance_assets.xml',
        'views/maintenance_views.xml',
        'views/mail_activity_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'demo': ['data/maintenance_demo.xml'],
    'installable': True,
    'application': True,
    'assets': {
        'web.assets_backend': [
            'maintenance/static/src/scss/maintenance.scss',
        ],
        'web.assets_tests': [
            'maintenance/static/tests/tours/**/*',
        ],
    },
    'license': 'LGPL-3',
}
