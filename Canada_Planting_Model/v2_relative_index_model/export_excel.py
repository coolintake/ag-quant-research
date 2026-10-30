"""One diagnostic workbook per pricing window, in "The Desk | Chilmeran Research" style.

    Prairie_RPA_Acreage_Diagnostic_R1.xlsx / _R2.xlsx / _R3.xlsx   (SPECIFICATION_V2.md 7.2)

Each workbook is a view of output/v2_panel.csv for ONE window, indexed to that window's own
2019-2024 base. Tabs: README, Manitoba, Saskatchewan, Alberta, Prairie Aggregate, Plan-B Forecast,
Evaluation. Geography tabs: Block 1 RPA, 2 Index, 3 RelIndex vs CWRS, 4 YoY % change in the raw
relative signal (base-free), 5 CANSIM acres, 6 inputs; then a PerfChart and per-crop stacked panels
(% change in signal above % change in acres). No dual axes.

Style source: Python/Design/DesignSkills.md (Calibri; 18pt bold title; black header row with white
bold text; #C6EFCE/#006100 CHECK rows; #,##0 / 0.000 / 0.0% formats; charts with a black outline,
white plot, #E5E5E5 horizontal gridlines only, #595959 18pt titles and 10pt axes with x labels rotated
90 degrees, #D4F1D6 bars with 0.75pt black borders, black/gray/green line hierarchy).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.comments import Comment
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties, RegularTextRun, RichTextProperties
from openpyxl.drawing.text import Font as DrawingFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config as C

# --------------------------------------------------------------------------- #
# The Desk palette and type
# --------------------------------------------------------------------------- #
BLACK, WHITE, MUTED, GRID = "000000", "FFFFFF", "595959", "E5E5E5"
CHECK_FILL, CHECK_TEXT, BAR_FILL = "C6EFCE", "006100", "D4F1D6"
AVG_GRAY, LIGHT_GRAY, HIGHLIGHT_GREEN = "A6A6A6", "D9D9D9", "16A34A"
FONT = "Calibri"
EMU_PER_PT = 12700

F_TITLE = Font(name=FONT, size=18, bold=True)
F_HEAD = Font(name=FONT, size=11, bold=True, color=WHITE)
F_BLOCK = Font(name=FONT, size=11, bold=True)
F_LABEL = Font(name=FONT, size=11)
F_LABEL_B = Font(name=FONT, size=11, bold=True)
F_DATA = Font(name=FONT, size=11)
F_DATA_I = Font(name=FONT, size=11, italic=True, color=MUTED)
F_NOTE = Font(name=FONT, size=9, italic=True, color=MUTED)
F_CHECK = Font(name=FONT, size=11, bold=True, color=CHECK_TEXT)
FILL_HEAD = PatternFill(start_color=BLACK, end_color=BLACK, fill_type="solid")
FILL_CHECK = PatternFill(start_color=CHECK_FILL, end_color=CHECK_FILL, fill_type="solid")
FILL_FLAG = PatternFill(start_color=GRID, end_color=GRID, fill_type="solid")
A_HEAD = Alignment(horizontal="center", vertical="center", wrap_text=True)
A_LEFT = Alignment(horizontal="left", vertical="center")
A_RIGHT = Alignment(horizontal="right", vertical="center")
ROW_LINE = Border(bottom=Side(style="thin", color=GRID))
TABLE_END = Border(bottom=Side(style="medium", color=BLACK))
SUMMARY = Border(top=Side(style="thin", color=BLACK), bottom=Side(style="medium", color=BLACK))

FMT_QTY, FMT_ACRES, FMT_YIELD, FMT_PCT, FMT_RATIO = "#,##0", "0.000", "0.000", "0.0%", "0%"
FMT_INDEX, FMT_PRICE = "0.0", "0.00"

CELL_FLAGS = ("THIN_WINDOW", "NO_QUOTES", "UNIT_SUSPECT", "NONPOSITIVE_RPA", "UNSTABLE_BASE",
              "INSUFFICIENT_YIELD_HISTORY", "PARTIAL_AGGREGATE")
GEOS = {**C.PROVINCES, C.PRAIRIE: "Prairie Aggregate"}

# PerfChart line roles (DesignSkills.md 3, multiple-line hierarchy)
LINE_STYLE = {
    "CANOLA": (BLACK, 2.5, "solid"),          # hero / focus asset
    "DURUM": (HIGHLIGHT_GREEN, 2.0, "solid"),  # highlight series
    "Y_PEAS": (MUTED, 1.5, "lgDash"),          # secondary comparison
    "CPSR": (MUTED, 1.5, "sysDot"),            # secondary comparison
    "CWRS": (AVG_GRAY, 1.25, "dash"),          # benchmark reference (= 100)
}


# --------------------------------------------------------------------------- #
# Table helpers
# --------------------------------------------------------------------------- #
def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)


def _sheet(wb, name: str, title: str, note: str | None = None):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    c = ws.cell(1, 1, title)
    c.font, c.alignment = F_TITLE, A_LEFT
    if note:
        ws.cell(2, 1, note).font = F_NOTE
    return ws


def _header(ws, row: int, labels: list[str], left: int = 1):
    for j, text in enumerate(labels):
        c = ws.cell(row, left + j, text)
        c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, A_HEAD
    ws.row_dimensions[row].height = 30


def _table(ws, top: int, df: pd.DataFrame, formats: dict, left: int = 1, label_cols: int = 1) -> int:
    """Black header + rows. First ``label_cols`` columns are left-aligned labels; the rest right-aligned."""
    _header(ws, top, [str(c) for c in df.columns], left)
    for i, rec in enumerate(df.itertuples(index=False), start=1):
        for j, (col, v) in enumerate(zip(df.columns, rec)):
            c = ws.cell(top + i, left + j, _num(v))
            label = j < label_cols
            c.font, c.alignment, c.border = F_LABEL if label else F_DATA, A_LEFT if label else A_RIGHT, ROW_LINE
            if col in formats:
                c.number_format = formats[col]
    for j in range(len(df.columns)):
        ws.cell(top + len(df), left + j).border = TABLE_END
    return top + len(df) + 2


def _check_row(ws, row: int, label: str, value: str | None, width: int = 4):
    """Green CHECK row. With ``value=None`` the label spills across the empty filled cells."""
    for j in range(width):
        c = ws.cell(row, 1 + j)
        c.fill, c.font = FILL_CHECK, F_CHECK
    ws.cell(row, 1, label).alignment = A_LEFT
    if value is not None:
        ws.cell(row, 2, value).alignment = A_LEFT


def _autofit(ws, min_width: float = 8, max_width: float = 48, skip_rows: int = 2):
    """Width per column from the longest displayed value (titles and notes in rows 1-2 excluded)."""
    widths: dict[int, float] = {}
    merged = {(r, c) for rng in ws.merged_cells.ranges for r in range(rng.min_row, rng.max_row + 1)
              for c in range(rng.min_col, rng.max_col + 1)}
    for row in ws.iter_rows(min_row=skip_rows + 1):
        for c in row:
            if c.value is None or (c.row, c.column) in merged:
                continue
            if c.font == F_BLOCK and c.fill.fill_type is None:     # section heading, spills across columns
                continue
            v = c.value
            if isinstance(v, float):
                fmt = c.number_format
                text = f"{v:.1%}" if "%" in fmt else f"{v:,.3f}" if "0.000" in fmt else f"{v:,.2f}"
            else:
                text = str(v)
            widths[c.column] = max(widths.get(c.column, 0), len(text) * (1.15 if c.font and c.font.bold else 1.0))
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, w + 2))


# --------------------------------------------------------------------------- #
# Chart helpers (DesignSkills.md section 3)
# --------------------------------------------------------------------------- #
def _rich(size_pt: float, color: str = MUTED, bold: bool = False, rot: int | None = None) -> RichText:
    cp = CharacterProperties(sz=int(size_pt * 100), b=bold, solidFill=color, latin=DrawingFont(typeface=FONT))
    body = RichTextProperties(rot=rot, vert="horz") if rot is not None else RichTextProperties()
    return RichText(bodyPr=body, p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])


def _title(text: str, size_pt: float = 18) -> Title:
    cp = CharacterProperties(sz=int(size_pt * 100), b=False, solidFill=MUTED, latin=DrawingFont(typeface=FONT))
    para = Paragraph(pPr=ParagraphProperties(defRPr=cp), r=[RegularTextRun(rPr=cp, t=text)])
    return Title(tx=Text(rich=RichText(p=[para])), overlay=False)


def _axis_title(text: str) -> Title:
    return _title(text.upper(), 10)


def _desk_chart(chart, title: str, y_fmt: str, y_title: str, width: float = 24, height: float = 9,
                legend: bool = False):
    chart.title = _title(title)
    chart.width, chart.height = width, height
    chart.roundedCorners = False                          # clean single black outline
    chart.graphical_properties = GraphicalProperties(ln=LineProperties(solidFill=BLACK, w=EMU_PER_PT))
    chart.plot_area.graphicalProperties = GraphicalProperties(solidFill=WHITE)
    for ax in (chart.x_axis, chart.y_axis):
        ax.delete = False
        ax.graphicalProperties = GraphicalProperties(ln=LineProperties(solidFill=GRID))
    chart.y_axis.majorGridlines = ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill=GRID)))
    chart.x_axis.majorGridlines = None
    chart.y_axis.number_format = y_fmt
    chart.y_axis.txPr = _rich(10)
    chart.x_axis.txPr = _rich(10, rot=-5400000)          # 90 degrees upward
    chart.x_axis.tickLblPos = "low"
    chart.y_axis.title = _axis_title(y_title)
    if legend:
        chart.legend.position = "r"
        chart.legend.overlay = False
        chart.legend.txPr = _rich(10)
    else:
        chart.legend = None
    return chart


def _style_line(series, color: str, width_pt: float, dash: str = "solid", marker: bool = False):
    series.smooth = False
    series.graphicalProperties.line.solidFill = color
    series.graphicalProperties.line.width = int(width_pt * EMU_PER_PT)
    series.graphicalProperties.line.prstDash = dash
    if marker:
        series.marker.symbol = "circle"
        series.marker.size = 4
        series.marker.graphicalProperties = GraphicalProperties(solidFill=color, ln=LineProperties(solidFill=color))
    else:
        series.marker.symbol = "none"


def _style_bars(chart):
    chart.gapWidth = 90
    for s in chart.series:
        s.graphicalProperties = GraphicalProperties(solidFill=BAR_FILL, ln=LineProperties(solidFill=BLACK, w=9525))
        s.invertIfNegative = False


# --------------------------------------------------------------------------- #
# Geography tab
# --------------------------------------------------------------------------- #
BLOCKS = (
    ("Block 1 — Return per acre ($/ac) = trend yield × prompt price − (seed + fertilizer + chemicals)", "rpa", FMT_QTY),
    ("Block 2 — RPA index (2019–2024 average of this window = 100)", "index", FMT_INDEX),
    ("Block 3 — Relative index vs CWRS = 100 × Index / Index(CWRS); CWRS = 100", "rel_index", FMT_INDEX),
    ("Block 4 — YoY % change in the raw relative signal RPA / RPA(CWRS); CWRS uses RPA(CWRS) / RPA(Canola). "
     "Base-free.", "pct_d_signal", FMT_PCT),
)


def _crop_table(ws, top: int, title: str, d: pd.DataFrame, crops: list[str], col: str, fmt: str, prelim: int,
                scale: float = 1.0) -> tuple[int, int]:
    """Year rows x crop columns. Returns (first data row, next free row)."""
    ws.cell(top, 1, title).font = F_BLOCK
    _header(ws, top + 1, ["Crop year", *[C.CROPS[c]["label"] for c in crops]])
    idx = d.set_index(["crop", "year"])
    first = top + 2
    for i, year in enumerate(C.YEARS):
        r = first + i
        yc = ws.cell(r, 1, year)
        yc.font, yc.alignment, yc.border = (F_DATA_I if year == prelim else F_LABEL_B), A_LEFT, ROW_LINE
        for j, crop in enumerate(crops):
            c = ws.cell(r, 2 + j)
            c.alignment, c.border, c.number_format = A_RIGHT, ROW_LINE, fmt
            c.font = F_DATA_I if year == prelim else F_DATA
            if (crop, year) not in idx.index:
                continue
            rec = idx.loc[(crop, year)]
            v = _num(rec[col])
            c.value = v * scale if v is not None else None
            flags = [f for f in str(rec.get("flags") or "").split(";") if f in CELL_FLAGS]
            if flags:
                c.fill = FILL_FLAG
                c.comment = Comment("; ".join(f"{f}: {C.FLAGS.get(f, '')}" for f in flags), "The Desk")
    last = first + len(C.YEARS) - 1
    for j in range(len(crops) + 1):
        ws.cell(last, 1 + j).border = TABLE_END
    return first, last + 3


def _geo_sheet(wb, geo: str, d: pd.DataFrame, run: str, prelim: int):
    crops = [c for c in C.CROP_ORDER if c in set(d["crop"])]
    note = (f"{C.WINDOW_LABELS[run]} prompt cash bids (PDQ; Durum: S ALTA / SW SASK). Trend yields: OLS on 2005..T-1. "
            f"Index base: 2019–2024 average of {C.WINDOW_SHORT[run]}. Shaded cells carry a flag (hover). "
            f"Italic: {prelim} CANSIM acres are preliminary.")
    excluded = [f"{C.CROPS[c]['label']} ({why})" for (p, c), why in C.EXCLUSIONS.items() if p == geo]
    if excluded:
        note += " Excluded: " + "; ".join(excluded) + "."
    if geo == C.PRAIRIE:
        note += " Prairie RPA uses fixed 2019–2024 acreage-share weights over in-scope provinces."
    ws = _sheet(wb, GEOS[geo], f"{GEOS[geo]} — Relative Cash RPA Index vs CANSIM Acreage ({C.WINDOW_SHORT[run]})", note)
    row, first_rows = 4, {}
    for title, col, fmt in BLOCKS:
        first_rows[col], row = _crop_table(ws, row, title, d, crops, col, fmt, prelim)
    first_rows["acres"], row = _crop_table(ws, row, f"Block 5a — CANSIM seeded area (MLN AC; {prelim} preliminary)",
                                           d, crops, "acres", FMT_ACRES, prelim, scale=1e-6)
    first_rows["pct_d_acres"], row = _crop_table(ws, row, "Block 5b — CANSIM YoY % change in seeded area",
                                                 d, crops, "pct_d_acres", FMT_PCT, prelim)
    _, row = _crop_table(ws, row, f"Block 6a — Prompt price ($/bu), {C.WINDOW_SHORT[run]} window average",
                         d, crops, "price", FMT_PRICE, prelim)
    _, row = _crop_table(ws, row, "Block 6b — Trend yield (bu/ac), OLS 2005..T-1", d, crops, "trend_yield",
                         FMT_YIELD, prelim)
    _, row = _crop_table(ws, row, "Block 6c — Variable costs ($/ac) = seed + fertilizer + chemicals", d, crops, "vc",
                         FMT_QTY, prelim)
    # uniform crop columns (widest header), year column fixed
    crop_w = max(len(C.CROPS[c]["label"]) for c in crops) * 1.15 + 4
    ws.column_dimensions["A"].width = 12
    for j in range(len(crops)):
        ws.column_dimensions[get_column_letter(2 + j)].width = crop_w
    ws.freeze_panes = "B4"

    # ---- charts ----
    ws.cell(row, 1, "Charts").font = F_BLOCK
    anchor = row + 1
    n = len(C.YEARS)
    perf = LineChart()
    rel_first = first_rows["rel_index"]
    perf.add_data(Reference(ws, min_col=2, max_col=1 + len(crops), min_row=rel_first - 1, max_row=rel_first + n - 1),
                  titles_from_data=True)
    perf.set_categories(Reference(ws, min_col=1, min_row=rel_first, max_row=rel_first + n - 1))
    for s, crop in zip(perf.series, crops):
        color, width, dash = LINE_STYLE[crop]
        _style_line(s, color, width, dash)
    _desk_chart(perf, f"{GEOS[geo]} PerfChart: Relative Index vs CWRS ({C.WINDOW_SHORT[run]})", "0", "Index",
                width=26, height=10, legend=True)
    perf.display_blanks = "gap"
    ws.add_chart(perf, f"B{anchor}")
    anchor += 22

    sig_first, ac_first = first_rows["pct_d_signal"], first_rows["pct_d_acres"]
    for j, crop in enumerate(crops):
        col = 2 + j
        line = LineChart()
        line.add_data(Reference(ws, min_col=col, min_row=sig_first + 1, max_row=sig_first + n - 1))
        line.set_categories(Reference(ws, min_col=1, min_row=sig_first + 1, max_row=sig_first + n - 1))
        _style_line(line.series[0], BLACK, 2.25, marker=True)
        _desk_chart(line, f"{C.CROPS[crop]['label']}: YoY % Change in Relative Signal", "0%", "% change",
                    width=26, height=7.5)
        ws.add_chart(line, f"B{anchor}")
        bar = BarChart()
        bar.type = "col"
        bar.add_data(Reference(ws, min_col=col, min_row=ac_first + 1, max_row=ac_first + n - 1))
        bar.set_categories(Reference(ws, min_col=1, min_row=ac_first + 1, max_row=ac_first + n - 1))
        _style_bars(bar)
        _desk_chart(bar, f"{C.CROPS[crop]['label']}: CANSIM YoY % Change in Seeded Area", "0%", "% change",
                    width=26, height=7.5)
        ws.add_chart(bar, f"B{anchor + 15}")
        anchor += 32


# --------------------------------------------------------------------------- #
# README, Plan-B and Evaluation tabs
# --------------------------------------------------------------------------- #
def _readme(wb, run: str, panel_run: pd.DataFrame, fit: pd.Series, validation: pd.DataFrame, prelim: int,
            extract: str):
    ws = wb.active
    ws.title = "README"
    ws.sheet_view.showGridLines = False
    ws.cell(1, 1, f"Prairie RPA Acreage Diagnostic — {C.WINDOW_LABELS[run]}").font = F_TITLE
    ws.cell(2, 1, "The Desk | Chilmeran Research · V2 relative cash RPA index · spec: SPECIFICATION_V2.md · "
                  "source of truth: output/v2_panel.csv").font = F_NOTE
    defs = pd.DataFrame([
        ("RPA", "OLS trend yield (2005..T-1) × PDQ prompt cash price − (seed + fertilizer + chemicals), $/ac"),
        ("Index", f"100 × RPA / mean RPA of {C.WINDOW_SHORT[run]} over 2019–2024 (window-matched base)"),
        ("RelIndex", "100 × Index / Index(CWRS): above 100 = more profitable vs CWRS than normal for this window"),
        ("% Δ signal", "YoY change in RPA / RPA(CWRS), same window, base-free; CWRS uses RPA(CWRS) / RPA(Canola)"),
        ("Acres", f"CANSIM 32-10-0359 seeded area, MLN AC; extract {extract}; {prelim} preliminary"),
        ("Exclusions", "; ".join(f"{p} {C.CROPS[c]['label']} ({w})" for (p, c), w in C.EXCLUSIONS.items())),
    ], columns=["Term", "Definition"])
    row = _table(ws, 4, defs, {}, label_cols=2)
    ws.cell(row, 1, "Validation").font = F_BLOCK
    bases = panel_run[panel_run["province"].isin(C.PROVINCES)].drop_duplicates(["province", "crop"])
    unstable = int(bases["base_flags"].notna().sum())
    rejected = int(panel_run.get("n_rejected", pd.Series(dtype=float)).fillna(0).sum())
    checks = [
        ("CHECK — budget costs vs Historical_Ratios",
         f"{int((validation['abs_diff'] <= 1e-4).sum())} / {len(validation)} reproduced "
         f"(max |diff| {validation['abs_diff'].max():.1e})"),
        (f"CHECK — {C.WINDOW_SHORT[run]} bases stable", f"{len(bases) - unstable} / {len(bases)} province-crop bases"),
        ("CHECK — $/bu unit guard", f"{rejected} bids rejected"),
        ("CHECK — Plan-B slope b > 0", "PASS" if fit.get("status") == "OK" and fit.get("b", 0) > 0 else
         str(fit.get("flag") or fit.get("status"))),
    ]
    for i, (label, value) in enumerate(checks):
        _check_row(ws, row + 1 + i, label, value, width=2)
    row += len(checks) + 3
    ws.cell(row, 1, "Flags").font = F_BLOCK
    _table(ws, row + 1, pd.DataFrame(list(C.FLAGS.items()), columns=["Flag", "Meaning"]), {}, label_cols=2)
    _autofit(ws, max_width=110)


def _norm_window(w) -> str:
    """Plan-A 'window' may be RUN3, R3, Run 3 or 3."""
    s = str(w).upper().replace(" ", "")
    digit = next((ch for ch in s if ch.isdigit()), "")
    return f"RUN{digit}" if digit else s


def release_forecast(run: str, preds: pd.DataFrame, obs: pd.DataFrame, plan_a_rows: pd.DataFrame,
                     forecast_year: int) -> pd.DataFrame:
    """Release forecast for one window: Plan-A where the analyst entered a call, else Plan-B (fallback).

    Gate flags (SPECIFICATION_V2.md 6.1): PLAN_A_SIGN_CONFLICT when Plan-A and Plan-B point opposite ways;
    PLAN_A_OUTSIDE_RANGE when |Plan-A| exceeds the series' largest |%dAcres| over the fit years.
    """
    p = preds[(preds["year"] == forecast_year) & (preds["window"] == run)].copy()
    if plan_a_rows is not None and len(plan_a_rows):
        a = plan_a_rows[plan_a_rows["year"] == forecast_year].copy()
        a["window"] = a["window"].map(_norm_window)
        a = a[a["window"] == run][["province", "crop", "pct_change"]].rename(columns={"pct_change": "pct_plan_a"})
    else:
        a = pd.DataFrame(columns=["province", "crop", "pct_plan_a"])
    p = p.merge(a, on=["province", "crop"], how="left")
    p["pct_plan_a"] = pd.to_numeric(p["pct_plan_a"], errors="coerce")
    hist = (obs[obs["window"] == run].assign(abs_y=lambda d: d["pct_d_acres"].abs())
            .groupby(["province", "crop"])["abs_y"].max().rename("hist_max").reset_index())
    p = p.merge(hist, on=["province", "crop"], how="left")
    has_a = p["pct_plan_a"].notna()
    p["pct_release"] = np.where(has_a, p["pct_plan_a"], p["pct_plan_b"])
    p["source"] = np.where(has_a, "Plan-A (analyst)", np.where(p["pct_plan_b"].notna(), "Plan-B (fallback)", "—"))
    gates = []
    for _, r in p.iterrows():
        g = []
        if pd.notna(r["pct_plan_a"]):
            if pd.notna(r["pct_plan_b"]) and r["pct_plan_a"] * r["pct_plan_b"] < 0:
                g.append("PLAN_A_SIGN_CONFLICT")
            if pd.notna(r["hist_max"]) and abs(r["pct_plan_a"]) > r["hist_max"]:
                g.append("PLAN_A_OUTSIDE_RANGE")
        gates.append("; ".join(g) or "—")
    p["gate"] = gates
    p["acres_release"] = p["acres_prev"] * (1 + p["pct_release"])
    p["pct_actual"] = p["acres_actual"] / p["acres_prev"] - 1
    return p


def _plan_b_sheet(wb, run: str, fit: pd.Series, preds: pd.DataFrame, obs: pd.DataFrame, forecast_year: int,
                  plan_a_rows: pd.DataFrame | None = None):
    short = C.WINDOW_SHORT[run]
    b = fit.get("b")
    ws = _sheet(wb, "Plan-B Forecast", f"Plan-B Forecast — {forecast_year} ({short})",
                "Rule: % Δ acres = b × % Δ relative signal, intercept forced to 0 (a zero signal means no change), one "
                "slope pooled over MB / SK / AB and all in-scope crops. Plan-B is the automated prior and fallback; "
                "the release forecast takes the analyst's Plan-A wherever one is entered. "
                f"The {forecast_year} slope is fitted on years before {forecast_year} only.")
    ok = fit.get("status") == "OK" and (b or 0) > 0
    row = 4
    ws.cell(row, 1, "Model (full sample)").font = F_BLOCK
    f = pd.DataFrame([{"Window": C.WINDOW_LABELS[run], "Rule": f"% Δ acres = {b:.3f} × % Δ signal" if ok else "—",
                       "Fit years": fit.get("years"), "N": fit.get("n"), "Intercept": "0 (forced)", "b": b,
                       "SE(b)": fit.get("b_se"), "p(b)": fit.get("b_p"), "R² (centered)": fit.get("r2")}])
    row = _table(ws, row + 1, f, {"b": "0.000", "SE(b)": "0.000", "p(b)": "0.000", "R² (centered)": "0.000"},
                 label_cols=3)
    _check_row(ws, row - 1, f"CHECK — slope b > 0: {'PASS' if ok else 'PLAN_B_NO_SIGNAL'}", None, width=3)
    row += 1

    rel = release_forecast(run, preds, obs, plan_a_rows, forecast_year)
    fy_b = rel["b"].dropna().iloc[0] if rel["b"].notna().any() else float("nan")
    ws.cell(row, 1, f"{forecast_year} release forecast vs CANSIM ({forecast_year} preliminary) — slope used "
                    f"{fy_b:.3f}, fitted on {C.YEARS[0] + 1}–{forecast_year - 1}").font = F_BLOCK
    t = pd.DataFrame({
        "Province": rel["province"], "Crop": rel["crop"].map(lambda c: C.CROPS[c]["label"]),
        "% Δ signal": rel["pct_d_signal"], "Plan-B % Δ": rel["pct_plan_b"], "Plan-A % Δ": rel["pct_plan_a"],
        "Release % Δ": rel["pct_release"], "Source": rel["source"],
        f"{forecast_year - 1} acres (MLN)": rel["acres_prev"] / 1e6, "Release (MLN)": rel["acres_release"] / 1e6,
        f"CANSIM {forecast_year} (MLN)": rel["acres_actual"] / 1e6, "CANSIM % Δ": rel["pct_actual"],
        "Release error": rel["acres_release"] / rel["acres_actual"] - 1,
        "No-change error": rel["acres_prev"] / rel["acres_actual"] - 1, "Gate": rel["gate"]})
    pr = rel.groupby("crop")[["acres_prev", "acres_release", "acres_actual"]].sum(min_count=1).reset_index()
    prairie = pd.DataFrame({
        "Province": "Prairie", "Crop": pr["crop"].map(lambda c: C.CROPS[c]["label"]), "% Δ signal": np.nan,
        "Plan-B % Δ": np.nan, "Plan-A % Δ": np.nan, "Release % Δ": pr["acres_release"] / pr["acres_prev"] - 1,
        "Source": "sum of provinces", f"{forecast_year - 1} acres (MLN)": pr["acres_prev"] / 1e6,
        "Release (MLN)": pr["acres_release"] / 1e6, f"CANSIM {forecast_year} (MLN)": pr["acres_actual"] / 1e6,
        "CANSIM % Δ": pr["acres_actual"] / pr["acres_prev"] - 1,
        "Release error": pr["acres_release"] / pr["acres_actual"] - 1,
        "No-change error": pr["acres_prev"] / pr["acres_actual"] - 1, "Gate": "—"})
    order = {C.CROPS[k]["label"]: i for i, k in enumerate(C.CROP_ORDER)}
    prairie = prairie.sort_values("Crop", key=lambda s: s.map(order))
    t = pd.concat([t, prairie], ignore_index=True)
    fm = {c: FMT_PCT for c in ("% Δ signal", "Plan-B % Δ", "Plan-A % Δ", "Release % Δ", "CANSIM % Δ",
                               "Release error", "No-change error")}
    fm.update({c: FMT_ACRES for c in t.columns if "(MLN)" in c})
    fc_top = row + 1
    row = _table(ws, fc_top, t, fm, label_cols=2)
    first_prairie = fc_top + 1 + len(rel)
    for j in range(len(t.columns)):                       # Prairie rows: bold, thin rule above
        top = ws.cell(first_prairie, 1 + j)
        top.border = Border(top=Side(style="thin", color=BLACK), bottom=Side(style="thin", color=GRID))
        for k in range(len(prairie)):
            c = ws.cell(first_prairie + k, 1 + j)
            c.font = Font(name=FONT, size=11, bold=True)
    for j in range(len(t.columns)):
        ws.cell(fc_top + len(t), 1 + j).border = TABLE_END
    n_a = int(rel["pct_plan_a"].notna().sum())
    ws.cell(row - 1, 1, f"Source: {n_a} Plan-A call(s) entered for {short}; {len(rel) - n_a} line(s) use the Plan-B "
                        "fallback. Gate flags mark Plan-A calls that need a second review.").font = F_NOTE
    row += 1

    ws.cell(row, 1, f"Observations behind the rule (fit {fit.get('years')})").font = F_BLOCK
    o = obs[obs["window"] == run][["province", "crop", "year", "pct_d_signal", "pct_d_acres"]].sort_values(
        ["province", "crop", "year"])
    o = pd.DataFrame({"Province": o["province"], "Crop": o["crop"].map(lambda c: C.CROPS[c]["label"]),
                      "Year": o["year"], "% Δ signal": o["pct_d_signal"], "% Δ acres": o["pct_d_acres"]})
    obs_top = row + 1
    _table(ws, obs_top, o, {"% Δ signal": FMT_PCT, "% Δ acres": FMT_PCT}, label_cols=3)
    _autofit(ws)
    ws.column_dimensions["A"].width = 24                    # fits "Run 3 (Feb 15 - Mar 15)"

    # chart data (right of the tables)
    cd = len(t.columns) + 3
    ws.cell(fc_top, cd, "Chart data").font = F_NOTE
    for j, h in enumerate(("Series", f"CANSIM % Δ {forecast_year}", "Release % Δ")):
        ws.cell(fc_top + 1, cd + j, h).font = F_NOTE
    for i, r in enumerate(rel.itertuples(), start=fc_top + 2):
        ws.cell(i, cd, f"{r.province} {C.CROPS[r.crop]['label']}")
        ws.cell(i, cd + 1, _num(r.pct_actual)).number_format = FMT_PCT
        ws.cell(i, cd + 2, _num(r.pct_release)).number_format = FMT_PCT
    n = len(rel)
    bar = BarChart()
    bar.type = "col"
    bar.add_data(Reference(ws, min_col=cd + 1, max_col=cd + 2, min_row=fc_top + 1, max_row=fc_top + 1 + n),
                 titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=cd, min_row=fc_top + 2, max_row=fc_top + 1 + n))
    _style_bars(bar)
    bar.series[1].graphicalProperties = GraphicalProperties(solidFill=MUTED, ln=LineProperties(solidFill=BLACK, w=9525))
    _desk_chart(bar, f"{forecast_year} Release Forecast vs CANSIM: % Change in Seeded Area ({short})", "0%",
                "% change", width=30, height=11, legend=True)
    ws.add_chart(bar, f"{get_column_letter(cd + 4)}{fc_top}")

    # scatter: observations with the zero-intercept rule
    first, last = obs_top + 1, obs_top + len(o)
    ch = ScatterChart()
    pts = Series(Reference(ws, min_col=5, min_row=first, max_row=last),
                 Reference(ws, min_col=4, min_row=first, max_row=last), title="Province-crop-year")
    pts.marker.symbol, pts.marker.size = "circle", 5
    pts.marker.graphicalProperties = GraphicalProperties(solidFill=BLACK, ln=LineProperties(solidFill=BLACK))
    pts.graphicalProperties.line.noFill = True
    ch.series.append(pts)
    if ok and len(o):
        xmin, xmax = float(o["% Δ signal"].min()), float(o["% Δ signal"].max())
        ws.cell(obs_top, cd, "Rule x").font = F_NOTE
        ws.cell(obs_top, cd + 1, "Rule y").font = F_NOTE
        for k, x in enumerate((xmin, 0.0, xmax), start=1):
            ws.cell(obs_top + k, cd, x).number_format = FMT_PCT
            ws.cell(obs_top + k, cd + 1, b * x).number_format = FMT_PCT
        line = Series(Reference(ws, min_col=cd + 1, min_row=obs_top + 1, max_row=obs_top + 3),
                      Reference(ws, min_col=cd, min_row=obs_top + 1, max_row=obs_top + 3),
                      title=f"Rule: {b:.3f} × signal (through origin)")
        _style_line(line, HIGHLIGHT_GREEN, 2.0)
        ch.series.append(line)
    _desk_chart(ch, f"Plan-B Rule: % Δ Relative Signal vs % Δ Seeded Area ({short})", "0%", "% change in acres",
                width=24, height=12, legend=True)
    ch.x_axis.number_format = "0%"
    ch.x_axis.txPr = _rich(10)
    ch.x_axis.title = _axis_title("% change in relative signal")
    ch.x_axis.majorGridlines = None
    ws.add_chart(ch, f"{get_column_letter(cd + 4)}{obs_top}")


def _evaluation_sheet(wb, run: str, ev: dict, plan_a_rows: pd.DataFrame):
    ws = _sheet(wb, "Evaluation", f"Evaluation — Signal Skill and Forecast Error ({C.WINDOW_SHORT[run]})",
                "Hit rate: sign(% Δ signal) = sign(% Δ acres). Spearman: rank correlation across crops within a "
                "province-year. WAPE = Σ|F − A| / ΣA; Plan-B is out-of-sample.")
    row = 4
    hr = ev["hit_rate"][ev["hit_rate"]["run"] == run]
    sp = ev["spearman"][ev["spearman"]["run"] == run]["rho"]
    summary = pd.DataFrame([{"Measure": "Directional hit rate", "Value": float(hr["hit_rate"].iloc[0]),
                             "N": int(hr["n"].iloc[0])},
                            {"Measure": "Mean Spearman ρ across crops", "Value": float(sp.mean()), "N": int(len(sp))}])
    ws.cell(row, 1, "Signal skill, 2020 onward (provinces pooled)").font = F_BLOCK
    _header(ws, row + 1, list(summary.columns))
    for i, rec in enumerate(summary.itertuples(index=False), start=row + 2):
        ws.cell(i, 1, rec.Measure).font = F_LABEL
        v = ws.cell(i, 2, rec.Value)
        v.number_format, v.alignment = (FMT_PCT if "hit" in rec.Measure else "0.00"), A_RIGHT
        ws.cell(i, 3, rec.N).alignment = A_RIGHT
        for j in range(3):
            ws.cell(i, 1 + j).border = ROW_LINE
    for j in range(3):
        ws.cell(row + 3, 1 + j).border = TABLE_END
    row += 6
    ws.cell(row, 1, "Hit rate by crop").font = F_BLOCK
    hc = ev["hit_by_crop"].set_index("run").loc[[run]].T.reset_index()
    hc.columns = ["Crop", "Hit rate"]
    hc["Crop"] = hc["Crop"].map(lambda c: C.CROPS[c]["label"] if c in C.CROPS else c)
    row = _table(ws, row + 1, hc, {"Hit rate": FMT_PCT})
    ws.cell(row, 1, "Out-of-sample WAPE: Plan-B vs no-change").font = F_BLOCK
    w = ev["wape"][ev["wape"]["window"] == run]
    w = pd.DataFrame({"Year": w["year"], "N": w["n"], "Plan-B WAPE": w["wape_plan_b"],
                      "No-change WAPE": w["wape_nochange"],
                      "Plan-B better": np.where(w["wape_plan_b"] < w["wape_nochange"], "yes", "no")})
    row = _table(ws, row + 1, w, {"Plan-B WAPE": FMT_PCT, "No-change WAPE": FMT_PCT})
    ws.cell(row, 1, "Plan-A (analyst) — inputs/plan_a_assignments.csv").font = F_BLOCK
    a = plan_a_rows[plan_a_rows["window"] == run] if len(plan_a_rows) and "window" in plan_a_rows else plan_a_rows
    if a is None or a.empty:
        ws.cell(row + 1, 1, f"No Plan-A assignments entered for {C.WINDOW_SHORT[run]} yet.").font = F_DATA_I
    else:
        _table(ws, row + 1, a, {"pct_change": FMT_PCT, "acres_plan_a": FMT_QTY, "acres_actual": FMT_QTY}, label_cols=3)
    _autofit(ws)


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #
def write_workbooks(panel, fits, preds, obs, ev, plan_a_rows, validation, prelim, extract, forecast_year) -> list:
    paths = []
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for run in C.WINDOWS:
        d = panel[panel["run"] == run]
        fit = fits[fits["window"] == run].iloc[0]
        wb = Workbook()
        _readme(wb, run, d, fit, validation, prelim, extract)
        for geo in [*C.PROVINCES, C.PRAIRIE]:
            _geo_sheet(wb, geo, d[d["province"] == geo], run, prelim)
        _plan_b_sheet(wb, run, fit, preds, obs, forecast_year, plan_a_rows)
        _evaluation_sheet(wb, run, ev, plan_a_rows)
        path = C.OUTPUT_DIR / C.WORKBOOK_PATTERN.format(short=C.WINDOW_SHORT[run])
        wb.save(path)
        wb.close()
        paths.append(path)
    return paths
