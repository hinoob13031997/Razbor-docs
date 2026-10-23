"""Минимальная запись документа Word (.docx) без сторонних библиотек."""

from __future__ import annotations

import zipfile
from xml.sax.saxutils import escape

_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/word/document.xml" '
       'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
       '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" '
         'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
         'Target="word/document.xml"/></Relationships>')
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _run(text: str, bold: bool = False, highlight: bool = False) -> str:
    rpr = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
    if bold:
        rpr += "<w:b/>"
    rpr += '<w:sz w:val="26"/>'
    if highlight:
        rpr += '<w:highlight w:val="yellow"/>'
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(runs: list[tuple[str, bool, bool]], align: str = "both", left: int = 0, after: int = 120) -> str:
    ppr = f'<w:spacing w:after="{after}"/>'
    if left:
        ppr += f'<w:ind w:left="{left}"/>'
    ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{''.join(_run(*r) for r in runs)}</w:p>"


def tokens_to_paragraphs(tokens: list[dict], align: str = "both", left: int = 0) -> list[str]:
    """Токены ({text, filled}) -> абзацы. Перенос строки в тексте начинает новый абзац."""
    paras: list[list[tuple[str, bool, bool]]] = [[]]
    for t in tokens:
        parts = t["text"].split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                paras.append([])
            if part:
                paras[-1].append((part, False, t.get("missing", False)))
    out = []
    for runs in paras:
        out.append(_para(runs, align, left) if runs else _para([("", False, False)], align, left, 0))
    return out


def build_docx(path: str, head: list[dict], title: str, body: list[dict]) -> None:
    parts = tokens_to_paragraphs(head, "left", 4500)
    parts.append(_para([("", False, False)], "left", 0, 120))
    parts.append(_para([(title, True, False)], "center", 0, 240))
    parts += tokens_to_paragraphs(body, "both", 0)
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>'
            '</w:sectPr>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_NS}><w:body>'
           f'{"".join(parts)}{sect}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
