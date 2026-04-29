"""Admin-Dashboard mit MRR/ARR/KPIs.

TransientModel + computed Felder. Bei jedem Aufruf der Action wird
ein neuer Record erzeugt mit aktuellen Werten (Live-Daten).

Annahmen für MRR/ARR (vereinfacht):
- Ein aktiver Lizenz-Schlüssel mit jährlicher Laufzeit zählt als
  product.list_price / 12 für MRR
- Trials zählen NICHT in MRR/ARR
- Grace-Lizenzen zählen weiterhin (noch nicht gekündigt)
"""

from odoo import _, api, fields, models


class WbDashboard(models.TransientModel):
    _name = 'wb.dashboard'
    _description = 'WB Subscription Admin-Dashboard'

    name = fields.Char(
        default=lambda self: _('WB Lizenz-Plattform — Stand %s')
                              % fields.Datetime.now().strftime('%d.%m.%Y %H:%M'),
        readonly=True,
        help="Snapshot-Zeitpunkt — wird beim Klick auf Dashboard-Menue "
             "neu erzeugt, also entspricht der aktuellen Live-Abfrage.",
    )

    license_active_count = fields.Integer(compute='_compute_kpis')
    license_trial_count = fields.Integer(compute='_compute_kpis')
    license_grace_count = fields.Integer(compute='_compute_kpis')
    license_expired_count = fields.Integer(compute='_compute_kpis')
    license_issued_pending_count = fields.Integer(compute='_compute_kpis')

    mrr = fields.Monetary(compute='_compute_kpis', currency_field='currency_id',
                          string='MRR (Monthly Recurring Revenue)')
    arr = fields.Monetary(compute='_compute_kpis', currency_field='currency_id',
                          string='ARR (Annual Recurring Revenue)')
    currency_id = fields.Many2one('res.currency',
                                  default=lambda self: self.env.company.currency_id)

    expiring_30d_count = fields.Integer(compute='_compute_kpis',
                                        string='Lizenzen die in 30 Tagen ablaufen')
    expiring_60d_count = fields.Integer(compute='_compute_kpis',
                                        string='Lizenzen die in 60 Tagen ablaufen')

    pending_migrations_count = fields.Integer(compute='_compute_kpis')
    pending_tickets_count = fields.Integer(compute='_compute_kpis')
    failed_otp_24h_count = fields.Integer(compute='_compute_kpis',
                                          string='OTP-Fehlversuche letzte 24h')
    activation_failed_24h_count = fields.Integer(compute='_compute_kpis',
                                                 string='Activation-Fehlversuche letzte 24h')

    trial_to_paid_30d_count = fields.Integer(compute='_compute_kpis',
                                              string='Trial→Paid Konversionen letzte 30 Tage')

    @api.model
    def _kpi_data(self):
        """Berechnet alle KPIs als dict — wiederverwendbar für Daily-Telegram."""
        from datetime import timedelta
        Key = self.env['wb.license.key']
        Event = self.env['wb.license.event']
        today = fields.Date.today()
        now = fields.Datetime.now()

        active_keys = Key.search([('state', '=', 'active')])
        mrr = sum((k.product_id.list_price or 0.0) / 12.0 for k in active_keys
                  + Key.search([('state', '=', 'grace')]))
        arr = mrr * 12

        return {
            'active': Key.search_count([('state', '=', 'active')]),
            'trial': Key.search_count([('state', '=', 'trial')]),
            'grace': Key.search_count([('state', '=', 'grace')]),
            'expired': Key.search_count([('state', '=', 'expired')]),
            'issued_pending': Key.search_count([('state', '=', 'issued')]),
            'mrr': mrr,
            'arr': arr,
            'expiring_30d': Key.search_count([
                ('state', 'in', ('active', 'grace')),
                ('valid_to', '<=', today + timedelta(days=30)),
                ('valid_to', '>=', today),
            ]),
            'expiring_60d': Key.search_count([
                ('state', 'in', ('active', 'grace')),
                ('valid_to', '<=', today + timedelta(days=60)),
                ('valid_to', '>', today + timedelta(days=30)),
            ]),
            'pending_migrations': self.env['wb.license.migration.request'].search_count(
                [('state', '=', 'pending')]),
            'pending_tickets': self.env['wb.activation.ticket'].search_count(
                [('state', 'in', ('pending', 'awaiting_otp'))]),
            'failed_otp_24h': Event.search_count([
                ('event_type', '=', 'otp_failed'),
                ('timestamp', '>=', now - timedelta(hours=24)),
            ]),
            'activation_failed_24h': Event.search_count([
                ('event_type', '=', 'activation_failed'),
                ('timestamp', '>=', now - timedelta(hours=24)),
            ]),
            'trial_to_paid_30d': Event.search_count([
                ('event_type', '=', 'trial_converted'),
                ('timestamp', '>=', now - timedelta(days=30)),
            ]),
        }

    def _compute_kpis(self):
        kpis = self._kpi_data()
        for rec in self:
            rec.license_active_count = kpis['active']
            rec.license_trial_count = kpis['trial']
            rec.license_grace_count = kpis['grace']
            rec.license_expired_count = kpis['expired']
            rec.license_issued_pending_count = kpis['issued_pending']
            rec.mrr = kpis['mrr']
            rec.arr = kpis['arr']
            rec.expiring_30d_count = kpis['expiring_30d']
            rec.expiring_60d_count = kpis['expiring_60d']
            rec.pending_migrations_count = kpis['pending_migrations']
            rec.pending_tickets_count = kpis['pending_tickets']
            rec.failed_otp_24h_count = kpis['failed_otp_24h']
            rec.activation_failed_24h_count = kpis['activation_failed_24h']
            rec.trial_to_paid_30d_count = kpis['trial_to_paid_30d']

    @api.model
    def action_open_dashboard(self):
        record = self.create({})
        return {
            'type': 'ir.actions.act_window',
            'name': _('WB Dashboard'),
            'res_model': 'wb.dashboard',
            'res_id': record.id,
            'view_mode': 'form',
            'view_id': self.env.ref('wb_subscription.wb_dashboard_view_form').id,
            'target': 'current',
        }

    @api.model
    def _cron_send_daily_telegram_summary(self):
        """Schickt morgens KPI-Übersicht an Tobias (DECISION #45)."""
        kpis = self._kpi_data()
        message = (
            "📊 <b>WB Dashboard — Daily</b>\n\n"
            "<b>Lizenzen:</b>\n"
            f"  ✅ Aktiv: {kpis['active']}\n"
            f"  🆕 Trial: {kpis['trial']}\n"
            f"  🟡 Grace: {kpis['grace']}\n"
            f"  ⏳ Issued (nicht aktiviert): {kpis['issued_pending']}\n"
            f"  🔴 Expired: {kpis['expired']}\n\n"
            f"<b>Recurring:</b>\n"
            f"  MRR: {kpis['mrr']:.2f} €\n"
            f"  ARR: {kpis['arr']:.2f} €\n\n"
            f"<b>Aktion erforderlich:</b>\n"
            f"  📦 Pending Migrations: {kpis['pending_migrations']}\n"
            f"  🎫 Offene Tickets: {kpis['pending_tickets']}\n"
            f"  ⚠️ Läuft in 30d ab: {kpis['expiring_30d']}\n\n"
            f"<b>Auffällig (24h):</b>\n"
            f"  Activation-Fehlversuche: {kpis['activation_failed_24h']}\n"
            f"  OTP-Fehlversuche: {kpis['failed_otp_24h']}\n"
        )
        self.env['wb.telegram.notifier'].send_message(message)
