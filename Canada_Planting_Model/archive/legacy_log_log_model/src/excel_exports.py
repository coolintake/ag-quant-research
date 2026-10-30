"""Excel exporters: RPA elasticity matrices and yield-trend diagnostics.

    write_elasticity_matrices(vintage_fits)   -> output/rpa_elasticity_matrices.xlsx
    write_yield_diagnostics(components)       -> output/yield_trend_diagnostics.xlsx

Elasticity workbook: one tab per calibration vintage (crop year V = calibration on t <= V,
spec 7.1 living calibration). Each tab holds, per province, a crop x anchor beta matrix for Run 1,
Run 2 and Run 3 side by side. Floor values (0.10) are highlighted: yellow = AT_LOWER_BOUND
(0 < OLS beta < 0.10), orange = INVALID_INVERTED_SLOPE (OLS beta <= 0). Regional exclusions read
N/A. Each estimated cell carries a comment with alpha0, R^2, N and p.

Yield workbook: one tab per province with actual StatCan yields, the 21-year rolling OLS trend,
the 5-year SMA fallback and the value the model uses (spec 5), for 1998-2026, plus a native
Excel line chart per crop (actual vs rolling trend).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import regression_engine as reg
import settings as S

# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
TITLE = Font(bold=True, size=14)
H1 = Font(bold=True, size=12)
BOLD = Font(bold=True)
NOTE = Font(italic=True, size=9, color="52514E")
MUTED = Font(italic=True, color="7F7F7F")
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
FLOOR_FILL = PatternFill("solid", fgColor="FFF2CC")       # AT_LOWER_BOUND
INVERTED_FILL = PatternFill("solid", fgColor="F8CBAD")    # INVALID_INVERTED_SLOPE (floor prior)
PARTIAL_FILL = PatternFill("solid", fgColor="E2EFDA")     # average: some (not all) vintages on the floor
EXCLUDED_FILL = PatternFill("solid", fgColor="D9D9D9")
EMPTY_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
BETA_FMT = "0.000"


def _crop_label(crop: str) -> str:
    return S.CROPS[crop]["label"]


# --------------------------------------------------------------------------- #
# 1. Elasticity matrices
# --------------------------------------------------------------------------- #
def _write_matrix(ws, top: int, left: int, fits: pd.DataFrame, prov: str, run: str, render=None) -> int:
    """Crop x anchor matrix for one province and run at (top, left). Returns the next free row.

    ``render(cell, row)`` fills an estimated cell (default: one vintage's fit)."""
    render = render or _render_fit
    ws.cell(top, left, S.WINDOW_LABELS[run]).font = BOLD
    hdr = top + 1
    ws.cell(hdr, left, "Crop \\ Anchor")
    for k, anchor in enumerate(S.ANCHORS, start=1):
        ws.cell(hdr, left + k, _crop_label(anchor))
    for c in range(left, left + 1 + len(S.ANCHORS)):
        cell = ws.cell(hdr, c)
        cell.font, cell.fill, cell.alignment, cell.border = BOLD, HEADER_FILL, CENTER, BOX

    crops = list(dict.fromkeys(c for c, _ in S.PAIRS))
    anchor_of = dict(S.PAIRS)
    for i, crop in enumerate(crops):
        row = hdr + 1 + i
        name = ws.cell(row, left, f"T{S.TIERS[(crop, anchor_of[crop])]} · {_crop_label(crop)}")
        name.border = BOX
        for k, anchor in enumerate(S.ANCHORS, start=1):
            cell = ws.cell(row, left + k)
            cell.border, cell.alignment = BOX, CENTER
            if anchor_of[crop] != anchor:
                cell.fill = EMPTY_FILL
                continue
            hit = fits[(fits["province"] == prov) & (fits["run"] == run) & (fits["pair"] == f"{crop}/{anchor}")]
            if hit.empty:
                cell.value, cell.font = "—", MUTED
                continue
            r = hit.iloc[0]
            if r["status"] == S.EXCLUDED_STATUS:
                cell.value, cell.fill, cell.font = "N/A", EXCLUDED_FILL, MUTED
                cell.comment = Comment(f"{S.EXCLUDED_STATUS}: acres held at T-1", "model")
            elif r["status"] == "UNCALIBRATED" or (r["status"] not in reg.FIT_OK and r["status"] != "AVERAGED"):
                n = r.get("n", r.get("n_vintages", 0))
                cell.value, cell.font = f"uncal. (N={int(n if pd.notna(n) else 0)})", MUTED
                cell.comment = Comment(f"UNCALIBRATED: {r.get('reason', '') or 'no estimated fit'}; "
                                       "acres held at T-1", "model")
            else:
                render(cell, r)
    return hdr + 1 + len(crops)


def _render_fit(cell, r) -> None:
    """One vintage's calibrated beta, floor-highlighted, with fit statistics in a comment."""
    cell.value, cell.number_format = float(r["beta_bounded"]), BETA_FMT
    if r["flag"] == "AT_LOWER_BOUND":
        cell.fill = FLOOR_FILL
    elif r["flag"] == "INVALID_INVERTED_SLOPE":
        cell.fill = INVERTED_FILL
    p = "n/a" if r["flag"] == "INVALID_INVERTED_SLOPE" else f"{r['beta_p']:.3f}"
    cell.comment = Comment(
        f"{r['flag']}{' / LOW_N' if r['status'] == 'LOW_N' else ''}\n"
        f"alpha0 = {r['alpha0_bounded']:.4f}\nR2 = {r['r2_bounded']:.3f}\nN = {int(r['n'])} "
        f"({r['years']})\np(beta) = {p}", "model")


def _render_avg(cell, r) -> None:
    """Mean beta over the averaged vintages. Fill: all on the floor -> floor colour (orange if any
    inverted); some on the floor -> green; none -> plain. Comment lists every vintage."""
    cell.value, cell.number_format = float(r["beta_avg"]), BETA_FMT
    n, floors = int(r["n_vintages"]), int(r["floor_count"])
    if floors == n:
        cell.fill = INVERTED_FILL if r["inverted_count"] else FLOOR_FILL
    elif floors:
        cell.fill = PARTIAL_FILL
    cell.comment = Comment(
        f"Mean of {n} vintage(s) ({r['vintages']}); {floors} on the 0.10 floor\n"
        f"range {r['beta_min']:.3f} - {r['beta_max']:.3f}\nmean alpha0 = {r['alpha0_avg']:.4f}\n"
        f"mean R2 = {r['r2_avg']:.3f}\n" + r["detail"].replace("; ", "\n"), "model")


def _legend(ws, row: int, average: bool = False) -> int:
    items = [
        (None, "0.000", "Calibrated beta, inside [0.10, 2.00]"),
        (FLOOR_FILL, "0.100", "Clamped floor: AT_LOWER_BOUND (0 < OLS beta < 0.10)"),
        (INVERTED_FILL, "0.100", "Clamped floor: INVALID_INVERTED_SLOPE (OLS beta <= 0; negative value not reported)"),
        (EXCLUDED_FILL, "N/A", f"{S.EXCLUDED_STATUS}: no regression, acres held at T-1"),
        (None, "uncal. (N=k)", "UNCALIBRATED: fewer than 3 observations, acres held at T-1"),
        (EMPTY_FILL, "", "Not a modelled crop/anchor pair"),
    ]
    if average:
        items[0] = (None, "0.000", "Mean beta, no averaged vintage on the floor")
        items[1] = (FLOOR_FILL, "0.100", "All averaged vintages on the floor (AT_LOWER_BOUND)")
        items[2] = (INVERTED_FILL, "0.100", "All averaged vintages on the floor, at least one INVALID_INVERTED_SLOPE")
        items.insert(3, (PARTIAL_FILL, "0.x", "Some (not all) averaged vintages on the 0.10 floor; mean is pulled toward 0.10"))
    ws.cell(row, 2, "Legend").font = BOLD
    for i, (fill, sample, text) in enumerate(items, start=1):
        c = ws.cell(row + i, 2, sample)
        c.border, c.alignment = BOX, CENTER
        if fill:
            c.fill = fill
        ws.cell(row + i, 3, text).font = NOTE
    return row + len(items) + 2


def _write_province_blocks(ws, fits: pd.DataFrame, render=None, average: bool = False) -> None:
    width = 1 + len(S.ANCHORS)
    row = 4
    for prov in S.PROVINCES:
        ws.cell(row, 2, f"{S.PROVINCES[prov]} ({prov})").font = H1
        end = row + 1
        for j, run in enumerate(S.WINDOWS):
            left = 2 + j * (width + 1)
            end = max(end, _write_matrix(ws, row + 1, left, fits, prov, run, render))
        row = end + 2
    _legend(ws, row, average)
    for j in range(len(S.WINDOWS)):
        left = 2 + j * (width + 1)
        ws.column_dimensions[get_column_letter(left)].width = 34
        for k in range(1, width):
            ws.column_dimensions[get_column_letter(left + k)].width = 13
        ws.column_dimensions[get_column_letter(left + width)].width = 3
    ws.column_dimensions["A"].width = 2
    ws.freeze_panes = "A4"


def write_elasticity_matrices(vintage_fits: pd.DataFrame, average: pd.DataFrame | None = None,
                              path=S.ELASTICITY_WORKBOOK) -> None:
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    lines = [
        ("RPA elasticity matrices (beta) by calibration vintage, province and pricing window", TITLE),
        ("Each tab = crop year V (V/V+1): log-log fits on planting years t <= V only (spec 7.1 living calibration). "
         f"The {S.VINTAGES[-1]} tab equals the production parameters used by the releases.", None),
        ("Rows = crop j (tier), columns = anchor A; one anchor per crop (spec 3). T0 CWRS uses the inverted CWRS/Canola "
         "ratio. Cell = bounded beta on [0.10, 2.00]; hover a cell for alpha0, R2, N, years and p(beta).", None),
        ("The first fitted year is 2020 (2019 supplies t-1 and the first baseline), so the 2020 and 2021 vintages "
         "have N < 3 and are UNCALIBRATED.", None),
        ("Regional exclusions: " + ", ".join(f"{p} {c}" for p, c in sorted(S.REGIONAL_EXCLUSIONS))
         + " (CORN is not a modelled crop; listed for the allocator).", None),
    ]
    for i, (text, font) in enumerate(lines, start=1):
        c = readme.cell(i, 1, text)
        if font:
            c.font = font
    _legend(readme, len(lines) + 2)
    readme.column_dimensions["A"].width = 12
    readme.column_dimensions["B"].width = 14
    readme.column_dimensions["C"].width = 90

    if average is not None and not average.empty:
        span = average["vintages"].iloc[0]
        first, last = (int(x) for x in span.split("-"))
        ws = wb.create_sheet(f"Avg {first}-{str(first + 1)[-2:]} to {last}-{str(last + 1)[-2:]}")
        ws.cell(1, 2, f"Indicative cross-commodity RPA elasticities — mean β of crop years "
                      f"{first}/{first + 1} to {last}/{last + 1} ({last - first + 1} vintages)").font = TITLE
        ws.cell(2, 2, "Mean of the bounded β on the matching vintage tabs (estimated vintages only). The vintages are "
                      "expanding-window fits that share years, so this smooths the recent calibrations rather than "
                      "averaging independent estimates. Production releases still use the latest vintage. Hover a cell "
                      "for each vintage's β and flag.").font = NOTE
        _write_province_blocks(ws, average, _render_avg, average=True)
        readme.cell(len(lines) + 12, 1, f"'{ws.title}' tab: indicative mean β over vintages {span} (see its note).").font = NOTE

    for v in S.VINTAGES:
        fits = vintage_fits[vintage_fits["vintage"] == v]
        ws = wb.create_sheet(f"{v}-{str(v + 1)[-2:]}")
        ws.cell(1, 2, f"RPA elasticity matrices — crop year {v}/{v + 1} (calibration on t ≤ {v})").font = TITLE
        n = fits.loc[fits["status"].isin(reg.FIT_OK), "n"]
        ws.cell(2, 2, f"Estimated fits: {len(n)} of {len(fits)}; N per fit {int(n.min()) if len(n) else 0}–"
                      f"{int(n.max()) if len(n) else 0}. Hover a beta for alpha0 / R2 / N / p.").font = NOTE
        _write_province_blocks(ws, fits)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()


# --------------------------------------------------------------------------- #
# 2. Yield trend diagnostics
# --------------------------------------------------------------------------- #
YIELD_COLS = ("Actual", "Trend 21y OLS", "SMA 5y", "Model expected", "Method")
ACTUAL_COLOR, TREND_COLOR = "2A78D6", "EB6834"


def _yield_unit(crop: str) -> str:
    return "lb/ac" if S.CROPS[crop]["unit"] == "lb" else "bu/ac"


def write_yield_diagnostics(components: pd.DataFrame, path=S.YIELD_WORKBOOK) -> None:
    """components: data_loader.yield_components output for S.YIELD_DIAGNOSTIC_YEARS."""
    wb = Workbook()
    wb.remove(wb.active)
    years = list(S.YIELD_DIAGNOSTIC_YEARS)
    crops = list(S.CROPS)
    first_data_row, n_cols = 6, len(YIELD_COLS)
    last_data_row = first_data_row + len(years) - 1

    for prov, name in S.PROVINCES.items():
        ws = wb.create_sheet(name)
        ws["A1"] = f"Yield trend diagnostics — {name}"
        ws["A1"].font = TITLE
        ws["A2"] = (f"StatCan 32-10-0359 actuals vs the rolling OLS trend fitted on T-{S.YIELD_TREND_YEARS} … T-1 "
                    f"and evaluated at T (needs ≥ {S.YIELD_TREND_MIN_N} points), and the T-{S.YIELD_SMA_YEARS} … T-1 "
                    f"SMA fallback (≥ {S.YIELD_SMA_MIN_N} points). 'Model expected' is what the RPA uses (spec 5). "
                    "Blank = not published or not computable.")
        ws["A2"].font = NOTE
        ws.cell(4, 1, "Year").font = BOLD
        ws.merge_cells(start_row=4, start_column=1, end_row=5, end_column=1)
        ws.cell(4, 1).alignment = CENTER
        sub = components[components["province"] == prov].set_index(["crop", "year"])

        for i, crop in enumerate(crops):
            c0 = 2 + i * n_cols
            ws.cell(4, c0, f"{_crop_label(crop)} ({_yield_unit(crop)})")
            ws.merge_cells(start_row=4, start_column=c0, end_row=4, end_column=c0 + n_cols - 1)
            for k, h in enumerate(YIELD_COLS):
                ws.cell(5, c0 + k, h)
            for c in range(c0, c0 + n_cols):
                for r in (4, 5):
                    cell = ws.cell(r, c)
                    cell.font, cell.fill, cell.alignment, cell.border = BOLD, HEADER_FILL, CENTER, BOX
            fmt = "0" if S.CROPS[crop]["unit"] == "lb" else "0.0"
            for j, year in enumerate(years):
                row = first_data_row + j
                rec = sub.loc[(crop, year)] if (crop, year) in sub.index else None
                vals = [np.nan] * 4 + [None]
                if rec is not None:
                    use_trend = rec["trend_n"] >= S.YIELD_TREND_MIN_N
                    expected = rec["trend_yield"] if use_trend else rec["sma_yield"]
                    method = ("TREND_21Y" if use_trend else "SMA_5Y") if pd.notna(expected) else None
                    vals = [rec["actual"], rec["trend_yield"], rec["sma_yield"], expected, method]
                for k, v in enumerate(vals):
                    cell = ws.cell(row, c0 + k, None if (v is None or (isinstance(v, float) and np.isnan(v))) else v)
                    cell.border = BOX
                    if k < 4:
                        cell.number_format = fmt
                    if k == 4 and v == "SMA_5Y":
                        cell.font = Font(color="C00000")
            ws.column_dimensions[get_column_letter(c0 + n_cols - 1)].width = 11
            for k in range(n_cols - 1):
                ws.column_dimensions[get_column_letter(c0 + k)].width = 9.5
        for j, year in enumerate(years):
            c = ws.cell(first_data_row + j, 1, year)
            c.font, c.border = BOLD, BOX
        ws.column_dimensions["A"].width = 7
        ws.freeze_panes = ws.cell(first_data_row, 2)

        # Charts: Actual vs rolling trend per crop, three per row below the table.
        chart_top = last_data_row + 3
        ws.cell(chart_top - 1, 1, "Actual yield vs 21-year rolling OLS trend (evaluated at T)").font = H1
        cats = Reference(ws, min_col=1, min_row=first_data_row, max_row=last_data_row)
        placed = 0
        for i, crop in enumerate(crops):
            c0 = 2 + i * n_cols
            actual_n = sum(1 for j in range(len(years)) if ws.cell(first_data_row + j, c0).value is not None)
            if actual_n < S.YIELD_SMA_MIN_N:
                continue
            chart = LineChart()
            chart.title = f"{_crop_label(crop)} — {name} ({_yield_unit(crop)})"
            chart.title.overlay = False
            chart.height, chart.width = 8.5, 16
            chart.display_blanks = "gap"
            chart.x_axis.delete = False
            chart.y_axis.delete = False
            chart.x_axis.tickLblSkip = 2
            chart.legend.position = "t"
            for k in (0, 1):     # Actual, Trend 21y OLS
                chart.add_data(Reference(ws, min_col=c0 + k, min_row=5, max_row=last_data_row), titles_from_data=True)
            chart.set_categories(cats)
            actual, trend = chart.series
            actual.smooth = trend.smooth = False
            actual.graphicalProperties.line.solidFill = ACTUAL_COLOR
            actual.graphicalProperties.line.width = 22000
            actual.marker.symbol = "circle"
            actual.marker.size = 5
            actual.marker.graphicalProperties.solidFill = ACTUAL_COLOR
            actual.marker.graphicalProperties.line.solidFill = ACTUAL_COLOR
            trend.graphicalProperties.line.solidFill = TREND_COLOR
            trend.graphicalProperties.line.width = 22000
            trend.graphicalProperties.line.dashStyle = "dash"
            trend.marker.symbol = "none"
            anchor_col = 1 + (placed % 3) * 10
            anchor_row = chart_top + (placed // 3) * 18
            ws.add_chart(chart, f"{get_column_letter(anchor_col)}{anchor_row}")
            placed += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()
