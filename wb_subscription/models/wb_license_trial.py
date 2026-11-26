"""Trial-Anfrage mit Lead-Capture.

Trials haben:
- KEIN Activation-Code (Friction-Reduction, DECISION #28)
- Laufzeit aus ``product.wb_trial_days`` (Standard 7 Tage)
- 1 Trial je Produkt pro Email + 1 pro Domain + 1 pro DB-UUID (DECISION #29, #94)
- Freigabe sofort, außer bei Missbrauchsverdacht (DECISION #94)
"""

import ipaddress
import logging
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
# Aufbewahrung (DECISIONS.md #97): IP nach 90 Tagen, Kontaktdaten nicht
# umgewandelter Anträge nach 12 Monaten. 0 schaltet die Stufe ab.
RETENTION_IP_PARAM = 'wb_subscription.retention_trial_ip_days'
RETENTION_IP_DEFAULT = 90
RETENTION_CONTACT_PARAM = 'wb_subscription.retention_trial_contact_days'
RETENTION_CONTACT_DEFAULT = 365
# Endungen privater Netze: die Regel „1 Test je Domain“ gilt dort nicht (#97, I3)
PRIVATE_HOST_SUFFIXES = ('.local', '.localdomain', '.lan', '.internal', '.intranet',
                         '.home', '.home.arpa', '.corp', '.test', '.localhost')

_logger = logging.getLogger(__name__)


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
    consent_at = fields.Datetime(
        string='Zustimmung am', readonly=True,
        help="Zeitpunkt, zu dem der Antrag mit Zustimmung zur Datenübermittlung einging.",
    )
    consent_version = fields.Char(
        string='Fassung Datenschutzhinweis', readonly=True,
        help="Vom Client gemeldete Fassung der Datenschutzhinweise, denen zugestimmt wurde.",
    )
    anonymised = fields.Boolean(
        string='Anonymisiert', readonly=True,
        help="Kontaktdaten nach Ablauf der Aufbewahrungsfrist entfernt.",
    )
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
            # E-Mail immer klein und ohne Leerraum: Vergleiche sind exakt (#97, I1)
            if vals.get('contact_email'):
                vals['contact_email'] = vals['contact_email'].strip().lower()
            if not vals.get('name') or vals['name'] == 'Neu':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'wb.license.trial.request') or 'TRL/XXXX'
        return super().create(vals_list)

    # ------------------------------------------------------------------ Regeln

    @api.model
    def _is_public_host(self, host):
        """True for a public DNS name; False for localhost, IP literals, private suffixes, single labels."""
        host = normalize_trial_domain(host)
        if not host or host == 'localhost' or '.' not in host and ':' not in host:
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return not any(host.endswith(suffix) for suffix in PRIVATE_HOST_SUFFIXES)

    @api.model
    def _find_previous(self, product_code, email, domain, db_uuid):
        """Earlier trial requests (any state) of this product for the same email, domain or database.

        Exact comparisons only (lower-case email, normalised host, UUID), no
        LIKE patterns (#97, I1). The domain counts only for public host names
        (#97, I3). Returns ``{'email': rec, 'domain': rec, 'db_uuid': rec}`` with
        the first match per criterion (empty recordsets if none). A trial of
        another product does not count (DECISIONS.md #94).
        """
        email = (email or '').strip().lower()
        normalized = normalize_trial_domain(domain)
        base = [('product_id.wb_technical_code', '=', product_code)]
        empty = self.browse()
        return {
            'email': self.search(base + [('contact_email', '=', email)], limit=1) if email else empty,
            'domain': self.search(base + [('domain_normalized', '=', normalized)], limit=1)
            if normalized and self._is_public_host(normalized) else empty,
            'db_uuid': self.search(base + [('db_uuid', '=', db_uuid)], limit=1) if db_uuid else empty,
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
        return {'error': 'TRIAL_NOT_AVAILABLE'}

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

            # Ungeprüfte Angaben nie mit vorhandenen Kontakten zusammenführen,
            # immer ein eigener Kontakt je Antrag (#97, I1). Eine von Hand
            # gesetzte Verknüpfung bleibt.
            partner = rec.partner_id
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

    @api.model
    def _retention_days(self, param, default):
        return self.env['wb.license.event']._retention_days(param, default)

    @api.model
    def _cron_apply_retention(self):
        """Anonymise trial requests (DECISIONS.md #97, I6).

        1. IP address after ``retention_trial_ip_days`` (default 90).
        2. Requests not converted to a paid license after
           ``retention_trial_contact_days`` (default 365): contact data of the
           request, the contact created at approval (only if nothing else uses
           it) and the email address in audit events. Product, domain and
           database ID stay, so the rule "one trial per database" keeps working.

        One savepoint per request; expected failures (validation, database
        constraint) are logged per record, anything else propagates.
        """
        self.env.flush_all()
        ip_tage = self._retention_days(RETENTION_IP_PARAM, RETENTION_IP_DEFAULT)
        kontakt_tage = self._retention_days(RETENTION_CONTACT_PARAM, RETENTION_CONTACT_DEFAULT)
        now = fields.Datetime.now()
        if ip_tage:
            self.search([('ip_address', '!=', False),
                         ('create_date', '<', now - timedelta(days=ip_tage))]).write(
                {'ip_address': False})
        if not kontakt_tage:
            return
        alt = self.search([('state', '!=', 'converted'), ('anonymised', '=', False),
                           ('create_date', '<', now - timedelta(days=kontakt_tage))])
        erledigt = 0
        for rec in alt:
            try:
                with self.env.cr.savepoint():
                    rec._anonymise_one()
                erledigt += 1
            except (UserError, ValidationError, psycopg2.Error) as exc:
                _logger.warning("[wb_subscription] Trial-Retention %s übersprungen: %s",
                                rec.name, exc)
        if erledigt:
            _logger.info("[wb_subscription] Trial-Retention: %d Anträge anonymisiert", erledigt)

    def _partner_in_use_elsewhere(self, partner):
        """True if ``partner`` is used by anything but this trial request and its own trial license."""
        self.ensure_one()
        env = self.env
        if env['wb.license.key'].sudo().search_count([
                ('partner_id', 'child_of', partner.id), ('id', '!=', self.license_id.id)], limit=1):
            return True
        if self.sudo().search_count([('partner_id', '=', partner.id), ('id', '!=', self.id)], limit=1):
            return True
        if env['res.users'].sudo().with_context(active_test=False).search_count(
                [('partner_id', '=', partner.id)], limit=1):
            return True
        for model in ('sale.order', 'account.move'):
            if model in env and env[model].sudo().search_count(
                    [('partner_id', 'child_of', partner.id)], limit=1):
                return True
        return bool(partner.child_ids)

    def _anonymise_one(self):
        """Remove personal data of one expired, unconverted trial request (#97)."""
        self.ensure_one()
        email = self.contact_email
        partner = self.partner_id
        # Kontakt und Ereignisse nur, wenn der Kontakt sonst nirgends genutzt
        # wird; sonst gehört die Adresse (auch) einem Kunden und bleibt (#98).
        if not (partner and self._partner_in_use_elsewhere(partner)):
            if partner:
                partner.sudo().write({
                    'name': 'Anonymisiert', 'email': False, 'phone': False,
                    'street': False, 'street2': False, 'zip': False, 'city': False,
                    'website': False, 'vat': False, 'comment': False, 'active': False,
                })
            if email and self.license_id:
                # Nur Ereignisse der eigenen Test-Lizenz. Das Audit-Log ist per ORM
                # unveränderlich (wb.license.event.write), daher SQL wie die Event-Retention.
                self.env.flush_all()
                self.env.cr.execute(
                    "UPDATE wb_license_event SET details = replace(details, %s, 'anonymisiert') "
                    "WHERE license_id = %s AND strpos(details, %s) > 0",
                    (email, self.license_id.id, email))
        self.write({
            'contact_name': 'anonymisiert',
            'contact_email': f'anonymisiert-{self.id}@invalid',
            'contact_phone': False,
            'company_name': 'anonymisiert',
            'industry': False,
            'expected_use_case': False,
            'ip_address': False,
            'anonymised': True,
        })

    def action_reject(self):
        """Manuell ablehnen (Missbrauchsverdacht bestätigt). Kein Schlüssel."""
        self.filtered(lambda r: r.state == 'pending').write({'state': 'rejected'})
