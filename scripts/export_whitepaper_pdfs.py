"""Export publication whitepapers (Markdown) to PDF.

Reads standalone markdown from docs/whitepapers/*.md.
Uses fpdf2 with bundled DejaVu fonts for Unicode (EUR, degrees, etc.).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs" / "whitepapers"
OUTPUT_DIR = SOURCE_DIR / "pdf"

WHITEPAPERS = (
    "ATPE-Whitepaper-v1.0.md",
    "PCMRITMS-Whitepaper-v1.0.md",
    "Phoenix-Integrated-Whitepaper-v1.0.md",
)

PAGE_W = 210
PAGE_H = 297
MARGIN_L = 18
MARGIN_R = 18
MARGIN_T = 20
MARGIN_B = 20
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R


def _ensure_fpdf():
    try:
        import fpdf as fpdf_pkg
        from fpdf import FPDF
    except ImportError:
        print("Installing fpdf2...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
        import fpdf as fpdf_pkg
        from fpdf import FPDF
    font_dir = Path(fpdf_pkg.__file__).parent / "font"
    return FPDF, font_dir


def _ascii_safe(text: str) -> str:
    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2212": "-",
        "\u2022": "-",
        "\u2026": "...",
        "\u00b0": " deg",
        "\u2248": "~",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u03c4": "tau",
        "\u03b1": "alpha",
        "\u03c9": "omega",
        "\u03a3": "Sum",
        "\u00d7": "x",
        "\u2192": "->",
        "\u00ac": "not ",
        "\u20ac": "EUR ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _make_pdf_class(FPDF):
    class WhitepaperPDF(FPDF):
        def footer(self) -> None:
            self.set_y(-12)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, f"Project Phoenix  |  Page {self.page_no()}", align="C")

    return WhitepaperPDF


def _strip_md_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return _ascii_safe(text.strip())


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _parse_table_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return [_strip_md_inline(c) for c in cells]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{1,}:?", c.replace(" ", "")) for c in cells if c
    )


def _new_pdf(WhitepaperPDF, font_dir: Path) -> WhitepaperPDF:
    pdf = WhitepaperPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=MARGIN_B)
    pdf.set_margins(MARGIN_L, MARGIN_T, MARGIN_R)
    pdf.add_page()
    return pdf


def _render_table(pdf: WhitepaperPDF, rows: list[list[str]]) -> None:
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    col_w = CONTENT_W / ncols
    line_h = 5.0

    for i, row in enumerate(rows):
        if pdf.get_y() > PAGE_H - MARGIN_B - 15:
            pdf.add_page()

        if i == 0:
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_fill_color(235, 238, 245)
        else:
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_fill_color(255, 255, 255)

        x_start = MARGIN_L
        y_start = pdf.get_y()
        cell_lines: list[list[str]] = []
        row_h = line_h

        for cell in row:
            pdf.set_xy(x_start, y_start)
            lines = pdf.multi_cell(col_w, line_h, cell, border=0, split_only=True)
            cell_lines.append(lines)
            row_h = max(row_h, line_h * len(lines))

        for j, lines in enumerate(cell_lines):
            x = x_start + j * col_w
            pdf.rect(x, y_start, col_w, row_h)
            pdf.set_xy(x + 1, y_start + 1)
            for k, ln in enumerate(lines):
                if k:
                    pdf.set_xy(x + 1, y_start + 1 + k * line_h)
                pdf.cell(col_w - 2, line_h, ln)

        pdf.set_xy(MARGIN_L, y_start + row_h)


def _render_blockquote(pdf: WhitepaperPDF, lines: list[str]) -> None:
    text = " ".join(_strip_md_inline(l.lstrip("> ").strip()) for l in lines)
    pdf.set_fill_color(248, 248, 252)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_x(MARGIN_L)
    pdf.multi_cell(CONTENT_W, 5, text, fill=True)
    pdf.ln(2)


def _render_code_block(pdf: WhitepaperPDF, lines: list[str]) -> None:
    pdf.set_font("Helvetica", "", 8)
    pdf.set_fill_color(245, 245, 245)
    for line in lines:
        pdf.set_x(MARGIN_L)
        pdf.multi_cell(CONTENT_W, 4.5, line, fill=True)
    pdf.ln(2)


def _render_bullet(pdf: WhitepaperPDF, text: str) -> None:
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(MARGIN_L)
    pdf.multi_cell(CONTENT_W, 5.5, "- " + _strip_md_inline(text.lstrip("- ").strip()))


def _render_heading(pdf: WhitepaperPDF, line: str) -> None:
    level = len(line) - len(line.lstrip("#"))
    text = _strip_md_inline(line.lstrip("# ").strip())
    if level == 1:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(20, 40, 80)
    elif level == 2:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(30, 50, 90)
    else:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(40, 60, 100)
    pdf.set_x(MARGIN_L)
    pdf.multi_cell(CONTENT_W, 7 if level <= 2 else 6, text)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)


def _render_paragraph(pdf: WhitepaperPDF, text: str) -> None:
    text = _strip_md_inline(text)
    if not text:
        return
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(MARGIN_L)
    pdf.multi_cell(CONTENT_W, 5.5, text)
    pdf.ln(1)


def markdown_to_pdf(md_path: Path, pdf_path: Path) -> None:
    FPDF, font_dir = _ensure_fpdf()
    WhitepaperPDF = _make_pdf_class(FPDF)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    pdf = _new_pdf(WhitepaperPDF, font_dir)

    table_buf: list[list[str]] = []
    quote_buf: list[str] = []
    code_buf: list[str] = []
    in_code = False

    def flush_table() -> None:
        nonlocal table_buf
        if table_buf:
            _render_table(pdf, table_buf)
            table_buf = []
            pdf.ln(2)

    def flush_quote() -> None:
        nonlocal quote_buf
        if quote_buf:
            _render_blockquote(pdf, quote_buf)
            quote_buf = []

    def flush_code() -> None:
        nonlocal code_buf
        if code_buf:
            _render_code_block(pdf, code_buf)
            code_buf = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_table()
                flush_quote()
                in_code = True
            i += 1
            continue

        if in_code:
            code_buf.append(_ascii_safe(line))
            i += 1
            continue

        if stripped.startswith(">"):
            flush_table()
            quote_buf.append(line)
            i += 1
            continue
        flush_quote()

        if _is_table_row(line):
            cells = _parse_table_row(line)
            if _is_separator_row(cells):
                i += 1
                continue
            table_buf.append(cells)
            i += 1
            continue
        flush_table()

        if stripped.startswith("#"):
            _render_heading(pdf, stripped)
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            pdf.ln(2)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(MARGIN_L, pdf.get_y(), PAGE_W - MARGIN_R, pdf.get_y())
            pdf.ln(4)
            i += 1
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            _render_bullet(pdf, stripped)
            i += 1
            continue

        if not stripped:
            pdf.ln(2)
            i += 1
            continue

        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            _render_paragraph(pdf, stripped.strip("*"))
            i += 1
            continue

        _render_paragraph(pdf, stripped)
        i += 1

    flush_table()
    flush_quote()
    flush_code()

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pdf_path))


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in WHITEPAPERS:
        md = SOURCE_DIR / name
        if not md.exists():
            print(f"Missing: {md}", file=sys.stderr)
            return 1
        pdf_out = OUTPUT_DIR / name.replace(".md", ".pdf")
        print(f"  {md.name} -> pdf/{pdf_out.name}")
        markdown_to_pdf(md, pdf_out)

    print(f"\nWrote {len(WHITEPAPERS)} PDF(s) to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
