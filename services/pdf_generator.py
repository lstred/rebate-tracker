"""
services/pdf_generator.py
--------------------------
Generates professional PDF rebate statements using ReportLab.

Each statement includes:
  • Company header with logo
  • Dealer name, address, contact
  • Date range of the statement
  • Summary: current sales, prior year sales, growth, rebate earned
  • Tier progression table showing each tier and whether it was reached
  • Monthly sales breakdown table (optional)
  • Footer with company contact / thank-you text

Template config keys (from PdfTemplate.get_config()):
  company_name, primary_color, secondary_color, accent_color,
  logo_path, header_text, footer_text,
  show_tier_breakdown (bool), show_monthly_sales (bool),
  paper_size ('letter' | 'A4')
"""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from db.local_db import Account, RebateStructure, get_session
from services.rebate_calculator import (
    RebateResult,
    Tier,
    calculate_tiered_rebate,
    get_monthly_sales,
    get_prior_year_period,
    get_period_sales,
    calculate_account_rebate,
)

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _hex_to_rl(hex_color: str, fallback: str = "#1a3a6e") -> colors.Color:
    """Convert a #RRGGBB hex string to a ReportLab Color."""
    try:
        h = hex_color.lstrip("#")
        if len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return colors.Color(r / 255, g / 255, b / 255)
    except (ValueError, AttributeError):
        pass
    return _hex_to_rl(fallback)


# ---------------------------------------------------------------------------
# Statement builder
# ---------------------------------------------------------------------------

class StatementBuilder:
    """Builds a single account's PDF rebate statement."""

    def __init__(self, template_config: dict):
        self.cfg = template_config
        self.paper = letter if template_config.get("paper_size", "letter") == "letter" else A4
        self.primary = _hex_to_rl(template_config.get("primary_color", "#1a3a6e"))
        self.secondary = _hex_to_rl(template_config.get("secondary_color", "#f5f5f5"))
        self.accent = _hex_to_rl(template_config.get("accent_color", "#2ecc71"))
        self._styles = self._build_styles()

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()
        p = self.primary

        return {
            "title": ParagraphStyle(
                "title",
                fontName="Helvetica-Bold",
                fontSize=22,
                textColor=p,
                spaceAfter=4,
                alignment=TA_LEFT,
            ),
            "subtitle": ParagraphStyle(
                "subtitle",
                fontName="Helvetica",
                fontSize=11,
                textColor=colors.HexColor("#555555"),
                spaceAfter=2,
                alignment=TA_LEFT,
            ),
            "section_header": ParagraphStyle(
                "section_header",
                fontName="Helvetica-Bold",
                fontSize=10,
                textColor=p,
                spaceBefore=12,
                spaceAfter=4,
            ),
            "body": ParagraphStyle(
                "body",
                fontName="Helvetica",
                fontSize=9,
                textColor=colors.black,
                spaceAfter=2,
            ),
            "body_right": ParagraphStyle(
                "body_right",
                fontName="Helvetica",
                fontSize=9,
                textColor=colors.black,
                alignment=TA_RIGHT,
            ),
            "small": ParagraphStyle(
                "small",
                fontName="Helvetica",
                fontSize=8,
                textColor=colors.HexColor("#666666"),
            ),
            "footer": ParagraphStyle(
                "footer",
                fontName="Helvetica-Oblique",
                fontSize=8,
                textColor=colors.HexColor("#888888"),
                alignment=TA_CENTER,
            ),
            "kpi_value": ParagraphStyle(
                "kpi_value",
                fontName="Helvetica-Bold",
                fontSize=14,
                textColor=p,
                alignment=TA_RIGHT,
            ),
            "kpi_label": ParagraphStyle(
                "kpi_label",
                fontName="Helvetica",
                fontSize=8,
                textColor=colors.HexColor("#666666"),
                alignment=TA_RIGHT,
            ),
        }

    def build(
        self,
        account: Account,
        result: RebateResult,
        structure: RebateStructure,
        output_path: Optional[str] = None,
    ) -> bytes:
        """
        Generate the PDF statement.
        Returns raw PDF bytes.  Also writes to output_path if provided.
        """
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=self.paper,
            leftMargin=0.65 * inch,
            rightMargin=0.65 * inch,
            topMargin=0.65 * inch,
            bottomMargin=0.65 * inch,
        )

        story = []
        story += self._header_section(account)
        story += self._dealer_info_section(account, result)
        story += self._kpi_section(result)
        if self.cfg.get("show_tier_breakdown", True):
            story += self._tier_section(result, structure)
        if self.cfg.get("show_monthly_sales", True):
            story += self._monthly_section(result)
        story += self._footer_section()

        doc.build(story)
        pdf_bytes = buf.getvalue()

        if output_path:
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)

        return pdf_bytes

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _header_section(self, account: Account) -> list:
        items = []
        logo_path = self.cfg.get("logo_path", "")
        company_name = self.cfg.get("company_name", "Your Company")
        header_text = self.cfg.get("header_text", "Rebate Statement")

        left_col_content = []
        # Logo
        if logo_path and os.path.isfile(logo_path):
            try:
                img = Image(logo_path, width=1.8 * inch, height=0.7 * inch, kind="proportional")
                left_col_content.append(img)
            except Exception:
                left_col_content.append(
                    Paragraph(company_name, self._styles["title"])
                )
        else:
            left_col_content.append(Paragraph(company_name, self._styles["title"]))

        right_col_content = [
            Paragraph(header_text, self._styles["title"]),
        ]

        header_table = Table(
            [[left_col_content, right_col_content]],
            colWidths=[3.5 * inch, 3.5 * inch],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("BACKGROUND", (0, 0), (-1, -1), self.secondary),
                    ("ROUNDEDCORNERS", [4]),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        items.append(header_table)
        items.append(Spacer(1, 0.15 * inch))
        items.append(HRFlowable(width="100%", thickness=2, color=self.primary))
        items.append(Spacer(1, 0.1 * inch))
        return items

    def _dealer_info_section(self, account: Account, result: RebateResult) -> list:
        s = self._styles
        name = account.account_name or account.account_number
        addr_lines = [account.address1, account.address2]
        city_line_parts = [
            p for p in [account.city, account.state, " ".join(filter(None, [account.zip1, account.zip2]))]
            if p
        ]
        city_line = ", ".join(city_line_parts[:2])
        if len(city_line_parts) > 2:
            city_line += "  " + city_line_parts[2]

        phone = account.phone or ""
        period = (
            f"{result.period_start.strftime('%m/%d/%Y')} — "
            f"{result.period_end.strftime('%m/%d/%Y')}"
        )

        left = [
            Paragraph("<b>Bill To:</b>", s["section_header"]),
            Paragraph(f"<b>{name}</b>", s["body"]),
            *[Paragraph(l, s["body"]) for l in addr_lines if l],
            Paragraph(city_line, s["body"]),
            Paragraph(phone, s["body"]) if phone else Spacer(1, 1),
            Paragraph(f"Account #: {account.account_number}", s["small"]),
        ]

        right = [
            Paragraph("<b>Statement Period:</b>", s["section_header"]),
            Paragraph(period, s["body"]),
            Spacer(1, 4),
            Paragraph(f"<b>Rebate Program:</b>", s["section_header"]),
            Paragraph(result.structure_name, s["body"]),
        ]

        info_table = Table([[left, right]], colWidths=[3.5 * inch, 3.5 * inch])
        info_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ]
            )
        )
        return [info_table, Spacer(1, 0.15 * inch)]

    def _kpi_section(self, result: RebateResult) -> list:
        """Large KPI summary boxes."""
        s = self._styles

        def kpi_cell(label: str, value: str) -> list:
            return [
                Paragraph(value, s["kpi_value"]),
                Paragraph(label, s["kpi_label"]),
            ]

        growth_pct = ""
        if result.prior_year_sales > 0:
            pct = (result.current_sales - result.prior_year_sales) / result.prior_year_sales * 100
            growth_pct = f" ({pct:+.1f}%)"

        kpi_data = [
            [
                kpi_cell("Current Period Sales", f"${result.current_sales:,.2f}"),
                kpi_cell(
                    "Prior Year Sales",
                    f"${result.prior_year_sales:,.2f}" + (" *" if result.override_applied else ""),
                ),
                kpi_cell(
                    f"Growth{growth_pct}" if result.structure_type == "growth" else "Growth",
                    f"${result.growth_amount:,.2f}",
                ),
                kpi_cell(
                    "Projected Rebate",
                    f"${result.rebate_amount:,.2f}",
                ),
            ]
        ]

        kpi_table = Table(kpi_data, colWidths=[1.75 * inch] * 4)
        kpi_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), self.secondary),
                    ("BACKGROUND", (3, 0), (3, 0), colors.Color(
                        self.accent.red * 0.15 + 0.85,
                        self.accent.green * 0.15 + 0.85,
                        self.accent.blue * 0.15 + 0.85,
                    )),
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("LINEAFTER", (0, 0), (2, 0), 1, colors.white),
                ]
            )
        )

        items = [kpi_table, Spacer(1, 0.08 * inch)]
        if result.override_applied and result.override_note:
            items.append(
                Paragraph(f"* {result.override_note}", self._styles["small"])
            )
        items.append(Spacer(1, 0.1 * inch))
        return items

    def _tier_section(self, result: RebateResult, structure: RebateStructure) -> list:
        s = self._styles
        items = [
            HRFlowable(width="100%", thickness=1, color=self.secondary),
            Paragraph("Rebate Tier Breakdown", s["section_header"]),
        ]

        tiers_raw = structure.get_tiers()
        if not tiers_raw:
            items.append(Paragraph("No tiers defined.", s["body"]))
            return items

        eval_sales = result.growth_amount if result.structure_type == "growth" else result.current_sales
        base_label = "Growth Amount" if result.structure_type == "growth" else "Sales"

        header_row = ["Tier", f"Threshold ({base_label})", "Rate", "Type", "Status", "Rebate Earned"]
        data = [header_row]

        for i, td in enumerate(sorted(tiers_raw, key=lambda x: x.get("threshold", 0))):
            tier_num = i + 1
            threshold = float(td.get("threshold", 0))
            rate = float(td.get("rate", 0))
            mode = td.get("mode", "dollar_one")
            reached = eval_sales >= threshold
            status = "✓ Reached" if reached else "Not Reached"

            # Find contribution from tier_results
            contribution = 0.0
            for tr in result.tier_results:
                if tr.tier_number == tier_num:
                    contribution = tr.rebate_contribution

            mode_label = "All Sales (Dollar One)" if mode == "dollar_one" else "Incremental Only"
            data.append([
                f"Tier {tier_num}",
                f"${threshold:,.0f}",
                f"{rate * 100:.2f}%",
                mode_label,
                status,
                f"${contribution:,.2f}" if reached else "—",
            ])

        # Total row
        data.append(["", "", "", "", "Total Rebate", f"${result.rebate_amount:,.2f}"])

        col_w = [0.55 * inch, 1.3 * inch, 0.7 * inch, 1.55 * inch, 1.0 * inch, 1.0 * inch]
        tier_table = Table(data, colWidths=col_w, repeatRows=1)
        tier_table.setStyle(
            TableStyle(
                [
                    # Header
                    ("BACKGROUND", (0, 0), (-1, 0), self.primary),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    # Alternating rows
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, self.secondary]),
                    # Total row
                    ("BACKGROUND", (0, -1), (-1, -1), self.secondary),
                    ("FONTNAME", (4, -1), (5, -1), "Helvetica-Bold"),
                    ("TEXTCOLOR", (5, -1), (5, -1), self.primary),
                    # Padding
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    # Grid
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                ]
            )
        )
        items.append(tier_table)
        items.append(Spacer(1, 0.1 * inch))
        return items

    def _monthly_section(self, result: RebateResult) -> list:
        s = self._styles
        monthly = get_monthly_sales(
            result.account_number, result.period_start, result.period_end
        )
        if not monthly:
            return []

        items = [
            HRFlowable(width="100%", thickness=1, color=self.secondary),
            Paragraph("Monthly Sales Detail", s["section_header"]),
        ]

        data = [["Month", "Sales", "Cumulative Sales"]]
        for m in monthly:
            data.append([
                m["label"],
                f"${m['sales']:,.2f}",
                f"${m['cumulative']:,.2f}",
            ])

        data.append(["Total", f"${result.current_sales:,.2f}", ""])

        col_w = [1.8 * inch, 2.0 * inch, 2.0 * inch]
        mo_table = Table(data, colWidths=col_w, repeatRows=1)
        mo_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.primary),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, self.secondary]),
                    ("BACKGROUND", (0, -1), (-1, -1), self.secondary),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                ]
            )
        )
        items.append(mo_table)
        items.append(Spacer(1, 0.1 * inch))
        return items

    def _footer_section(self) -> list:
        footer_text = self.cfg.get(
            "footer_text", "Thank you for your continued business."
        )
        return [
            Spacer(1, 0.2 * inch),
            HRFlowable(width="100%", thickness=1, color=self.secondary),
            Spacer(1, 0.05 * inch),
            Paragraph(footer_text, self._styles["footer"]),
            Paragraph(
                f"Generated {date.today().strftime('%B %d, %Y')}",
                self._styles["footer"],
            ),
        ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_statement(
    account: Account,
    period_start: date,
    period_end: date,
    template_config: dict,
    structure: RebateStructure,
    output_path: Optional[str] = None,
) -> bytes:
    """Generate a single PDF statement and return raw bytes."""
    result = calculate_account_rebate(account, structure, period_start, period_end)
    builder = StatementBuilder(template_config)
    return builder.build(account, result, structure, output_path=output_path)


def batch_generate(
    output_dir: str,
    period_start: date,
    period_end: date,
    template_config: dict,
    progress_cb=None,
) -> list[str]:
    """
    Generate one PDF per active account and save to output_dir.
    File name: {account_number}.pdf
    Returns list of file paths written.
    Returns list of paths that were written.
    """
    from db.local_db import AccountRebateAssignment

    with get_session() as session:
        accounts = session.query(Account).filter_by(is_active=True).all()
        assignments = {
            a.account_number: a
            for a in session.query(AccountRebateAssignment).all()
        }
        structures = {s.id: s for s in session.query(RebateStructure).all()}

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    written = []
    total = len(accounts)

    for i, acct in enumerate(accounts):
        if progress_cb:
            progress_cb(int(i / total * 100), f"Generating {acct.account_number}…")

        assignment = assignments.get(acct.account_number)
        if not assignment or assignment.rebate_structure_id not in structures:
            continue  # Skip accounts with no rebate structure

        structure = structures[assignment.rebate_structure_id]
        out_path = os.path.join(output_dir, f"{acct.account_number}.pdf")

        try:
            generate_statement(
                acct, period_start, period_end,
                template_config, structure,
                output_path=out_path,
            )
            written.append(out_path)
        except Exception as exc:
            # Log but continue batch
            print(f"[PDF] Error generating {acct.account_number}: {exc}")

    if progress_cb:
        progress_cb(100, f"Done — {len(written)} statements generated.")

    return written
