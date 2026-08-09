#!/usr/bin/env python3
"""Erzeugt eine ZUGFeRD 2.5 / Factur-X 1.09 E-Rechnung (CII-XML, Profil EN16931).

Alle Betrags-, Steuer- und Summenberechnungen erfolgen deterministisch mit
`decimal` (kaufmännische Rundung, ROUND_HALF_UP) — niemals im Kopf rechnen!

Aufruf:
    python generate_invoice.py <input.json> [--seller <seller_profile.yaml>]
                               [--output <rechnung.xml>]

Eingabeformat (input.json) — siehe SKILL.md für die vollständige Referenz:
{
  "invoice_number": "RE-2025-0042",           // Pflicht, vom Nutzer!
  "invoice_type": "rechnung",                 // rechnung | korrektur | gutschrift
  "issue_date": "2025-07-01",                 // Pflicht, ISO-Datum
  "delivery_date": "2025-06-30",              // Liefer-/Leistungsdatum (empfohlen)
  "due_date": "2025-07-31",                   // optional, sonst default_payment_days
  "buyer": {
    "name": "Kunden AG", "street": "Kundenstraße 15", "postcode": "69876",
    "city": "Frankfurt", "country": "DE", "vat_id": ""
  },
  "buyer_reference": "",                      // optional (BT-10, z.B. Leitweg-ID)
  "order_reference": "",                      // optional Bestellnummer
  "referenced_invoice": {"id": "RE-2025-0001", "issue_date": "2025-06-01"},
                                              // Pflicht bei invoice_type=korrektur
  "skonto": {"percent": 2, "days": 10},       // optional
  "notes": ["Freitext-Hinweis"],              // optional
  "lines": [
    {"name": "Beratung", "quantity": 8, "unit_code": "HUR",
     "net_price": 120.00, "tax_rate": 19,
     "seller_assigned_id": "", "description": ""}
  ]
}
"""

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR.parent / "assets"

PROFILE_URN = "urn:cen.eu:en16931:2017"
TYPE_CODES = {"rechnung": "380", "gutschrift": "381", "korrektur": "384"}
CENT = Decimal("0.01")
FOUR_DP = Decimal("0.0001")

KLEINUNTERNEHMER_REASON = (
    "Kein Ausweis von Umsatzsteuer, da Kleinunternehmer gemäß § 19 UStG"
)


def d(value) -> Decimal:
    """Wert verlustfrei in Decimal wandeln (Floats über str)."""
    return Decimal(str(value))


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def rate_fmt(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def qty_fmt(value: Decimal) -> str:
    return str(value.quantize(FOUR_DP, rounding=ROUND_HALF_UP))


def iso_to_102(iso_date: str) -> str:
    """ISO-Datum (2025-07-01) → CII-Format 102 (20250701)."""
    return date.fromisoformat(iso_date).strftime("%Y%m%d")


def fail(msg: str):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def check_required(data: dict, seller_cfg: dict):
    """Pflichtfeld-Prüfung VOR der Generierung — keine stillen Platzhalter."""
    errors = []
    if not data.get("invoice_number"):
        errors.append("invoice_number fehlt (muss vom Nutzer kommen, nie erfinden!)")
    if data.get("invoice_type", "rechnung") not in TYPE_CODES:
        errors.append(f"invoice_type muss eines von {list(TYPE_CODES)} sein")
    if not data.get("issue_date"):
        errors.append("issue_date fehlt")
    buyer = data.get("buyer") or {}
    for f in ("name", "street", "postcode", "city", "country"):
        if not buyer.get(f):
            errors.append(f"buyer.{f} fehlt")
    lines = data.get("lines") or []
    if not lines:
        errors.append("mindestens eine Rechnungsposition (lines) erforderlich")
    for i, line in enumerate(lines, 1):
        for f in ("name", "quantity", "net_price"):
            if line.get(f) in (None, ""):
                errors.append(f"lines[{i}].{f} fehlt")
        if line.get("tax_rate") is None:
            errors.append(f"lines[{i}].tax_rate fehlt (19, 7 oder 0)")
    if data.get("invoice_type") == "korrektur" and not (
        (data.get("referenced_invoice") or {}).get("id")
    ):
        errors.append(
            "referenced_invoice.id fehlt (bei Rechnungskorrektur ist die Nummer "
            "der korrigierten Ursprungsrechnung Pflicht)"
        )
    seller = seller_cfg.get("seller") or {}
    if not seller.get("vat_id") and not seller.get("tax_number"):
        errors.append(
            "seller_profile.yaml: USt-IdNr. oder Steuernummer des Verkäufers "
            "erforderlich (BR-CO-26)"
        )
    if errors:
        fail("Pflichtangaben unvollständig:\n  - " + "\n  - ".join(errors))


def tax_category(rate: Decimal, kleinunternehmer: bool) -> str:
    """Steuerkategorie nach UNCL5305: S = Standard, Z = Nullsatz, E = befreit."""
    if kleinunternehmer:
        return "E"
    return "S" if rate > 0 else "Z"


def build_context(data: dict, seller_cfg: dict) -> dict:
    seller = seller_cfg["seller"]
    payment_cfg = seller_cfg.get("payment") or {}
    kleinunternehmer = bool(seller.get("kleinunternehmer"))
    currency = data.get("currency") or (seller_cfg.get("defaults") or {}).get(
        "currency", "EUR"
    )

    # --- Positionen berechnen -------------------------------------------
    lines_ctx = []
    line_total_sum = Decimal("0")
    # Steuergruppen: (kategorie, satz) -> Basis-Summe
    tax_groups: dict[tuple, Decimal] = {}
    for i, line in enumerate(data["lines"], 1):
        qty = d(line["quantity"])
        price = d(line["net_price"])
        rate = d(line["tax_rate"])
        if kleinunternehmer and rate != 0:
            fail(
                f"Position {i}: Kleinunternehmer nach § 19 UStG dürfen keine "
                "Umsatzsteuer ausweisen — tax_rate muss 0 sein."
            )
        category = tax_category(rate, kleinunternehmer)
        line_total = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
        line_total_sum += line_total
        key = (category, rate)
        tax_groups[key] = tax_groups.get(key, Decimal("0")) + line_total
        lines_ctx.append(
            {
                "line_id": str(i),
                "name": line["name"],
                "description": line.get("description") or "",
                "seller_assigned_id": line.get("seller_assigned_id") or "",
                "quantity": qty_fmt(qty),
                "unit_code": line.get("unit_code") or "H87",  # H87 = Stück
                "net_price": str(price.quantize(FOUR_DP, rounding=ROUND_HALF_UP)),
                "tax_category": category,
                "tax_rate": rate_fmt(rate),
                "line_total": money(line_total),
            }
        )

    # --- Steueraufschlüsselung (BG-23) je Kategorie+Satz -----------------
    tax_breakdown = []
    tax_total = Decimal("0")
    for (category, rate), basis in sorted(tax_groups.items(), key=lambda kv: kv[0][1]):
        calculated = (basis * rate / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        tax_total += calculated
        exemption = None
        if category == "E":
            exemption = data.get("exemption_reason") or KLEINUNTERNEHMER_REASON
        tax_breakdown.append(
            {
                "calculated": money(calculated),
                "basis": money(basis),
                "category": category,
                "rate": rate_fmt(rate),
                "exemption_reason": exemption,
            }
        )

    # --- Summen (BG-22), BR-CO-konform -----------------------------------
    charge_total = Decimal("0")
    allowance_total = Decimal("0")
    tax_basis = line_total_sum - allowance_total + charge_total
    grand_total = tax_basis + tax_total
    prepaid = d(data.get("prepaid", 0))
    due_payable = grand_total - prepaid

    # --- Zahlungsbedingungen ---------------------------------------------
    issue = date.fromisoformat(data["issue_date"])
    due_date_iso = data.get("due_date")
    if not due_date_iso and payment_cfg.get("default_payment_days"):
        due_date_iso = (
            issue + timedelta(days=int(payment_cfg["default_payment_days"]))
        ).isoformat()
    terms_parts = []
    if due_date_iso:
        due_de = date.fromisoformat(due_date_iso).strftime("%d.%m.%Y")
        terms_parts.append(f"Zahlbar ohne Abzug bis {due_de}")
    skonto = data.get("skonto")
    if skonto:
        sk_date = (issue + timedelta(days=int(skonto["days"]))).strftime("%d.%m.%Y")
        terms_parts.append(
            f"{skonto['percent']}% Skonto bei Zahlung innerhalb "
            f"{skonto['days']} Tagen (bis {sk_date})"
        )
    terms_description = ", ".join(terms_parts) if terms_parts else None

    # --- Notizen ----------------------------------------------------------
    notes = [{"content": n, "subject_code": None} for n in data.get("notes") or []]
    legal = seller.get("legal_info") or {}
    if legal.get("managing_director"):
        notes.append(
            {
                "content": f"Geschäftsführung: {legal['managing_director']}",
                "subject_code": "REG",
            }
        )
    if legal.get("registration"):
        notes.append({"content": legal["registration"], "subject_code": "REG"})

    referenced = data.get("referenced_invoice")
    referenced_ctx = None
    if referenced and referenced.get("id"):
        referenced_ctx = {
            "id": referenced["id"],
            "issue_date": iso_to_102(referenced["issue_date"])
            if referenced.get("issue_date")
            else None,
        }

    return {
        "profile_urn": PROFILE_URN,
        "invoice_number": data["invoice_number"],
        "type_code": TYPE_CODES[data.get("invoice_type", "rechnung")],
        "issue_date": iso_to_102(data["issue_date"]),
        "delivery_date": iso_to_102(data["delivery_date"])
        if data.get("delivery_date")
        else None,
        "currency": currency,
        "notes": notes,
        "seller": seller,
        "buyer": {
            "name": data["buyer"]["name"],
            "vat_id": data["buyer"].get("vat_id") or "",
            "address": {
                "street": data["buyer"]["street"],
                "postcode": data["buyer"]["postcode"],
                "city": data["buyer"]["city"],
                "country": data["buyer"]["country"],
            },
        },
        "buyer_reference": data.get("buyer_reference") or "",
        "order_reference": data.get("order_reference") or "",
        "lines": lines_ctx,
        "tax_breakdown": tax_breakdown,
        "payment": {
            "means_code": "58",  # 58 = SEPA-Überweisung
            "iban": (payment_cfg.get("iban") or "").replace(" ", ""),
            "bic": payment_cfg.get("bic") or "",
            "account_name": payment_cfg.get("account_name") or "",
            "terms_description": terms_description,
            "due_date": iso_to_102(due_date_iso) if due_date_iso else None,
        },
        "totals": {
            "line_total": money(line_total_sum),
            "charge_total": money(charge_total),
            "allowance_total": money(allowance_total),
            "tax_basis": money(tax_basis),
            "tax_total": money(tax_total),
            "grand_total": money(grand_total),
            "prepaid": money(prepaid),
            "due_payable": money(due_payable),
        },
        "referenced_invoice": referenced_ctx,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Pfad zur input.json mit den Rechnungsdaten")
    parser.add_argument(
        "--seller",
        default=str(ASSETS_DIR / "seller_profile.yaml"),
        help="Pfad zur Verkäufer-Stammdatendatei (YAML)",
    )
    parser.add_argument(
        "--template",
        default=str(ASSETS_DIR / "invoice_template.xml.j2"),
        help="Pfad zum Jinja2-CII-Template",
    )
    parser.add_argument(
        "--output", default="factur-x.xml", help="Pfad der erzeugten XML-Datei"
    )
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    with open(args.seller, encoding="utf-8") as f:
        seller_cfg = yaml.safe_load(f)

    check_required(data, seller_cfg)
    ctx = build_context(data, seller_cfg)

    template_path = Path(args.template)
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=True,  # XML-Escaping für alle Variablen
        keep_trailing_newline=True,
    )
    xml = env.get_template(template_path.name).render(**ctx)

    out = Path(args.output)
    out.write_text(xml, encoding="utf-8")

    t = ctx["totals"]
    print(f"E-Rechnung erzeugt: {out}")
    print(f"  Profil:        EN16931 (ZUGFeRD 2.5 / Factur-X 1.09)")
    print(f"  Rechnungsnr.:  {ctx['invoice_number']} (Typ {ctx['type_code']})")
    print(f"  Netto:         {t['tax_basis']} {ctx['currency']}")
    print(f"  Umsatzsteuer:  {t['tax_total']} {ctx['currency']}")
    print(f"  Brutto:        {t['grand_total']} {ctx['currency']}")
    print(f"  Zahlbetrag:    {t['due_payable']} {ctx['currency']}")
    print("WICHTIG: Jetzt validieren mit: python validate_invoice.py " + str(out))


if __name__ == "__main__":
    main()
