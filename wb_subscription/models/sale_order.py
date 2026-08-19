"""sale.order Erweiterungen + License-Issuance-Logik.

Lizenz-Erzeugung läuft NICHT auf sale.order._action_confirm, sondern erst
wenn die zugehörige Rechnung als bezahlt markiert ist — siehe
account_move.py. So folgt der Bestell-/Zahlungs-Flow dem Odoo-Standard:

  1. Bestellung wird angelegt (Webseite POST oder manuell)
  2. Tobias bestätigt sale.order → Rechnung-Draft entsteht
  3. Rechnung wird versendet (mit Standard-Payment-Link)
  4. Kunde zahlt über den Link (Stripe / SEPA / was auch immer in Odoo
     als Payment-Provider konfiguriert ist)
  5. Zahlungseingang setzt account.move.payment_state = 'paid'
  6. account.move.write hook → _wb_issue_license_keys auf der zugehörigen Order

Damit ist wb_subscription frei von Stripe-spezifischem Code (DECISION #7:
Odoo-Standards nutzen). Tobias kann Provider wechseln ohne dieses Modul anzufassen.

Klartext-Activation-Code lebt nur im RAM zwischen Generate und Mail-Versand,
wird danach mit `del` verworfen. In der DB steht nur der bcrypt-Hash + der
Fernet-encrypted Code im Ticket (siehe DECISION #48).
"""

import logging
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import RedirectWarning

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    wb_license_key_ids = fields.One2many(
        'wb.license.key',
        'sale_order_id',
        string='WB-Lizenzschlüssel',
        groups='wb_subscription.group_wb_subscription_user',
    )
    wb_is_license_sub = fields.Boolean(
        compute='_compute_wb_is_license_sub',
        store=True,
        help="True wenn ≥1 Order-Line ein Lizenz-Produkt referenziert.",
    )
    wb_license_count = fields.Integer(
        compute='_compute_wb_license_count',
        string='Anzahl Lizenzen',
        groups='wb_subscription.group_wb_subscription_user',
    )
    wb_first_period_invoiced = fields.Boolean(
        string='Erste Periode bereits anteilig abgerechnet',
        default=False, copy=False, readonly=True,
        help="Idempotenz-Marker fuer den Pro-Rata-Override. Sobald die "
             "erste Rechnung dieser Subscription erzeugt und anteilig "
             "berechnet wurde, wird der Flag gesetzt — Folge-Rechnungen "
             "laufen unangetastet durch.",
    )

    @api.depends('order_line.product_id.wb_is_license_product')
    def _compute_wb_is_license_sub(self):
        for order in self:
            order.wb_is_license_sub = any(
                line.product_id.wb_is_license_product for line in order.order_line
            )

    @api.depends('wb_license_key_ids')
    def _compute_wb_license_count(self):
        for order in self:
            order.wb_license_count = len(order.wb_license_key_ids)

    def _wb_compute_valid_to(self):
        """Anteilige Erstlaufzeit bis 31.12. (DECISION #4).

        Wenn schon im Dezember bestellt → bis 31.12. nächstes Jahr.
        """
        today = fields.Date.context_today(self)
        eoy = date(today.year, 12, 31)
        if (eoy - today).days < 30:
            eoy = date(today.year + 1, 12, 31)
        return eoy

    @staticmethod
    def _wb_first_period_end(start_date, billing_calendar):
        """Letzter Tag der aktuellen Kalenderperiode für Pro-Rata-Logik.

        Beispiele (start_date=15.05.):
        - monthly   → 31.05.
        - quarterly → 30.06. (Rest-Q2: Apr-Jun)
        - biannual  → 30.06. (Rest-H1: Jan-Jun)
        - yearly    → 31.12.

        Quartale = Kalenderquartale (Q1 Jan-Mär, Q2 Apr-Jun, Q3 Jul-Sep, Q4 Okt-Dez),
        Halbjahre = Kalenderhalbjahre (H1 Jan-Jun, H2 Jul-Dez), kein Rolling.
        """
        if billing_calendar == 'monthly':
            return start_date + relativedelta(day=1, months=1, days=-1)
        if billing_calendar == 'quarterly':
            q_end_month = ((start_date.month - 1) // 3 + 1) * 3
            return date(start_date.year, q_end_month, 1) + relativedelta(months=1, days=-1)
        if billing_calendar == 'biannual':
            h_end_month = 6 if start_date.month <= 6 else 12
            return date(start_date.year, h_end_month, 1) + relativedelta(months=1, days=-1)
        if billing_calendar == 'yearly':
            return date(start_date.year, 12, 31)
        raise ValueError(f"Unknown billing_calendar: {billing_calendar!r}")

    @staticmethod
    def _wb_full_period_days(start_date, billing_calendar):
        """Tage in der vollen Kalenderperiode, in der start_date liegt.

        Beispiele:
        - monthly + 15.05.   → 31 (Mai hat 31 Tage)
        - quarterly + 15.05. → 91 (Q2 = Apr+Mai+Jun)
        - biannual + 15.05.  → 181 (H1 2026 = Jan-Jun)
        - yearly + 15.05.    → 365 (oder 366 in Schaltjahren)
        """
        if billing_calendar == 'monthly':
            month_start = date(start_date.year, start_date.month, 1)
            month_end = month_start + relativedelta(months=1, days=-1)
            return (month_end - month_start).days + 1
        if billing_calendar == 'quarterly':
            q_idx = (start_date.month - 1) // 3
            q_start = date(start_date.year, q_idx * 3 + 1, 1)
            q_end = q_start + relativedelta(months=3, days=-1)
            return (q_end - q_start).days + 1
        if billing_calendar == 'biannual':
            h_start_month = 1 if start_date.month <= 6 else 7
            h_start = date(start_date.year, h_start_month, 1)
            h_end = h_start + relativedelta(months=6, days=-1)
            return (h_end - h_start).days + 1
        if billing_calendar == 'yearly':
            y_start = date(start_date.year, 1, 1)
            y_end = date(start_date.year, 12, 31)
            return (y_end - y_start).days + 1
        raise ValueError(f"Unknown billing_calendar: {billing_calendar!r}")

    @classmethod
    def _wb_pro_rata_factor(cls, start_date, billing_calendar):
        """Anteils-Faktor (0..1) fuer die Erstrechnung.

        = Tage(start_date..first_period_end) / Tage(volle_Periode)

        Bei start_date am 1. der Periode: factor = 1.0 (kein Pro-Rata).
        Bei start_date in der Mitte: 0..1.
        """
        first_end = cls._wb_first_period_end(start_date, billing_calendar)
        days_in_first = (first_end - start_date).days + 1
        full_days = cls._wb_full_period_days(start_date, billing_calendar)
        if full_days == 0:
            return 1.0
        return days_in_first / full_days

    def action_confirm(self):
        """Override: bei Lizenz-Orders Subscription-Setup automatisch durchführen."""
        res = super().action_confirm()
        self._wb_promote_to_subscription()
        return res

    def _wb_promote_to_subscription(self):
        """Setzt is_subscription + plan_id + Eckdaten bei Lizenz-Orders.

        Idempotent: überschreibt nichts was schon gesetzt ist.

        Wirft RedirectWarning wenn das Lizenz-Produkt keinen Default-Plan hat —
        Tobias bekommt einen "Produkt öffnen"-Button und kann das Feld dort
        pflegen, dann nochmal auf Bestätigen klicken.

        Eckdaten:
        - is_subscription = True
        - plan_id = Default vom Produkt
        - start_date = today (falls leer)
        - end_date = _wb_compute_valid_to() (= 31.12. dieses/nächsten Jahres)
        - next_invoice_date = 1. der nächsten Kalenderperiode
        """
        for order in self.filtered('wb_is_license_sub'):
            if 'is_subscription' not in order._fields:
                _logger.warning(
                    "[wb_subscription] sale.order.is_subscription nicht verfügbar "
                    "— Subscription-Promote skipped für %s", order.name)
                continue
            license_lines = order.order_line.filtered(
                lambda l: l.product_id.wb_is_license_product
            )
            if not license_lines:
                continue
            # Plan setzen (mit RedirectWarning-Fallback)
            if 'plan_id' in order._fields and not order.plan_id:
                plans = license_lines.product_id.wb_default_subscription_plan_id
                plan = plans[:1]
                if not plan:
                    missing = license_lines[0].product_id
                    raise RedirectWarning(
                        _("Lizenz-Produkt '%s' hat keinen Default-Subscription-Plan "
                          "konfiguriert. Ohne Plan kann keine Subscription erstellt "
                          "werden.\n\n"
                          "Bitte am Produkt unter Tab 'WB Lizenz' einen Plan setzen, "
                          "dann hier nochmal auf 'Bestätigen' klicken.")
                        % missing.display_name,
                        {
                            'type': 'ir.actions.act_window',
                            'res_model': 'product.template',
                            'res_id': missing.product_tmpl_id.id,
                            'views': [(False, 'form')],
                            'target': 'current',
                        },
                        _("Produkt öffnen"),
                    )
                order.plan_id = plan.id
            # is_subscription
            if not order.is_subscription:
                order.is_subscription = True
            # Eckdaten
            today = fields.Date.context_today(order)
            start = (
                order.start_date if 'start_date' in order._fields and order.start_date
                else today
            )
            if 'start_date' in order._fields and not order.start_date:
                order.start_date = start
            if 'end_date' in order._fields and not order.end_date:
                order.end_date = order._wb_compute_valid_to()
            # next_invoice_date = 1. der nächsten Kalenderperiode
            if 'next_invoice_date' in order._fields and not order.next_invoice_date:
                billing_cal = license_lines[0].product_id.wb_billing_calendar or 'monthly'
                first_period_end = self._wb_first_period_end(start, billing_cal)
                order.next_invoice_date = first_period_end + timedelta(days=1)
            _logger.info(
                "[wb_subscription] Subscription-Promote für %s: plan=%s, "
                "end_date=%s, next_invoice_date=%s",
                order.name,
                order.plan_id.display_name if order.plan_id else '-',
                order.end_date if 'end_date' in order._fields else '-',
                order.next_invoice_date if 'next_invoice_date' in order._fields else '-',
            )

    def _wb_issue_license_keys(self):
        """Erzeugt oder verlängert für jede Lizenz-Zeile einen wb.license.key.

        Idempotent über state-basiertes Lookup pro (sale_order, product):

        - issued/active/grace → Mid-Cycle-Payment (z.B. monatliche Teil-Rechnung
          innerhalb eines Jahresvertrags). Key bleibt unverändert, keine Mails.
        - expired              → Year-Rollover (Renewal-Cron-Rechnung wurde bezahlt).
                                 action_renew verlängert valid_to. Bei nicht-aktivierter
                                 Lizenz bleibt state='expired' (egal — Kunde hat eh nie
                                 aktiviert), bei aktivierter wird state='active'.
        - revoked/cancelled    → bewusst abgeschaltet, keine Auto-Reaktivierung.
        - kein Key vorhanden   → Initial-Sale, neuen Key erzeugen + Mails versenden.

        Wird ausgelöst durch account.move._invoice_paid_hook wenn payment_state
        auf 'paid' wechselt — siehe account_move.py.
        """
        self.ensure_one()
        Key = self.env['wb.license.key'].sudo()
        Ticket = self.env['wb.activation.ticket'].sudo()
        gen = self.env['wb.key.generator'].sudo()

        for line in self.order_line:
            if not line.product_id.wb_is_license_product:
                continue

            existing = Key.search([
                ('sale_order_id', '=', self.id),
                ('product_id', '=', line.product_id.id),
            ], limit=1, order='create_date desc')

            if existing:
                if existing.state in ('issued', 'active', 'grace'):
                    _logger.info(
                        "[wb_subscription] Mid-cycle Payment auf Order %s — "
                        "Key %s (state=%s) bleibt unverändert.",
                        self.name, existing.name, existing.state)
                    continue
                if existing.state == 'expired':
                    new_valid_to = self._wb_compute_valid_to()
                    _logger.info(
                        "[wb_subscription] Year-Rollover auf Order %s — "
                        "Key %s (activated_at=%s) wird auf %s verlängert.",
                        self.name, existing.name, bool(existing.activated_at),
                        new_valid_to)
                    existing.action_renew(new_valid_to=new_valid_to)
                    continue
                # revoked / cancelled → bewusst abgeschaltet
                _logger.info(
                    "[wb_subscription] Key %s ist %s — keine Auto-Reaktivierung "
                    "auf Order %s.", existing.name, existing.state, self.name)
                continue

            valid_from = fields.Date.context_today(self)
            valid_to = self._wb_compute_valid_to()
            instance_limit = line.product_id.wb_instance_limit or 1
            grace_days = line.product_id.wb_activation_grace_days or 90

            activation_code = gen.generate_activation_code()
            activation_hash = gen.hash_activation_code(activation_code)

            key = Key.create({
                'product_id': line.product_id.id,
                'partner_id': self.partner_id.id,
                'sale_order_id': self.id,
                'state': 'issued',
                'valid_from': valid_from,
                'valid_to': valid_to,
                'instance_limit': instance_limit,
                'activation_hash': activation_hash,
                'activation_expires_at': fields.Datetime.now() + timedelta(days=grace_days),
                'company_id': self.company_id.id,
            })

            Ticket.create({
                'license_id': key.id,
                'email': self.partner_id.email or '',
                'encrypted_code': gen.encrypt_code(activation_code),
            })

            try:
                key.action_generate_certificate_pdf()
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Cert-Auto-Generate für %s fehlgeschlagen: %s",
                    key.name, e)

            cert_attachments = key.certificate_ids.ids
            key._send_template(
                'wb_subscription.mail_template_payment_received',
                attachments=cert_attachments,
            )
            key._send_template('wb_subscription.mail_template_activation_instructions')
            key._send_telegram(
                "💰 Neuer Kauf: {partner} — {product} ({key})",
                partner=self.partner_id.name or '',
                product=line.product_id.name,
                key=key.name,
            )

            del activation_code

    # -------------------------------------------------- Pro-Rata Erst-Rechnung

    def _create_invoices(self, grouped=False, final=False, date=None):
        """Override: bei Lizenz-Subscriptions wird die ERSTE Rechnung
        anteilig zum Ende der aktuellen Kalenderperiode berechnet.

        Idempotenz: wb_first_period_invoiced verhindert Re-Apply bei
        wiederholter Methoden-Call. Folge-Rechnungen laufen unangetastet
        durch — Standard-Odoo-Subscription-Mechanik handhabt die.
        """
        invoices = super()._create_invoices(
            grouped=grouped, final=final, date=date,
        )
        if invoices:
            self._wb_apply_pro_rata_to_invoices(invoices)
        return invoices

    def _wb_apply_pro_rata_to_invoices(self, invoices):
        """Pro betroffener Rechnung: prufe ob erste Rechnung der Subscription
        und passe Lizenz-Lines anteilig an.

        Robust gegen:
        - Rechnung enthaelt Lines aus mehreren SOs
        - SO hat schon eine erste Rechnung (idempotent)
        - SO ist nicht is_subscription oder hat keine Lizenz-Produkte
        """
        for invoice in invoices:
            if invoice.move_type != 'out_invoice':
                continue
            sos_to_mark = self.env['sale.order']
            for line in invoice.invoice_line_ids:
                if not line.product_id.wb_is_license_product:
                    continue
                so = line.sale_line_ids.order_id[:1]
                if not so:
                    continue
                if not so.wb_is_license_sub:
                    continue
                if so.wb_first_period_invoiced:
                    continue
                billing_cal = (line.product_id.wb_billing_calendar
                               or 'monthly')
                start = (so.start_date
                         or invoice.invoice_date
                         or fields.Date.context_today(so))
                factor = self._wb_pro_rata_factor(start, billing_cal)
                if factor >= 1.0:
                    # start_date am 1. der Periode → kein Pro-Rata
                    sos_to_mark |= so
                    continue
                first_end = self._wb_first_period_end(start, billing_cal)
                days_first = (first_end - start).days + 1
                days_full = self._wb_full_period_days(start, billing_cal)
                line.price_unit = line.price_unit * factor
                suffix = _(" (anteilig %(d)d/%(f)d Tage: %(s)s–%(e)s)") % {
                    'd': days_first,
                    'f': days_full,
                    's': start.strftime('%d.%m.%Y'),
                    'e': first_end.strftime('%d.%m.%Y'),
                }
                line.name = (line.name or line.product_id.name) + suffix
                sos_to_mark |= so
            for so in sos_to_mark:
                so.wb_first_period_invoiced = True

    @api.model
    def _cron_generate_renewal_invoices(self, force=False):
        """Cron 01.12., 06:00 UTC — generiert Draft-Renewal-Rechnungen.

        Pro sale.order mit is_subscription=True und wb_is_license_sub=True
        wird ein account.move (Draft) erstellt für die nächste Periode.
        Tobias gibt die Drafts dann manuell frei (DECISION #30).

        Läuft NUR im Dezember. Der Cron ist auf einen täglichen Takt
        gestellt, damit der 01.12. sicher getroffen wird; ohne diese Sperre
        würde er das ganze Jahr über Entwürfe mit Rechnungsdatum 01.01.
        des laufenden Jahres erzeugen, also rückdatiert in einen bereits
        abgeschlossenen Zeitraum. Mit force=True lässt sich der Lauf im
        Notfall von Hand auslösen.

        Idempotent: Es wird pro Auftrag geprüft, nicht pro Kunde und
        Produkt. Sonst blockieren sich zwei Abos desselben Kunden mit
        demselben Produkt gegenseitig. Zuordnung über invoice_origin.

        Nutzt Odoo 19 EE Standard: sale.order ist die Subscription
        (kein separates sale.subscription-Modell mehr).
        """
        from datetime import date

        today = fields.Date.today()
        if today.month != 12 and not force:
            _logger.info(
                "[wb_subscription] Renewal-Cron: nicht Dezember (%s), übersprungen.",
                today.isoformat())
            return
        target_year = today.year + 1 if today.month == 12 else today.year

        domain = [('wb_is_license_sub', '=', True), ('state', '=', 'sale')]
        if 'is_subscription' in self._fields:
            domain.append(('is_subscription', '=', True))
        if 'subscription_state' in self._fields:
            domain.append(('subscription_state', 'in', ['3_progress', '4_paused']))

        orders = self.search(domain)
        created = 0
        for order in orders:
            # Vorrangig über invoice_origin, das ist eindeutig je Auftrag.
            # Der zweite Zweig fängt Altbestand ab, der noch ohne Herkunft
            # angelegt wurde; ohne ihn entstünden dafür Doppelrechnungen.
            existing = self.env['account.move'].sudo().search([
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'draft'),
                ('partner_id', '=', order.partner_id.id),
                ('invoice_date', '>=', date(target_year, 1, 1)),
                ('invoice_date', '<=', date(target_year, 12, 31)),
                '|',
                ('invoice_origin', '=', order.name),
                '&',
                ('invoice_origin', 'in', [False, '']),
                ('invoice_line_ids.product_id', 'in', order.order_line.mapped('product_id').ids),
            ], limit=1)
            if existing:
                continue
            try:
                move = self.env['account.move'].sudo().create({
                    'move_type': 'out_invoice',
                    'partner_id': order.partner_id.id,
                    'invoice_origin': order.name,
                    'invoice_date': date(target_year, 1, 1),
                    'invoice_line_ids': [
                        (0, 0, {
                            'product_id': line.product_id.id,
                            'quantity': line.product_uom_qty,
                            'name': f"{line.product_id.name} — Renewal {target_year}",
                            'price_unit': line.price_unit,
                        })
                        for line in order.order_line
                        if line.product_id.wb_is_license_product
                    ],
                })
                if move.invoice_line_ids:
                    created += 1
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Renewal-Invoice-Generation für %s fehlgeschlagen: %s",
                    order.name, e)

        _logger.info(
            "[wb_subscription] Dezember-Renewal-Cron: %d Draft-Rechnungen erzeugt für %d",
            created, target_year)
        if created > 0:
            self.env['wb.telegram.notifier'].send_message(
                f"📋 Dezember-Renewal: {created} Draft-Rechnungen für {target_year} "
                f"erzeugt — bitte unter Buchhaltung prüfen und freigeben."
            )
