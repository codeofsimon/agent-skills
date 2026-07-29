---
name: e-rechnung
description: "Erzeugt normkonforme deutsche E-Rechnungen als ZUGFeRD 2.5 / Factur-X 1.09 CII-XML im Profil EN16931 (EU-Norm EN 16931, E-Rechnungspflicht Deutschland). Sammelt Rechnungsdaten im Dialog oder extrahiert sie aus hochgeladenen Dateien (PDF, Excel, Word), berechnet Beträge und Umsatzsteuer deterministisch per Python-Skript und validiert das Ergebnis gegen XSD-Schema und EN16931-Geschäftsregeln. Unterstützt Inlandsrechnungen (19%/7%/0%), Kleinunternehmer nach § 19 UStG, Rechnungskorrekturen und Gutschriften. NUTZE DIESEN SKILL bei: E-Rechnung, eRechnung, elektronische Rechnung, ZUGFeRD, Factur-X, XRechnung, EN16931, CII, Rechnung erstellen, Rechnungskorrektur, Gutschrift, Kleinunternehmer, Umsatzsteuer berechnen, invoice XML."
---

# E-Rechnung (ZUGFeRD 2.5 / Factur-X 1.09, Profil EN16931)

Dieser Skill erzeugt **valide elektronische Rechnungen als CII-XML** nach der
EU-Norm EN 16931 — das Format der deutschen E-Rechnungspflicht. Der Output ist
**ausschließlich die XML-Datei** (`factur-x.xml`), kein PDF.

## Eiserne Regeln

1. **NIEMALS Beträge, Steuern oder Summen selbst (im Kopf/als LLM) berechnen.**
   Alle Berechnungen macht `scripts/generate_invoice.py` mit `decimal`
   (kaufmännische Rundung). Du sammelst nur die Rohdaten.
2. **NIEMALS XML von Hand schreiben oder verändern.** Das XML entsteht
   ausschließlich durch das Skript aus dem Jinja2-Template.
3. **NIEMALS eine Rechnungsnummer erfinden.** Sie ist immer eine
   Pflichtangabe des Nutzers (rechtlich: fortlaufend und einmalig).
4. **NIEMALS Platzhalter für fehlende Pflichtfelder einsetzen.** Fehlt etwas,
   frage gezielt nach (siehe Checkliste). Erst generieren, wenn alles da ist.
5. **IMMER validieren.** Nach jeder Generierung `scripts/validate_invoice.py`
   ausführen. Nur eine Rechnung mit `ERGEBNIS: GÜLTIG` an den Nutzer ausgeben.
6. Die Verkäufer-Stammdaten kommen aus `assets/seller_profile.yaml` —
   nicht beim Nutzer erfragen, außer die Datei ist unvollständig oder der
   Nutzer will explizit abweichen.

## Workflow

### Schritt 1: Geschäftsfall bestimmen

Frage bzw. leite aus dem Kontext ab, welcher Fall vorliegt:

| Fall | `invoice_type` | Besonderheit |
|---|---|---|
| Normale Rechnung | `rechnung` | Standardfall (Typ 380) |
| Rechnungskorrektur | `korrektur` | Referenz auf Ursprungsrechnung **Pflicht** (Typ 384) |
| Gutschrift | `gutschrift` | Typ 381 |
| Kleinunternehmer | `rechnung` | wird über `seller_profile.yaml` (`kleinunternehmer: true`) gesteuert; alle Positionen `tax_rate: 0` |

Details und Steuerkategorien: `references/geschaeftsfaelle.md`

### Schritt 2: Daten sammeln

**Quelle A — Dialog:** Nutzer nennt die Daten im Chat.

**Quelle B — Hochgeladene Dateien:** Extrahiere die Daten per Python-Skript in
der Sandbox, zeige sie dem Nutzer **zur Bestätigung**, bevor du generierst:
- PDF: `pdfplumber` (Text + Tabellen: `page.extract_text()`, `page.extract_tables()`)
- Excel: `openpyxl` oder `pandas.read_excel`
- Word: `python-docx`

**Pflichtfeld-Checkliste** (alles muss vorliegen, sonst nachfragen):

- [ ] Rechnungsnummer (vom Nutzer!)
- [ ] Rechnungsdatum
- [ ] Käufer: Name, Straße, PLZ, Ort, Ländercode
- [ ] Mindestens 1 Position mit: Bezeichnung, Menge, Einheit, Netto-Einzelpreis, Steuersatz (19/7/0)
- [ ] Bei Korrektur: Nummer (+ Datum) der Ursprungsrechnung
- [ ] Liefer-/Leistungsdatum (dringend empfohlen, nachfragen)
- [ ] Fälligkeitsdatum ODER Standard-Zahlungsziel aus `seller_profile.yaml` verwenden

Optional: Käufer-USt-IdNr., Bestellnummer (`order_reference`),
Käufer-Referenz/Leitweg-ID (`buyer_reference`), Skonto, Freitext-Notizen,
Anzahlungen (`prepaid`).

Einheiten-Codes (UN/ECE Rec. 20) und weitere Codes: `references/codelisten.md`

### Schritt 3: input.json schreiben und generieren

Schreibe die gesammelten Daten als JSON-Datei und führe aus:

```
python scripts/generate_invoice.py input.json --output factur-x.xml
```

Das vollständige JSON-Schema steht im Docstring von
`scripts/generate_invoice.py`. Minimalbeispiel:

```json
{
  "invoice_number": "RE-2025-0042",
  "invoice_type": "rechnung",
  "issue_date": "2025-07-01",
  "delivery_date": "2025-06-30",
  "buyer": {"name": "Kunden AG", "street": "Kundenstraße 15",
            "postcode": "69876", "city": "Frankfurt", "country": "DE"},
  "lines": [
    {"name": "Beratung", "quantity": 8, "unit_code": "HUR",
     "net_price": 120.00, "tax_rate": 19}
  ]
}
```

Das Skript bricht mit klarer Fehlermeldung ab, wenn Pflichtfelder fehlen —
gib diese Fragen an den Nutzer weiter.

### Schritt 4: Validieren (Pflicht!)

```
python scripts/validate_invoice.py factur-x.xml
```

Prüft: (1) XSD-Schema Factur-X 1.09 EN16931, (2) EN16931-Rechenregeln
(BR-CO-10…17, Steuerbasis je Kategorie, Befreiungsgründe, IBAN-Mod-97).

- `ERGEBNIS: GÜLTIG` → Datei an den Nutzer ausgeben.
- `ERGEBNIS: UNGÜLTIG` → Fehler beheben (meist Eingabedaten), neu generieren.
  Niemals das XML direkt patchen.

### Schritt 5: Ergebnis präsentieren

Gib dem Nutzer aus:
1. Die XML-Datei zum Download.
2. Eine Zusammenfassung: Rechnungsnummer, Käufer, Netto / USt je Satz /
   Brutto / Zahlbetrag, Fälligkeit (Werte aus der Skript-Ausgabe übernehmen,
   nicht neu rechnen).
3. Den Hinweis: *"Validiert gegen Factur-X 1.09 EN16931 (XSD +
   EN16931-Rechenregeln). Das XML ist die rechtlich maßgebliche E-Rechnung
   im Sinne der deutschen E-Rechnungspflicht."*

## Ausrollen auf eine andere Firma

Nur `assets/seller_profile.yaml` austauschen (Stammdaten, Bankverbindung,
Kleinunternehmer-Flag, Zahlungsziel). Alles andere bleibt unverändert.

## Grenzen des Skills

Bei diesen Anforderungen den Nutzer informieren, dass der Skill sie (noch)
nicht abdeckt — nicht improvisieren:
- Reverse Charge (§ 13b UStG), innergemeinschaftliche Lieferungen, Export
- Rabatte/Zuschläge auf Dokumentebene
- Fremdwährungen mit Steuerumrechnung
- ZUGFeRD-Hybrid-PDF (PDF/A-3) — Output ist bewusst nur XML
- Andere Profile als EN16931 (MINIMUM/BASIC/EXTENDED/XRechnung-Extension)

## Dateien

| Datei | Zweck |
|---|---|
| `assets/seller_profile.yaml` | Verkäufer-Stammdaten (pro Firma austauschbar) |
| `assets/invoice_template.xml.j2` | Jinja2-Template des CII-XML (nicht manuell rendern) |
| `assets/schema/*.xsd` | Factur-X 1.09 EN16931 XSD für die Validierung |
| `scripts/generate_invoice.py` | Berechnung + XML-Generierung |
| `scripts/validate_invoice.py` | XSD- + Geschäftsregel-Validierung |
| `references/codelisten.md` | Einheiten, Steuerkategorien, Rechnungstypen, Zahlungsmittel |
| `references/geschaeftsfaelle.md` | Regeln je Geschäftsfall |
| `references/beispiele/*.xml` | Offizielle ZUGFeRD-Beispielrechnungen zum Nachschlagen |
