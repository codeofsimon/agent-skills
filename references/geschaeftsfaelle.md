# Geschäftsfälle: Regeln und Beispiele

## 1. Normale Inlandsrechnung (Typ 380)

Der Standardfall. Positionen mit `tax_rate` 19, 7 oder 0. Gemischte Sätze in
einer Rechnung sind erlaubt — das Skript gruppiert die Steuern automatisch
je Satz (getrennte `ApplicableTradeTax`-Blöcke).

```json
{
  "invoice_number": "RE-2025-0042",
  "invoice_type": "rechnung",
  "issue_date": "2025-07-01",
  "delivery_date": "2025-06-30",
  "due_date": "2025-07-31",
  "buyer": {"name": "Kunden AG", "street": "Kundenstraße 15",
            "postcode": "69876", "city": "Frankfurt", "country": "DE"},
  "lines": [
    {"name": "Beratung Juni", "quantity": 8, "unit_code": "HUR",
     "net_price": 120.00, "tax_rate": 19},
    {"name": "Fachbuch", "quantity": 1, "unit_code": "H87",
     "net_price": 34.58, "tax_rate": 7}
  ]
}
```

Hinweise:
- Ohne `due_date` nutzt das Skript `payment.default_payment_days` aus
  `seller_profile.yaml` (Fälligkeit = Rechnungsdatum + n Tage).
- `delivery_date` (Liefer-/Leistungsdatum) ist umsatzsteuerlich wichtig
  (§ 14 Abs. 4 UStG) — aktiv nachfragen, wenn nicht genannt.
- Skonto: `"skonto": {"percent": 2, "days": 10}` erzeugt den Skonto-Hinweis
  in den Zahlungsbedingungen.

## 2. Kleinunternehmer nach § 19 UStG

Gesteuert über `seller_profile.yaml` → `kleinunternehmer: true`. Dann gilt:

- **Alle** Positionen müssen `tax_rate: 0` haben (Skript erzwingt das).
- Steuerkategorie wird automatisch `E` (exempt) mit Befreiungsgrund
  *"Kein Ausweis von Umsatzsteuer, da Kleinunternehmer gemäß § 19 UStG"*.
- TaxTotal = 0.00, Brutto = Netto.
- Verkäufer braucht mindestens die Steuernummer (`tax_number`); eine
  USt-IdNr. ist nicht erforderlich.

Referenz: `beispiele/beispiel_kleinunternehmer.xml`

## 3. Rechnungskorrektur (Typ 384)

Korrigiert eine bereits gestellte Rechnung. Regeln:

- `referenced_invoice` mit `id` (Nummer der Ursprungsrechnung) ist
  **Pflicht** — ohne sie bricht das Skript ab. `issue_date` der
  Ursprungsrechnung mit angeben, wenn bekannt.
- Die Korrektur bekommt eine **eigene, neue Rechnungsnummer**.
- **Voll-Storno:** alle Positionen der Ursprungsrechnung mit **negativer
  Menge** übernehmen → Beträge werden negativ, Rechnung neutralisiert die
  Ursprungsrechnung. (Danach ggf. neue korrekte Rechnung als Typ 380 stellen.)
- **Differenz-Korrektur:** nur die Änderung als Position(en) erfassen
  (z. B. Preisminderung als negative Position).
- Empfehlung: Notiz mit Korrekturgrund ergänzen
  (`"notes": ["Korrektur der Rechnung RE-2025-0042 vom 01.07.2025: ..."]`).

```json
{
  "invoice_number": "RK-2025-0007",
  "invoice_type": "korrektur",
  "issue_date": "2025-07-15",
  "referenced_invoice": {"id": "RE-2025-0042", "issue_date": "2025-07-01"},
  "buyer": {"...": "..."},
  "lines": [
    {"name": "Beratung Juni (Storno)", "quantity": -8, "unit_code": "HUR",
     "net_price": 120.00, "tax_rate": 19}
  ],
  "notes": ["Vollständige Stornierung der Rechnung RE-2025-0042 vom 01.07.2025."]
}
```

Referenz: `beispiele/beispiel_rechnungskorrektur.xml`

## 4. Gutschrift (Typ 381)

Kaufmännische Gutschrift (Credit Note): Der Verkäufer schreibt dem Käufer
einen Betrag gut, **ohne** eine konkrete Rechnung zu korrigieren.

- `invoice_type: "gutschrift"`, Positionen mit **positiven** Mengen
  (der Typ 381 sagt bereits "Gutschrift", die Beträge bleiben positiv).
- Keine Referenz-Pflicht; `referenced_invoice` ist optional.
- Nicht verwechseln mit dem Gutschriftverfahren nach § 14 Abs. 2 S. 2 UStG
  (Selbstfakturierung, Code 389) — das unterstützt der Skill nicht.

**Abgrenzung Korrektur vs. Gutschrift:** Bezieht sich die Erstattung auf eine
konkrete frühere Rechnung → `korrektur` (384) mit Referenz. Eigenständige
Gutschrift ohne Rechnungsbezug → `gutschrift` (381).

## 5. Zahlungsangaben & Skonto

- SEPA-Überweisung (Code 58) mit IBAN/BIC aus `seller_profile.yaml` wird
  automatisch eingetragen.
- Fälligkeit: explizites `due_date` schlägt das Standard-Zahlungsziel.
- Skonto-Beispiel: `"skonto": {"percent": 2, "days": 10}` →
  Zahlungsbedingung "2 % Skonto bei Zahlung innerhalb von 10 Tagen".
- Anzahlungen: `"prepaid": 100.00` reduziert den Zahlbetrag
  (DuePayableAmount = Brutto − prepaid).

## Häufige Validierungsfehler und Ursachen

| Fehler | Ursache / Lösung |
|---|---|
| BR-CO-15 verletzt | Nie von Hand rechnen — input.json korrigieren und neu generieren |
| Kleinunternehmer + tax_rate ≠ 0 | Alle Positionen auf `tax_rate: 0` setzen |
| Korrektur ohne Referenz | `referenced_invoice.id` ergänzen |
| BR-CO-26 | `seller_profile.yaml` braucht `vat_id` oder `tax_number` |
| XSD-Fehler "element not expected" | Template wurde manuell verändert — Original wiederherstellen |
| IBAN-Warnung | Tippfehler in der IBAN in `seller_profile.yaml` prüfen |
