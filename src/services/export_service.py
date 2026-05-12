"""
CSV / PDF / Interactive HTML export service.
Ports export logic from wwr-interactive/src/export.py into a service usable by Dash callbacks.
"""
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.io as pio

logger = logging.getLogger(__name__)


def convert_df_to_csv(df: pd.DataFrame) -> str:
    """Convert a DataFrame to CSV string (for Dash dcc.Download).

    Prefixes a `#`-commented BETA notice for provenance. pandas/numpy
    consumers can skip it with `read_csv(..., comment='#')`; spreadsheet
    apps (Excel/Numbers) will show it as a single text row above the data.
    """
    banner = "# BETA — capabilities trial for evaluation. Not for operational use.\n"
    return banner + df.to_csv()


def generate_pdf_report(
    site_name: str,
    wind_df: pd.DataFrame,
    wave_df: pd.DataFrame,
    wind_threshold: float,
    wave_threshold: float,
    model_name: str,
    gust_df: Optional[pd.DataFrame] = None,
    model_agreement: Optional[Dict] = None,
) -> bytes:
    """
    Generate a PDF report for marine risk analysis.
    Returns PDF as bytes for dcc.Download.
    """
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        logger.error("ReportLab not installed — cannot generate PDF")
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle(
        "Title2", parent=styles["Title"], fontSize=18, spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "Sub", parent=styles["Normal"], fontSize=10, textColor=colors.gray, spaceAfter=10,
    )

    # ── Header ───────────────────────────────────────────────────────────
    # BETA banner — matches the in-app top-of-page strip for provenance.
    banner_text_style = ParagraphStyle(
        "BannerText", parent=styles["Normal"],
        fontSize=10, alignment=1, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1e293b"),
    )
    banner_table = Table(
        [[Paragraph(
            "BETA — capabilities trial for evaluation. Not for operational use.",
            banner_text_style,
        )]],
        colWidths=[doc.width],
    )
    banner_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbbf24")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(banner_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(f"Wind & Wave Risk Report — {site_name}", title_style))
    elements.append(Paragraph(
        f"Model: {model_name} | Generated: {datetime.now().strftime('%H:%M %d-%b-%Y')} | "
        f"Wind Threshold: {wind_threshold} kn | Wave Threshold: {wave_threshold} m",
        subtitle_style,
    ))

    # ── Model agreement ──────────────────────────────────────────────────
    if model_agreement and model_agreement.get("score") is not None:
        elements.append(Paragraph(
            f"Model Agreement: {model_agreement['score']:.0f}% — {model_agreement['level']}",
            styles["Normal"],
        ))
        elements.append(Spacer(1, 8))

    # ── Executive summary ────────────────────────────────────────────────
    any_exceedance = False

    # Wind analysis
    if not wind_df.empty:
        wind_cols = [c for c in wind_df.columns if "wind_speed_10m" in c and "member" in c]
        if wind_cols:
            median_wind = wind_df[wind_cols].median(axis=1, skipna=True)
            peak = median_wind.max()
            if peak > wind_threshold:
                any_exceedance = True
                # Deterministic models (1 pseudo-member) have no median to speak of.
                peak_label = "peak" if len(wind_cols) == 1 else "median peak"
                elements.append(Paragraph(
                    f"WARNING: Wind {peak_label} {peak:.0f} kn exceeds {wind_threshold} kn threshold",
                    ParagraphStyle("warn", parent=styles["Normal"], textColor=colors.red),
                ))

    # Wave analysis
    if not wave_df.empty and "wave_height" in wave_df.columns:
        peak_wave = wave_df["wave_height"].max()
        if peak_wave > wave_threshold:
            any_exceedance = True
            elements.append(Paragraph(
                f"WARNING: Wave height peak {peak_wave:.1f} m exceeds {wave_threshold} m threshold",
                ParagraphStyle("warn2", parent=styles["Normal"], textColor=colors.red),
            ))

    if not any_exceedance:
        elements.append(Paragraph(
            "All parameters within thresholds.",
            ParagraphStyle("ok", parent=styles["Normal"], textColor=colors.green),
        ))

    elements.append(Spacer(1, 12))

    # ── Wind data table (daily summary) ──────────────────────────────────
    if not wind_df.empty:
        wind_cols = [c for c in wind_df.columns if "wind_speed_10m" in c and "member" in c]
        if wind_cols:
            daily = wind_df[wind_cols].resample("D")
            is_deterministic = len(wind_cols) == 1

            if is_deterministic:
                table_data = [["Date", "Max (kn)", "Exceed %", "Status"]]
            else:
                table_data = [["Date", "Max Median (kn)", "Max P90 (kn)", "Exceed %", "Status"]]

            for date, group in daily:
                if group.empty:
                    continue
                med = group.median(axis=1, skipna=True)
                exceed = (group > wind_threshold).sum(axis=1).mean() / len(wind_cols) * 100

                peak_med = med.max()
                status = "EXCEEDS" if peak_med > wind_threshold else "OK"

                if is_deterministic:
                    table_data.append([
                        date.strftime("%a %d %b"),
                        f"{peak_med:.1f}",
                        f"{exceed:.0f}%",
                        status,
                    ])
                else:
                    peak_p90 = group.quantile(0.9, axis=1).max()
                    table_data.append([
                        date.strftime("%a %d %b"),
                        f"{peak_med:.1f}",
                        f"{peak_p90:.1f}",
                        f"{exceed:.0f}%",
                        status,
                    ])

            if len(table_data) > 1:
                elements.append(Paragraph("Wind Risk Summary (Daily)", styles["Heading2"]))
                col_widths = [80, 120, 80, 80] if is_deterministic else [80, 100, 100, 80, 80]
                t = Table(table_data, colWidths=col_widths)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#4a5568")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#0d1320"), colors.HexColor("#111827")]),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#e2e8f0")),
                ]))
                elements.append(t)
                elements.append(Spacer(1, 12))

    # ── Wave data table ──────────────────────────────────────────────────
    if not wave_df.empty and "wave_height" in wave_df.columns:
        daily_wave = wave_df.resample("D")
        wave_table = [["Date", "Max Wave (m)", "Avg Period (s)", "Sea State", "Status"]]

        for date, group in daily_wave:
            if group.empty:
                continue
            max_h = group["wave_height"].max()
            avg_p = group["wave_period"].mean() if "wave_period" in group.columns else float("nan")

            if avg_p < 6:
                sea_state = "Choppy"
            elif avg_p < 10:
                sea_state = "Standard"
            else:
                sea_state = "Swell"

            status = "EXCEEDS" if max_h > wave_threshold else "OK"
            wave_table.append([
                date.strftime("%a %d %b"),
                f"{max_h:.2f}",
                f"{avg_p:.1f}" if not pd.isna(avg_p) else "N/A",
                sea_state,
                status,
            ])

        if len(wave_table) > 1:
            elements.append(Paragraph("Wave Forecast Summary (Daily)", styles["Heading2"]))
            t = Table(wave_table, colWidths=[80, 100, 100, 80, 80])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#4a5568")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#0d1320"), colors.HexColor("#111827")]),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#e2e8f0")),
            ]))
            elements.append(t)

    # ── Footer ───────────────────────────────────────────────────────────
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        "Weather Intelligence Dashboard | Data: Open-Meteo Ensemble APIs and Bureau of Meteorology Weather API | This report is automatically generated.",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=8, textColor=colors.gray),
    ))

    doc.build(elements)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Interactive HTML export
# ─────────────────────────────────────────────────────────────────────────────

def generate_interactive_html(
    figures: Dict[str, "go.Figure"],
    site_name: str,
    wind_threshold: float,
    wave_threshold: float,
    forecast_window: int,
    model_agreement: Optional[Dict] = None,
    summary_stats: Optional[Dict[str, str]] = None,
) -> str:
    """
    Generate a standalone interactive HTML report from Plotly figures.

    The exported file bundles Plotly.js so recipients can zoom, pan, and hover
    over every chart — no server or Python needed.

    Args:
        figures:          dict mapping section title -> Plotly Figure
        site_name:        offshore site name
        wind_threshold:   current wind threshold in knots
        wave_threshold:   current wave threshold in metres
        forecast_window:  forecast window in hours
        model_agreement:  optional dict with score, level, color, interpretation
        summary_stats:    optional dict of label -> value for summary cards

    Returns:
        Complete HTML string ready for download.
    """
    now_str = datetime.now().strftime("%H:%M %d %b %Y")

    # Build each chart as an HTML <div> (no full page, no duplicate plotly.js)
    chart_sections = []
    for title, fig in figures.items():
        chart_html = pio.to_html(
            fig,
            full_html=False,
            include_plotlyjs=False,
            config={"displaylogo": False, "responsive": True},
        )
        chart_sections.append(f"""
        <div class="chart-section">
            <h2>{title}</h2>
            {chart_html}
        </div>
        """)

    charts_html = "\n".join(chart_sections)

    # Model agreement badge
    agreement_html = ""
    if model_agreement and model_agreement.get("score") is not None:
        ag = model_agreement
        agreement_html = f"""
        <div class="agreement-card" style="border-left: 4px solid {ag['color']};">
            <div class="agreement-title">Model Agreement</div>
            <div class="agreement-score" style="color: {ag['color']};">{ag['score']:.0f}% &mdash; {ag['level']}</div>
            <div class="agreement-detail">{ag['interpretation']}</div>
        </div>
        """

    # Summary stats cards
    stats_html = ""
    if summary_stats:
        cards = []
        for label, value in summary_stats.items():
            cards.append(f"""
            <div class="stat-card">
                <div class="stat-label">{label}</div>
                <div class="stat-value">{value}</div>
            </div>
            """)
        stats_html = f'<div class="stats-row">{"".join(cards)}</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Marine Risk Report &mdash; {site_name}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        :root {{
            --bg-primary: #0d1320;
            --bg-card: #111827;
            --border: #1e293b;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
            --accent: #f59e0b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text);
            line-height: 1.6;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }}
        /* BETA banner — matches the in-app top strip */
        .report-banner {{
            background: #fbbf24;
            color: #1e293b;
            text-align: center;
            font-weight: 600;
            font-size: 13px;
            letter-spacing: 0.02em;
            padding: 10px 24px;
            border-radius: 8px;
            margin-bottom: 16px;
        }}
        /* Header */
        .report-header {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 32px;
            margin-bottom: 20px;
        }}
        .report-header h1 {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent);
            margin-bottom: 8px;
        }}
        .header-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
            margin-top: 12px;
        }}
        .meta-item {{
            font-size: 13px;
            color: var(--text-dim);
        }}
        .meta-item strong {{
            color: var(--text);
        }}
        /* Agreement card */
        .agreement-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 20px;
        }}
        .agreement-title {{ font-size: 13px; color: var(--text-dim); margin-bottom: 4px; }}
        .agreement-score {{ font-size: 20px; font-weight: 700; }}
        .agreement-detail {{ font-size: 12px; color: var(--text-dim); margin-top: 4px; }}
        /* Stats cards */
        .stats-row {{
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }}
        .stat-card {{
            flex: 1;
            min-width: 160px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 18px;
        }}
        .stat-label {{ font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 18px; font-weight: 700; color: var(--text); margin-top: 4px; }}
        /* Chart sections */
        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .chart-section h2 {{
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}
        /* Footer */
        .report-footer {{
            text-align: center;
            padding: 20px;
            font-size: 11px;
            color: var(--text-dim);
            border-top: 1px solid var(--border);
            margin-top: 12px;
        }}
        @media print {{
            body {{ background: #fff; color: #000; }}
            .chart-section, .report-header, .stat-card, .agreement-card {{
                background: #fff; border-color: #ddd;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-banner">BETA &mdash; capabilities trial for evaluation. Not for operational use.</div>
        <div class="report-header">
            <h1>Marine Risk Report &mdash; {site_name}</h1>
            <div class="header-meta">
                <div class="meta-item">Generated: <strong>{now_str}</strong></div>
                <div class="meta-item">Wind Threshold: <strong>{wind_threshold} kn</strong></div>
                <div class="meta-item">Wave Threshold: <strong>{wave_threshold} m</strong></div>
                <div class="meta-item">Forecast Window: <strong>{forecast_window}h</strong></div>
            </div>
        </div>

        {agreement_html}
        {stats_html}
        {charts_html}

        <div class="report-footer">
            Weather Intelligence Dashboard &mdash; Data: Open-Meteo Ensemble APIs and Bureau of Meteorology Weather API
            &mdash; Interactive report generated {now_str}
            <br>All charts are fully interactive: zoom, pan, and hover for details.
        </div>
    </div>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────────────────────────────────────
# Solar interactive HTML export
# ─────────────────────────────────────────────────────────────────────────────

def generate_solar_interactive_html(
    figures: Dict[str, "go.Figure"],
    site_name: str,
    lat: float,
    lon: float,
    summary_stats: Optional[Dict[str, str]] = None,
) -> str:
    """
    Generate a standalone interactive HTML report for the Real-time Solar module.

    Args:
        figures:        dict mapping section title -> Plotly Figure
        site_name:      weather station name
        lat:            station latitude
        lon:            station longitude
        summary_stats:  optional dict of label -> value for metric cards

    Returns:
        Complete HTML string ready for download.
    """
    now_str = datetime.now().strftime("%H:%M %d %b %Y")

    # Build each chart as an HTML <div>
    chart_sections = []
    for title, fig in figures.items():
        chart_html = pio.to_html(
            fig,
            full_html=False,
            include_plotlyjs=False,
            config={"displaylogo": False, "responsive": True},
        )
        chart_sections.append(f"""
        <div class="chart-section">
            <h2>{title}</h2>
            {chart_html}
        </div>
        """)

    charts_html = "\n".join(chart_sections)

    # Summary stats cards
    stats_html = ""
    if summary_stats:
        cards = []
        for label, value in summary_stats.items():
            cards.append(f"""
            <div class="stat-card">
                <div class="stat-label">{label}</div>
                <div class="stat-value">{value}</div>
            </div>
            """)
        stats_html = f'<div class="stats-row">{"".join(cards)}</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Real-time Solar Report &mdash; {site_name}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        :root {{
            --bg-primary: #0d1320;
            --bg-card: #111827;
            --border: #1e293b;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
            --accent: #f59e0b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text);
            line-height: 1.6;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }}
        .report-header {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 32px;
            margin-bottom: 20px;
        }}
        .report-header h1 {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent);
            margin-bottom: 8px;
        }}
        .header-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
            margin-top: 12px;
        }}
        .meta-item {{
            font-size: 13px;
            color: var(--text-dim);
        }}
        .meta-item strong {{
            color: var(--text);
        }}
        .stats-row {{
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }}
        .stat-card {{
            flex: 1;
            min-width: 140px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 18px;
        }}
        .stat-label {{ font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 18px; font-weight: 700; color: var(--text); margin-top: 4px; }}
        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .chart-section h2 {{
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}
        .report-footer {{
            text-align: center;
            padding: 20px;
            font-size: 11px;
            color: var(--text-dim);
            border-top: 1px solid var(--border);
            margin-top: 12px;
        }}
        @media print {{
            body {{ background: #fff; color: #000; }}
            .chart-section, .report-header, .stat-card {{
                background: #fff; border-color: #ddd;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>&#9728; Real-time Solar Report &mdash; {site_name}</h1>
            <div class="header-meta">
                <div class="meta-item">Generated: <strong>{now_str}</strong></div>
                <div class="meta-item">Location: <strong>{lat:.4f}&deg;S, {lon:.4f}&deg;E</strong></div>
                <div class="meta-item">Source: <strong>BoM GSO Nowcast</strong></div>
            </div>
        </div>

        {stats_html}
        {charts_html}

        <div class="report-footer">
            Weather Intelligence Dashboard &mdash; Data: Bureau of Meteorology GSO Model
            &mdash; Interactive report generated {now_str}
            <br>All charts are fully interactive: zoom, pan, and hover for details.
        </div>
    </div>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────────────────────────────────────
# River interactive HTML export
# ─────────────────────────────────────────────────────────────────────────────

def generate_river_interactive_html(
    figures: Dict[str, "go.Figure"],
    station_name: str,
    sensor_id: str,
    summary_stats: Optional[Dict[str, str]] = None,
) -> str:
    """
    Generate a standalone interactive HTML report for a River Monitoring station.

    Args:
        figures:        dict mapping section title -> Plotly Figure
        station_name:   river gauge station name
        sensor_id:      BoM sensor identifier
        summary_stats:  optional dict of label -> value for metric cards

    Returns:
        Complete HTML string ready for download.
    """
    now_str = datetime.now().strftime("%H:%M %d %b %Y")

    # Build each chart as an HTML <div>
    chart_sections = []
    for title, fig in figures.items():
        chart_html = pio.to_html(
            fig,
            full_html=False,
            include_plotlyjs=False,
            config={"displaylogo": False, "responsive": True},
        )
        chart_sections.append(f"""
        <div class="chart-section">
            <h2>{title}</h2>
            {chart_html}
        </div>
        """)

    charts_html = "\n".join(chart_sections)

    # Summary stats cards
    stats_html = ""
    if summary_stats:
        cards = []
        for label, value in summary_stats.items():
            cards.append(f"""
            <div class="stat-card">
                <div class="stat-label">{label}</div>
                <div class="stat-value">{value}</div>
            </div>
            """)
        stats_html = f'<div class="stats-row">{"".join(cards)}</div>'

    river_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>River Station Report &mdash; {station_name}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        :root {{
            --bg-primary: #0d1320;
            --bg-card: #111827;
            --border: #1e293b;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
            --accent: #3b82f6;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text);
            line-height: 1.6;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }}
        .report-header {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 32px;
            margin-bottom: 20px;
        }}
        .report-header h1 {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent);
            margin-bottom: 8px;
        }}
        .header-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
            margin-top: 12px;
        }}
        .meta-item {{
            font-size: 13px;
            color: var(--text-dim);
        }}
        .meta-item strong {{
            color: var(--text);
        }}
        .stats-row {{
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }}
        .stat-card {{
            flex: 1;
            min-width: 140px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 18px;
        }}
        .stat-label {{ font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 18px; font-weight: 700; color: var(--text); margin-top: 4px; }}
        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .chart-section h2 {{
            font-size: 15px;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}
        .report-footer {{
            text-align: center;
            padding: 20px;
            font-size: 11px;
            color: var(--text-dim);
            border-top: 1px solid var(--border);
            margin-top: 12px;
        }}
        @media print {{
            body {{ background: #fff; color: #000; }}
            .chart-section, .report-header, .stat-card {{
                background: #fff; border-color: #ddd;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>&#x1F30A; River Station Report &mdash; {station_name}</h1>
            <div class="header-meta">
                <div class="meta-item">Generated: <strong>{now_str}</strong></div>
                <div class="meta-item">Sensor ID: <strong>{sensor_id}</strong></div>
                <div class="meta-item">Source: <strong>BoM River Gauges (Archived)</strong></div>
            </div>
        </div>

        {stats_html}
        {charts_html}

        <div class="report-footer">
            Weather Intelligence Dashboard &mdash; Data: Bureau of Meteorology River Gauges
            &mdash; Interactive report generated {now_str}
            <br>All charts are fully interactive: zoom, pan, and hover for details.
        </div>
    </div>
</body>
</html>"""

    return river_html
