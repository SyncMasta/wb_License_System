# -*- coding: utf-8 -*-
"""Sanitizer für Audit-Log-Details (Sprint 2 / B-M1 analog).

Spiegelung von ``wb_bitwarden/models/_redact.py``. Wird beim Schreiben in
``wb.license.event.details`` angewendet, damit ein versehentlich
mitgegebener ``access_token``, ``client_secret``, bcrypt-Hash oder
Fernet-Token nicht im Audit-Log landet.

Bewusste Duplizierung statt Cross-Module-Import — wb_subscription hängt
nicht an wb_bitwarden, und ein Helper-Paket wäre Overkill für 30 Zeilen.
"""
import re

_PATTERNS = [
    (re.compile(r'gAAAA[A-Za-z0-9_=-]{30,}'),
     '[REDACTED-FERNET]'),
    (re.compile(r'\$2[aby]\$\d{2}\$[A-Za-z0-9./]{53}'),
     '[REDACTED-BCRYPT]'),
    (re.compile(
        r'(["\'])(access_token|refresh_token|client_secret|secret|api_key|'
        r'password|master_key|service_user_password|api[_-]?token|'
        r'activation_code)'
        r'(["\'])\s*:\s*(["\'])([^"\']*)\4',
        re.IGNORECASE),
     r'\1\2\3: \4[REDACTED]\4'),
    (re.compile(
        r'(client_secret|access_token|refresh_token|password|api[_-]?key|'
        r'activation_code)='
        r'[^&\s]+',
        re.IGNORECASE),
     r'\1=[REDACTED]'),
]


def redact_secrets(text):
    """Ersetzt bekannte Secret-Patterns durch Redact-Marker.

    Robust gegen None/Nicht-Strings (returnt unverändert).
    """
    if not text or not isinstance(text, str):
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
