"""Hauptmodell der Lizenz-Plattform.

Enthält Public Key, bcrypt-Hash des Activation-Codes, Instanz-Bindung,
Laufzeiten und State-Machine. Siehe ARCHITECTURE.md Kapitel 5.4.

Sicherheits-Kritisch:
- Activation-Code wird NUR als bcrypt-Hash gespeichert (activation_hash).
- Nach erfolgreicher Activation wird activation_hash auf False gesetzt ("burned").
- Activation-Code darf NIEMALS geloggt werden.
"""

import base64
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


STATE_SELECTION = [
    ('issued', 'Issued (Key erstellt, nicht aktiviert)'),
    ('trial', 'Trial (7 Tage)'),
    ('active', 'Aktiv'),
    ('grace', 'Grace (Rechnung überfällig)'),
    ('expired', 'Expired'),
    ('revoked', 'Revoked (gesperrt)'),
    ('cancelled', 'Cancelled'),
]


class WbLicenseKey(models.Model):
    _name = 'wb.license.key'
    _description = 'WB Lizenzschlüssel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    name = fields.Char(
        string='Public Key',
        required=True,
        copy=False,
        index=True,
        tracking=True,
        help="Lizenzschlüssel im Format WB-{PROD}-{UUID8}{CHK}. Read-only nach Erzeugung.",
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    activation_hash = fields.Binary(
        string='Activation-Hash (bcrypt)',
        attachment=False,
        help="bcrypt-Hash des Activation-Codes. Klartext wird nirgendwo gespeichert. "
             "Wird nach erfolgreicher Activation auf False gesetzt.",
        groups='wb_subscription.group_wb_subscription_manager',
    )
    activation_hash_method = fields.Char(default='bcrypt', required=True)
    activated_at = fields.Datetime(string='Aktiviert am', tracking=True)
    activated_fingerprint = fields.Char(
        string='Fingerprint (SHA256)',
        help="Wird beim Activate gesetzt — SHA256(domain + db_uuid).",
    )
    activation_expires_at = fields.Datetime(
        string='Activation-Code läuft ab',
        help="Code kann bis zu diesem Datum verwendet werden.",
    )

    product_id = fields.Many2one(
        'product.product',
        string='Produkt',
        required=True,
        domain=[('wb_is_license_product', '=', True)],
        tracking=True,
    )
    product_code = fields.Char(
        related='product_id.wb_technical_code',
        store=True,
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Kunde',
        required=True,
        tracking=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Abo / Order',
        ondelete='set null',
        help="Sale-Order, aus der die Lizenz erzeugt wurde. "
             "In Odoo 19 EE ist die Subscription gleichzeitig die Order "
             "(is_subscription=True). Bei Trials NULL.",
    )

    state = fields.Selection(
        STATE_SELECTION,
        required=True,
        default='issued',
        tracking=True,
        index=True,
    )

    valid_from = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    valid_to = fields.Date(required=True, tracking=True)
    grace_until = fields.Date(
        compute='_compute_grace_until',
        store=True,
        help="Abgeleitet aus partner.followup_status. Nur bei state='grace' relevant.",
    )
    last_renewal_date = fields.Date()

    bound_domain = fields.Char(string='Gebundene Domain', tracking=True)
    bound_db_uuid = fields.Char(string='Gebundene DB-UUID')
    instance_limit = fields.Integer(default=1, required=True)
    instance_current = fields.Integer(compute='_compute_instance_current', store=False)

    last_seen_at = fields.Datetime()
    last_seen_ip = fields.Char()
    last_seen_user_agent = fields.Char()
    ping_count_total = fields.Integer(default=0)

    event_ids = fields.One2many('wb.license.event', 'license_id', string='Events', readonly=True)
    migration_ids = fields.One2many('wb.license.migration.request', 'license_id', string='Migrationen')
    ticket_ids = fields.One2many('wb.activation.ticket', 'license_id', string='Tickets')

    is_expiring_soon = fields.Boolean(
        compute='_compute_is_expiring_soon',
        search='_search_is_expiring_soon',
        string='Läuft bald ab (≤30d)',
        help="True wenn valid_to in den nächsten 30 Tagen liegt und Lizenz aktiv/grace ist.",
    )

    notified_activation_7d_at = fields.Datetime(
        string='7-Tage-Reminder gesendet',
        copy=False,
        help="Wann der activation_reminder_7d versendet wurde. Reset bei jedem Activate.",
    )
    notified_activation_30d_at = fields.Datetime(
        string='30-Tage-Reminder gesendet',
        copy=False,
    )
    notified_renewal_60d_at = fields.Datetime(
        string='Renewal-60d-Reminder gesendet',
        copy=False,
        help="Reset bei jeder Verlängerung (action_renew).",
    )
    notified_renewal_30d_at = fields.Datetime(
        string='Renewal-30d-Reminder gesendet',
        copy=False,
    )
    notified_grace_started_at = fields.Datetime(
        string='Grace-Started-Mail gesendet',
        copy=False,
    )
    notified_expired_at = fields.Datetime(
        string='Expired-Mail gesendet',
        copy=False,
    )

    certificate_number = fields.Char(
        string='Zertifikat-Nr',
        copy=False,
        readonly=True,
        help="Fortlaufende Nummer LIZ-YYYY-NNNNN. Wird beim ersten PDF-Erzeugen vergeben.",
    )
    certificate_ids = fields.One2many(
        'ir.attachment',
        compute='_compute_certificate_ids',
        string='Zertifikats-PDFs',
    )
    certificate_count = fields.Integer(compute='_compute_certificate_ids')
    issue_date = fields.Date(
        string='Ausstelldatum',
        default=fields.Date.context_today,
        readonly=True,
    )

    tag_ids = fields.Many2many(
        'wb.license.tag',
        'wb_license_key_tag_rel',
        'license_id',
        'tag_id',
        string='Tags',
    )
    note = fields.Text(string='Notiz (kunden-sichtbar)')
    internal_note = fields.Text(string='Interne Notiz')

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Lizenzschlüssel muss eindeutig sein.'),
        ('instance_limit_positive', 'CHECK(instance_limit >= 1)',
         'instance_limit muss mindestens 1 sein.'),
    ]

    @api.depends('name', 'product_id', 'partner_id')
    def _compute_display_name(self):
        for rec in self:
            parts = [rec.product_id.name or 'Unknown', rec.partner_id.name or '', f"({rec.name})" if rec.name else '']
            rec.display_name = ' — '.join(p for p in parts if p).strip()

    def _compute_is_expiring_soon(self):
        today = fields.Date.today()
        threshold = today + timedelta(days=30)
        for rec in self:
            rec.is_expiring_soon = bool(
                rec.valid_to
                and today <= rec.valid_to <= threshold
                and rec.state in ('active', 'grace')
            )

    def _search_is_expiring_soon(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return []
        today = fields.Date.today()
        threshold = today + timedelta(days=30)
        positive_domain = [
            ('valid_to', '>=', today),
            ('valid_to', '<=', threshold),
            ('state', 'in', ['active', 'grace']),
        ]
        if (operator == '=' and value) or (operator == '!=' and not value):
            return positive_domain
        return ['!'] + ['&', '&'] + positive_domain

    @api.depends('partner_id', 'partner_id.followup_status', 'state')
    def _compute_grace_until(self):
        """Leitet grace_until aus Odoo account_followup-Daten ab.

        Siehe ARCHITECTURE.md 8.1: wenn letzte Mahnstufe erreicht ist,
        startet die Grace-Period, und grace_until = heute + delay aus
        aktueller followup_line.
        """
        for rec in self:
            partner = rec.partner_id
            if not partner or rec.state != 'grace':
                rec.grace_until = False
                continue
            followup_line = getattr(partner, 'followup_line_id', False)
            if followup_line and getattr(followup_line, 'delay', False):
                rec.grace_until = fields.Date.today() + timedelta(days=followup_line.delay)
            else:
                rec.grace_until = fields.Date.today() + timedelta(days=7)

    def _compute_instance_current(self):
        """Zählt distinct bound_db_uuid+domain-Kombinationen aus Events.

        Aktuell einfach: 1 wenn aktiviert, sonst 0. Kann später erweitert
        werden wenn Multi-Instance-Licenses implementiert werden.
        """
        for rec in self:
            rec.instance_current = 1 if rec.activated_at and rec.bound_domain else 0

    @api.constrains('state', 'activated_at')
    def _check_state_activation(self):
        for rec in self:
            if rec.state in ('active', 'grace', 'expired') and not rec.activated_at:
                raise ValidationError(_(
                    "Lizenz %s ist im State '%s', hat aber kein activated_at. "
                    "Aktivierung muss vor State-Wechsel erfolgen."
                ) % (rec.name, rec.state))

    @api.constrains('valid_from', 'valid_to')
    def _check_validity_range(self):
        for rec in self:
            if rec.valid_from and rec.valid_to and rec.valid_to < rec.valid_from:
                raise ValidationError(_(
                    "valid_to (%s) darf nicht vor valid_from (%s) liegen."
                ) % (rec.valid_to, rec.valid_from))

    @api.model_create_multi
    def create(self, vals_list):
        """Generiert Public Key und Activation-Code, wenn nicht explizit angegeben.

        Der Activation-Code wird NUR im RAM gehalten, als bcrypt gehasht
        und dann verworfen. Für die spätere Portal-Anzeige muss er beim
        Caller (sale.order._wb_issue_license_keys) parallel an das
        Ticket übergeben werden (Fernet-encrypted).
        """
        for vals in vals_list:
            if not vals.get('name'):
                product = self.env['product.product'].browse(vals.get('product_id'))
                code = product.wb_technical_code
                if not code:
                    raise ValidationError(_(
                        "Produkt '%s' hat keinen wb_technical_code gesetzt."
                    ) % product.display_name)
                vals['name'] = self.env['wb.key.generator'].generate_public_key(code)
            if vals.get('state') in ('issued',) and not vals.get('activation_expires_at'):
                product = self.env['product.product'].browse(vals.get('product_id'))
                grace_days = getattr(product, 'wb_activation_grace_days', 90) or 90
                vals['activation_expires_at'] = fields.Datetime.now() + timedelta(days=grace_days)

        records = super().create(vals_list)
        for rec in records:
            self.env['wb.license.event'].log_event(rec, 'key_generated')
        return records

    def action_revoke(self, reason=None):
        """Manuell sperren. Audit-Event wird geloggt + Mails."""
        self.ensure_one()
        self.write({'state': 'revoked'})
        self.env['wb.license.event'].log_event(
            self, 'revoked',
            details={'reason': reason or '(kein Grund angegeben)'},
        )
        self._send_template('wb_subscription.mail_template_license_revoked')
        self._send_telegram(
            "🚫 Lizenz revoked: {key} ({partner}) — Grund: {reason}",
            key=self.name, partner=self.partner_id.name or '', reason=reason or '-',
        )
        return True

    def action_reactivate(self):
        """Von revoked oder expired zurück auf active."""
        self.ensure_one()
        if self.state not in ('revoked', 'expired'):
            raise UserError(_(
                "Reactivate nur möglich wenn Lizenz im State 'revoked' oder 'expired' ist."
            ))
        if not self.activated_at:
            raise UserError(_(
                "Lizenz wurde nie aktiviert — bitte Kunde neu aktivieren lassen."
            ))
        self.write({'state': 'active'})
        self.env['wb.license.event'].log_event(self, 'reactivated')
        return True

    def action_renew(self, new_valid_to):
        """Verlängert Laufzeit auf new_valid_to. Loggt renewal-Event + reset notif flags."""
        self.ensure_one()
        if not new_valid_to:
            raise UserError(_("Neues valid_to erforderlich."))
        self.write({
            'valid_to': new_valid_to,
            'last_renewal_date': fields.Date.today(),
            'state': 'active' if self.state in ('grace', 'expired') and self.activated_at else self.state,
        })
        self._on_state_active()
        self.env['wb.license.event'].log_event(
            self, 'renewal',
            details={'new_valid_to': str(new_valid_to)},
        )
        return True

    def activate_with_code(self, activation_code, domain, db_uuid, ip=None, user_agent=None):
        """Prüft Activation-Code und aktiviert die Lizenz.

        Wird vom Controller /api/license/activate aufgerufen. Siehe
        ARCHITECTURE.md 6.2 für den kompletten Flow.

        Returns:
            dict mit 'status' und ggf. 'error' (für Controller-Response).
        """
        self.ensure_one()
        gen = self.env['wb.key.generator']

        if self.activated_at:
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent, domain=domain, db_uuid=db_uuid,
                details={'reason': 'already_activated'},
            )
            return {'status': 'error', 'error': 'ALREADY_ACTIVATED',
                    'bound_to': self.bound_domain}

        if self.activation_expires_at and self.activation_expires_at < fields.Datetime.now():
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent,
                details={'reason': 'code_expired'},
            )
            return {'status': 'error', 'error': 'ACTIVATION_EXPIRED'}

        if not gen.verify_activation_code(activation_code, self.activation_hash):
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent, domain=domain, db_uuid=db_uuid,
                details={'reason': 'wrong_code'},
            )
            return {'status': 'error', 'error': 'WRONG_CODE'}

        fingerprint = gen.compute_fingerprint(domain, db_uuid)
        self.write({
            'bound_domain': domain,
            'bound_db_uuid': db_uuid,
            'activated_at': fields.Datetime.now(),
            'activated_fingerprint': fingerprint,
            'activation_hash': False,
            'state': 'active' if self.state == 'issued' else self.state,
        })
        self.env['wb.license.event'].log_event(
            self, 'activation',
            ip_address=ip, user_agent=user_agent,
            domain=domain, db_uuid=db_uuid,
        )
        return {'status': 'ok'}

    def _compute_certificate_ids(self):
        Attach = self.env['ir.attachment'].sudo()
        for rec in self:
            attachments = Attach.search([
                ('res_model', '=', 'wb.license.key'),
                ('res_id', '=', rec.id),
                ('name', '=like', 'Zertifikat%'),
            ])
            rec.certificate_ids = attachments
            rec.certificate_count = len(attachments)

    def _ensure_certificate_number(self):
        for rec in self:
            if not rec.certificate_number:
                rec.certificate_number = self.env['ir.sequence'].next_by_code(
                    'wb.license.certificate') or f"LIZ-{fields.Date.today().year}-{rec.id:05d}"

    def _get_verify_url(self):
        """Öffentliche Verifikations-URL für QR-Code auf dem Zertifikat.

        Route wird in Sprint 7 angelegt. Für jetzt: URL existiert als
        Ziel auf dem Zertifikat, rendert aber noch 404. Das ist OK,
        weil das Zertifikat vor Ausrollen von Sprint 7 nicht extern
        gebraucht wird.
        """
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url') or 'https://wissen-beratung.de'
        return f"{base.rstrip('/')}/license/verify/{self.name}"

    def action_generate_certificate_pdf(self):
        """Erzeugt Zertifikat-PDF und hängt es als ir.attachment an.

        Bei wiederholtem Aufruf: neue Version mit aktuellem Zeitstempel
        im Dateinamen. Alte bleiben erhalten (Audit-Historie).
        """
        self.ensure_one()
        self._ensure_certificate_number()
        report = self.env.ref('wb_subscription.action_report_license_certificate')
        pdf_content, _mime = report._render_qweb_pdf(
            report.report_name, res_ids=self.ids,
        )
        filename = f"Zertifikat_{self.certificate_number}_{fields.Datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': 'wb.license.key',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })
        self.env['wb.license.event'].log_event(
            self, 'certificate_generated',
            details={'attachment_id': attachment.id, 'number': self.certificate_number},
        )
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def record_ping(self, ip=None, user_agent=None):
        """Trägt Ping-Metadaten ein. Wird vom Check-Endpoint aufgerufen."""
        self.ensure_one()
        self.write({
            'last_seen_at': fields.Datetime.now(),
            'last_seen_ip': ip,
            'last_seen_user_agent': user_agent,
            'ping_count_total': self.ping_count_total + 1,
        })

    # ----------------------------------------
    # Notification-Helfer
    # ----------------------------------------

    def _send_template(self, template_xmlid, attachments=None, force_send=False):
        """Versendet ein mail.template und protokolliert es in wb.notification.log.

        :param template_xmlid: XML-ID, z.B. 'wb_subscription.mail_template_grace_started'
        :param attachments: Liste von ir.attachment-IDs zum Anhängen
        :param force_send: Wenn True wird sofort versendet (sync), sonst Queue
        :return: mail.mail Record oder False bei Fehler
        """
        self.ensure_one()
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            _logger.warning("[wb_subscription] mail.template %s nicht gefunden — skip", template_xmlid)
            return False

        log = self.env['wb.notification.log'].sudo().create({
            'name': template.name,
            'channel': 'email',
            'state': 'pending',
            'template_xmlid': template_xmlid,
            'recipient': self.partner_id.email or '',
            'subject': '',
            'license_id': self.id,
            'partner_id': self.partner_id.id,
        })
        try:
            email_values = {}
            if attachments:
                email_values['attachment_ids'] = [(6, 0, attachments)]
            mail_id = template.with_context(lang=self.partner_id.lang).send_mail(
                self.id, force_send=force_send, email_values=email_values or None,
            )
            log.write({
                'state': 'sent' if force_send else 'pending',
                'sent_at': fields.Datetime.now() if force_send else False,
                'subject': template.subject or '',
            })
            return self.env['mail.mail'].browse(mail_id)
        except Exception as e:
            _logger.exception("[wb_subscription] Mail-Send für %s fehlgeschlagen: %s",
                              template_xmlid, e)
            log.mark_failed(str(e))
            return False

    def _send_telegram(self, message_template, **format_kwargs):
        """Sendet Telegram-Alert an Tobias und protokolliert.

        Setzt voraus dass ir.config_parameter wb_subscription.telegram_token
        und .telegram_chat_id konfiguriert sind. Wenn nicht: silent skip
        (kein Fehler, nur Log-Warning).
        """
        self.ensure_one()
        message = message_template.format(**format_kwargs)
        notifier = self.env['wb.telegram.notifier']
        log = self.env['wb.notification.log'].sudo().create({
            'name': 'Telegram-Alert',
            'channel': 'telegram',
            'state': 'pending',
            'recipient': 'tobias',
            'license_id': self.id,
            'partner_id': self.partner_id.id,
            'body_preview': message[:500],
        })
        try:
            sent = notifier.send_message(message)
            if sent:
                log.mark_sent()
            else:
                log.mark_failed("Telegram nicht konfiguriert — Skip")
        except Exception as e:
            _logger.exception("[wb_subscription] Telegram-Send fehlgeschlagen: %s", e)
            log.mark_failed(str(e))

    # ----------------------------------------
    # Action-Hook-Erweiterungen (Mail-Notifications)
    # ----------------------------------------

    def _on_state_active(self):
        """Reset alle 'notified'-Felder, wenn Lizenz wieder aktiv ist."""
        self.write({
            'notified_renewal_60d_at': False,
            'notified_renewal_30d_at': False,
            'notified_grace_started_at': False,
            'notified_expired_at': False,
        })

    def _on_state_grace(self):
        """Mail + Telegram bei Übergang in Grace, wenn noch nicht gesendet."""
        for rec in self:
            if rec.notified_grace_started_at:
                continue
            rec._send_template('wb_subscription.mail_template_grace_started')
            rec._send_telegram(
                "🟡 Lizenz in Grace: {key} ({partner}) — Rechnung überfällig",
                key=rec.name, partner=rec.partner_id.name or '',
            )
            rec.notified_grace_started_at = fields.Datetime.now()

    def _on_state_expired(self):
        for rec in self:
            if rec.notified_expired_at:
                continue
            rec._send_template('wb_subscription.mail_template_license_expired')
            rec._send_telegram(
                "🔴 Lizenz expired: {key} ({partner})",
                key=rec.name, partner=rec.partner_id.name or '',
            )
            rec.notified_expired_at = fields.Datetime.now()

    # ----------------------------------------
    # Cron-Methoden
    # ----------------------------------------

    @api.model
    def _cron_update_states(self):
        """Täglicher State-Übergang basierend auf Datum + followup_status.

        Lizenzen werden:
        - 'active' → 'grace': wenn partner.followup_status == 'with_overdue_invoices'
        - 'grace' → 'active': wenn followup_status zurück auf 'no_action_needed'
        - 'grace' → 'expired': wenn grace_until überschritten
        - 'trial' → 'expired': wenn valid_to überschritten
        """
        today = fields.Date.today()
        candidates = self.search([
            ('state', 'in', ['active', 'grace', 'trial']),
        ])
        for rec in candidates:
            old_state = rec.state
            new_state = old_state
            partner = rec.partner_id
            followup_status = getattr(partner, 'followup_status', False)

            if rec.state == 'trial':
                if rec.valid_to and rec.valid_to < today:
                    new_state = 'expired'
            elif rec.state == 'active':
                if followup_status == 'with_overdue_invoices':
                    new_state = 'grace'
                elif rec.valid_to and rec.valid_to < today:
                    new_state = 'expired'
            elif rec.state == 'grace':
                if followup_status in ('no_action_needed', 'in_need_of_action'):
                    new_state = 'active'
                elif rec.grace_until and rec.grace_until < today:
                    new_state = 'expired'

            if new_state != old_state:
                rec.state = new_state
                if new_state == 'active':
                    rec._on_state_active()
                elif new_state == 'grace':
                    rec._on_state_grace()
                elif new_state == 'expired':
                    rec._on_state_expired()
                    if old_state == 'trial':
                        self.env['wb.license.event'].log_event(rec, 'trial_expired')

    @api.model
    def _cron_send_activation_reminders(self):
        """Tägliche Reminder für nicht aktivierte Keys (state='issued')."""
        now = fields.Datetime.now()
        candidates = self.search([('state', '=', 'issued')])

        for rec in candidates:
            if not rec.create_date:
                continue
            age = now - rec.create_date

            if age >= timedelta(days=30) and not rec.notified_activation_30d_at:
                rec._send_template('wb_subscription.mail_template_activation_reminder_30d')
                rec._send_telegram(
                    "⚠️ Activation 30d offen: {key} ({partner})",
                    key=rec.name, partner=rec.partner_id.name or '',
                )
                rec.notified_activation_30d_at = now
            elif age >= timedelta(days=7) and not rec.notified_activation_7d_at:
                rec._send_template('wb_subscription.mail_template_activation_reminder_7d')
                rec.notified_activation_7d_at = now

    @api.model
    def _cron_send_trial_reminders(self):
        """Trial-Reminder am Tag 5 (2 Tage vor Ablauf).

        Idempotent über notified_activation_7d_at — wird bei Trials
        zweckentfremdet als Trial-Reminder-Marker (Trials haben keine
        7d-Activation-Logik, also ist das Feld frei).
        """
        today = fields.Date.today()
        trials = self.search([
            ('state', '=', 'trial'),
            ('valid_to', '!=', False),
        ])
        for rec in trials:
            days_left = (rec.valid_to - today).days
            if 1 <= days_left <= 2 and not rec.notified_activation_7d_at:
                rec._send_template('wb_subscription.mail_template_trial_ending_soon')
                rec.notified_activation_7d_at = fields.Datetime.now()
            elif days_left < 1 and rec.state == 'trial':
                rec._send_template('wb_subscription.mail_template_trial_expired')

    @api.model
    def _cron_send_renewal_reminders(self):
        """Renewal-Reminder 60 und 30 Tage vor valid_to."""
        today = fields.Date.today()
        candidates = self.search([
            ('state', 'in', ['active', 'grace']),
        ])
        for rec in candidates:
            if not rec.valid_to:
                continue
            days_until = (rec.valid_to - today).days

            if days_until <= 30 and days_until > 0 and not rec.notified_renewal_30d_at:
                rec._send_template('wb_subscription.mail_template_renewal_reminder_30d')
                rec.notified_renewal_30d_at = fields.Datetime.now()
            elif days_until <= 60 and days_until > 30 and not rec.notified_renewal_60d_at:
                rec._send_template('wb_subscription.mail_template_renewal_reminder_60d')
                rec.notified_renewal_60d_at = fields.Datetime.now()
