from odoo import fields, models


class WbLicenseTag(models.Model):
    _name = 'wb.license.tag'
    _description = 'Lizenz-Tag für Kategorisierung'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    color = fields.Integer(default=0)
    sequence = fields.Integer(default=10)
    description = fields.Text()
    active = fields.Boolean(default=True)
    license_ids = fields.Many2many(
        'wb.license.key',
        'wb_license_key_tag_rel',
        'tag_id',
        'license_id',
        string='Lizenzen',
    )

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Tag-Name muss einzigartig sein.'),
    ]
