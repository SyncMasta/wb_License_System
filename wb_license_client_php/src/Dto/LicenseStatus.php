<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Dto;

use DateTimeImmutable;
use WissenBeratung\LicenseClient\Enum\LicenseState;
use WissenBeratung\LicenseClient\Enum\StatusSource;
use WissenBeratung\LicenseClient\Http\Signature;

/**
 * Lizenzstatus eines Mandanten.
 *
 * Achtung bei den Datumsfeldern: der Server liefert reine Datumswerte
 * (`YYYY-MM-DD`), keine Zeitstempel. Sie werden hier auf Mitternacht UTC
 * normalisiert. `graceUntil` wird serverseitig bei jedem Lesen aus dem
 * Mahnstatus neu berechnet und gleitet damit mit — daraus lässt sich keine
 * belastbare lokale Frist ableiten, nur eine Anzeige.
 */
final readonly class LicenseStatus
{
    /**
     * @param  array<string, mixed>  $raw  unveränderte Serverantwort
     */
    public function __construct(
        public string $tenant,
        public string $key,
        public LicenseState $state,
        public StatusSource $source,
        public DateTimeImmutable $checkedAt,
        public ?DateTimeImmutable $validFrom = null,
        public ?DateTimeImmutable $validTo = null,
        public ?DateTimeImmutable $graceUntil = null,
        public array $raw = [],
    ) {}

    /**
     * Baut den Status aus einer Serverantwort (`result`-Inhalt).
     *
     * @param  array<string, mixed>  $payload
     */
    public static function fromServerPayload(
        string $tenant,
        string $key,
        array $payload,
        DateTimeImmutable $checkedAt,
        StatusSource $source = StatusSource::Live,
    ): self {
        return new self(
            tenant: $tenant,
            key: $key,
            state: LicenseState::fromServer(
                is_string($payload['state'] ?? null) ? $payload['state'] : null
            ),
            source: $source,
            checkedAt: $checkedAt,
            validFrom: self::parseDate($payload['valid_from'] ?? null),
            validTo: self::parseDate($payload['valid_to'] ?? null),
            graceUntil: self::parseDate($payload['grace_until'] ?? null),
            raw: $payload,
        );
    }

    /**
     * Status ohne jede Serverantwort — der Startzustand und der Zustand,
     * wenn auch Local Grace abgelaufen ist.
     */
    public static function unknown(string $tenant, string $key, DateTimeImmutable $checkedAt): self
    {
        return new self(
            tenant: $tenant,
            key: $key,
            state: LicenseState::Unknown,
            source: StatusSource::LocalGrace,
            checkedAt: $checkedAt,
        );
    }

    /** Kopie mit anderer Quelle — für den Weg Cache → LocalGrace. */
    public function withSource(StatusSource $source): self
    {
        return new self(
            tenant: $this->tenant,
            key: $this->key,
            state: $this->state,
            source: $source,
            checkedAt: $this->checkedAt,
            validFrom: $this->validFrom,
            validTo: $this->validTo,
            graceUntil: $this->graceUntil,
            raw: $this->raw,
        );
    }

    /** Alter der letzten echten Serverantwort in Sekunden. */
    public function ageInSeconds(DateTimeImmutable $now): int
    {
        return max(0, $now->getTimestamp() - $this->checkedAt->getTimestamp());
    }

    /** Maskierter Schlüssel — die einzige Form, in der er in Logs gehört. */
    public function maskedKey(): string
    {
        return Signature::mask($this->key);
    }

    /**
     * Serialisierung für den Cache.
     *
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'tenant' => $this->tenant,
            'key' => $this->key,
            'state' => $this->state->value,
            'source' => $this->source->value,
            'checked_at' => $this->checkedAt->getTimestamp(),
            'valid_from' => $this->validFrom?->format('Y-m-d'),
            'valid_to' => $this->validTo?->format('Y-m-d'),
            'grace_until' => $this->graceUntil?->format('Y-m-d'),
            'raw' => $this->raw,
        ];
    }

    /**
     * Gegenstück zu toArray(). Liefert null, wenn der Eintrag nicht mehr zum
     * aktuellen Format passt — ein kaputter Cache-Eintrag darf nie zu einem
     * Fatal im Aufrufpfad führen, er führt zu einem Server-Call.
     *
     * @param  mixed  $data
     */
    public static function fromArray($data): ?self
    {
        if (! is_array($data) || ! isset($data['tenant'], $data['state'], $data['checked_at'])) {
            return null;
        }

        $state = LicenseState::tryFrom((string) $data['state']);
        if ($state === null) {
            return null;
        }

        return new self(
            tenant: (string) $data['tenant'],
            key: (string) ($data['key'] ?? ''),
            state: $state,
            source: StatusSource::tryFrom((string) ($data['source'] ?? '')) ?? StatusSource::Cache,
            checkedAt: (new DateTimeImmutable('@' . (int) $data['checked_at'])),
            validFrom: self::parseDate($data['valid_from'] ?? null),
            validTo: self::parseDate($data['valid_to'] ?? null),
            graceUntil: self::parseDate($data['grace_until'] ?? null),
            raw: is_array($data['raw'] ?? null) ? $data['raw'] : [],
        );
    }

    private static function parseDate(mixed $value): ?DateTimeImmutable
    {
        if (! is_string($value) || $value === '') {
            return null;
        }

        // Der Server liefert Datumswerte; ISO-Zeitstempel werden trotzdem
        // akzeptiert, falls das Feld serverseitig einmal umgestellt wird.
        $parsed = DateTimeImmutable::createFromFormat('Y-m-d|', substr($value, 0, 10), new \DateTimeZone('UTC'));

        return $parsed !== false ? $parsed : null;
    }
}
