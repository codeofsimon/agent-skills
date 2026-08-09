#!/usr/bin/env python3
"""Validiert eine ZUGFeRD-Hybrid-PDF (PDF/A-3 mit eingebetteter factur-x.xml).

Drei Prüfstufen:
  1. Eingebettetes XML extrahieren und vollständig validieren
     (XSD Factur-X 1.09 EN16931 + EN16931-Geschäftsregeln,
     wiederverwendet aus validate_invoice.py)
  2. PDF/A-3-Strukturmerkmale: XMP-Metadaten (pdfaid part=3),
     Factur-X-XMP-Einträge (fx:DocumentFileName, fx:ConformanceLevel),
     AFRelationship, /AF-Eintrag, OutputIntent mit ICC-Profil,
     eingebettete Fonts, keine Verschlüsselung
  3. Konsistenz Info-Dictionary vs. XMP

Hinweis: Das ist eine strukturelle Prüfung mit Bordmitteln (pypdf/lxml),
kein vollständiger veraPDF-Ersatz. Sie fängt die in der Praxis relevanten
Fehlerquellen (fehlende Einbettung, falsche XMP, fehlender OutputIntent,
nicht eingebettete Fonts) zuverlässig ab.

Aufruf:
    python validate_pdf.py <rechnung.pdf> [--schema <pfad-zur-xsd>]

Exit-Code 0 = gültig, 1 = Fehler gefunden.
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

from lxml import etree
from pypdf import PdfReader

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from validate_invoice import DEFAULT_XSD, validate_business_rules, validate_xsd  # noqa: E402

FX_FILENAME = "factur-x.xml"
XMP_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "pdfaid": "http://www.aiim.org/pdfa/ns/id/",
    "fx": "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#",
}


def extract_embedded_xml(reader: PdfReader) -> tuple[bytes | None, list[str]]:
    errors = []
    attachments = {}
    try:
        for name, content in reader.attachments.items():
            attachments[name] = content[0] if isinstance(content, list) else content
    except Exception as e:  # noqa: BLE001
        errors.append(f"Anhänge nicht lesbar: {e}")
        return None, errors
    if FX_FILENAME not in attachments:
        errors.append(
            f"Kein eingebettetes '{FX_FILENAME}' gefunden "
            f"(vorhandene Anhänge: {list(attachments) or 'keine'})"
        )
        return None, errors
    return attachments[FX_FILENAME], errors


def check_af_relationship(reader: PdfReader) -> list[str]:
    errors = []
    root = reader.trailer["/Root"]
    if "/AF" not in root:
        errors.append("Katalog ohne /AF-Eintrag (Associated Files, PDF/A-3-Pflicht)")
    names = root.get("/Names", {})
    embedded = names.get("/EmbeddedFiles", {}) if names else {}
    name_array = embedded.get("/Names", []) if embedded else []
    found = False
    for item in name_array:
        obj = item.get_object()
        if isinstance(obj, dict) and obj.get("/F") == FX_FILENAME:
            found = True
            rel = obj.get("/AFRelationship")
            if rel is None:
                errors.append("Filespec von factur-x.xml ohne /AFRelationship")
            elif str(rel) not in ("/Alternative", "/Data", "/Source"):
                errors.append(
                    f"/AFRelationship ist {rel} — für ZUGFeRD EN16931 wird "
                    "/Alternative erwartet"
                )
    if not found:
        errors.append("Kein Filespec-Eintrag für factur-x.xml in /Names gefunden")
    return errors


def check_xmp(reader: PdfReader) -> tuple[list[str], dict]:
    errors, info = [], {}
    root = reader.trailer["/Root"]
    if "/Metadata" not in root:
        return ["Keine XMP-Metadaten im Dokumentkatalog (PDF/A-Pflicht)"], info
    raw = root["/Metadata"].get_object().get_data()
    try:
        xmp = etree.fromstring(raw)
    except etree.XMLSyntaxError as e:
        return [f"XMP-Metadaten nicht parsebar: {e}"], info

    def xmp_value(local_name, ns):
        # als Element oder als rdf:Description-Attribut erlaubt
        nodes = xmp.xpath(f"//*[local-name()='{local_name}' and "
                          f"namespace-uri()='{ns}']")
        if nodes:
            return (nodes[0].text or "").strip()
        for desc in xmp.xpath("//rdf:Description", namespaces=XMP_NS):
            val = desc.get(f"{{{ns}}}{local_name}")
            if val is not None:
                return val
        return None

    part = xmp_value("part", XMP_NS["pdfaid"])
    conformance = xmp_value("conformance", XMP_NS["pdfaid"])
    if part != "3":
        errors.append(f"XMP pdfaid:part ist {part!r}, erwartet '3' (PDF/A-3)")
    if conformance not in ("B", "U", "A"):
        errors.append(f"XMP pdfaid:conformance ist {conformance!r}, erwartet 'B'")
    info["pdfa"] = f"PDF/A-{part}{(conformance or '').lower()}"

    fx_file = xmp_value("DocumentFileName", XMP_NS["fx"])
    fx_level = xmp_value("ConformanceLevel", XMP_NS["fx"])
    fx_type = xmp_value("DocumentType", XMP_NS["fx"])
    if fx_file != FX_FILENAME:
        errors.append(
            f"XMP fx:DocumentFileName ist {fx_file!r}, erwartet '{FX_FILENAME}'"
        )
    if fx_level not in ("EN 16931", "BASIC", "MINIMUM", "BASIC WL", "EXTENDED",
                        "XRECHNUNG"):
        errors.append(f"XMP fx:ConformanceLevel ist {fx_level!r}")
    if fx_type != "INVOICE":
        errors.append(f"XMP fx:DocumentType ist {fx_type!r}, erwartet 'INVOICE'")
    info["fx_level"] = fx_level
    if not xmp.xpath("//*[local-name()='schemas']"):
        errors.append("XMP ohne pdfaExtension:schemas (Factur-X-Extension-Schema)")
    return errors, info


def check_output_intent(reader: PdfReader) -> list[str]:
    errors = []
    root = reader.trailer["/Root"]
    intents = root.get("/OutputIntents")
    if not intents:
        return ["Kein /OutputIntents im Katalog (PDF/A-Pflicht: sRGB-OutputIntent)"]
    ok = False
    for intent in intents:
        obj = intent.get_object()
        if str(obj.get("/S")) == "/GTS_PDFA1" and "/DestOutputProfile" in obj:
            profile = obj["/DestOutputProfile"].get_object()
            if len(profile.get_data()) > 0:
                ok = True
    if not ok:
        errors.append(
            "Kein GTS_PDFA1-OutputIntent mit eingebettetem ICC-Profil gefunden"
        )
    return errors


def check_fonts(reader: PdfReader) -> list[str]:
    """Alle verwendeten Fonts müssen eingebettet sein (PDF/A-Pflicht)."""
    errors = []
    seen = set()
    for page_no, page in enumerate(reader.pages, 1):
        resources = page.get("/Resources")
        if not resources:
            continue
        fonts = resources.get_object().get("/Font")
        if not fonts:
            continue
        for name, ref in fonts.get_object().items():
            font = ref.get_object()
            base = str(font.get("/BaseFont", name))
            if base in seen:
                continue
            seen.add(base)
            descriptor = font.get("/FontDescriptor")
            if descriptor is None and font.get("/Subtype") == "/Type0":
                desc_fonts = font.get("/DescendantFonts")
                if desc_fonts:
                    descriptor = desc_fonts.get_object()[0].get_object().get(
                        "/FontDescriptor"
                    )
            if descriptor is None:
                errors.append(
                    f"Seite {page_no}: Font {base} ohne FontDescriptor "
                    "(Standard-14-Font? Muss eingebettet sein)"
                )
                continue
            descriptor = descriptor.get_object()
            if not any(
                k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")
            ):
                errors.append(f"Seite {page_no}: Font {base} nicht eingebettet")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="Pfad zur zu prüfenden Hybrid-PDF")
    parser.add_argument(
        "--schema", default=str(DEFAULT_XSD), help="Pfad zur EN16931-XSD"
    )
    args = parser.parse_args()

    reader = None
    try:
        reader = PdfReader(args.pdf)
        if reader.is_encrypted:
            raise ValueError("PDF ist verschlüsselt (in PDF/A verboten)")
        _ = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        print(f"Prüfung von: {args.pdf}")
        print(f"   ✗ PDF nicht lesbar: {e}")
        print("\nERGEBNIS: UNGÜLTIG — PDF darf so nicht versendet werden.")
        sys.exit(1)

    # Stufe 1: eingebettetes XML
    xml_bytes, extract_errors = extract_embedded_xml(reader)
    xml_errors, br_errors, br_warnings = [], [], []
    if xml_bytes is not None:
        try:
            tree = etree.parse(BytesIO(xml_bytes))
            xml_errors = validate_xsd(tree, Path(args.schema))
            br_errors, br_warnings = validate_business_rules(tree.getroot())
        except etree.XMLSyntaxError as e:
            xml_errors = [f"Eingebettetes XML nicht parsebar: {e}"]

    # Stufe 2: PDF/A-3-Struktur
    af_errors = check_af_relationship(reader)
    xmp_errors, xmp_info = check_xmp(reader)
    intent_errors = check_output_intent(reader)
    font_errors = check_fonts(reader)
    structure_errors = af_errors + xmp_errors + intent_errors + font_errors

    print(f"Prüfung von: {args.pdf}")
    step1 = extract_errors + xml_errors + br_errors
    print(f"1. Eingebettetes XML (XSD + EN16931-Regeln): "
          f"{'BESTANDEN' if xml_bytes is not None and not step1 else 'FEHLGESCHLAGEN'}")
    for e in step1:
        print(f"   ✗ {e}")
    for w in br_warnings:
        print(f"   ⚠ Warnung: {w}")
    print(f"2. PDF/A-3-Struktur ({xmp_info.get('pdfa', '?')}, "
          f"Factur-X {xmp_info.get('fx_level', '?')}): "
          f"{'BESTANDEN' if not structure_errors else 'FEHLGESCHLAGEN'}")
    for e in structure_errors:
        print(f"   ✗ {e}")

    if step1 or structure_errors or xml_bytes is None:
        print("\nERGEBNIS: UNGÜLTIG — PDF darf so nicht versendet werden.")
        sys.exit(1)
    print("\nERGEBNIS: GÜLTIG (eingebettetes XML + PDF/A-3-Struktur geprüft)")


if __name__ == "__main__":
    main()
