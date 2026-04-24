"""WB Key Generator — zentrale Kryptographie-Helfer.

Implementiert alle Algorithmen aus ARCHITECTURE.md Kapitel 4:
- Public-Key-Generierung (WB-{PROD}-{UUID8}{CHK})
- Activation-Code (25 Zeichen Base32-reduziert, 5er-Gruppen)
- bcrypt-Hashing für Activation-Codes
- Fernet-Encryption für temporäre Ticket-Speicherung
- Ticket-Token (TCKT-{base64url(uuid4)})
- Email-OTP (6-stellig)
- Fingerprint (SHA256 über domain + db_uuid)
"""

import base64
import hashlib
import logging
import os
import re
import secrets
import uuid

from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


KEY_FORMAT_RE = re.compile(r'^WB-([A-Z]{4})-([a-f0-9]{8})([A-Z2-7]{2})$')
CODE_FORMAT_RE = re.compile(r'^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}$')

CHECKSUM_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


class WbKeyGenerator(models.AbstractModel):
    _name = 'wb.key.generator'
    _description = 'WB Key/Code/Ticket-Generator und Krypto-Helfer'

    @api.model
    def compute_checksum(self, data):
        """2-stellige Base32-Prüfziffer über UPPERCASE(data).

        Zweck: Tippfehler-Erkennung, 1/1024 Kollisionen.
        """
        total = sum(ord(c) for c in data.upper()) % 1024
        return CHECKSUM_ALPHABET[total // 32] + CHECKSUM_ALPHABET[total % 32]

    @api.model
    def generate_public_key(self, product_code):
        """Erzeugt einen Public Key im Format WB-{PROD}-{UUID8}{CHK}."""
        if not product_code or not re.match(r'^[A-Z]{4}$', product_code):
            raise ValidationError(_(
                "Produkt-Code muss genau 4 Großbuchstaben sein (erhalten: %s)"
            ) % product_code)
        uuid_short = uuid.uuid4().hex[:8]
        payload = f"WB-{product_code.upper()}-{uuid_short}"
        checksum = self.compute_checksum(payload)
        return f"{payload}{checksum}"

    @api.model
    def generate_activation_code(self):
        """25-stelliger Activation-Code in 5er-Gruppen.

        Alphabet ohne Verwechslungsgefahr: kein 0, 1, I, O.
        Kryptografisch zufällig via secrets.choice.
        """
        raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(25))
        return '-'.join(raw[i:i + 5] for i in range(0, 25, 5))

    @api.model
    def validate_key_format(self, key):
        """Format-Check für Public Key inklusive Checksum-Validierung.

        Reine CPU-Operation ohne DB-Zugriff — billig und erlaubt
        frühes Ablehnen ungültiger Eingaben im Activation-Endpoint.
        """
        if not key:
            return False
        m = KEY_FORMAT_RE.match(key)
        if not m:
            return False
        product_code, uuid_short, checksum = m.groups()
        payload = f"WB-{product_code}-{uuid_short}"
        return self.compute_checksum(payload) == checksum

    @api.model
    def validate_activation_code_format(self, code):
        """Format-Check für Activation-Code (25 Zeichen, 5 Gruppen, ohne 0/1/I/O)."""
        if not code:
            return False
        return bool(CODE_FORMAT_RE.match(code))

    @api.model
    def hash_activation_code(self, code):
        """bcrypt-Hash für Activation-Code. 12 Runden ≈ 200ms.

        12 Runden ist der Compromise zwischen User-Experience (Aktivierung
        fühlt sich responsive an) und Brute-Force-Resistenz (5 Versuche/h
        Rate-Limit + 200ms = 120 Versuche/Tag, bei 10^37 Kombinationen
        Jahrtausende für Erfolg).
        """
        import bcrypt
        return bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=12))

    @api.model
    def verify_activation_code(self, code, stored_hash):
        """Prüft Klartext-Code gegen bcrypt-Hash.

        Returns False statt zu crashen, wenn stored_hash ungültig ist —
        wichtig für den Fall, dass DB-Daten korrumpiert wurden.
        """
        import bcrypt
        if not stored_hash or not code:
            return False
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')
        try:
            return bcrypt.checkpw(code.encode('utf-8'), stored_hash)
        except (ValueError, TypeError):
            return False

    @api.model
    def _get_fernet_key(self):
        """Lädt Fernet-Key: ENV-Variable bevorzugt, sonst ir.config_parameter.

        ENV-Variable ist bevorzugt, weil sie NICHT in DB-Backups landet —
        schützt gegen "DB-Leak kompromittiert alle Pending-Tickets".
        Siehe DECISION #48a.
        """
        key = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        if key:
            return key.encode('utf-8')
        key = self.env['ir.config_parameter'].sudo().get_param(
            'wb_subscription.fernet_key')
        if not key:
            raise UserError(_(
                "Fernet-Key nicht konfiguriert. Bitte ENV-Variable "
                "WB_SUBSCRIPTION_FERNET_KEY setzen oder "
                "ir.config_parameter 'wb_subscription.fernet_key' pflegen. "
                "Generierung: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""))
        return key.encode('utf-8')

    @api.model
    def encrypt_code(self, plaintext):
        """Fernet-Encryption für temporäre Ticket-Code-Speicherung.

        Nur für wb.activation.ticket.encrypted_code verwenden.
        Wird nach Ticket-Consume gelöscht (DECISION #48b).
        """
        from cryptography.fernet import Fernet
        f = Fernet(self._get_fernet_key())
        return f.encrypt(plaintext.encode('utf-8'))

    @api.model
    def decrypt_code(self, ciphertext):
        """Fernet-Decryption für Portal-Code-Anzeige nach OTP-Verify.

        Ergebnis darf nur im RAM an das Portal-Template übergeben werden —
        nicht in Session persistiert, nicht geloggt.
        """
        from cryptography.fernet import Fernet, InvalidToken
        if not ciphertext:
            raise UserError(_("Kein verschlüsselter Code im Ticket vorhanden."))
        f = Fernet(self._get_fernet_key())
        try:
            return f.decrypt(ciphertext).decode('utf-8')
        except InvalidToken:
            raise UserError(_(
                "Ticket-Decryption fehlgeschlagen. Möglicherweise wurde "
                "der Fernet-Key rotiert. Bitte Support kontaktieren."))

    @api.model
    def generate_ticket_token(self):
        """TCKT-{22-Zeichen-base64url-UUID4}. Siehe ARCHITECTURE.md 4.7."""
        raw = uuid.uuid4().bytes
        encoded = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
        return f"TCKT-{encoded}"

    @api.model
    def generate_email_otp(self):
        """6-stellige Ziffern-OTP für Portal-Ticket-Bestätigung.

        Kryptografisch zufällig, 10^6 Kombinationen. Mit 5 Versuchen Limit
        und 10-Minuten-Fenster statistisch sicher genug.
        """
        return ''.join(str(secrets.randbelow(10)) for _ in range(6))

    @api.model
    def hash_otp(self, otp):
        """bcrypt-Hash für OTP. Leicht günstiger (8 Runden) als Activation-Code,
        weil OTP nur 10min gültig und mit 5 Versuchen gekappt ist."""
        import bcrypt
        return bcrypt.hashpw(otp.encode('utf-8'), bcrypt.gensalt(rounds=8))

    @api.model
    def verify_otp(self, otp, stored_hash):
        import bcrypt
        if not stored_hash or not otp:
            return False
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')
        try:
            return bcrypt.checkpw(otp.encode('utf-8'), stored_hash)
        except (ValueError, TypeError):
            return False

    @api.model
    def compute_fingerprint(self, domain, db_uuid):
        """SHA256 über domain + db_uuid für Instanz-Fingerprint.

        Dient als zusätzlicher Identifikations-Layer über Domain+DB-UUID
        hinaus. Staging-Kopien einer Produktiv-DB bekommen anderen
        Fingerprint wenn sie auf anderer Domain laufen.
        """
        if not domain or not db_uuid:
            raise ValidationError(_(
                "Fingerprint benötigt domain und db_uuid (erhalten: domain=%s, db_uuid=%s)"
            ) % (domain, db_uuid))
        payload = f"{domain.lower().strip()}|{db_uuid.strip()}"
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()
