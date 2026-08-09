# agent-skills

Public repo for sharing [Claude Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) — public copies of skills that otherwise live in a private repo.

## Enthaltene Skills

### `e-rechnung`

Erzeugt normkonforme deutsche E-Rechnungen nach **ZUGFeRD 2.5 / Factur-X 1.09** im Profil **EN16931** — als CII-XML und als ZUGFeRD-Hybrid-PDF (PDF/A-3 mit eingebettetem XML). Deckt Inlandsrechnungen (19 %/7 %/0 %), Kleinunternehmer nach § 19 UStG, Rechnungskorrekturen und Gutschriften ab.

Alle Berechnungen (Beträge, Steuern, Summen) laufen deterministisch über Python-Skripte (`decimal`), nie über das LLM selbst — XML und PDF werden gegen XSD-Schema bzw. EN16931-Geschäftsregeln und PDF/A-3-Struktur validiert, bevor sie ausgegeben werden.

Die vollständige Funktionsweise, den Workflow und alle Regeln beschreibt [`SKILL.md`](./SKILL.md).

**Nutzung mit Claude Code:**
1. Ordnerinhalt in dein Skills-Verzeichnis kopieren (oder Repo klonen und dorthin verlinken).
2. `assets/seller_profile.yaml` auf die eigenen Firmenstammdaten anpassen — aktuell nur Platzhalterdaten ("Muster GmbH").
3. Python-Abhängigkeiten installieren: `jinja2`, `lxml`, `pypdf`, `reportlab`, `pyyaml`.
4. Claude Code fragt bei Bedarf nach den Rechnungsdaten und ruft die Skripte in `scripts/` selbst auf — siehe `SKILL.md` für den genauen Ablauf.

**Grenzen:** kein Reverse Charge, keine innergemeinschaftlichen Lieferungen/Exporte, keine Fremdwährungen, kein eigenes Corporate Design in der PDF, nur Profil EN16931. Details siehe `SKILL.md`.

Beispiel-Dateien (generierte XML/PDF-Paare) liegen unter `references/beispiele/`.

## Hinweis

Dieses Repo ist ein manuell gepflegter Spiegel eines internen privaten Repos — kein automatischer Sync. Bei Änderungen am Original kann der Stand hier zurückliegen.
