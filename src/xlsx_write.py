"""
xlsx_write.py — write a multi-tab .xlsx with live formulas. Pure stdlib.

An .xlsx is a zip of XML parts, so writing one needs nothing but `zipfile`.
This exists so the new season's workbook can be generated to the exact schema
the automation expects, instead of being rebuilt by hand and then debugged when
a column header drifts by one character.

Deliberately minimal: inline strings (no shared-string table), one bold header
style, and real `<f>` formula cells so TOTAL and Player Totals recompute live
in Google Sheets exactly as they do in the current workbook.

Cells are given as Python values:
    str            -> inline string
    int / float    -> number
    Formula("...") -> formula cell (no leading '=')
    None           -> blank
"""

import re
import zipfile
from xml.sax.saxutils import escape


class Formula(str):
    """Marker type: this cell holds a formula, not text."""
    __slots__ = ()


def col_letter(i):
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell_xml(ref, value, style=0):
    if value is None or value == "":
        return ""
    s = ' s="%d"' % style if style else ""
    if isinstance(value, Formula):
        return '<c r="%s"%s><f>%s</f></c>' % (ref, s, escape(str(value)))
    if isinstance(value, bool):
        return '<c r="%s"%s t="b"><v>%d</v></c>' % (ref, s, 1 if value else 0)
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, s, value)
    txt = _CTRL.sub("", str(value))
    return ('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (ref, s, escape(txt)))


def _sheet_xml(rows, header_style=1, freeze_header=True, widths=None):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if freeze_header:
        out.append('<sheetViews><sheetView workbookViewId="0">'
                   '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                   '</sheetView></sheetViews>')
    if widths:
        out.append("<cols>")
        for i, w in enumerate(widths):
            out.append('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
        out.append("</cols>")
    out.append("<sheetData>")
    for r_i, row in enumerate(rows, start=1):
        cells = []
        for c_i, val in enumerate(row):
            style = header_style if (r_i == 1 and header_style) else 0
            xml = _cell_xml("%s%d" % (col_letter(c_i), r_i), val, style)
            if xml:
                cells.append(xml)
        out.append('<row r="%d">%s</row>' % (r_i, "".join(cells)))
    out.append("</sheetData></worksheet>")
    return "".join(out)


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
  <font><sz val="11"/><name val="Calibri"/></font>
  <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
</fonts>
<fills count="3">
  <fill><patternFill patternType="none"/></fill>
  <fill><patternFill patternType="gray125"/></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FF00922E"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""


def write(path, sheets, widths=None):
    """
    sheets: list of (tab_name, rows) where rows is a list of lists.
    widths: optional list of column widths applied to every sheet.
    """
    n = len(sheets)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for i in range(1, n + 1):
        ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i)
    ct.append("</Types>")

    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    for i, (name, _) in enumerate(sheets, start=1):
        safe = escape(name)[:31]
        wb.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (safe, i, i))
    wb.append("</sheets></workbook>")

    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(1, n + 1):
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
                    'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, i))
    rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (n + 1))
    rels.append("</Relationships>")

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                 '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", STYLES)
        for i, (_, rows) in enumerate(sheets, start=1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, _sheet_xml(rows, widths=widths))
    return path
