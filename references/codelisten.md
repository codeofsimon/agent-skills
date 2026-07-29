# Codelisten für E-Rechnungen (ZUGFeRD 2.5 / EN16931)

Auszug der praktisch relevanten Codes. Vollständige Listen: UN/CEFACT bzw.
Genericode-Dateien im offiziellen ZUGFeRD-Paket.

## Rechnungstypen (UNTDID 1001) — `invoice_type`

| Code | JSON-Wert | Bedeutung |
|---|---|---|
| 380 | `rechnung` | Handelsrechnung (Standardfall) |
| 381 | `gutschrift` | Gutschrift (Credit Note) |
| 384 | `korrektur` | Rechnungskorrektur (korrigierte Rechnung) |

Weitere Codes (389 Selbstfakturierung, 326 Teilrechnung, …) werden vom Skill
nicht unterstützt.

## Steuerkategorien (UNCL 5305)

Wird vom Skript automatisch gesetzt — hier nur zum Verständnis:

| Code | Bedeutung | Wann |
|---|---|---|
| S | Standard rate | Positionen mit 19 % oder 7 % |
| Z | Zero rated | Positionen mit 0 % (steuerbar, Satz 0) |
| E | Exempt from tax | Kleinunternehmer § 19 UStG (mit Befreiungsgrund-Text) |

Nicht unterstützt: AE (Reverse Charge), K (innergem. Lieferung), G (Export), O (nicht steuerbar).

## Einheiten-Codes (UN/ECE Recommendation 20/21) — `unit_code`

| Code | Einheit | Typische Verwendung |
|---|---|---|
| H87 | Stück (piece) | Waren, Artikel (empfohlener Standard für "Stück") |
| C62 | Einheit (one/unit) | dimensionslose Einheit, Pauschalen |
| HUR | Stunde | Dienstleistung nach Stunden |
| DAY | Tag | Tagessätze |
| MON | Monat | monatliche Pauschalen, Wartungsverträge |
| ANN | Jahr | Jahresgebühren |
| KGM | Kilogramm | Gewicht |
| GRM | Gramm | Gewicht |
| TNE | Tonne | Gewicht |
| MTR | Meter | Länge |
| MTK | Quadratmeter | Fläche |
| MTQ | Kubikmeter | Volumen |
| LTR | Liter | Volumen |
| KWH | Kilowattstunde | Energie |
| SET | Satz/Set | zusammengehörige Teile |
| PK | Paket/Pack | Verpackungseinheiten |
| XPP | Stück (unverpackt) | alternative Stück-Angabe |
| P1 | Prozent | prozentuale Positionen |
| LS | Pauschale (lump sum) | Pauschalbeträge |

Faustregel: Stück → `H87`, Stunden → `HUR`, Pauschale → `LS` oder `C62`.

## Zahlungsmittel (UNTDID 4461)

Vom Template gesetzt; relevant nur bei Abweichungen:

| Code | Bedeutung |
|---|---|
| 58 | SEPA-Überweisung (Standard des Skills, wenn IBAN vorhanden) |
| 30 | Überweisung (allgemein) |
| 59 | SEPA-Lastschrift |
| 48 | Kartenzahlung |
| 10 | Bar |
| ZZZ | Gegenseitig vereinbart |

## Notiz-Betreffcodes (UNTDID 4451) — `notes[].subject_code`

| Code | Bedeutung |
|---|---|
| AAI | Allgemeine Information (Default) |
| REG | Regulatorische Information (z. B. Geschäftsführer, Registergericht — wird automatisch aus `seller_profile.yaml` `legal_info` gesetzt) |
| ABL | Rechtliche Information |
| PMD | Zahlungsdetails |
| SUR | Anmerkungen des Verkäufers |

## Datumsformat

Alle Datumsangaben im XML: `format="102"` = `JJJJMMTT` (z. B. `20250701`).
Im input.json dagegen immer ISO `JJJJ-MM-TT` (`2025-07-01`) — das Skript
konvertiert.

## Ländercodes

ISO 3166-1 alpha-2: `DE`, `AT`, `CH`, `FR`, `NL`, … (im `buyer.country`).

## Währungen

ISO 4217: `EUR` (Default aus seller_profile.yaml). Andere Währungen sind
möglich, aber ohne Steuerumrechnung — für deutsche USt-Rechnungen bei EUR bleiben.
