"""Trial-Anfrage mit Lead-Capture.

Trials haben:
- KEIN Activation-Code (Friction-Reduction, DECISION #28)
- Laufzeit aus ``product.wb_trial_days`` (Standard 7 Tage)
- 1 Trial je Produkt pro Email + 1 pro Domain + 1 pro DB-UUID (DECISION #29, #94)
- Freigabe sofort, außer bei Missbrauchsverdacht (DECISION #94)
"""

import ipaddress
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


TRIAL_STATES = [
    ('pending', 'Pending (wartet auf Freigabe)'),
    ('active', 'Active (Trial läuft)'),
    ('converted', 'Converted (zu Paid)'),
    ('expired', 'Expired'),
    ('rejected', 'Rejected (Missbrauchs-Verdacht)'),
]


COMPANY_SIZES = [
    ('solo', 'Solo / Freiberufler'),
    ('2-10', '2–10 Mitarbeiter'),
    ('11-50', '11–50 Mitarbeiter'),
    ('51-250', '51–250 Mitarbeiter'),
    ('251+', '251+ Mitarbeiter'),
]

# Wegwerf-Adressen: Antrag geht in die manuelle Prüfung statt sofort frei.
# Ergänzbar per Systemparameter wb_subscription.trial_review_email_domains
# (kommagetrennt).
DEFAULT_REVIEW_EMAIL_DOMAINS = frozenset({
    '10minutemail.com', 'guerrillamail.com', 'mailinator.com', 'sharklasers.com',
    'temp-mail.org', 'tempmail.com', 'trashmail.com', 'yopmail.com',
})
REVIEW_EMAIL_DOMAINS_PARAM = 'wb_subscription.trial_review_email_domains'
# Ab so vielen Anträgen derselben IP in 30 Tagen: manuelle Prüfung.
REVIEW_IP_THRESHOLD_PARAM = 'wb_subscription.trial_review_ip_threshold'
DEFAULT_REVIEW_IP_THRESHOLD = 3


def normalize_trial_domain(value):
    """Lower-case host of an Odoo base URL, without scheme, port, path, ``www.``."""
    value = (value or '').strip().lower()
    if '://' in value:
        value = value.split('://', 1)[1]
    value = value.split('/', 1)[0].split('?', 1)[0]
    if value.startswith('[') and ']' in value:
        value = value[1:value.index(']')]
    elif value.count(':') == 1:
        value = value.split(':', 1)[0]
    if value.startswith('www.'):
        value = value[4:]
    return value


class WbLicenseTrialRequest(models.Model):
    _name = 'wb.license.trial.request'
    _description = 'Trial-Anfrage mit Lead-Capture'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        required=True,
        readonly=True,
        default='Neu',
        copy=False,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Gewünschtes Produkt',
        required=True,
        domain=[('wb_is_license_product', '=', True)],
    )

    contact_name = fields.Char(string='Name', required=True)
    contact_email = fields.Char(string='Email', required=True, index=True)
    contact_phone = fields.Char(string='Telefon')
    company_name = fields.Char(string='Firma', required=True)
    company_size = fields.Selection(COMPANY_SIZES)
    industry = fields.Char(string='Branche')
    domain = fields.Char(string='Odoo-Domain', required=True, index=True)
    domain_normalized = fields.Char(
        string='Domain (normalisiert)', compute='_compute_domain_normalized',
        store=True, index=True,
        help="Host ohne Schema, Port, Pfad und www. Grundlage für die Regel "
             "„1 Test je Domain“.",
    )
    db_uuid = fields.Char(
        string='DB-UUID', index=True, readonly=True,
        help="database.uuid des Kunden-Odoo. Der Test-Schlüssel wird daran "
             "gebunden wie jede andere Lizenz.",
    )
    ip_address = fields.Char(string='IP-Adresse', readonly=True)
    expected_use_case = fields.Text(string='Geplanter Einsatz')
    review_reason = fields.Char(
        string='Grund der Prüfung', readonly=True,
        help="Warum der Antrag nicht sofort freigegeben wurde (Missbrauchsverdacht).",
    )

    state = fields.Selection(TRIAL_STATES, required=True, default='pending', tracking=True)
    license_id = fields.Many2one('wb.license.key', string='Erzeugte Trial-Lizenz', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Kontakt (auto-erstellt)')

    @api.depends('domain')
    def _compute_domain_normalized(self):
        for rec in self:
            rec.domain_normalized = normalize_trial_domain(rec.domain) or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'Neu':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'wb.license.trial.request') or 'TRL/XXXX'
        return super().create(vals_list)

    # ------------------------------------------------------------------ Regeln

    @api.model
    def _find_previous(self, product_code, email, domain, db_uuid):
        """Earlier trial requests (any state) of this product for the same email, domain or database.

        Returns a dict ``{'email': rec, 'domain': rec, 'db_uuid': rec}`` with the
        first match per criterion (empty recordsets if none). A trial of another
        product does not count (DECISIONS.md #94).
        """
        normalized = normalize_trial_domain(domain)
        base = [('product_id.wb_technical_code', '=', product_code)]
        return {
            'email': self.search(base + [('contact_email', '=ilike', email)], limit=1)
            if email else self.browse(),
            'domain': self.search(base + [('domain_normalized', '=', normalized)], limit=1)
            if normalized else self.browse(),
            'db_uuid': self.search(base + [('db_uuid', '=', db_uuid)], limit=1)
            if db_uuid else self.browse(),
        }

    @api.model
    def _review_reason(self, product, email, domain, db_uuid, ip):
        """Abuse suspicion: reason text if the request needs manual review, else False."""
        icp = self.env['ir.config_parameter'].sudo()
        email_domain = email.rsplit('@', 1)[-1].lower() if '@' in email else ''
        extra = {
            d.strip().lower() for d in (icp.get_param(REVIEW_EMAIL_DOMAINS_PARAM) or '').split(',')
            if d.strip()
        }
        if email_domain in (DEFAULT_REVIEW_EMAIL_DOMAINS | extra):
            return _("Wegwerf-Adresse (%s)") % email_domain

        host = normalize_trial_domain(domain)
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False
        if not is_ip and host != 'localhost' and '.' not in host:
            return _("Domain ohne gültigen Hostnamen (%s)") % (host or '-')

        # Diese Datenbank hatte schon eine Lizenz für das Produkt (z. B.
        # abgelaufen): kein kostenloser Neustart per Test.
        if db_uuid:
            License = self.env['wb.license.key'].sudo()
            if License.search_count([
                ('bound_db_uuid', '=', db_uuid),
                ('product_code', '=', product.wb_technical_code),
            ], limit=1):
                return _("Datenbank hatte schon eine Lizenz für %s") % product.wb_technical_code

        if ip:
            try:
                threshold = int(icp.get_param(REVIEW_IP_THRESHOLD_PARAM) or DEFAULT_REVIEW_IP_THRESHOLD)
            except ValueError:
                threshold = DEFAULT_REVIEW_IP_THRESHOLD
            since = fields.Datetime.now() - timedelta(days=30)
            if threshold > 0 and self.sudo().search_count([
                ('ip_address', '=', ip), ('create_date', '>=', since),
            ]) >= threshold:
                return _("%s Testanträge derselben IP in 30 Tagen") % threshold
        return False

    def _response(self):
        """Public answer of /api/license/trial for this request (no secrets but the key)."""
        self.ensure_one()
        if self.state == 'active' and self.license_id.state == 'trial':
            lic = self.license_id
            return {
                'status': 'ok',
                'trial_id': self.name,
                'key': lic.name,
                'state': lic.state,
                'product_code': lic.product_code,
                'valid_from': lic.valid_from.isoformat() if lic.valid_from else None,
                'valid_to': lic.valid_to.isoformat() if lic.valid_to else None,
                'max_users': lic.max_users,
            }
        if self.state == 'pending':
            return {
                'status': 'pending',
                'trial_id': self.name,
                'message': 'Trial request received. It is being reviewed; you will receive the key by email.',
            }
        return {'error': 'TRIAL_ALREADY_USED'}

    # --------------------------------------------------------------- Freigabe

    def action_approve_and_create_trial(self):
        """Legt res.partner + wb.license.key (state='trial') an und aktiviert das Trial.

        Laufzeit aus ``product.wb_trial_days``; der Schlüssel ist an Domain und
        DB-UUID des Antrags gebunden wie jede andere Lizenz.
        """
        for rec in self:
            if rec.state != 'pending':
                continue
            days = rec.product_id.wb_trial_days
            if days <= 0:
                raise UserError(_("Für %s ist kein Test vorgesehen (Trial-Tage = 0).")
                                % rec.product_id.display_name)

            partner = rec.partner_id
            if not partner:
                partner = self.env['res.partner'].search([
                    ('email', '=ilike', rec.contact_email)
                ], limit=1)
            if not partner:
                partner = self.env['res.partner'].create({
                    'name': rec.company_name or rec.contact_name,
                    'email': rec.contact_email,
                    'phone': rec.contact_phone,
                    'company_type': 'company' if rec.company_name else 'person',
                    'is_company': bool(rec.company_name),
                    'comment': _("Auto-erzeugt aus Trial-Request %s") % rec.name,
                })

            now = fields.Datetime.now()
            license = self.env['wb.license.key'].create({
                'product_id': rec.product_id.id,
                'partner_id': partner.id,
                'state': 'trial',
                'valid_from': fields.Date.today(),
                'valid_to': fields.Date.today() + timedelta(days=days),
                'activated_at': now,
                'activated_fingerprint': self.env['wb.key.generator'].compute_fingerprint(
                    rec.domain, rec.db_uuid) if rec.db_uuid else False,
                'bound_domain': rec.domain,
                'bound_db_uuid': rec.db_uuid or False,
            })
            rec.write({
                'state': 'active',
                'partner_id': partner.id,
                'license_id': license.id,
            })
            self.env['wb.license.event'].log_event(
                license, 'trial_started',
                domain=rec.domain,
                db_uuid=rec.db_uuid or None,
                details={'trial_request_id': rec.id},
            )
            license._send_template('wb_subscription.mail_template_trial_activated')
            license._send_telegram(
                "🆕 Trial gestartet: {key} ({company}, {email})",
                key=license.name,
                company=rec.company_name or '',
                email=rec.contact_email or '',
            )

    def action_reject(self):
        """Manuell ablehnen (Missbrauchsverdacht bestätigt). Kein Schlüssel."""
        self.filtered(lambda r: r.state == 'pending').write({'state': 'rejected'})
