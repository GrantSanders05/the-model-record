"""
xlsx.py — minimal read-only .xlsx reader, pure stdlib.

An .xlsx file is a zip of XML parts, so `zipfile` + `ElementTree` is all it
takes to read one. Doing it this way keeps the project's promise that nothing
outside the standard library can affect a result — and it means Grant can drop
his workbook in as-is instead of exporting fourteen tabs to CSV by hand.

Handles what a Google Sheets export actually produces: shared strings, inline
strings, cached formula values, blank cells, and ragged rows. Does not handle
styles, dates as serial numbers, or anything to do with writing.
"""

import re
import zipfile
from xml.etree import ElementTree as ET

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_CELL_RE = re.compile(r"([A-Z]+)(\d+)")


def _col_index(ref):
    """'A'->0, 'B'->1, 'AA'->26."""
    letters = _CELL_RE.match(ref).group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


class Workbook:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self._shared = self._read_shared_strings()
        self._sheets = self._read_sheet_index()

    def _read_shared_strings(self):
        if "xl/sharedStrings.xml" not in self.z.namelist():
            return []
        root = ET.fromstring(self.z.read("xl/sharedStrings.xml"))
        out = []
        for si in root.findall("m:si", NS):
            # A string can be split across several <t> runs; join them all.
            out.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        return out

    def _read_sheet_index(self):
        rels = {}
        root = ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))
        for rel in root:
            rels[rel.get("Id")] = rel.get("Target").lstrip("/")
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        out = []
        for sh in wb.find("m:sheets", NS):
            rid = sh.get("{%s}id" % NS["r"])
            target = rels.get(rid, "")
            if not target.startswith("xl/"):
                target = "xl/" + target
            out.append((sh.get("name"), target))
        return out

    @property
    def sheet_names(self):
        return [n for n, _ in self._sheets]

    def rows(self, sheet_name):
        """Yield each row as a list of cell values (str/float/None), left-aligned."""
        target = dict(self._sheets).get(sheet_name)
        if target is None:
            raise KeyError("no sheet named %r (have: %s)" % (sheet_name, self.sheet_names))
        root = ET.fromstring(self.z.read(target))
        data = root.find("m:sheetData", NS)
        if data is None:
            return
        for row in data.findall("m:row", NS):
            cells = {}
            for c in row.findall("m:c", NS):
                ref = c.get("r")
                idx = _col_index(ref) if ref else len(cells)
                ctype = c.get("t")
                if ctype == "inlineStr":
                    is_el = c.find("m:is", NS)
                    val = "".join(t.text or "" for t in is_el.iter("{%s}t" % NS["m"])) if is_el is not None else None
                else:
                    v = c.find("m:v", NS)
                    if v is None or v.text is None:
                        val = None
                    elif ctype == "s":
                        i = int(v.text)
                        val = self._shared[i] if 0 <= i < len(self._shared) else None
                    elif ctype == "str":
                        val = v.text            # cached formula result, as text
                    elif ctype == "b":
                        val = v.text == "1"
                    else:
                        try:
                            val = float(v.text)
                        except ValueError:
                            val = v.text
                cells[idx] = val
            if not cells:
                yield []
                continue
            width = max(cells) + 1
            yield [cells.get(i) for i in range(width)]

    def table(self, sheet_name):
        return list(self.rows(sheet_name))


def cell_str(v):
    """Normalize a cell to a trimmed string ('' for blanks)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()
