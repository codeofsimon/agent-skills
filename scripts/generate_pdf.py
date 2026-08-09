#!/usr/bin/env python3
"""Erzeugt die ZUGFeRD-Hybrid-PDF (PDF/A-3) mit eingebetteter factur-x.xml.

Die PDF ist die menschenlesbare Darstellung derselben Rechnungsdaten, aus
denen zuvor `generate_invoice.py` das CII-XML erzeugt hat. Das XML wird
als `factur-x.xml` PDF/A-3-konform eingebettet (AFRelationship=Alternative,
XMP-Metadaten mit Factur-X-Extension-Schema, sRGB-OutputIntent,
eingebettete Fonts). Alle Beträge stammen aus `build_context` von
`generate_invoice.py` — es wird nichts neu oder anders gerechnet.

Reihenfolge (siehe SKILL.md):
    1. python generate_invoice.py input.json --output factur-x.xml
    2. python validate_invoice.py factur-x.xml        (muss GÜLTIG sein!)
    3. python generate_pdf.py input.json --xml factur-x.xml --output rechnung.pdf
    4. python validate_pdf.py rechnung.pdf

Aufruf:
    python generate_pdf.py <input.json> [--xml <factur-x.xml>]
                           [--seller <seller_profile.yaml>]
                           [--output <rechnung.pdf>]

Benötigt nur reportlab + pypdf (in der Sandbox vorhanden); Fonts und
ICC-Profil liegen gebündelt in assets/fonts bzw. assets/icc.
"""

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR.parent / "assets"
FONT_REGULAR = ASSETS_DIR / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf"
ICC_PROFILE = ASSETS_DIR / "icc" / "sRGB.icc"

sys.path.insert(0, str(SCRIPT_DIR))
from generate_invoice import TYPE_CODES, build_context, check_required, fail  # noqa: E402

# Factur-X-Konstanten (Profil EN16931)
FX_FILENAME = "factur-x.xml"
FX_CONFORMANCE = "EN 16931"
FX_NAMESPACE = "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#"
PRODUCER = "e-rechnung skill (reportlab + pypdf)"

DOC_TITLES = {"380": "Rechnung", "381": "Gutschrift", "384": "Rechnungskorrektur"}

UNIT_LABELS = {
    "H87": "Stk.", "XPP": "Stk.", "C62": "Einh.", "HUR": "Std.", "DAY": "Tag(e)",
    "WEE": "Woche(n)", "MON": "Monat(e)", "ANN": "Jahr(e)", "MIN": "min",
    "KGM": "kg", "GRM": "g", "TNE": "t", "MTR": "m", "MTK": "m²", "MTQ": "m³",
    "LTR": "l", "KMT": "km", "KWH": "kWh", "SET": "Set", "PK": "Pack",
    "P1": "%", "LS": "pauschal",
}

TAX_CATEGORY_LABELS = {
    "S": "Umsatzsteuer", "Z": "Umsatzsteuer (Nullsatz)", "E": "steuerbefreit",
}


# ---------------------------------------------------------------------------
# Formatierung (nur Darstellung — Werte kommen fertig gerechnet aus context)
# ---------------------------------------------------------------------------

def de_number(value: str, min_decimals: int = 2) -> str:
    """'1234.50' → '1.234,50' (deutsche Zahldarstellung, keine Rundung).

    Überflüssige Nachkommanullen jenseits von min_decimals werden entfernt
    (z.B. '120.0000' → '120,00', '0.1234' → '0,1234').
    """
    d = Decimal(value)
    sign, digits, exponent = d.as_tuple()
    decimals = -exponent if exponent < 0 else 0
    while decimals > min_decimals and d == d.quantize(
        Decimal(1).scaleb(-(decimals - 1))
    ):
        decimals -= 1
    decimals = max(min_decimals, decimals)
    q = f"{d:.{decimals}f}"
    integer, _, frac = q.partition(".")
    neg = integer.startswith("-")
    integer = integer.lstrip("-")
    grouped = "{:,}".format(int(integer)).replace(",", ".")
    return ("-" if neg else "") + grouped + ("," + frac if frac else "")


def de_money(value: str, currency: str) -> str:
    symbol = "€" if currency == "EUR" else currency
    return f"{de_number(value, 2)} {symbol}"


def de_qty(value: str) -> str:
    d = Decimal(value).normalize()
    if d == d.to_integral_value():
        return de_number(str(d.to_integral_value()), 0)
    return de_number(str(d), 0)


def de_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def date_102_to_de(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}.{yyyymmdd[4:6]}.{yyyymmdd[0:4]}"


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Schritt 1: Visuelle Rechnung mit reportlab (alle Fonts eingebettet)
# ---------------------------------------------------------------------------

def register_fonts():
    for path in (FONT_REGULAR, FONT_BOLD):
        if not path.exists():
            fail(f"Font fehlt: {path} (assets/fonts muss mitgeliefert werden)")
    pdfmetrics.registerFont(TTFont("Invoice", str(FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont("Invoice-Bold", str(FONT_BOLD)))


def build_visual_pdf(data: dict, ctx: dict, meta: dict) -> bytes:
    register_fonts()
    seller = ctx["seller"]
    currency = ctx["currency"]
    title = DOC_TITLES.get(ctx["type_code"], "Rechnung")

    base = ParagraphStyle("base", fontName="Invoice", fontSize=9, leading=12)
    bold = ParagraphStyle("bold", base, fontName="Invoice-Bold")
    h1 = ParagraphStyle("h1", base, fontName="Invoice-Bold", fontSize=14, leading=18)
    small = ParagraphStyle("small", base, fontSize=7, leading=9)
    cell = ParagraphStyle("cell", base, fontSize=9, leading=11)
    cell_desc = ParagraphStyle(
        "cell_desc", cell, fontSize=8, textColor=colors.HexColor("#444444")
    )

    page_w, page_h = A4
    margin_l, margin_r, margin_bottom = 25 * mm, 20 * mm, 32 * mm
    content_w = page_w - margin_l - margin_r

    # --- Kopf der ersten Seite (Briefkopf, Anschriftfeld, Infoblock) -------
    def draw_first_page(canvas, doc):
        canvas.saveState()
        addr = seller["address"]
        # Briefkopf rechts oben
        y = page_h - 18 * mm
        canvas.setFont("Invoice-Bold", 11)
        canvas.drawRightString(page_w - margin_r, y, seller["name"])
        canvas.setFont("Invoice", 8)
        head_lines = [
            addr["street"],
            f"{addr['postcode']} {addr['city']}",
        ]
        contact = seller.get("contact") or {}
        if contact.get("phone"):
            head_lines.append(f"Tel. {contact['phone']}")
        if contact.get("email"):
            head_lines.append(contact["email"])
        if seller.get("vat_id"):
            head_lines.append(f"USt-IdNr. {seller['vat_id']}")
        elif seller.get("tax_number"):
            head_lines.append(f"Steuernr. {seller['tax_number']}")
        for line in head_lines:
            y -= 4 * mm
            canvas.drawRightString(page_w - margin_r, y, line)

        # Anschriftfeld (DIN-5008-Position)
        y_addr = page_h - 50 * mm
        canvas.setFont("Invoice", 6.5)
        canvas.drawString(
            margin_l, y_addr,
            f"{seller['name']} · {addr['street']} · {addr['postcode']} {addr['city']}",
        )
        canvas.line(margin_l, y_addr - 1.5 * mm, margin_l + 75 * mm, y_addr - 1.5 * mm)
        buyer = ctx["buyer"]
        canvas.setFont("Invoice", 10)
        yb = y_addr - 8 * mm
        buyer_lines = [
            buyer["name"],
            buyer["address"]["street"],
            f"{buyer['address']['postcode']} {buyer['address']['city']}",
        ]
        if buyer["address"]["country"] != seller["address"]["country"]:
            buyer_lines.append(buyer["address"]["country"])
        for line in buyer_lines:
            canvas.drawString(margin_l, yb, line)
            yb -= 5 * mm

        # Infoblock rechts
        info = [("Rechnungs-Nr.", ctx["invoice_number"]) if title == "Rechnung"
                else (f"{title}s-Nr.", ctx["invoice_number"])]
        info.append(("Datum", date_102_to_de(ctx["issue_date"])))
        if ctx["delivery_date"]:
            info.append(("Leistungsdatum", date_102_to_de(ctx["delivery_date"])))
        if ctx["payment"]["due_date"]:
            info.append(("Fällig am", date_102_to_de(ctx["payment"]["due_date"])))
        if ctx["referenced_invoice"]:
            ref = ctx["referenced_invoice"]
            val = ref["id"]
            if ref["issue_date"]:
                val += f" vom {date_102_to_de(ref['issue_date'])}"
            info.append(("Zu Rechnung", val))
        if ctx["buyer_reference"]:
            info.append(("Käufer-Referenz", ctx["buyer_reference"]))
        if ctx["order_reference"]:
            info.append(("Bestell-Nr.", ctx["order_reference"]))
        if ctx["buyer"]["vat_id"]:
            info.append(("USt-IdNr. Käufer", ctx["buyer"]["vat_id"]))
        yi = y_addr - 8 * mm
        for label, value in info:
            canvas.setFont("Invoice", 9)
            canvas.drawString(page_w - margin_r - 78 * mm, yi, label)
            canvas.setFont("Invoice-Bold", 9)
            canvas.drawRightString(page_w - margin_r, yi, str(value))
            yi -= 5 * mm

        draw_footer(canvas, doc)
        canvas.restoreState()

    def draw_footer(canvas, doc):
        canvas.saveState()
        addr = seller["address"]
        legal = seller.get("legal_info") or {}
        pay = ctx["payment"]
        col1 = [seller["name"], addr["street"], f"{addr['postcode']} {addr['city']}"]
        if seller.get("vat_id"):
            col1.append(f"USt-IdNr. {seller['vat_id']}")
        if seller.get("tax_number"):
            col1.append(f"Steuernr. {seller['tax_number']}")
        col2 = []
        if legal.get("registration"):
            col2.append(legal["registration"])
        if legal.get("managing_director"):
            col2.append(f"Geschäftsführung: {legal['managing_director']}")
        contact = seller.get("contact") or {}
        if contact.get("email"):
            col2.append(contact["email"])
        col3 = ["Bankverbindung:"]
        if pay["account_name"]:
            col3.append(pay["account_name"])
        if pay["iban"]:
            col3.append(f"IBAN {pay['iban']}")
        if pay["bic"]:
            col3.append(f"BIC {pay['bic']}")
        canvas.setLineWidth(0.3)
        canvas.line(margin_l, 26 * mm, page_w - margin_r, 26 * mm)
        canvas.setFont("Invoice", 6.5)
        for i, col in enumerate((col1, col2, col3)):
            x = margin_l + i * (content_w / 3)
            y = 23 * mm
            for line in col:
                canvas.drawString(x, y, line)
                y -= 3 * mm
        canvas.drawRightString(
            page_w - margin_r, 10 * mm, f"Seite {canvas.getPageNumber()}"
        )
        canvas.restoreState()

    def draw_later_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Invoice", 8)
        canvas.drawString(
            margin_l, page_h - 15 * mm,
            f"{title} {ctx['invoice_number']} — {seller['name']}",
        )
        draw_footer(canvas, doc)
        canvas.restoreState()

    first_frame = Frame(
        margin_l, margin_bottom, content_w, page_h - 118 * mm - margin_bottom,
        id="first",
    )
    later_frame = Frame(
        margin_l, margin_bottom, content_w, page_h - 25 * mm - margin_bottom,
        id="later",
    )

    buf = BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        title=meta["title"],
        author=meta["author"],
        subject=meta["subject"],
        creator=PRODUCER,
        # verhindert eine nicht eingebettete Helvetica in der Canvas-Präambel
        initialFontName="Invoice",
        pageTemplates=[
            PageTemplate(id="First", frames=[first_frame], onPage=draw_first_page),
            PageTemplate(id="Later", frames=[later_frame], onPage=draw_later_page),
        ],
    )

    story = [Paragraph(f"{title} Nr. {xml_escape(ctx['invoice_number'])}", h1),
             Spacer(0, 4 * mm)]

    # --- Positionstabelle --------------------------------------------------
    rows = [["Pos.", "Bezeichnung", "Menge", "Einheit",
             "Einzelpreis", "USt.", "Gesamt (netto)"]]
    for line in ctx["lines"]:
        desc = f"<b>{xml_escape(line['name'])}</b>"
        if line["seller_assigned_id"]:
            desc += f"<br/><font size='7'>Art.-Nr. {xml_escape(line['seller_assigned_id'])}</font>"
        name_para = [Paragraph(desc, cell)]
        if line["description"]:
            name_para.append(Paragraph(xml_escape(line["description"]), cell_desc))
        rows.append([
            line["line_id"],
            name_para,
            de_qty(line["quantity"]),
            UNIT_LABELS.get(line["unit_code"], line["unit_code"]),
            de_money(line["net_price"], currency),
            f"{de_qty(line['tax_rate'])} %",
            de_money(line["line_total"], currency),
        ])
    table = Table(
        rows,
        colWidths=[10 * mm, content_w - 111 * mm, 15 * mm, 16 * mm,
                   26 * mm, 14 * mm, 30 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Invoice-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Invoice", 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (3, 0), (3, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, colors.HexColor("#bbbbbb")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
    ]))
    story.append(table)
    story.append(Spacer(0, 5 * mm))

    # --- Summenblock (Werte 1:1 aus context.totals) -------------------------
    t = ctx["totals"]
    sum_rows = [["Summe netto", de_money(t["tax_basis"], currency)]]
    for tax in ctx["tax_breakdown"]:
        if tax["category"] == "S":
            label = (f"zzgl. {de_qty(tax['rate'])} % USt. "
                     f"auf {de_money(tax['basis'], currency)}")
        else:
            label = (f"{TAX_CATEGORY_LABELS.get(tax['category'], 'USt.')} "
                     f"({de_qty(tax['rate'])} %)")
        sum_rows.append([label, de_money(tax["calculated"], currency)])
    sum_rows.append(["Gesamtbetrag", de_money(t["grand_total"], currency)])
    bold_rows = [len(sum_rows) - 1]
    if Decimal(t["prepaid"]) != 0:
        sum_rows.append(["abzüglich Anzahlung", de_money(t["prepaid"], currency)])
        sum_rows.append(["Zahlbetrag", de_money(t["due_payable"], currency)])
        bold_rows.append(len(sum_rows) - 1)
    sum_table = Table(sum_rows, colWidths=[content_w - 40 * mm, 40 * mm], hAlign="RIGHT")
    sum_style = [
        ("FONT", (0, 0), (-1, -1), "Invoice", 9),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
    ]
    for r in bold_rows:
        sum_style.append(("FONT", (0, r), (-1, r), "Invoice-Bold", 10))
        sum_style.append(("LINEABOVE", (0, r), (-1, r), 0.6, colors.black))
    sum_table.setStyle(TableStyle(sum_style))
    story.append(sum_table)
    story.append(Spacer(0, 6 * mm))

    # --- Steuerbefreiung, Zahlungsbedingungen, Notizen ----------------------
    for tax in ctx["tax_breakdown"]:
        if tax.get("exemption_reason"):
            story.append(Paragraph(xml_escape(tax["exemption_reason"]), base))
            story.append(Spacer(0, 2 * mm))
    pay = ctx["payment"]
    if pay["terms_description"]:
        story.append(Paragraph(xml_escape(pay["terms_description"]) + ".", base))
        story.append(Spacer(0, 2 * mm))
    if pay["iban"]:
        pay_line = (f"Bitte überweisen Sie den Betrag auf IBAN {pay['iban']}"
                    + (f" (BIC {pay['bic']})" if pay["bic"] else "")
                    + f", Verwendungszweck: {ctx['invoice_number']}.")
        story.append(Paragraph(xml_escape(pay_line), base))
        story.append(Spacer(0, 2 * mm))
    for note in ctx["notes"]:
        if note.get("subject_code") == "REG":
            continue  # steht bereits in der Fußzeile
        story.append(Paragraph(xml_escape(note["content"]), base))
        story.append(Spacer(0, 2 * mm))
    story.append(Spacer(0, 2 * mm))
    story.append(Paragraph(
        "Dieses Dokument ist eine ZUGFeRD-/Factur-X-Hybridrechnung: Die "
        "maschinenlesbare Rechnung (factur-x.xml, EN 16931) ist in dieser "
        "PDF/A-3-Datei eingebettet.", small))

    def switch_template(canvas, doc):
        doc.handle_nextPageTemplate("Later")

    doc.pageTemplates[0].onPageEnd = switch_template
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Schritt 2: PDF/A-3-Post-Processing mit pypdf
# ---------------------------------------------------------------------------

def build_xmp(meta: dict, now_iso: str) -> bytes:
    """XMP-Paket mit PDF/A-3b-Kennung und Factur-X-Extension-Schema."""
    fx_properties = "".join(
        f"""
        <rdf:li rdf:parseType="Resource">
         <pdfaProperty:name>{name}</pdfaProperty:name>
         <pdfaProperty:valueType>Text</pdfaProperty:valueType>
         <pdfaProperty:category>external</pdfaProperty:category>
         <pdfaProperty:description>{desc}</pdfaProperty:description>
        </rdf:li>"""
        for name, desc in (
            ("DocumentFileName", "Name of the embedded XML invoice file"),
            ("DocumentType", "INVOICE"),
            ("Version", "The actual version of the Factur-X XML schema"),
            ("ConformanceLevel", "The conformance level of the embedded Factur-X data"),
        )
    )
    xmp = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{xml_escape(meta["title"])}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{xml_escape(meta["author"])}</rdf:li></rdf:Seq></dc:creator>
   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{xml_escape(meta["subject"])}</rdf:li></rdf:Alt></dc:description>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
   <pdf:Producer>{xml_escape(PRODUCER)}</pdf:Producer>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>{xml_escape(PRODUCER)}</xmp:CreatorTool>
   <xmp:CreateDate>{now_iso}</xmp:CreateDate>
   <xmp:ModifyDate>{now_iso}</xmp:ModifyDate>
  </rdf:Description>
  <rdf:Description rdf:about=""
    xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
    xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
    xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
   <pdfaExtension:schemas>
    <rdf:Bag>
     <rdf:li rdf:parseType="Resource">
      <pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
      <pdfaSchema:namespaceURI>{FX_NAMESPACE}</pdfaSchema:namespaceURI>
      <pdfaSchema:prefix>fx</pdfaSchema:prefix>
      <pdfaSchema:property>
       <rdf:Seq>{fx_properties}
       </rdf:Seq>
      </pdfaSchema:property>
     </rdf:li>
    </rdf:Bag>
   </pdfaExtension:schemas>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:fx="{FX_NAMESPACE}">
   <fx:DocumentType>INVOICE</fx:DocumentType>
   <fx:DocumentFileName>{FX_FILENAME}</fx:DocumentFileName>
   <fx:Version>1.0</fx:Version>
   <fx:ConformanceLevel>{FX_CONFORMANCE}</fx:ConformanceLevel>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xmp.encode("utf-8")


def pdf_date(dt: datetime) -> str:
    return dt.strftime("D:%Y%m%d%H%M%S+00'00'")


def make_pdfa3(visual_pdf: bytes, xml_bytes: bytes, meta: dict,
               now: datetime) -> PdfWriter:
    reader = PdfReader(BytesIO(visual_pdf))
    writer = PdfWriter(clone_from=reader)
    root = writer.root_object

    # 1. XMP-Metadaten (unkomprimiert, PDF/A-Pflicht)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    xmp_stream = DecodedStreamObject()
    xmp_stream.set_data(build_xmp(meta, now_iso))
    xmp_stream[NameObject("/Type")] = NameObject("/Metadata")
    xmp_stream[NameObject("/Subtype")] = NameObject("/XML")
    root[NameObject("/Metadata")] = writer._add_object(xmp_stream)

    # Info-Dictionary konsistent zur XMP halten (PDF/A-Anforderung)
    writer.add_metadata({
        "/Title": meta["title"],
        "/Author": meta["author"],
        "/Subject": meta["subject"],
        "/Creator": PRODUCER,
        "/Producer": PRODUCER,
        "/CreationDate": pdf_date(now),
        "/ModDate": pdf_date(now),
    })

    # 2. factur-x.xml als EmbeddedFile (AFRelationship=Alternative)
    ef_stream = DecodedStreamObject()
    ef_stream.set_data(xml_bytes)
    ef_stream[NameObject("/Type")] = NameObject("/EmbeddedFile")
    # pypdf escaped Sonderzeichen beim Schreiben selbst → roher Name mit "/"
    ef_stream[NameObject("/Subtype")] = NameObject("/text/xml")
    ef_stream[NameObject("/Params")] = DictionaryObject({
        NameObject("/Size"): NumberObject(len(xml_bytes)),
        NameObject("/ModDate"): TextStringObject(pdf_date(now)),
        NameObject("/CheckSum"): ByteStringObject(hashlib.md5(xml_bytes).digest()),
    })
    ef_ref = writer._add_object(ef_stream)

    filespec = DictionaryObject({
        NameObject("/Type"): NameObject("/Filespec"),
        NameObject("/F"): TextStringObject(FX_FILENAME),
        NameObject("/UF"): TextStringObject(FX_FILENAME),
        NameObject("/Desc"): TextStringObject(
            "Factur-X/ZUGFeRD-Rechnung (CII-XML, Profil EN 16931)"
        ),
        NameObject("/AFRelationship"): NameObject("/Alternative"),
        NameObject("/EF"): DictionaryObject({
            NameObject("/F"): ef_ref,
            NameObject("/UF"): ef_ref,
        }),
    })
    fs_ref = writer._add_object(filespec)

    root[NameObject("/Names")] = DictionaryObject({
        NameObject("/EmbeddedFiles"): DictionaryObject({
            NameObject("/Names"): ArrayObject(
                [TextStringObject(FX_FILENAME), fs_ref]
            ),
        }),
    })
    root[NameObject("/AF")] = ArrayObject([fs_ref])

    # 3. OutputIntent mit sRGB-ICC-Profil (PDF/A-Pflicht)
    if not ICC_PROFILE.exists():
        fail(f"ICC-Profil fehlt: {ICC_PROFILE}")
    icc_stream = DecodedStreamObject()
    icc_stream.set_data(ICC_PROFILE.read_bytes())
    icc_stream[NameObject("/N")] = NumberObject(3)
    icc_ref = writer._add_object(icc_stream)
    output_intent = DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"),
        NameObject("/S"): NameObject("/GTS_PDFA1"),
        NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB"),
        NameObject("/Info"): TextStringObject("sRGB IEC61966-2.1"),
        NameObject("/RegistryName"): TextStringObject("http://www.color.org"),
        NameObject("/DestOutputProfile"): icc_ref,
    })
    root[NameObject("/OutputIntents")] = ArrayObject(
        [writer._add_object(output_intent)]
    )

    # 4. Datei-ID und PDF-Version
    file_hash = hashlib.md5(visual_pdf + xml_bytes).digest()
    writer._ID = ArrayObject(
        [ByteStringObject(file_hash), ByteStringObject(file_hash)]
    )
    return writer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Pfad zur input.json mit den Rechnungsdaten")
    parser.add_argument(
        "--xml", default=FX_FILENAME,
        help="Pfad zum bereits generierten und validierten CII-XML",
    )
    parser.add_argument(
        "--seller", default=str(ASSETS_DIR / "seller_profile.yaml"),
        help="Pfad zur Verkäufer-Stammdatendatei (YAML)",
    )
    parser.add_argument(
        "--output", default="rechnung.pdf", help="Pfad der erzeugten Hybrid-PDF"
    )
    args = parser.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.exists():
        fail(
            f"XML nicht gefunden: {xml_path}\n"
            "Zuerst generieren und validieren:\n"
            "  python generate_invoice.py input.json --output factur-x.xml\n"
            "  python validate_invoice.py factur-x.xml"
        )
    xml_bytes = xml_path.read_bytes()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    with open(args.seller, encoding="utf-8") as f:
        seller_cfg = yaml.safe_load(f)

    check_required(data, seller_cfg)
    ctx = build_context(data, seller_cfg)

    # Konsistenz-Schutz: XML muss zu input.json gehören
    if f"<ram:ID>{ctx['invoice_number']}</ram:ID>".encode() not in xml_bytes:
        fail(
            f"Die Rechnungsnummer {ctx['invoice_number']} aus {args.input} kommt "
            f"in {xml_path} nicht vor — XML und Eingabedaten passen nicht zusammen. "
            "XML neu generieren."
        )

    title_word = DOC_TITLES.get(ctx["type_code"], "Rechnung")
    meta = {
        "title": f"{title_word} {ctx['invoice_number']}",
        "author": ctx["seller"]["name"],
        "subject": (
            f"{title_word} {ctx['invoice_number']} von {ctx['seller']['name']} "
            f"an {ctx['buyer']['name']} (Factur-X {FX_CONFORMANCE})"
        ),
    }

    visual_pdf = build_visual_pdf(data, ctx, meta)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    writer = make_pdfa3(visual_pdf, xml_bytes, meta, now)

    out = Path(args.output)
    with open(out, "wb") as f:
        writer.write(f)

    t = ctx["totals"]
    print(f"ZUGFeRD-Hybrid-PDF erzeugt: {out}")
    print(f"  Format:        PDF/A-3b mit eingebetteter {FX_FILENAME}")
    print(f"  Profil:        Factur-X 1.09 / ZUGFeRD 2.5, {FX_CONFORMANCE}")
    print(f"  Rechnungsnr.:  {ctx['invoice_number']} (Typ {ctx['type_code']})")
    print(f"  Zahlbetrag:    {t['due_payable']} {ctx['currency']}")
    print("WICHTIG: Jetzt prüfen mit: python validate_pdf.py " + str(out))


if __name__ == "__main__":
    main()
