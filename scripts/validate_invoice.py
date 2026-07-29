#!/usr/bin/env python3
"""Validiert eine ZUGFeRD 2.5 / Factur-X 1.09 E-Rechnung (CII-XML).

Zwei Prüfstufen:
  1. XSD-Schema-Validierung (Struktur) gegen Factur-X 1.09 EN16931
  2. EN16931-Geschäftsregeln (Rechenregeln BR-CO-10..17 u.a.) mit `decimal`

Aufruf:
    python validate_invoice.py <rechnung.xml> [--schema <pfad-zur-xsd>]

Exit-Code 0 = gültig, 1 = Fehler gefunden.
"""

import argparse
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from lxml import etree

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_XSD = SCRIPT_DIR.parent / "assets" / "schema" / "Factur-X_1.09_EN16931.xsd"

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

CENT = Decimal("0.01")


def dec(node_or_text) -> Decimal:
    text = node_or_text if isinstance(node_or_text, str) else node_or_text.text
    return Decimal(text.strip())


def first(root, xpath):
    result = root.xpath(xpath, namespaces=NS)
    return result[0] if result else None


def validate_xsd(tree, xsd_path: Path) -> list[str]:
    schema = etree.XMLSchema(etree.parse(str(xsd_path)))
    if schema.validate(tree):
        return []
    return [f"XSD: {e.line}: {e.message}" for e in schema.error_log]


def iban_valid(iban: str) -> bool:
    """ISO 7064 Mod-97-Prüfung der IBAN."""
    iban = iban.replace(" ", "").upper()
    if len(iban) < 15 or not iban[:2].isalpha():
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def validate_business_rules(root) -> tuple[list[str], list[str]]:
    """EN16931-Rechenregeln. Rückgabe: (fehler, warnungen)."""
    errors, warnings = [], []

    sums = first(root, "//ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    if sums is None:
        return ["Keine SpecifiedTradeSettlementHeaderMonetarySummation gefunden"], []

    def total(name, default=None):
        node = first(sums, f"ram:{name}")
        if node is None:
            return default
        return dec(node)

    line_total = total("LineTotalAmount")
    charge_total = total("ChargeTotalAmount", Decimal("0"))
    allowance_total = total("AllowanceTotalAmount", Decimal("0"))
    tax_basis = total("TaxBasisTotalAmount")
    tax_total = total("TaxTotalAmount", Decimal("0"))
    grand_total = total("GrandTotalAmount")
    prepaid = total("TotalPrepaidAmount", Decimal("0"))
    due_payable = total("DuePayableAmount")

    # BR-CO-10: Summe der Positionsbeträge = LineTotalAmount
    line_amounts = [
        dec(n)
        for n in root.xpath(
            "//ram:IncludedSupplyChainTradeLineItem"
            "//ram:SpecifiedTradeSettlementLineMonetarySummation"
            "/ram:LineTotalAmount",
            namespaces=NS,
        )
    ]
    if sum(line_amounts, Decimal("0")) != line_total:
        errors.append(
            f"BR-CO-10: Positionssumme {sum(line_amounts, Decimal('0'))} "
            f"≠ LineTotalAmount {line_total}"
        )

    # BR-CO-13: TaxBasisTotal = LineTotal - AllowanceTotal + ChargeTotal
    expected_basis = line_total - allowance_total + charge_total
    if expected_basis != tax_basis:
        errors.append(
            f"BR-CO-13: TaxBasisTotalAmount {tax_basis} ≠ "
            f"LineTotal - Allowance + Charge = {expected_basis}"
        )

    # Steueraufschlüsselung (nur die der Rechnungswährung, mit CalculatedAmount)
    header_taxes = root.xpath(
        "//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax",
        namespaces=NS,
    )
    calc_sum = Decimal("0")
    for tax in header_taxes:
        calculated = first(tax, "ram:CalculatedAmount")
        basis = first(tax, "ram:BasisAmount")
        rate = first(tax, "ram:RateApplicablePercent")
        category = first(tax, "ram:CategoryCode")
        if calculated is None or basis is None:
            errors.append("BR-45/46: ApplicableTradeTax ohne Basis-/Steuerbetrag")
            continue
        calc_sum += dec(calculated)
        # BR-CO-17: Steuerbetrag = Basis × Satz / 100, kaufmännisch gerundet
        if rate is not None:
            expected = (dec(basis) * dec(rate) / Decimal("100")).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            if abs(dec(calculated) - expected) > CENT:
                errors.append(
                    f"BR-CO-17: Steuerbetrag {dec(calculated)} für Kategorie "
                    f"{category.text if category is not None else '?'} "
                    f"({dec(rate)}%) ≠ erwartet {expected}"
                )
        # BR-E-10 (sinngemäß): Befreiung braucht Begründung
        if category is not None and category.text == "E":
            if first(tax, "ram:ExemptionReason") is None and first(
                tax, "ram:ExemptionReasonCode"
            ) is None:
                errors.append(
                    "BR-E-10: Steuerkategorie E ohne ExemptionReason "
                    "(Befreiungsgrund, z.B. Kleinunternehmer § 19 UStG)"
                )

    # BR-CO-14: TaxTotal = Summe der CalculatedAmounts
    if header_taxes and calc_sum != tax_total:
        errors.append(
            f"BR-CO-14: TaxTotalAmount {tax_total} ≠ Summe Steuerbeträge {calc_sum}"
        )

    # BR-CO-15: GrandTotal = TaxBasis + TaxTotal
    if tax_basis + tax_total != grand_total:
        errors.append(
            f"BR-CO-15: GrandTotalAmount {grand_total} ≠ "
            f"TaxBasis + TaxTotal = {tax_basis + tax_total}"
        )

    # BR-CO-16: DuePayable = GrandTotal - Prepaid
    if due_payable is not None and grand_total - prepaid != due_payable:
        errors.append(
            f"BR-CO-16: DuePayableAmount {due_payable} ≠ "
            f"GrandTotal - Prepaid = {grand_total - prepaid}"
        )

    # BR-CO-26: Verkäufer braucht USt-IdNr. oder Steuernummer
    seller_regs = root.xpath(
        "//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID",
        namespaces=NS,
    )
    if not seller_regs:
        errors.append(
            "BR-CO-26: Verkäufer ohne Steuerregistrierung "
            "(USt-IdNr. oder Steuernummer erforderlich)"
        )

    # Steuerbasis je (Kategorie, Satz) muss Positionssummen entsprechen (BR-S-08)
    group_from_lines: dict[tuple, Decimal] = {}
    for item in root.xpath("//ram:IncludedSupplyChainTradeLineItem", namespaces=NS):
        cat = first(item, ".//ram:ApplicableTradeTax/ram:CategoryCode")
        rate = first(item, ".//ram:ApplicableTradeTax/ram:RateApplicablePercent")
        amount = first(
            item,
            ".//ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
        )
        if cat is None or amount is None:
            continue
        key = (cat.text, dec(rate) if rate is not None else Decimal("0"))
        group_from_lines[key] = group_from_lines.get(key, Decimal("0")) + dec(amount)
    for tax in header_taxes:
        cat = first(tax, "ram:CategoryCode")
        rate = first(tax, "ram:RateApplicablePercent")
        basis = first(tax, "ram:BasisAmount")
        if cat is None or basis is None:
            continue
        key = (cat.text, dec(rate) if rate is not None else Decimal("0"))
        expected = group_from_lines.get(key)
        if expected is not None and expected != dec(basis):
            errors.append(
                f"BR-S-08: BasisAmount {dec(basis)} für Kategorie {key[0]} "
                f"{key[1]}% ≠ Positionssumme {expected}"
            )

    # IBAN-Plausibilität (Warnung, keine EN16931-Regel)
    for iban_node in root.xpath("//ram:IBANID", namespaces=NS):
        if not iban_valid(iban_node.text or ""):
            warnings.append(f"IBAN '{iban_node.text}' besteht die Mod-97-Prüfung nicht")

    # Rechnungskorrektur (384) / Gutschrift (381): Referenz empfohlen
    type_code = first(root, "//rsm:ExchangedDocument/ram:TypeCode")
    if type_code is not None and type_code.text == "384":
        if first(root, "//ram:InvoiceReferencedDocument/ram:IssuerAssignedID") is None:
            errors.append(
                "Rechnungskorrektur (384) ohne InvoiceReferencedDocument "
                "(Referenz auf die Ursprungsrechnung)"
            )

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", help="Pfad zur zu prüfenden Rechnungs-XML")
    parser.add_argument(
        "--schema", default=str(DEFAULT_XSD), help="Pfad zur EN16931-XSD"
    )
    args = parser.parse_args()

    tree = etree.parse(args.xml)
    root = tree.getroot()

    xsd_errors = validate_xsd(tree, Path(args.schema))
    br_errors, br_warnings = validate_business_rules(root)

    print(f"Prüfung von: {args.xml}")
    print(f"1. XSD-Schema (Factur-X 1.09 EN16931): "
          f"{'BESTANDEN' if not xsd_errors else 'FEHLGESCHLAGEN'}")
    for e in xsd_errors:
        print(f"   ✗ {e}")
    print(f"2. EN16931-Geschäftsregeln: "
          f"{'BESTANDEN' if not br_errors else 'FEHLGESCHLAGEN'}")
    for e in br_errors:
        print(f"   ✗ {e}")
    for w in br_warnings:
        print(f"   ⚠ Warnung: {w}")

    if xsd_errors or br_errors:
        print("\nERGEBNIS: UNGÜLTIG — Rechnung darf so nicht versendet werden.")
        sys.exit(1)
    print("\nERGEBNIS: GÜLTIG (Struktur + Rechenregeln geprüft)")


if __name__ == "__main__":
    main()
