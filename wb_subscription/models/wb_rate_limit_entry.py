"""Rate-Limit-Zähler mit TTL.

Schützt öffentliche API-Endpoints gegen Brute-Force und Abuse.
Siehe docs/guides/security.md Layer 2 für Limits pro Endpoint.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WbRateLimitEntry(models.Model):
    _name = 'wb.rate.limit.entry'
    _description = 'Rate-Limit-Zähler (Bucket) mit TTL'
    _order = 'window_start desc'

    bucket_key = fields.Char(
        required=True,
        index=True,
        help="Eindeutiger Bucket-Schlüssel, z.B. 'activate:192.168.1.1' oder 'trial:email:foo@bar.de'.",
    )
    endpoint = fields.Char(
        required=True,
        help="Name des Endpoints zur Kategorisierung in Reports.",
    )
    count = fields.Integer(default=0)
    window_start = fields.Datetime(required=True, default=fields.Datetime.now)
    window_seconds = fields.Integer(
        required=True,
        help="Fenstergröße in Sekunden. window_start + window_seconds = Ablauf.",
    )

    _sql_constraints = [
        ('bucket_unique', 'UNIQUE(bucket_key)', 'Bucket-Key muss einzigartig sein.'),
    ]

    @api.model
    def check_and_increment(self, bucket_key, endpoint, max_count, window_seconds):
        """Atomarer Rate-Limit-Check.

        Returns:
            True wenn Request erlaubt (Counter wurde inkrementiert),
            False wenn Limit überschritten.

        Verwendet SELECT ... FOR UPDATE für Atomicity zwischen parallelen
        Requests. Bei Fenster-Ablauf wird der Counter zurückgesetzt.
        """
        self.env.cr.execute(
            "SELECT id, count, window_start, window_seconds "
            "FROM wb_rate_limit_entry WHERE bucket_key = %s FOR UPDATE",
            (bucket_key,),
        )
        row = self.env.cr.fetchone()
        now = fields.Datetime.now()

        if row is None:
            self.create({
                'bucket_key': bucket_key,
                'endpoint': endpoint,
                'count': 1,
                'window_start': now,
                'window_seconds': window_seconds,
            })
            return True

        entry_id, current_count, window_start, current_window = row
        window_end = window_start + timedelta(seconds=current_window)

        if now >= window_end:
            self.browse(entry_id).write({
                'count': 1,
                'window_start': now,
                'window_seconds': window_seconds,
                'endpoint': endpoint,
            })
            return True

        if current_count >= max_count:
            return False

        self.env.cr.execute(
            "UPDATE wb_rate_limit_entry SET count = count + 1 WHERE id = %s",
            (entry_id,),
        )
        return True

    @api.model
    def _cron_cleanup_expired(self):
        """Stündlicher Cleanup: löscht alle Einträge außerhalb ihres Fensters.

        Skaliert notfalls auf Millionen Zeilen, weil bucket_key indexiert
        ist und wir per SQL arbeiten statt per ORM.

        Sprint 3 / L-H3: alle Schritte hart in try/except. Wenn der Cron
        eine Exception werfen würde, deaktiviert Odoo ihn nach 5 Failures
        — das wäre ein DoS auf den Rate-Limiter selbst (Tabelle wächst
        unbegrenzt). Logging-Failure ist OK, Cron läuft weiter.
        """
        try:
            self.env.cr.execute(
                "DELETE FROM wb_rate_limit_entry "
                "WHERE window_start + (window_seconds * INTERVAL '1 second') < NOW()"
            )
            removed = self.env.cr.rowcount
        except Exception as exc:
            # Re-raise wäre falsch — siehe Sprint-3-Plan B-H3.
            # Stattdessen prominent loggen, der Cron darf nicht aussterben.
            _logger.exception(
                "[wb_subscription] Rate-Limit-Cleanup-Cron fehlgeschlagen — "
                "Tabelle waechst weiter, Cleanup beim naechsten Lauf nochmal "
                "versucht. Fehler: %s", exc,
            )
            return

        _logger.info(
            "[wb_subscription] Rate-Limit-Cleanup: %d expired entries removed",
            removed,
        )

    @api.model
    def get_limit_from_config(self, param_name, default_max, default_window):
        """Parst Rate-Limit-Config aus ir.config_parameter.

        Format: "<max>/<window>/<scope>" z.B. "5/3600/ip" oder "1/86400/email".
        Fallback auf defaults wenn Parameter fehlt oder ungültig.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(param_name)
        if not raw:
            return default_max, default_window
        try:
            parts = raw.split('/')
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            _logger.warning(
                "[wb_subscription] Ungültiger Rate-Limit-Wert für %s: %r — nutze Defaults",
                param_name, raw,
            )
            return default_max, default_window
