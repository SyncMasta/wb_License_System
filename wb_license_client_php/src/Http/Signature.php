<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Http;

/**
 * HMAC-Signatur für Requests an den WB-Lizenzserver (Schema v1).
 *
 * Gegenstück zu wb.key.generator.build_signature_base im Odoo-Modul
 * wb_subscription. Signiert wird der rohe Request-Body, nicht ein
 * kanonisiertes Objekt — Client und Server sehen damit garantiert dieselben
 * Bytes, ohne sich über Key-Reihenfolge, Zahlenformate oder Unicode-Escaping
 * einigen zu müssen.
 *
 * Aufbau des signierten Strings (LF-getrennt, kein abschließender Umbruch):
 *
 *     WB-HMAC-V1
 *     <public key>
 *     <unix timestamp>
 *     <nonce>
 *     <sha256-hex des Request-Bodys>
 *
 * Der Server toleriert ±300 s Zeitdrift und lehnt eine bereits verwendete
 * Nonce ab. Die Systemzeit des Dienstes muss also grob stimmen.
 */
final class Signature
{
    public const SCHEME = 'WB-HMAC-V1';

    public const PREFIX = 'v1=';

    public const HEADER_KEY = 'X-WB-Key';

    public const HEADER_TIMESTAMP = 'X-WB-Timestamp';

    public const HEADER_NONCE = 'X-WB-Nonce';

    public const HEADER_SIGNATURE = 'X-WB-Signature';

    /**
     * Baut den zu signierenden String.
     */
    public static function base(string $key, int $timestamp, string $nonce, string $body): string
    {
        return implode("\n", [
            self::SCHEME,
            $key,
            (string) $timestamp,
            $nonce,
            hash('sha256', $body),
        ]);
    }

    /**
     * Berechnet die Signatur (hex, lowercase) ohne das v1=-Präfix.
     */
    public static function compute(
        string $secret,
        string $key,
        int $timestamp,
        string $nonce,
        string $body
    ): string {
        return hash_hmac('sha256', self::base($key, $timestamp, $nonce, $body), $secret);
    }

    /**
     * Liefert die vollständigen Signatur-Header für einen Request.
     *
     * Ohne Secret wird ein leeres Array zurückgegeben: der Aufrufer schickt
     * den Request dann unsigniert, was der Server je nach Enforcement-Modus
     * toleriert. Der Client wirft hier bewusst nicht — eine fehlende
     * Konfiguration darf den Verarbeitungspfad des Dienstes nicht anhalten.
     *
     * @return array<string, string>
     */
    public static function headers(
        string $secret,
        string $key,
        string $body,
        ?int $timestamp = null,
        ?string $nonce = null
    ): array {
        if ($secret === '' || $key === '') {
            return [];
        }

        $timestamp ??= time();
        $nonce ??= self::nonce();

        return [
            self::HEADER_KEY => $key,
            self::HEADER_TIMESTAMP => (string) $timestamp,
            self::HEADER_NONCE => $nonce,
            self::HEADER_SIGNATURE => self::PREFIX.self::compute($secret, $key, $timestamp, $nonce, $body),
        ];
    }

    /**
     * Erzeugt eine Nonce: 32 Hex-Zeichen aus dem CSPRNG.
     *
     * Der Server begrenzt auf 64 Zeichen und sperrt jede Nonce 900 s lang
     * gegen Wiederverwendung.
     */
    public static function nonce(): string
    {
        return bin2hex(random_bytes(16));
    }

    /**
     * Prüft eine Signatur in konstanter Zeit.
     *
     * Wird vom Client selbst nicht gebraucht — der Server signiert seine
     * Antworten (noch) nicht. Steht für Tests und für den Fall bereit, dass
     * Response-Signing nachgerüstet wird.
     */
    public static function verify(
        string $secret,
        string $key,
        int $timestamp,
        string $nonce,
        string $body,
        string $signature
    ): bool {
        if (str_starts_with($signature, self::PREFIX)) {
            $signature = substr($signature, strlen(self::PREFIX));
        }

        return hash_equals(
            self::compute($secret, $key, $timestamp, $nonce, $body),
            strtolower(trim($signature))
        );
    }

    /**
     * Maskiert einen Public Key oder ein Secret für Logs.
     *
     * Regel aus dem Übergabe-Dokument: ein Schlüssel erscheint in keinem Log
     * vollständig. Zu kurze Werte werden komplett maskiert statt fast
     * vollständig ausgegeben.
     */
    public static function mask(string $value): string
    {
        $length = strlen($value);
        if ($length <= 8) {
            return str_repeat('*', max($length, 4));
        }

        return substr($value, 0, 7).'****'.substr($value, -4);
    }
}
