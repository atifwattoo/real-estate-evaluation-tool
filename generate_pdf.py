"""
generate_pdf.py — Converts report.md to a professional client-facing PDF.
Usage:  python generate_pdf.py
Output: output/client_report.pdf
"""
import re
from pathlib import Path
from fpdf import FPDF


REPORT_PATH = Path("report.md")
OUTPUT_PATH = Path("output") / "client_report.pdf"


class ClientReportPDF(FPDF):
    """Custom PDF class for professional client-facing reports."""

    MARGIN = 20
    PAGE_W = 210
    CONTENT_W = PAGE_W - 2 * MARGIN

    C_PRIMARY = (22, 42, 72)
    C_ACCENT = (0, 105, 180)
    C_LIGHT = (235, 242, 252)
    C_DARK = (50, 50, 55)
    C_GREY = (160, 160, 165)
    C_WHITE = (255, 255, 255)
    C_TABLE_ALT = (245, 248, 253)
    C_CODE_BG = (238, 241, 248)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_y(7)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*self.C_GREY)
        self.cell(0, 3, "PROPERTY VALUATION REPORT", align="L")
        self.cell(0, 3, "CONFIDENTIAL", align="R")
        self.ln(6)
        self.set_draw_color(200, 208, 220)
        self.set_line_width(0.3)
        self.line(self.MARGIN, self.get_y(), self.PAGE_W - self.MARGIN, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_draw_color(200, 208, 220)
        self.set_line_width(0.3)
        self.line(self.MARGIN, self.get_y(), self.PAGE_W - self.MARGIN, self.get_y())
        self.ln(3)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*self.C_GREY)
        self.cell(0, 5, f"Page {self.page_no() - 1}", align="C")

    def _sanitize(self, text: str) -> str:
        replacements = {
            '\u2014': '--', '\u2013': '-',
            '\u2018': "'", '\u2019': "'",
            '\u201c': '"', '\u201d': '"',
            '\u2022': '-', '\u2026': '...',
            '\u00d7': 'x', '\u2192': '->',
            '\u2190': '<-', '\u2191': '^',
            '\u2193': 'v',
        }
        for ch, repl in replacements.items():
            text = text.replace(ch, repl)
        text = text.encode('latin-1', errors='replace').decode('latin-1')
        return text

    def _body_font(self):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*self.C_DARK)

    def _check_space(self, mm: float = 15):
        if self.get_y() > self.h - self.b_margin - mm:
            self.add_page()

    # ── Title page ────────────────────────────────────────────

    def build_title_page(self):
        self.add_page()

        self.set_fill_color(*self.C_PRIMARY)
        self.rect(0, 0, self.PAGE_W, 5, "F")

        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.MARGIN, 45, 3, 50, "F")

        self.set_xy(self.MARGIN + 12, 48)
        self.set_font("Helvetica", "B", 24)
        self.set_text_color(*self.C_PRIMARY)
        self.multi_cell(self.CONTENT_W - 12, 10, "Property Valuation\nReport")

        self.set_x(self.MARGIN + 12)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*self.C_ACCENT)
        self.cell(self.CONTENT_W - 12, 6, "Comparable Sales Analysis & Investment Recommendations")

        self.ln(22)
        self.set_draw_color(*self.C_ACCENT)
        self.set_line_width(0.4)
        y = self.get_y()
        self.line(self.MARGIN + 12, y, self.PAGE_W - self.MARGIN, y)
        self.ln(10)

        self.set_x(self.MARGIN + 12)
        info_lines = [
            ("Prepared for:", "CCLBA - Cook County Land Bank Authority"),
            ("Date:", "June 2026"),
            ("Document:", "Automated Valuation Report"),
            ("Subject:", "981 Tax-Delinquent Properties"),
        ]
        for label, value in info_lines:
            self.set_x(self.MARGIN + 12)
            self.set_font("Helvetica", "B", 8.5)
            self.set_text_color(*self.C_PRIMARY)
            self.cell(28, 5.5, self._sanitize(label))
            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(80, 80, 85)
            self.cell(0, 5.5, self._sanitize(value))
            self.ln(6)

        self.ln(15)
        self.set_x(self.MARGIN + 12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*self.C_GREY)
        self.multi_cell(self.CONTENT_W - 12, 4,
            "This report documents the methodology used to estimate market values "
            "for Cook County tax-delinquent properties. Each property was enriched "
            "using the ATTOM Data API, analyzed through a comparable sales valuation "
            "engine, and assigned an investment recommendation based on a $300,000 threshold."
        )

    # ── Table of Contents ─────────────────────────────────────

    def build_toc(self, items: list[str]):
        self.add_page()
        self._section_heading("Contents", 1)

        self.ln(2)
        for idx, item in enumerate(items, 1):
            self._check_space(8)
            self.set_font("Helvetica", "", 9.5)
            self.set_text_color(*self.C_PRIMARY)
            w_num = self.get_string_width(f"{idx}.") + 4
            self.cell(w_num, 7, f"{idx}.", align="R")
            self.set_text_color(*self.C_DARK)
            self.cell(0, 7, self._sanitize(item))
            self.ln(7)

    # ── Section headings ──────────────────────────────────────

    def _section_heading(self, text: str, level: int):
        text = self._sanitize(text)
        self._check_space(15)

        if level == 1:
            self.ln(1)
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(*self.C_PRIMARY)
            self.cell(0, 8, text.upper())
            self.ln(4)
            self.set_draw_color(*self.C_ACCENT)
            self.set_line_width(0.5)
            self.line(self.MARGIN, self.get_y(), self.MARGIN + 40, self.get_y())
            self.ln(5)
        elif level == 2:
            self.ln(1)
            self.set_font("Helvetica", "B", 11.5)
            self.set_text_color(*self.C_PRIMARY)
            self.cell(0, 7, text)
            self.ln(4)
            self.set_draw_color(190, 200, 215)
            self.set_line_width(0.2)
            self.line(self.MARGIN, self.get_y(), self.PAGE_W - self.MARGIN, self.get_y())
            self.ln(4)
        elif level == 3:
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*self.C_ACCENT)
            self.cell(0, 6, text)
            self.ln(4)

    # ── Paragraph ─────────────────────────────────────────────

    def _paragraph(self, text: str):
        text = self._sanitize(text)
        self._check_space(8)
        self._body_font()
        text = re.sub(r" +", " ", text).strip()
        if not text:
            return
        self.multi_cell(self.CONTENT_W, 4.5, text)
        self.ln(1.5)

    # ── Bullet list ───────────────────────────────────────────

    def _bullet_list(self, items: list[str]):
        items = [self._sanitize(i) for i in items]
        self._check_space(6)
        self._body_font()
        for item in items:
            x0 = self.get_x()
            self.cell(4, 4.5, "-")
            self.multi_cell(self.CONTENT_W - 4, 4.5, item)
            self.set_x(x0)
            self.ln(0.3)
        self.ln(1)

    # ── Table ─────────────────────────────────────────────────

    def _table(self, headers: list[str], rows: list[list[str]],
               col_widths: list[int] | None = None):
        headers = [self._sanitize(h) for h in headers]
        rows = [[self._sanitize(c) for c in r] for r in rows]
        self._check_space(30)

        if col_widths is None:
            n = len(headers)
            col_widths = [self.CONTENT_W // n] * n

        # Header
        self.set_font("Helvetica", "B", 7)
        self.set_fill_color(*self.C_PRIMARY)
        self.set_text_color(*self.C_WHITE)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, h, border=1, fill=True, align="C")
        self.ln()

        # Rows
        self.set_font("Helvetica", "", 6.8)
        self.set_text_color(*self.C_DARK)
        for ri, row in enumerate(rows):
            max_lines = 1
            for ci, ct in enumerate(row):
                ls = self.multi_cell(col_widths[ci], 4, str(ct),
                                     border=0, split_only=True)
                max_lines = max(max_lines, len(ls))
            rh = max(5, max_lines * 4)

            if self.get_y() + rh > self.h - self.b_margin:
                self.ln(3)
                self.add_page()
                self.set_font("Helvetica", "B", 7)
                self.set_fill_color(*self.C_PRIMARY)
                self.set_text_color(*self.C_WHITE)
                for i2, h2 in enumerate(headers):
                    self.cell(col_widths[i2], 6, h2, border=1, fill=True, align="C")
                self.ln()
                self.set_font("Helvetica", "", 6.8)
                self.set_text_color(*self.C_DARK)

            fill = ri % 2 == 1
            self.set_fill_color(*self.C_TABLE_ALT) if fill else self.set_fill_color(*self.C_WHITE)

            x_start = self.get_x()
            for ci, ct in enumerate(row):
                xp = x_start + sum(col_widths[:ci])
                self.set_xy(xp, self.get_y())
                self.multi_cell(col_widths[ci], 4, str(ct),
                                border=1, fill=True, align="L")
            self.set_y(self.get_y() + rh - 4)

        self.ln(3)

    # ── Code/formula block ────────────────────────────────────

    def _code_block(self, text: str):
        text = self._sanitize(text)
        self._check_space(10)

        lines = text.strip().split("\n")
        self.set_x(self.MARGIN + 4)
        block_w = self.CONTENT_W - 4

        self.set_fill_color(*self.C_CODE_BG)
        self.set_draw_color(200, 208, 220)
        y0 = self.get_y()

        self.set_font("Courier", "", 7.5)
        total_h = len(lines) * 4

        if self.get_y() + total_h > self.h - self.b_margin:
            self.add_page()
            y0 = self.get_y()

        self.rect(self.MARGIN + 4, y0, block_w, total_h, "FD")

        for li, line in enumerate(lines):
            self.set_xy(self.MARGIN + 8, y0 + 1.5 + li * 4)
            self.set_font("Courier", "", 7.5)
            self.set_text_color(55, 55, 65)
            self.cell(0, 4, line)

        self.set_y(y0 + total_h + 3)

    # ── Blockquote ────────────────────────────────────────────

    def _blockquote(self, text: str):
        text = self._sanitize(text)
        self._check_space(8)
        self._body_font()

        self.set_fill_color(242, 246, 252)
        self.set_draw_color(*self.C_ACCENT)
        y0 = self.get_y()

        self.set_font("Helvetica", "I", 8.5)
        lines = self.multi_cell(self.CONTENT_W - 12, 4.5, text, split_only=True)
        h = max(8, len(lines) * 4.5 + 3)

        if y0 + h > self.h - self.b_margin:
            self.add_page()
            y0 = self.get_y()

        self.set_line_width(0.6)
        self.rect(self.MARGIN + 2, y0, self.CONTENT_W - 4, h, "F")
        self.set_fill_color(*self.C_ACCENT)
        self.rect(self.MARGIN + 2, y0, 1.2, h, "F")

        self.set_xy(self.MARGIN + 8, y0 + 1.5)
        self.set_font("Helvetica", "I", 8.5)
        self.set_text_color(70, 75, 85)
        self.multi_cell(self.CONTENT_W - 14, 4.5, text)
        self.set_y(y0 + h + 2.5)

    # ── Horizontal rule ───────────────────────────────────────

    def _hr(self):
        self.ln(2)
        self.set_draw_color(195, 202, 215)
        self.set_line_width(0.2)
        self.line(self.MARGIN, self.get_y(), self.PAGE_W - self.MARGIN, self.get_y())
        self.ln(4)

    # ── Markdown renderer ─────────────────────────────────────

    def render_markdown(self, md_path: str | Path):
        raw = Path(md_path).read_text(encoding="utf-8")
        raw = self._sanitize(raw)

        raw = re.sub(r"^## Table of Contents.*?(?=^## \d)", "", raw,
                     count=1, flags=re.DOTALL | re.MULTILINE)

        lines = raw.split("\n")
        i = 0
        in_code = False
        code_buf = []
        in_table = False
        table_buf = []

        while i < len(lines):
            line = lines[i].strip()

            # Code fence
            if line.startswith("```"):
                if in_code:
                    self._code_block("\n".join(code_buf))
                    code_buf = []
                    in_code = False
                else:
                    in_code = True
                i += 1
                continue
            if in_code:
                code_buf.append(lines[i])
                i += 1
                continue

            # Table
            if line.startswith("|"):
                in_table = True
                table_buf.append(lines[i])
                i += 1
                continue
            if in_table:
                self._render_table(table_buf)
                table_buf = []
                in_table = False

            # Empty
            if not line:
                i += 1
                continue

            # HR
            if line == "---":
                self._hr()
                i += 1
                continue

            # Headings
            hm = re.match(r"^(#{1,3})\s+(.+?)(?:\s+#+)?$", line)
            if hm:
                self._section_heading(hm.group(2), len(hm.group(1)))
                i += 1
                continue

            # Blockquote
            if line.startswith(">"):
                quotes = []
                while i < len(lines):
                    l2 = lines[i].strip()
                    if l2.startswith(">"):
                        quotes.append(l2[1:].strip())
                        i += 1
                    else:
                        break
                self._blockquote(" ".join(quotes))
                continue

            # Unordered list
            if re.match(r"^[\s]*[-*+]\s+", line):
                bullets = []
                while i < len(lines):
                    m = re.match(r"^[\s]*[-*+]\s+(.+)$", lines[i])
                    if not m:
                        break
                    bullets.append(m.group(1))
                    i += 1
                self._bullet_list(bullets)
                continue

            # Ordered list
            if re.match(r"^\s*\d+\.\s+", line):
                items = []
                while i < len(lines):
                    m = re.match(r"^\s*\d+\.\s+(.+)$", lines[i])
                    if not m:
                        break
                    items.append(m.group(1))
                    i += 1
                self._bullet_list(items)
                continue

            # Paragraph
            paras = []
            while i < len(lines):
                lt = lines[i].strip()
                if not lt or lt.startswith("#") or lt == "---" or \
                   lt.startswith("```") or lt.startswith("|") or \
                   lt.startswith(">") or re.match(r"^[\s]*[-*+]\s+", lt) or \
                   re.match(r"^\s*\d+\.\s+", lt):
                    break
                paras.append(lt)
                i += 1
            if paras:
                self._paragraph(" ".join(paras))
                continue

            i += 1

        if in_code and code_buf:
            self._code_block("\n".join(code_buf))
        if in_table and table_buf:
            self._render_table(table_buf)

    def _render_table(self, lines: list[str]):
        data_lines = [l for l in lines if not re.match(r"^\|[\s\-:]+\|", l)]
        if not data_lines:
            return
        rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in data_lines]
        if not rows:
            return
        hdrs = rows[0]
        data = rows[1:]

        n = len(hdrs)
        tw = self.CONTENT_W
        clens = [len(hdrs[ci]) for ci in range(n)]
        for r in data:
            for ci in range(min(n, len(r))):
                clens[ci] = max(clens[ci], len(r[ci]))
        tc = sum(clens) or 1
        cw = [int(max(18, (cl / tc) * tw)) for cl in clens]
        diff = tw - sum(cw)
        if cw:
            cw[-1] += diff
        self._table(hdrs, data, cw)


def main():
    if not REPORT_PATH.exists():
        print(f"ERROR: {REPORT_PATH} not found.")
        return 1

    pdf = ClientReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(pdf.MARGIN, pdf.MARGIN, pdf.MARGIN)

    # Title page
    pdf.build_title_page()

    # Table of contents
    toc = [
        "What You Gave Us (Input CSV)",
        "Where We Got Property Data (ATTOM API)",
        "Where We Got Comparable Sales (ATTOM Comps API)",
        "How We Cleaned the Data First",
        "How We Removed Bad Comparable Sales (Outlier Removal)",
        "Our Valuation Formula - Step by Step",
        "How Confident Are We in Each Estimate?",
        "Investment Recommendation (YES / NO / MAYBE)",
        "The Output File You Receive",
        "Summary of Results So Far",
    ]
    pdf.build_toc(toc)

    # Report body
    pdf.add_page()
    pdf.render_markdown(REPORT_PATH)

    pdf.output(OUTPUT_PATH)
    print(f"PDF generated: {OUTPUT_PATH}")
    print(f"  Pages:  {pdf.page_no() - 1}")
    return 0


if __name__ == "__main__":
    exit(main())
