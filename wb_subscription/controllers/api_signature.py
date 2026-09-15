"""HMAC-Verifikation für die öffentlichen Lizenz-Endpoints.

Hintergrund: die /api/license/*-Routen laufen mit ``auth='public'``. Bis hierhin
war der Public Key das einzige Geheimnis — wer ihn kennt, konnte Status abrufen
und ``last_seen_*`` überschreiben. Mit einem Secret je Lizenzschlüssel kann ein
Client seine Requests signieren; der Server erkennt damit, ob der Aufrufer
tatsächlich im Besitz des Secrets ist.

Signatur-Schema (v1), Header:

* ``X-WB-Key``        — Public Key, identifiziert das Secret
* ``X-WB-Timestamp``  — Unix-Sekunden, Drift-Fenster ±300 s
* ``X-WB-Nonce``      — pro Request einmalig, max. 64 Zeichen
* ``X-WB-Signature``  — ``v1=<hex>``

Signiert wird der **rohe Request-Body**, nicht ein kanonisiertes Objekt:
Client und Server sehen damit dieselben Bytes, ohne sich über Key-Reihenfolge,
Zahlenformate oder Unicode-Escaping einig werden zu müssen. Details in
``wb.key.generator.build_signature_base``.

Enforcement über ``ir.config_parameter wb_subscription.hmac_enforcement``:

* ``off``       — Header werden ignoriert, Verhalten wie vor dem Rollout
* ``optional``  — Default. Gültige Signatur wird als Vertrauensmerkmal
                  vermerkt, ungültige abgewiesen, fehlende toleriert.
* ``required``  — Auf /check zusätzlich: Keys MIT hinterlegtem Secret müssen
                  signieren. Keys ohne Secret bleiben zugelassen, sonst würde
                  der Schalter jede Bestandsinstanz aussperren.

Das Secret wird nie geloggt, nie in Events geschrieben, nie zurückgegeben.
"""

import logging
import time

from odoo.http import request

_logger = logging.getLogger(__name__)

# Maximale Abweichung zwischen Client-Timestamp und Serverzeit. 300 s ist
# großzügig genug für Instanzen ohne NTP und eng genug, damit ein
# abgefangener Request nicht beliebig lange nachspielbar bleibt.
TIMESTAMP_TOLERANCE_SECONDS = 300

# Nonce-Sperrfenster. Muss größer sein als die Timestamp-Toleranz, sonst
# fällt eine Nonce aus dem Speicher, während ihr Timestamp noch gültig ist.
NONCE_WINDOW_SECONDS = 900

MAX_NONCE_LENGTH = 64

# Ergebnis der Prüfung
TRUST_ABSENT = 'absent'      # keine Signatur mitgeschickt
TRUST_HMAC = 'hmac'          # Signatur geprüft und gültig
TRUST_DISABLED = 'disabled'  # Enforcement steht auf 'off'

ERROR_INVALID = 'SIGNATURE_INVALID'
ERROR_REQUIRED = 'SIGNATURE_REQUIRED'
ERROR_TIMESTAMP = 'SIGNATURE_TIMESTAMP'
ERROR_REPLAY = 'SIGNATURE_REPLAY'


def get_enforcement_mode():
    """Liest den Enforcement-Modus, mit Fallback auf 'optional'."""
    raw = (request.env['ir.config_parameter'].sudo().get_param(
        'wb_subscription.hmac_enforcement') or '').strip().lower()
    return raw if raw in ('off', 'optional', 'required') else 'optional'


def _header(name):
    return (request.httprequest.headers.get(name) or '').strip()


def _raw_body():
    """Roher Request-Body als bytes.

    Odoo hat den Body zu diesem Zeitpunkt bereits gelesen und geparst;
    ``get_data(cache=True)`` liefert die gepufferten Originalbytes.
    """
    try:
        return request.httprequest.get_data(cache=True) or b''
    except Exception:
        _logger.warning("[wb_subscription] Roher Request-Body nicht lesbar")
        return b''


def _log_failure(license, reason, key):
    """Schreibt einen Audit-Eintrag für einen gescheiterten Signaturversuch.

    Best-effort: ein fehlgeschlagener Audit-Eintrag darf die Abweisung
    des Requests nicht verhindern.
    """
    try:
        ip = request.httprequest.remote_addr or '0.0.0.0'
        ua = (request.httprequest.user_agent.string[:255]
              if request.httprequest.user_agent else '')
        request.env['wb.license.event'].sudo().log_event(
            license or False, 'signature_failed',
            ip_address=ip, user_agent=ua,
            details={'reason': reason, 'key': key or ''},
        )
    except Exception as exc:
        _logger.warning(
            "[wb_subscription] signature_failed-Event nicht geschrieben: %s", exc)


def _nonce_is_fresh(key, nonce):
    """True, wenn diese Nonce zu diesem Key noch nicht verwendet wurde.

    Nutzt die vorhandenen Rate-Limit-Buckets: ``max_count=1`` bedeutet, der
    erste Aufruf wird angenommen, jeder weitere im Fenster abgelehnt. Der
    stündliche Cleanup-Cron räumt die Einträge mit ab.
    """
    return request.env['wb.rate.limit.entry'].sudo().check_and_increment(
        f"hmacnonce:{key}:{nonce}", 'hmac_nonce', 1, NONCE_WINDOW_SECONDS,
    )


def verify_signature(expected_key=None, license=None):
    """Prüft die Signatur des aktuellen Requests.

    :param expected_key: Public Key aus dem Body. Weicht er vom Header ab,
        gilt die Signatur als ungültig — sonst könnte ein Aufrufer mit dem
        Secret des einen Keys Requests für einen anderen signieren.
    :param license: optional bereits geladener ``wb.license.key``-Record,
        spart einen Suchlauf.
    :return: ``(trust, error)``. ``error`` ist None, wenn der Request
        weiterlaufen darf. ``trust`` ist einer der TRUST_*-Werte.
    """
    mode = get_enforcement_mode()
    if mode == 'off':
        return TRUST_DISABLED, None

    signature = _header('X-WB-Signature')
    header_key = _header('X-WB-Key')
    timestamp = _header('X-WB-Timestamp')
    nonce = _header('X-WB-Nonce')

    if not signature:
        return TRUST_ABSENT, None

    key = header_key or expected_key or ''
    if expected_key and header_key and header_key != expected_key:
        _log_failure(license, 'key_mismatch', key)
        return TRUST_ABSENT, ERROR_INVALID
    if not key or not timestamp or not nonce:
        _log_failure(license, 'incomplete_headers', key)
        return TRUST_ABSENT, ERROR_INVALID
    if len(nonce) > MAX_NONCE_LENGTH:
        _log_failure(license, 'nonce_too_long', key)
        return TRUST_ABSENT, ERROR_INVALID

    try:
        drift = abs(int(time.time()) - int(timestamp))
    except (TypeError, ValueError):
        _log_failure(license, 'timestamp_unparsable', key)
        return TRUST_ABSENT, ERROR_TIMESTAMP
    if drift > TIMESTAMP_TOLERANCE_SECONDS:
        _log_failure(license, 'timestamp_drift', key)
        return TRUST_ABSENT, ERROR_TIMESTAMP

    if license is None:
        license = request.env['wb.license.key'].sudo().search(
            [('name', '=', key)], limit=1)
    if not license:
        # Kein Key, keine Aussage darüber, ob er existiert: derselbe
        # Fehlercode wie bei falscher Signatur, damit der Endpoint nicht
        # zum Key-Orakel wird.
        _log_failure(False, 'unknown_key', key)
        return TRUST_ABSENT, ERROR_INVALID

    secret = license._get_api_secret()
    if not secret:
        _log_failure(license, 'no_secret_configured', key)
        return TRUST_ABSENT, ERROR_INVALID

    gen = request.env['wb.key.generator'].sudo()
    if not gen.verify_request_signature(
            secret, key, timestamp, nonce, _raw_body(), signature):
        _log_failure(license, 'signature_mismatch', key)
        return TRUST_ABSENT, ERROR_INVALID

    # Replay-Schutz erst NACH gültiger Signatur: sonst könnte ein Fremder
    # mit geratenen Nonces die Buckets des Keys vollschreiben.
    if not _nonce_is_fresh(key, nonce):
        _log_failure(license, 'nonce_reused', key)
        return TRUST_ABSENT, ERROR_REPLAY

    return TRUST_HMAC, None


def signature_required_for(license):
    """Ob dieser Key im Modus 'required' signieren MUSS.

    Nur Keys mit hinterlegtem Secret. Ein Key ohne Secret kann nicht
    signieren — den auszusperren wäre ein Selbsttor beim Rollout.
    """
    if get_enforcement_mode() != 'required':
        return False
    return bool(license and license.sudo().api_secret_hint)
