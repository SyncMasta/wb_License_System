# wb_elster_reports — Modul-Briefing

**Status:** 🟡 MVP-Skelett vorhanden, **NICHT produktionsreif**
**Version:** `19.0.1.0.0`
**License:** OPL-1, Preis: 149 €
**Läuft auf:** Kunden-Odoo-Instanzen (verkäufliches Produkt)
**Dependencies:** `base`, `account`
**Zukünftig:** `wb_license_client` hinzufügen (für Lizenz-Gating)

---

## Zweck

Erzeugt ELSTER-konforme XML-Dateien für:

- **UStVA** (Umsatzsteuer-Voranmeldung) — monatlich oder quartalsweise
- **ZM** (Zusammenfassende Meldung) — innergemeinschaftliche Lieferungen

Die XML-Dateien werden vom Nutzer manuell im ELSTER-Online-Portal
hochgeladen. Keine direkte Schnittstelle zu ELSTER-Servern (wäre
komplex wegen Zertifikats-Auth).

**Zielgruppe:** KMU und Solos in Deutschland die Odoo nutzen und nicht
DATEV oder Lexware für Steuer-Meldung einsetzen wollen.

**Wettbewerbsvorteil:** Odoo-nativ (keine externe Software), günstig
(149 € statt 30-50 €/Monat DATEV), einmalig kaufbar (App Store) oder
abonnierbar (Webseite mit Updates).

---

## WICHTIGER WARNHINWEIS

⚠️ **Dieses Modul ist NOCH NICHT gegen den offiziellen ELSTER-Validator
getestet.** Vor Release:

1. XML-Output gegen ELSTER-XSD validieren
2. Test-Upload im ELSTER-Portal (Testmodus) durchführen
3. Mit Steuerberater durchsprechen

Aktuell ist das Skelett ein **Prototyp** der die Datenmodelle und Workflows
hat, aber die XML-Erzeugung ist **nicht vollständig spec-konform**. Siehe
"Offene Punkte" weiter unten.

---

## Module-Struktur (Ist-Zustand)

```
wb_elster_reports/
├── __manifest__.py                         # v19.0.1.0.0, OPL-1, 149€
├── __init__.py
├── README.rst                              # Nutzer-Dokumentation + Haftungsausschluss
├── models/
│   ├── __init__.py
│   ├── res_company.py                      # Unternehmens-Stammdaten (Steuernummer, Finanzamt)
│   ├── wb_elster_tax_code.py               # Kennziffern (KZ81, KZ86, etc.)
│   ├── wb_elster_tax_mapping.py            # Konto→Kennziffer-Mapping
│   └── wb_elster_ustva.py                  # UStVA-Datensatz (Hauptmodell)
├── data/
│   ├── wb_elster_tax_code_data.xml         # Alle Kennziffern 2024/2025
│   └── wb_elster_tax_mapping_skr03_data.xml # Default-Mapping für SKR03
├── security/
│   ├── wb_elster_security.xml              # Gruppen: user + manager
│   └── ir.model.access.csv
├── static/description/
│   ├── icon.png                            # App-Icon
│   └── (fehlt: banner.png für App Store)
├── views/
│   ├── res_company_views.xml
│   ├── wb_elster_tax_code_views.xml
│   ├── wb_elster_tax_mapping_views.xml
│   ├── wb_elster_ustva_views.xml
│   └── menu.xml
└── doc/                                    # Platzhalter, leer
```

---

## Models im Detail

### 1. `res.company` — Erweiterungen

Felder die das Finanzamt braucht:

| Feld | Typ | Required |
|---|---|---|
| `elster_steuernummer` | Char | Required für Export |
| `elster_finanzamt_nr` | Char(4) | Required — Finanzamt-Nummer |
| `elster_finanzamt_name` | Char | Display-Zweck |
| `elster_ustidnr` | Char | USt-ID-Nr |
| `elster_filing_frequency` | Selection | 'monthly' oder 'quarterly' |
| `elster_dauerfrist` | Boolean | § 46 UStDV aktiv? |

**Constraint:** `elster_steuernummer` muss 10 oder 11 Ziffern enthalten
(bundeslandabhängig). Aktuell nur Länge geprüft, nicht Struktur.

### 2. `wb.elster.tax.code` — Kennziffern-Stammdaten

Die ELSTER-UStVA kennt ~50 Kennziffern (KZ):

- KZ 81 = Umsätze zu 19 % USt
- KZ 86 = Umsätze zu 7 % USt
- KZ 41 = Innergemeinschaftliche Lieferungen
- KZ 66 = Vorsteuer aus Rechnungen
- ... (siehe data/wb_elster_tax_code_data.xml)

**Felder:**

| Feld | Typ |
|---|---|
| `code` | Char (z.B. 'KZ81') |
| `name` | Char (z.B. 'Umsätze zu 19% USt') |
| `description` | Text |
| `sign` | Selection ('base', 'tax', 'refund') |
| `active` | Boolean |
| `valid_from` | Date |
| `valid_to` | Date |
| `form_type` | Selection ('ustva', 'zm') |

**Wichtig:** `valid_from`/`valid_to` ermöglichen Historie. Wenn ELSTER ab
2027 eine neue KZ einführt, kann man die alte deaktivieren ohne zu löschen.

### 3. `wb.elster.tax.mapping` — Konto→Kennziffer

Das Herzstück. Welches Buchhaltungskonto (`account.account`) fließt in
welche Kennziffer?

**Felder:**

| Feld | Typ |
|---|---|
| `name` | Char computed |
| `company_id` | M2O res.company |
| `account_id` | M2O account.account |
| `tax_code_id` | M2O wb.elster.tax.code |
| `sign` | Selection ('+' oder '-') |
| `tax_rate` | Float (info, nicht berechnet) |
| `active` | Boolean |
| `note` | Text |

**Vordefiniert:** Default-Mapping für SKR03 in `wb_elster_tax_mapping_skr03_data.xml`.
Kunde kann das anpassen wenn seine Kontostruktur abweicht.

### 4. `wb.elster.ustva` — UStVA-Report

Das Hauptmodell. Ein Record pro Meldezeitraum.

**Felder:**

| Feld | Typ |
|---|---|
| `name` | Char computed (z.B. "UStVA Q1/2026") |
| `company_id` | M2O res.company |
| `period_type` | Selection ('monthly', 'quarterly') |
| `period_year` | Integer |
| `period_month` | Integer (nur monthly) |
| `period_quarter` | Integer (nur quarterly) |
| `date_from` | Date computed |
| `date_to` | Date computed |
| `state` | Selection siehe unten |
| `line_ids` | O2M zu wb.elster.ustva.line |
| `total_sales` | Monetary computed |
| `total_vat_payable` | Monetary computed |
| `xml_content` | Text (das fertige XML) |
| `xml_filename` | Char computed |
| `xml_generated_date` | Datetime |
| `submitted_date` | Date |
| `submitted_by` | M2O res.users |
| `notes` | Text |
| `company_currency_id` | related |

**State-Machine:**

```
draft ──[action_compute]──► computed
                               │
                               ├─[action_export]──► exported
                               │                      │
                               │                      └─[action_mark_submitted]──► submitted
                               │
                               └─[action_back_to_draft]──► draft
```

**Methoden:**

```python
def action_compute(self):
    """Durchläuft alle tax_mappings der Company und summiert Beträge
    pro Kennziffer. Erstellt line_ids."""

def action_export(self):
    """Generiert XML gemäß ELSTER-Schema.
    xml_content wird gefüllt.
    State wird 'exported'."""

def action_download_xml(self):
    """Download-Action für UI-Button"""

def action_mark_submitted(self):
    """Wenn Kunde sagt 'jetzt hochgeladen bei ELSTER',
    state → submitted + submitted_date"""

def action_back_to_draft(self):
    """Korrektur möglich bis State=exported"""

def _check_plausibility(self):
    """Warnungen wenn:
       - Negative Beträge
       - Fehlende USt-IDs bei EU-Kunden
       - Summenfehler"""
```

### 5. `wb.elster.ustva.line` — Report-Zeilen

Für jeden KZ eine Zeile (nicht bei 0-Werten).

| Feld | Typ |
|---|---|
| `report_id` | M2O wb.elster.ustva |
| `tax_code_id` | M2O wb.elster.tax.code |
| `amount_base` | Monetary (Netto-Betrag) |
| `amount_tax` | Monetary (Steuer) |
| `entry_count` | Integer (Anzahl Buchungen) |

---

## Workflow aus Kunden-Sicht

```
1. Install Modul
   → Onboarding-Wizard: Steuernummer, Finanzamt, Frequenz
   → Default-Tax-Mappings werden angelegt

2. Monatlich / Quartalsweise:
   → UStVA → Neuer Bericht
   → Zeitraum wählen
   → "Berechnen" → Summen aus Buchhaltung ziehen
   → Überprüfen im Formular (Zeilen editierbar bis "Exportiert")
   → "XML exportieren" → Download der .xml Datei
   → Manuell hochladen auf www.elster.de
   → Nach erfolgreichem Upload: "Als übermittelt markieren"
   → State=submitted, unveränderbar

3. Audit:
   → Alle Reports historisch verfügbar
   → Chatter-Log an jedem Report
   → XML-Datei als Attachment gespeichert
```

---

## Security

### Gruppen

| Gruppe | Zweck |
|---|---|
| `group_wb_elster_user` | Steuerberater/Mitarbeiter: Lesen + Entwürfe bearbeiten |
| `group_wb_elster_manager` | Geschäftsführer: Freigabe, "als übermittelt markieren" |

### Wichtige ACL-Regeln

- User kann Berichte **nicht löschen** (nur Manager)
- Submitted-Berichte sind **read-only** auch für Manager
- Multi-Company: Record-Rule auf company_id

---

## Offene Punkte vor Produktions-Release

### Kritisch (blockierend für Release)

1. **XML-Validierung gegen ELSTER-XSD**
   - XSD-Dateien von www.elster.de ziehen
   - `lxml` für XSD-Validation im `action_export()` einbauen
   - Tests mit realen Beispiel-Daten
   - **Aufwand:** 4-6h

2. **Test-Upload im ELSTER-Testportal**
   - Test-Account bei ELSTER einrichten
   - Von 3 verschiedenen Test-Companies UStVAs generieren
   - Alle manuell hochladen, Erfolg dokumentieren
   - **Aufwand:** 4-6h

3. **Steuerberater-Review**
   - Einen Steuerberater beauftragen das Modul + Mappings zu prüfen
   - Besonders SKR03-Default-Mapping gegen Praxis-Beispiele testen
   - **Aufwand:** Externer Kostenpunkt 300-500 €

4. **Steuernummer-Validation**
   - Regex pro Bundesland (10 oder 11 stellig, spezifische Struktur)
   - Aktuell nur Längen-Check
   - **Aufwand:** 2h

### Wichtig (vor Release)

5. **ZM-Report (Zusammenfassende Meldung)**
   - Aktuell nur UStVA gebaut
   - ZM braucht eigenes Model `wb.elster.zm` analog zu ustva
   - Pro EU-Kunde eine Zeile mit Land, USt-ID, Betrag
   - **Aufwand:** 6-8h

6. **SKR04-Support**
   - Zweites Default-Mapping für SKR04 (neuer Kontenrahmen)
   - Als separates Data-File
   - **Aufwand:** 3h

7. **Onboarding-Wizard**
   - Bei Install → Wizard: Steuernummer, Finanzamt, Kontenplan, Frequenz
   - Dann Auto-Installation des passenden Mapping-Sets
   - **Aufwand:** 4h

8. **Plausibilitätsprüfungen**
   - Negative Beträge warnen
   - Summen-Abweichungen zur Buchhaltung
   - Fehlende USt-IDs bei EU-Umsätzen
   - **Aufwand:** 3-4h

### Nice-to-have (v1.1)

9. **Dauerfristverlängerung § 46 UStDV**
   - Separates Formular für die jährliche 1/11-Sonderzahlung
   - Checkbox in Company-Einstellungen + separater Report

10. **PDF-Export zusätzlich zu XML**
    - Für Archivierung/Papierablage
    - QWeb-Template mit Zahlen aufbereitet

11. **Berichts-Vergleich**
    - Nebeneinander zwei Perioden vergleichen
    - "Was hat sich vs. Vorjahresquartal verändert?"

12. **Integration mit wb_license_client**
    - Feature-Gates auf `action_export()` und `action_mark_submitted()`
    - Nach Lizenz-Expire: Read-Only auf alte Reports, keine neuen
    - **Aufwand:** 2-3h (wenn wb_license_client fertig)

---

## Roadmap zu v1.0 (produktionsreif)

Nur mit diesen Schritten ist das Modul **verkäuflich**:

| # | Aufgabe | Stunden | Muss für Release? |
|---|---|---|---|
| 1 | XML gegen XSD validiert | 6 | ✅ |
| 2 | Test-Upload erfolgreich | 6 | ✅ |
| 3 | Steuerberater-Review | 1 (+Extern 500€) | ✅ |
| 4 | Steuernummer-Validation | 2 | ✅ |
| 5 | ZM-Report | 7 | ✅ |
| 6 | SKR04-Support | 3 | 🟡 Kann in v1.1 |
| 7 | Onboarding-Wizard | 4 | ✅ |
| 8 | Plausibilitätsprüfungen | 4 | ✅ |
| 9 | wb_license_client Integration | 3 | ✅ (wenn verkauft via Webseite) |
| 10 | App-Store-Assets (Banner, Screenshots) | 4 | ✅ |
| 11 | Handbuch PDF | 4 | ✅ |
| 12 | Marketing-Landing-Page | 6 | ✅ |

**Total:** ~50h Arbeit + 500 € Steuerberater + ggf. App-Store-Submission-Gebühren

---

## Geschäftliche Überlegungen

### Vertriebskanäle

**Odoo App Store:**
- Preis 149 € (Jahresversion "ELSTER 2026")
- Vorteil: Viele potenzielle Käufer die eh im Store stöbern
- Nachteil: 20% Odoo-Anteil, jährlich neu kaufen

**Eigene Webseite:**
- Preis 199 €/Jahr (Abo mit Lizenzschlüssel)
- Vorteil: Mehr Marge, Direktbeziehung zum Kunden, Updates laufend
- Nachteil: Eigenes Marketing nötig

**Beide parallel:**
- Geringerer Preis im Store als Einstieg
- Höhere Marge bei direkt-Käufern
- Bestehende Kunden können von Store zu Webseite migrieren bei Renewal

### Zielkunden

- Solos / Freelancer mit USt-Pflicht und Odoo
- Kleine GmbHs (1-10 Mitarbeiter)
- Odoo-Consultants die ELSTER-Modul an ihre Kunden weiterverkaufen

### Wettbewerb

- **DATEV Unternehmen Online** (30-50 €/Monat, Standard für Steuerberater)
- **Lexware büro plus** (~200 €/Jahr)
- **SevDesk / Lexoffice** (15-40 €/Monat, Abo)
- **Odoo + DATEV-Interface** (bestehende Module, aber keine ELSTER-Direktlösung)

**Deine Positionierung:** Das einzige **native** ELSTER-Modul für
Odoo. Wer Odoo nutzt und keine externe Lösung will, hat kaum Alternativen.

---

## Integration mit wb_license_client (zukünftig)

```python
# In wb_elster_ustva.py, nach Integration:

from odoo.addons.wb_license_client.models.wb_license_client import license_required

class WbElsterUstva(models.Model):
    _inherit = 'wb.elster.ustva'

    @license_required('ELST')
    def action_export(self):
        """Export XML. Gated — nur mit gültiger Lizenz."""
        # ... bestehende Logik

    @license_required('ELST')
    def action_mark_submitted(self):
        """Gated — nur mit gültiger Lizenz."""
        # ... bestehende Logik

    # Read-Actions (list, form) sind NICHT gated!
    # Nach Lizenz-Expire: Kunde kann alte Reports einsehen, nur keine neuen.
```

**Dependency-Update im Manifest:**
```python
'depends': [
    'base',
    'account',
    'wb_license_client',  # NEU für v1.0
],
```

---

## Ist das Modul überhaupt eine gute Idee?

Ein paar ehrliche Fragen die Tobias für sich beantworten sollte:

1. **Markt-Größe:** Wie viele Odoo-User gibt's in Deutschland die USt-Pflicht haben und NICHT bereits DATEV nutzen?
   - Schätzung: ~500-2000 Firmen
   - Bei 2% Conversion: 10-40 Käufer/Jahr
   - Bei 149 € Preis: 1.500-6.000 € Umsatz/Jahr
   - Abzüglich Aufwand Roadmap + Support + Wartung: **Break-even erst bei 50+ Kunden**

2. **Support-Aufwand:** ELSTER ändert jedes Jahr Kennziffern. Du musst pro Jahr Updates liefern.
   - Aufwand ~20h/Jahr für Updates + Support-Tickets

3. **Haftungsrisiko:** Wenn dein Modul einen Fehler hat der zu Steuer-Nachzahlungen führt — bist du haftbar? (Darum braucht's EULA!)

**Mein Rat, unverblümt:** Das Modul ist **technisch machbar** und **strategisch interessant als Case-Study/Lead-Magnet**, aber **wirtschaftlich eher grenzwertig** als Hauptprodukt. Als **Türöffner für größere Beratungs-Mandate** ("Ich habe das Modul gebaut, lasst mich eure Odoo-Installation insgesamt betreuen") könnte es stark sein.

Alternativ: ELSTER-Modul **als Lead-Magnet kostenlos anbieten**, das zieht Kunden in dein Beratungs-Geschäft.

---

## Aufwandsschätzung

| Phase | Stunden |
|---|---|
| Aktueller Stand (MVP-Skelett) | ✅ Done |
| Bis v1.0 (produktionsreif) | ~50h |
| Laufender Support pro Jahr | ~20h |
| Updates für neue Steuerjahre | ~10-20h/Jahr |

**Entscheidung offen:** Weiter bauen zu v1.0 — ja/nein? Siehe oben
"Ist das Modul überhaupt eine gute Idee?".
