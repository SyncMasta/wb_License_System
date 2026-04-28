"""Replay-Schutz fuer ``/api/license/activate`` (Sprint 3 / L-C1).

Speichert pro Aktivierungs-Versuch das Resultat — damit identische Calls
(z.B. nach Doppelklick im Wizard, Netzwerk-Retry, oder DB-Clone) genau
dasselbe Resultat zurueckbekommen, nicht eine zweite echte Aktivierung
ausloesen.

Zwei Dedup-Modi:

1. **Mit request_id (UUID4 vom Client):** strikter Replay-Schutz, beliebig
   lange (nur durch TTL-Cleanup begrenzt). Optimal fuer DB-Clone-Szenarien
   in denen der Test-Server denselben request_id der ursprünglichen
   Activation noch im Speicher hat.

2. **Ohne request_id (Legacy-Client):** Fallback auf die Tupel-Achse
   ``(key, db_uuid, domain, hour-Bucket)``. Gleiche DB-Clone, gleicher
   Key, gleiche Stunde → wird zurückgewiesen mit dem zwischenzeitlich
   gespeicherten Resultat.

TTL-Cleanup loescht Eintraege aelter als 7 Tage (Cron stuendlich).
"""

import json
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


# Wenn KEIN request_id mitkommt: dedup-Bucket auf Stunden-Granularitaet.
LEGACY_DEDUP_WINDOW_SECONDS = 3600

# Lebensdauer eines Dedup-Eintrags. Nach Ablauf werden Replays NICHT mehr
# gefiltert — das ist OK weil eine erfolgreiche Aktivierung den Activation-
# Code bereits konsumiert hat (Code-Hash-Reset).
RECORD_TTL_DAYS = 7


class WbLicenseActivationRequest(models.Model):
    _name = 'wb.license.activation_request'
    _description = 'License-Activation-Replay-Dedup (Sprint 3 / L-C1)'
    _order = 'create_date desc, id desc'
    _rec_name = 'request_id'

    request_id = fields.Char(
        string='Request-ID',
        index=True,
        help="UUID4 vom Client. Leer = Legacy-Client ohne Replay-Schutz "
             "via Header — fallback auf (key, domain, db_uuid, hour-Bucket).",
    )
    key = fields.Char(
        string='License-Key',
        required=True, index=True,
    )
    domain = fields.Char(string='Domain', index=True)
    db_uuid = fields.Char(string='DB-UUID', index=True)
    result_state = fields.Selection(
        [('ok', 'Erfolgreich'), ('fail', 'Fehlgeschlagen')],
        string='Resultat', required=True,
    )
    result_payload = fields.Text(
        string='Resultat-Payload (JSON)',
        help="Vollständiges JSON-Response-Body, wie er beim ersten Call "
             "zurückging. Wird auf Replay 1:1 wieder rausgegeben.",
    )

    @api.model
    def find_replay(self, request_id, key, domain, db_uuid):
        """Sucht passenden Dedup-Eintrag.

        Returns:
            dict mit dem zwischengespeicherten Payload, wenn ein Replay
            erkannt wird. ``None`` wenn keiner gefunden — Caller fährt
            mit normaler Activation-Logik fort.
        """
        if request_id:
            existing = self.sudo().search([('request_id', '=', request_id)], limit=1)
            if existing:
                _logger.info(
                    "[wb_subscription] Replay erkannt via request_id=%s "
                    "(key-Bucket %s) — Payload aus Cache zurueck.",
                    request_id, (key or '')[:8],
                )
                return self._payload_or_default(existing)
            return None

        # Legacy-Pfad ohne request_id: Bucket auf Stunden-Ebene
        if not (key and domain and db_uuid):
            return None
        cutoff = fields.Datetime.now() - timedelta(seconds=LEGACY_DEDUP_WINDOW_SECONDS)
        existing = self.sudo().search([
            ('request_id', '=', False),
            ('key', '=', key),
            ('domain', '=', domain),
            ('db_uuid', '=', db_uuid),
            ('create_date', '>=', cutoff),
        ], order='create_date desc', limit=1)
        if existing:
            _logger.info(
                "[wb_subscription] Replay erkannt via Legacy-Bucket "
                "(key=%s, domain=%s, db_uuid=%s) — Payload aus Cache.",
                (key or '')[:8], domain, (db_uuid or '')[:8],
            )
            return self._payload_or_default(existing)
        return None

    @api.model
    def record_result(self, request_id, key, domain, db_uuid, payload):
        """Schreibt Resultat einer Activation in den Dedup-Store.

        Bei vorhandenem request_id: ein Insert pro UUID4 (DB-Constraint
        erzwingt das, bei Race ignorieren).
        """
        try:
            payload_json = json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            payload_json = json.dumps({'status': 'unknown'})
        result_state = 'ok' if (payload or {}).get('status') == 'ok' else 'fail'
        try:
            self.sudo().create({
                'request_id': request_id or False,
                'key': key,
                'domain': domain or False,
                'db_uuid': db_uuid or False,
                'result_state': result_state,
                'result_payload': payload_json,
            })
        except Exception as exc:
            # Race-Insert oder DB-Fehler — niemals den eigentlichen
            # Activation-Flow blockieren.
            _logger.warning(
                "[wb_subscription] Replay-Dedup-Insert fehlgeschlagen "
                "(request_id=%s key=%s): %s",
                request_id, (key or '')[:8], exc,
            )

    @api.model
    def _payload_or_default(self, rec):
        if rec.result_payload:
            try:
                return json.loads(rec.result_payload)
            except (ValueError, TypeError):
                pass
        # Beschaedigt → konservativ als Fail melden.
        return {'status': 'error', 'error': 'REPLAY_DETECTED'}

    @api.model
    def _cron_cleanup(self):
        """TTL-Cleanup. Wie L-H3 hart in try/except, damit der Cron
        nicht ausstirbt (sonst waechst die Tabelle unbegrenzt)."""
        try:
            cutoff = fields.Datetime.now() - timedelta(days=RECORD_TTL_DAYS)
            self.env.cr.execute(
                "DELETE FROM wb_license_activation_request "
                "WHERE create_date < %s",
                (cutoff,),
            )
            removed = self.env.cr.rowcount
        except Exception as exc:
            _logger.exception(
                "[wb_subscription] Activation-Request-Cleanup fehlgeschlagen: %s", exc,
            )
            return
        _logger.info(
            "[wb_subscription] Activation-Request-Cleanup: %d Eintrag(e) entfernt", removed,
        )
