"""Versionierte Releases von Lizenz-Produkt-Modulen.

Source of Truth für „welcher Tarball liegt für welche Version bereit".
Pro ``product.template`` darf zu jeder Zeit höchstens eine Release auf
``is_current=True`` stehen — diese wird vom täglichen Lizenz-Ping als
neueste Version ausgeliefert.

Ältere Releases bleiben mit ``state='published'`` weiter über das
Kunden-Portal abrufbar (Rollback-Szenario), bis sie auf ``archived``
gesetzt werden.
"""

import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


RELEASE_STATES = [
    ('draft', 'Entwurf (intern)'),
    ('published', 'Veröffentlicht'),
    ('archived', 'Archiviert'),
]


class WbProductRelease(models.Model):
    _name = 'wb.product.release'
    _description = 'Modul-Release (versionierte Tarballs für Lizenz-Produkte)'
    _order = 'product_tmpl_id, released_at desc, id desc'
    _rec_name = 'display_name'

    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Produkt',
        required=True,
        ondelete='cascade',
        index=True,
        domain=[('wb_is_license_product', '=', True)],
    )
    product_code = fields.Char(
        related='product_tmpl_id.wb_technical_code',
        store=True,
        index=True,
    )
    version = fields.Char(
        required=True,
        index=True,
        help="Modul-Version aus __manifest__.py, z.B. '19.0.1.2.0'.",
    )
    attachment_id = fields.Many2one(
        'ir.attachment',
        string='Tarball-Anhang',
        ondelete='restrict',
        domain=[('res_model', '=', 'wb.product.release')],
        help="ir.attachment mit dem .tar.gz-Build der Modul-Version. "
             "Wird von /my/downloads gestreamt.",
    )
    filename = fields.Char(
        string='Dateiname',
        help="Wird im Content-Disposition-Header beim Download verwendet, "
             "z. B. 'wb_bitwarden_pro-19.0.1.2.0.tar.gz'. "
             "Leer = Name aus dem Attachment.",
    )
    released_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    state = fields.Selection(
        RELEASE_STATES,
        required=True,
        default='draft',
        index=True,
        tracking=True,
    )
    is_current = fields.Boolean(
        index=True,
        tracking=True,
        help="Genau eine Release pro Produkt darf 'is_current' sein. "
             "Diese Release wird vom Lizenz-Ping als neueste Version "
             "ausgeliefert. Wird durch action_set_current gesetzt.",
    )
    changelog = fields.Html(
        string='Changelog',
        help="Optional. Wird im Kunden-Portal über der Release angezeigt.",
    )
    download_count = fields.Integer(
        readonly=True,
        default=0,
        help="Wie oft die Release über das Kunden-Portal heruntergeladen wurde.",
    )
    has_attachment = fields.Boolean(
        compute='_compute_has_attachment',
        store=True,
    )

    display_name = fields.Char(compute='_compute_display_name', store=True)

    company_id = fields.Many2one(
        related='product_tmpl_id.company_id', store=True, index=True,
    )

    _sql_constraints = [
        ('version_unique_per_product',
         'UNIQUE(product_tmpl_id, version)',
         'Pro Produkt darf jede Versionsnummer nur einmal vergeben werden.'),
    ]

    @api.depends('product_tmpl_id', 'version')
    def _compute_display_name(self):
        for rec in self:
            tmpl = rec.product_tmpl_id
            label = tmpl.wb_module_technical_name or tmpl.name or '?'
            rec.display_name = f"{label} {rec.version or '?'}"

    @api.depends('attachment_id')
    def _compute_has_attachment(self):
        for rec in self:
            rec.has_attachment = bool(rec.attachment_id)

    @api.constrains('is_current', 'product_tmpl_id', 'state')
    def _check_single_current_per_product(self):
        for rec in self:
            if not rec.is_current:
                continue
            if rec.state != 'published':
                raise ValidationError(_(
                    "Nur 'published' Releases können auf 'is_current' "
                    "gesetzt werden — Release '%s' ist '%s'."
                ) % (rec.display_name, rec.state))
            duplicates = self.sudo().search([
                ('product_tmpl_id', '=', rec.product_tmpl_id.id),
                ('is_current', '=', True),
                ('id', '!=', rec.id),
            ])
            if duplicates:
                raise ValidationError(_(
                    "Pro Produkt darf nur eine Release 'is_current' sein. "
                    "Bestehende: %s. Bitte action_set_current verwenden, "
                    "die das automatisch umsetzt."
                ) % ', '.join(duplicates.mapped('display_name')))

    def action_set_current(self):
        """Markiert diese Release als aktuelle Version.

        Setzt ``is_current=False`` auf allen anderen Releases desselben
        Produkts und ``is_current=True`` auf dieser. Falls die Release
        noch im Draft ist, wird sie zugleich auf 'published' gesetzt.
        """
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_(
                "Release '%s' hat keinen Tarball-Anhang — bitte zuerst "
                "eine Datei hochladen."
            ) % self.display_name)

        siblings = self.sudo().search([
            ('product_tmpl_id', '=', self.product_tmpl_id.id),
            ('is_current', '=', True),
            ('id', '!=', self.id),
        ])
        siblings.write({'is_current': False})

        vals = {'is_current': True}
        if self.state == 'draft':
            vals['state'] = 'published'
        self.write(vals)
        return True

    def action_publish(self):
        """Setzt eine Draft-Release auf 'published' (ohne is_current zu ändern)."""
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'published'
        return True

    def action_archive_release(self):
        """Setzt Release auf 'archived'. Wenn sie current war, wird is_current
        entfernt — der Kunde sieht dann keine current-Version mehr,
        bis eine andere Release als current markiert wird."""
        for rec in self:
            rec.write({'state': 'archived', 'is_current': False})
        return True

    def increment_download_count(self):
        """Wird vom Portal-Controller bei jedem Download aufgerufen."""
        for rec in self:
            rec.sudo().download_count = rec.download_count + 1

    @api.model
    def create_with_file(self, product_tmpl_id, version, file_b64, filename,
                          changelog=None, set_current=False):
        """Convenience-Helper: legt Release + Attachment in einem Rutsch an.

        :param product_tmpl_id: ID des Lizenz-Produkts
        :param version: Versions-String, z. B. '19.0.1.2.0'
        :param file_b64: Base64-encoded Tarball-Inhalt
        :param filename: Dateiname für Content-Disposition
        :param changelog: Optional HTML-String
        :param set_current: Wenn True, sofort als current setzen
        :return: erzeugte wb.product.release
        """
        release = self.sudo().create({
            'product_tmpl_id': product_tmpl_id,
            'version': version,
            'filename': filename,
            'changelog': changelog or False,
            'state': 'published' if set_current else 'draft',
        })
        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'res_model': 'wb.product.release',
            'res_id': release.id,
            'datas': file_b64,
        })
        release.attachment_id = attachment
        if set_current:
            release.action_set_current()
        return release
