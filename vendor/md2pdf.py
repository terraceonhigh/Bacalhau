"""
Markdown-to-PDF converter using vendored mistletoe + pdfme.
Zero external dependencies — pure Python, stdlib only.

Usage:
    from vendor.md2pdf import markdown_to_pdf
    markdown_to_pdf("# Hello\n\nWorld", "/path/to/output.pdf")
"""

import sys
import os

# Add vendor dir to path so pdfme/mistletoe can find each other
_vendor_dir = os.path.dirname(os.path.abspath(__file__))
if _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

from io import BytesIO
from pdfme import PDF
from mistletoe import Document
from mistletoe.base_renderer import BaseRenderer
from mistletoe import block_token, span_token


# ── Unicode to WinAnsiEncoding sanitizer ───────────��──────────────────────────
# pdfme's standard fonts only know chr(0)-chr(255) in WinAnsi encoding.
# Map common Unicode chars to their WinAnsi byte equivalents.

_UNICODE_TO_WINANSI = {
    '\u2013': '\x96',   # en dash
    '\u2014': '\x97',   # em dash
    '\u2018': '\x91',   # left single quote
    '\u2019': '\x92',   # right single quote / apostrophe
    '\u201c': '\x93',   # left double quote
    '\u201d': '\x94',   # right double quote
    '\u2026': '\x85',   # ellipsis
    '\u2022': '\x95',   # bullet
    '\u2020': '\x86',   # dagger
    '\u2021': '\x87',   # double dagger
    '\u2030': '\x89',   # per mille
    '\u0152': '\x8c',   # OE ligature
    '\u0153': '\x9c',   # oe ligature
    '\u2122': '\x99',   # trademark
    '\ufb01': 'fi',     # fi ligature
    '\ufb02': 'fl',     # fl ligature
}

def _sanitize(text):
    """Replace Unicode characters with WinAnsi equivalents or safe fallbacks."""
    out = []
    for ch in text:
        if ord(ch) < 256:
            out.append(ch)
        elif ch in _UNICODE_TO_WINANSI:
            out.append(_UNICODE_TO_WINANSI[ch])
        else:
            out.append('?')
    return ''.join(out)


# ���─ Style constants ───────────────────────────────────────────────────────────

HEADING_SIZES = {1: 24, 2: 20, 3: 16, 4: 14, 5: 12, 6: 11}
BODY_SIZE = 11
MONO_FONT = "Courier"
BODY_FONT = "Times"
CODE_BG = 0.93  # light gray


class PdfRenderer(BaseRenderer):
    """Walks mistletoe's AST and yields pdfme text() calls on a PDF object."""

    def __init__(self, pdf=None):
        self._pdf = pdf
        super().__init__()

    def __enter__(self):
        return super().__enter__()

    # ── Document ──────────────────────────────────────────────────────────

    def render_document(self, token):
        self.footnotes.update(token.footnotes)
        for child in token.children:
            self.render(child)
        return ""

    # ── Block tokens ──────────────────────────────────────────────────────

    def render_heading(self, token):
        size = HEADING_SIZES.get(token.level, BODY_SIZE)
        spans = self._collect_spans(token)
        content = {".": spans, "s": size, "b": True, "f": BODY_FONT}
        self._pdf.text(content)
        # Small gap after heading
        self._pdf.text({".": " ", "s": size * 0.3, "f": BODY_FONT})
        return ""

    def render_paragraph(self, token):
        spans = self._collect_spans(token)
        if not spans:
            return ""
        content = {".": spans, "s": BODY_SIZE, "f": BODY_FONT}
        self._pdf.text(content, text_align="j", line_height=1.5)
        # Gap between paragraphs
        self._pdf.text({".": " ", "s": 6, "f": BODY_FONT})
        return ""

    def render_block_code(self, token):
        # Render as monospace paragraph
        text = _sanitize(token.content.rstrip("\n"))
        content = {".": text, "s": 9, "f": MONO_FONT}
        self._pdf.text(content, line_height=1.4)
        self._pdf.text({".": " ", "s": 6, "f": BODY_FONT})
        return ""

    def render_code_fence(self, token):
        return self.render_block_code(token)

    def render_list(self, token):
        ordered = token.start is not None
        for i, child in enumerate(token.children):
            if ordered:
                bullet = "{}. ".format((token.start or 1) + i)
            else:
                bullet = "- "
            self._render_list_item(child, bullet)
        # Gap after list
        self._pdf.text({".": " ", "s": 4, "f": BODY_FONT})
        return ""

    def _render_list_item(self, token, bullet):
        spans = []
        for child in token.children:
            if isinstance(child, block_token.Paragraph):
                spans.extend(self._collect_spans(child))
            elif isinstance(child, block_token.List):
                # Nested list — render the first-level text, then recurse
                if spans:
                    content = {".": spans, "s": BODY_SIZE, "f": BODY_FONT}
                    self._pdf.text(
                        content, text_align="l", line_height=1.4,
                        list_text=bullet, list_indent=20
                    )
                    spans = []
                ordered = child.start is not None
                for j, sub in enumerate(child.children):
                    sub_bullet = "{}. ".format((child.start or 1) + j) if ordered else "- "
                    self._render_list_item(sub, "    " + sub_bullet)
                return ""
            else:
                self.render(child)

        if spans:
            content = {".": spans, "s": BODY_SIZE, "f": BODY_FONT}
            self._pdf.text(
                content, text_align="l", line_height=1.4,
                list_text=bullet, list_indent=20
            )
        return ""

    def render_quote(self, token):
        # Render blockquote as indented italic text
        parts = []
        for child in token.children:
            if isinstance(child, block_token.Paragraph):
                parts.extend(self._collect_spans(child, extra_style={"i": True}))
        if parts:
            content = {".": parts, "s": BODY_SIZE, "f": BODY_FONT, "i": True}
            self._pdf.text(content, text_align="l", line_height=1.4, indent=20)
            self._pdf.text({".": " ", "s": 6, "f": BODY_FONT})
        return ""

    def render_thematic_break(self, token):
        # Horizontal rule as a line of dashes
        self._pdf.text({".": " ", "s": 4, "f": BODY_FONT})
        self._pdf.text(
            {".": "-" * 60, "s": 9, "f": BODY_FONT, "c": 0.6},
            text_align="c"
        )
        self._pdf.text({".": " ", "s": 4, "f": BODY_FONT})
        return ""

    def render_table(self, token):
        # Render table using pdfme's table support
        rows = []
        if hasattr(token, "header") and token.header:
            header_cells = []
            for cell in token.header.children:
                header_cells.append({
                    ".": self._collect_spans(cell),
                    "s": BODY_SIZE, "f": BODY_FONT, "b": True
                })
            rows.append(header_cells)
        for row_token in token.children:
            row_cells = []
            for cell in row_token.children:
                row_cells.append({
                    ".": self._collect_spans(cell),
                    "s": BODY_SIZE, "f": BODY_FONT
                })
            rows.append(row_cells)
        if rows:
            ncols = max(len(r) for r in rows)
            widths = [100.0 / ncols] * ncols
            self._pdf.table(rows, widths=widths)
            self._pdf.text({".": " ", "s": 6, "f": BODY_FONT})
        return ""

    # ── Span collection ───────────────────────────────────────────────────

    def _collect_spans(self, token, extra_style=None):
        """Walk inline tokens and return a list of pdfme span dicts/strings."""
        spans = []
        if token.children is None:
            return spans
        for child in token.children:
            self._collect_span(child, spans, extra_style or {})
        return spans

    def _collect_span(self, token, spans, style):
        """Recursively collect a single span token into the spans list."""
        cls_name = token.__class__.__name__

        if cls_name == "RawText":
            text = _sanitize(token.content)
            if style:
                spans.append(dict(style, **{".": text}))
            else:
                spans.append(text)

        elif cls_name == "Strong":
            child_style = dict(style, b=True)
            for child in token.children:
                self._collect_span(child, spans, child_style)

        elif cls_name == "Emphasis":
            child_style = dict(style, i=True)
            for child in token.children:
                self._collect_span(child, spans, child_style)

        elif cls_name == "Strikethrough":
            # pdfme doesn't support strikethrough natively; render as-is
            for child in token.children:
                self._collect_span(child, spans, style)

        elif cls_name == "InlineCode":
            text = _sanitize(token.children[0].content)
            code_style = dict(style, f=MONO_FONT, s=9)
            spans.append(dict(code_style, **{".": text}))

        elif cls_name == "LineBreak":
            spans.append("\n")

        elif cls_name == "Link":
            link_style = dict(style, c="blue", u=True)
            for child in token.children:
                self._collect_span(child, spans, link_style)

        elif cls_name == "AutoLink":
            spans.append(dict(style, **{".": _sanitize(token.target), "c": "blue", "u": True}))

        elif cls_name == "EscapeSequence":
            for child in token.children:
                self._collect_span(child, spans, style)

        elif cls_name == "Image":
            # Can't embed images easily; render alt text
            alt = "[image]"
            if token.children:
                alt = "".join(
                    c.content for c in token.children
                    if hasattr(c, "content")
                )
            spans.append(dict(style, **{".": "[" + alt + "]", "i": True}))

        elif hasattr(token, "children") and token.children:
            for child in token.children:
                self._collect_span(child, spans, style)

        elif hasattr(token, "content"):
            text = _sanitize(token.content)
            if style:
                spans.append(dict(style, **{".": text}))
            else:
                spans.append(text)

    # ── Fallback ──────────────────────────────────────────────────────────

    def render_line_break(self, token):
        return ""

    def render_html_block(self, token):
        # Skip raw HTML in PDF output
        return ""

    def render_html_span(self, token):
        return ""

    def __getattr__(self, name):
        """Catch-all for unhandled token types — skip silently."""
        if name.startswith("render_"):
            return lambda token: ""
        raise AttributeError(name)


# ── Public API ────────────────────────────────────────────────────────────────

def markdown_to_pdf(text, output_path=None):
    """Convert a markdown string to PDF.

    Args:
        text: Markdown source string.
        output_path: If given, write PDF to this file path and return None.
                     If None, return the PDF as bytes.

    Returns:
        bytes or None
    """
    pdf = PDF(
        page_size="a4",
        margin={"top": 72, "bottom": 72, "left": 72, "right": 72},
        font_family=BODY_FONT,
        font_size=BODY_SIZE,
        font_color=0.1,
        line_height=1.5,
    )
    pdf.add_page()

    renderer = PdfRenderer(pdf=pdf)
    with renderer:
        doc = Document(text)
        renderer.render(doc)

    if output_path:
        with open(output_path, "wb") as f:
            pdf.output(f)
        return None
    else:
        buf = BytesIO()
        pdf.output(buf)
        return buf.getvalue()


def markdown_to_pdf_bytes(text):
    """Convenience: return PDF as bytes."""
    return markdown_to_pdf(text)
