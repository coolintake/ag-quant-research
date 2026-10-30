"""World (Hammersmith) tabs of Matrix_FOB_Calculator.xlsx, built and wired through Excel COM.

Called by wire_workbook.py (never run on its own). Every write is idempotent: sheets are created only when
missing, World_Config is rewritten from Hammersmith/hs_config.py, formulas and conditional formats are
re-entered, and the user's dropdown selections are kept.

  World_FOB_Matrix   world FOB by delivery month x origin, Canadian FOB for the same months, and the
                     Canada - World spread matrix (green = Canada cheaper, red = Canada premium)
  Global_CIF_Matrix  landed cost at a destination: FOB + ocean freight proxy, and the Canada - competitor CIF spread
  FOB_Indications    the parsed world FOB rows, filtered by commodity
  Freight_Rates      origin x destination freight matrix (the row CIF uses) and the filtered freight rows
  World_Config       (hidden) series map, freight proxies, dropdown lists - written from hs_config.py
  Data_World / Data_Freight   Power Query tables over data_drop/world_*_latest.csv (wired by wire_workbook.py)
"""

from __future__ import annotations

import logging

import config
import fob_styles as S
import layout as L

log = logging.getLogger("wire_world")
hc = L.hs.hc                                  # Hammersmith/hs_config.py (loaded via layout)

XL_EXPRESSION, XL_VALIDATE_LIST, XL_ALERT_STOP, XL_BETWEEN, XL_SHEET_HIDDEN = 2, 3, 1, 1, 0
XL_EDGE_BOTTOM, XL_EDGE_TOP, XL_CONTINUOUS, XL_MEDIUM, XL_THIN = 9, 8, 1, -4138, 2
XL_LEFT, XL_CENTER, XL_RIGHT = -4131, -4108, -4152
RED_FILL, RED_TEXT = "FFC7CE", "9C0006"        # soft red (same token as the S&D engine's warn fill)
WORLD_SLOTS, CANADA_SLOTS = 10, 6              # world series columns / Canadian crop x route slots
SLOT_COLS = [L.col_letter(c) for c in range(2, 2 + WORLD_SLOTS)]          # B..K
FMT_SPOT = '$#,##0.00"†";($#,##0.00)"†";"-"'
VIEW_TABS = [L.W_MTX, L.W_CIF, L.W_IND, L.W_FRT]


# --------------------------------------------------------------------------- #
# Small COM helpers (Design folder rules: Calibri, black headers, green CHECK rows)
# --------------------------------------------------------------------------- #
def rgb(h: str) -> int:
    return S.rgb_int(h)


def font(rng, size=11, bold=False, italic=False, color="000000") -> None:
    rng.Font.Name, rng.Font.Size, rng.Font.Bold, rng.Font.Italic = S.FONT, size, bold, italic
    rng.Font.Color = rgb(color)


def title(ws, text: str, subtitle_formula: str) -> None:
    ws.Range("A1").Value = text
    font(ws.Range("A1"), 18, bold=True)
    ws.Rows(1).RowHeight = 28
    ws.Range("A2").Formula2 = subtitle_formula
    font(ws.Range("A2"), 11, italic=True, color=S.MUTED_AXIS_GRAY)


def section(rng, formula_or_text: str) -> None:
    if formula_or_text.startswith("="):
        rng.Formula2 = formula_or_text
    else:
        rng.Value = formula_or_text
    font(rng, 13, bold=True)


def header(rng, labels: list[str] | None = None) -> None:
    if labels is not None:
        rng.Value = tuple(labels)
    rng.Interior.Color = rgb(S.HEADER_BLACK)
    font(rng, 11, bold=True, color=S.HEADER_TEXT_WHITE)
    rng.HorizontalAlignment, rng.VerticalAlignment, rng.WrapText = XL_CENTER, XL_CENTER, True
    rng.RowHeight = 30


def note(rng, text: str) -> None:
    rng.Value = text
    font(rng, 10, italic=True, color=S.MUTED_AXIS_GRAY)


def check_row(rng) -> None:
    rng.Interior.Color = rgb(S.CHECK_GREEN_FILL)
    font(rng, 11, bold=True, color=S.CHECK_GREEN_TEXT)
    b = rng.Borders(XL_EDGE_BOTTOM)
    b.LineStyle, b.Weight = XL_CONTINUOUS, XL_MEDIUM
    b = rng.Borders(XL_EDGE_TOP)
    b.LineStyle, b.Weight = XL_CONTINUOUS, XL_THIN


def control(ws, label_cell: str, input_cell: str, label: str, source: str, default) -> None:
    """Bold label + blue dropdown input. The current selection is kept on re-wiring."""
    ws.Range(label_cell).Value = label
    font(ws.Range(label_cell), 11, bold=True)
    c = ws.Range(input_cell)
    if source:
        c.Validation.Delete()
        c.Validation.Add(XL_VALIDATE_LIST, XL_ALERT_STOP, XL_BETWEEN, source)
    if c.Value in (None, ""):
        c.Value = default
    font(c, 11, color=S.INPUT_BLUE)
    c.HorizontalAlignment = XL_CENTER


def helper(rng, formula: str) -> None:
    rng.Formula2 = formula
    font(rng, 10, italic=True, color=S.MUTED_AXIS_GRAY)


def cf(rng, formula: str, fill: str | None = None, color: str | None = None, italic: bool | None = None,
       number_format: str | None = None) -> None:
    fc = rng.FormatConditions.Add(XL_EXPRESSION, None, formula)
    if fill:
        fc.Interior.Color = rgb(fill)
    if color:
        fc.Font.Color = rgb(color)
    if italic is not None:
        fc.Font.Italic = italic
    if number_format:
        fc.NumberFormat = number_format
    fc.StopIfTrue = False


def widths(ws, spec: dict[str, float]) -> None:
    for col, w in spec.items():
        ws.Columns(col).ColumnWidth = w


def wlk(cond: str, col: str, missing: str = '"N/A"') -> str:
    """Row lookup on a multi-column condition; `missing` when absent OR the cell is blank (XLOOKUP would give 0)."""
    return f'LET(i,XMATCH(1,{cond}),IF(ISNA(i),{missing},IF(INDEX({col},i)="",{missing},INDEX({col},i))))'


def stale_subtitle(extra: str) -> str:
    return ('=IF(World_Date="","No Hammersmith data yet - run Refresh_FOB.bat","Hammersmith report "&'
            'TEXT(World_Date,"yyyy-mm-dd")&" ("&World_Age&" days old"&IF(World_Age>WC_StaleDays,", STALE","")&")'
            '  ·  PDQ "&TEXT(PDQ_Date,"yyyy-mm-dd")&"  ·  USD/CAD "&TEXT(FX_USDCAD,"0.0000")'
            f'{extra})')


# --------------------------------------------------------------------------- #
# Sheets
# --------------------------------------------------------------------------- #
def ensure_sheets(wb) -> None:
    """Create the world tabs if missing: views after Basis, World_Config after Config, data tabs at the end."""
    names = [ws.Name for ws in wb.Worksheets]

    def add(name: str, after: str):
        if name in names:
            return
        ws = wb.Worksheets.Add(None, wb.Worksheets(after))
        ws.Name = name
        names.insert(names.index(after) + 1, name)
        log.info("created sheet %s", name)

    prev = L.BASIS
    for n in VIEW_TABS:
        add(n, prev)
        prev = n
    add(L.W_CFG, L.CFG)
    add(L.D_WORLD, wb.Worksheets(wb.Worksheets.Count).Name)
    add(L.D_FRT, L.D_WORLD)
    wb.Worksheets(L.W_CFG).Visible = XL_SHEET_HIDDEN
    for n in (L.D_WORLD, L.D_FRT):
        wb.Worksheets(n).Tab.Color = rgb(S.MUTED_AXIS_GRAY)


def write_config(wb) -> None:
    """World_Config: one column block per list, rewritten from hs_config.py, each with a named range."""
    ws = wb.Worksheets(L.W_CFG)
    ws.Cells.Clear()
    ws.Range("A1").Value = "World_Config - written by src/wire_world.py from Hammersmith/hs_config.py. Do not edit."
    font(ws.Range("A1"), 11, bold=True)
    series = [s for s in hc.WORLD_SERIES]
    peers = [(com, c["crop"]) for com in hc.WORLD_COMMODITIES for c in config.CROPS
             if any(c["crop"] in s["peer"].split(";") for s in series if s["commodity"] == com)]
    com_bu = [(com, next(s["bu"] for s in series if s["commodity"] == com)) for com in hc.WORLD_COMMODITIES]
    all_com = ["All"] + sorted({s["commodity"] for s in series})
    blocks = [  # (first column, headers, rows, {name: column offset})
        ("A", ["series_id", "commodity", "display", "canada_peer", "freight_key"],
         [(s["id"], s["commodity"], hc.SERIES_META[s["id"]][0], s["peer"], hc.SERIES_META[s["id"]][1])
          for s in series], {"WC_Series": 0, "WC_SCom": 1, "WC_Display": 2, "WC_FrKey": 4}),
        ("G", ["commodity", "bu_per_mt"], com_bu, {"WC_Commodities": 0, "WC_ComBu": 1}),
        ("J", ["commodity (all)"], [(c,) for c in all_com], {"WC_AllCom": 0}),
        ("L", ["route", "freight_key"], list(hc.CANADA_FREIGHT_PROXY.items()), {"WC_Route": 0, "WC_RouteFr": 1}),
        ("O", ["destination"], [(d,) for d in hc.CIF_DESTINATIONS], {"WC_Dest": 0}),
        ("Q", ["destination (all)"], [("All",)] + [(d,) for d in hc.CIF_DESTINATIONS], {"WC_DestAll": 0}),
        ("S", ["commodity", "canada_crop"], peers, {"WC_PeerCom": 0, "WC_PeerCrop": 1}),
        ("V", ["origin_key", "label"], hc.FREIGHT_ORIGIN_KEYS, {"WC_FrOriginKey": 0, "WC_FrOriginLabel": 1}),
        ("Y", ["origin (all)"], [("All",)] + [(lab,) for _, lab in hc.FREIGHT_ORIGIN_KEYS], {"WC_FrOriginAll": 0}),
        ("AA", ["vessel"], [(v,) for v in hc.FREIGHT_VESSEL_FILTER], {"WC_Vessel": 0}),
        ("AC", ["setting", "value"], [("World report stale after (days)", hc.WORLD_STALE_DAYS)], {}),
    ]
    for col, hdr, rows, names in blocks:
        c0 = ws.Range(f"{col}3").Column
        ws.Range(ws.Cells(3, c0), ws.Cells(3, c0 + len(hdr) - 1)).Value = tuple(hdr)
        header(ws.Range(ws.Cells(3, c0), ws.Cells(3, c0 + len(hdr) - 1)))
        ws.Range(ws.Cells(4, c0), ws.Cells(3 + len(rows), c0 + len(hdr) - 1)).Value = tuple(
            tuple("" if v is None else v for v in r) for r in rows)
        for nm, off in names.items():
            letter = L.col_letter(c0 + off)
            wb.Names.Add(nm, f"='{L.W_CFG}'!${letter}$4:${letter}${3 + len(rows)}")
    wb.Names.Add("WC_StaleDays", f"='{L.W_CFG}'!$AD$4")
    ws.Columns("A:AD").AutoFit()


# --------------------------------------------------------------------------- #
# World_FOB_Matrix
# --------------------------------------------------------------------------- #
def write_matrix(wb) -> None:
    ws = wb.Worksheets(L.W_MTX)
    p = "Wm_"
    title(ws, "World FOB Matrix", stale_subtitle('&"  ·  "&Wm_Com&"  ·  "&Wm_Zone&"  ·  "&Wm_Unit'))
    control(ws, "A4", "B4", "Commodity", "=WC_Commodities", "Wheat")
    control(ws, "C4", "D4", "Ref zone", "=Cfg_ZoneList", "SW SASK")
    control(ws, "E4", "F4", "Unit", "=Cfg_FOBUnits", "USD/MT")
    control(ws, "G4", "H4", "Spread month", "=Win_Sel", "Month 2")   # Hammersmith near = run month + 1
    control(ws, "J4", "K4", "Band ±", "", 5)
    ws.Range("K4").NumberFormat = "0.00"
    helper(ws.Range("I4"), "=INDEX(Win_Label,Wm_K)")
    for nm, cell in [("Com", "B4"), ("Zone", "D4"), ("Unit", "F4"), ("Month", "H4"), ("Band", "K4"),
                     ("K", "M4"), ("UnitIdx", "N4"), ("UF", "O4"), ("Bu", "P4"), ("YM", "Q4")]:
        wb.Names.Add(p + nm, f"='{L.W_MTX}'!${cell[0]}${cell[1:]}")
    helper(ws.Range("M4"), "=IFERROR(MATCH(Wm_Month,Win_Sel,0),1)")
    helper(ws.Range("N4"), "=IFERROR(MATCH(Wm_Unit,Cfg_FOBUnits,0),1)")
    helper(ws.Range("P4"), "=XLOOKUP(Wm_Com,WC_Commodities,WC_ComBu,1)")
    helper(ws.Range("O4"), "=CHOOSE(Wm_UnitIdx,1,1/Wm_Bu,FX_USDCAD)")
    helper(ws.Range("Q4"), "=INDEX(Win_YM,Wm_K)")

    # hidden helper rows: world series ids (row 6), Canadian crops / routes (rows 16-17)
    ws.Range("B6").Formula2 = (f'=LET(s,FILTER(WC_Series,(WC_SCom=Wm_Com)*(COUNTIF(W_Series,WC_Series)>0),""),'
                               f'TRANSPOSE(INDEX(s,SEQUENCE(MIN(ROWS(s),{WORLD_SLOTS})))))')
    wb.Names.Add("Wm_Series", f"='{L.W_MTX}'!$B$6#")
    crops = ('LET(c,FILTER(WC_PeerCrop,WC_PeerCom=Wm_Com,""),r,Rt_Display,s,SEQUENCE(1,MIN(ROWS(c)*ROWS(r),'
             f'{CANADA_SLOTS})),')
    ws.Range("B16").Formula2 = "=" + crops + "INDEX(c,INT((s-1)/ROWS(r))+1))"
    ws.Range("B17").Formula2 = "=" + crops + "INDEX(r,MOD(s-1,ROWS(r))+1))"
    for r in (6, 16, 17):
        ws.Rows(r).Hidden = True

    # Block A: world FOB by delivery month
    section(ws.Range("A7"), '="World FOB by delivery month (Hammersmith)  ·  "&Wm_Unit')
    header(ws.Range("A8:K8"))
    ws.Range("A8").Value = "Delivery"
    ws.Range("B8:K8").Formula2 = '=IF(B$6="","",XLOOKUP(B$6,WC_Series,WC_Display,B$6))'
    for k in range(1, config.WINDOW_MONTHS + 1):
        ws.Range(f"A{8 + k}").Formula2 = f"=INDEX(Win_Label,{k})"
    cond = '(W_Series=B$6)*(W_YM=XLOOKUP($A9,Win_Label,Win_YM))'
    a = ws.Range(f"B9:K{8 + config.WINDOW_MONTHS}")
    a.Formula2 = f'=IF(B$6="","",LET(v,{wlk(cond, "W_USDMT")},IF(ISNUMBER(v),v*Wm_UF,"N/A")))'
    a.NumberFormat, a.HorizontalAlignment = S.FMT_CURRENCY, XL_RIGHT
    a.FormatConditions.Delete()
    deriv = 'COUNTIFS(W_Series,B$6,W_YM,XLOOKUP($A9,Win_Label,Win_YM),{f},"{v}")>0'
    cf(a, "=" + deriv.format(f="W_Flag", v="OUTLIER"), fill=RED_FILL, color=RED_TEXT)
    cf(a, "=" + deriv.format(f="W_Deriv", v="INTERPOLATED"), color=S.MUTED_AXIS_GRAY, italic=True)
    cf(a, "=" + deriv.format(f="W_Deriv", v="SPOT_PROXY"), italic=True, number_format=FMT_SPOT)
    cf(a, '=B9="N/A"', color=S.HIST_GRAY)
    note(ws.Range("A15"), "Grey italic = interpolated between Hammersmith's near and far quotes.  † = spot quote used "
                          "for the near month only.  Flat window quotes (OND/JFM style) fill their months.  Red = "
                          "outlier flagged for review (kept).  N/A = not quoted.")

    # Block B: Canadian FOB for the same months (from Calc)
    section(ws.Range("A18"), '="Canadian FOB  ·  "&Wm_Zone&"  ·  "&Wm_Unit')
    header(ws.Range("A19:G19"))
    ws.Range("A19").Value = "Delivery"
    ws.Range("B19:G19").Formula2 = '=IF(LEN(B$16)=0,"",B$16&" · "&SUBSTITUTE(B$17,"FOB ",""))'
    for k in range(1, config.WINDOW_MONTHS + 1):
        ws.Range(f"A{19 + k}").Formula2 = f"=INDEX(Win_Label,{k})"
    key = 'B$16&"|"&Wm_Zone&"|"&B$17&"|"&XMATCH($A20,Win_Label)'
    b = ws.Range(f"B20:G{19 + config.WINDOW_MONTHS}")
    b.Formula2 = (f'=IF(LEN(B$16)=0,"",LET(v,XLOOKUP({key},Calc_Key,CHOOSE(Wm_UnitIdx,Calc_USDMT,Calc_USDBU,'
                  f'Calc_CADMT),"N/A"),IF(ISNUMBER(v),v,"N/A")))')
    b.NumberFormat, b.HorizontalAlignment = S.FMT_CURRENCY, XL_RIGHT
    b.FormatConditions.Delete()
    cf(b, f'=XLOOKUP({key},Calc_Key,Calc_Source,"")="Manual"', italic=True, number_format=S.FMT_CURRENCY_MANUAL)
    cf(b, '=B20="N/A"', color=S.HIST_GRAY)
    note(ws.Range("A26"), "Canadian FOB from the Calc tab (PDQ or * manual indication) for the selected zone. "
                          "Same values as FOB_Summary.")

    # Block C: spread Canada - World for the selected month
    section(ws.Range("A28"), '="Spread: Canada − World  ·  "&INDEX(Win_Label,Wm_K)&"  ·  "&Wm_Unit')
    header(ws.Range("A29:K29"))
    ws.Range("A29").Value = "Canada (crop · route)"
    ws.Range("B29:K29").Formula2 = "=B8"
    helper(ws.Range("M30"), "=TRANSPOSE(B16#)")
    helper(ws.Range("N30"), "=TRANSPOSE(B17#)")
    last = 29 + CANADA_SLOTS
    ws.Range(f"A30:A{last}").Formula2 = '=IF(LEN($M30)=0,"",$M30&" · "&SUBSTITUTE($N30,"FOB ",""))'
    c = ws.Range(f"B30:K{last}")
    c.Formula2 = (f'=IF(OR(LEN($M30)=0,B$6=""),"",LET(cn,XLOOKUP($M30&"|"&Wm_Zone&"|"&$N30&"|"&Wm_K,Calc_Key,'
                  f'Calc_USDMT,""),w,{wlk("(W_Series=B$6)*(W_YM=Wm_YM)", "W_USDMT")},'
                  f'IF(AND(ISNUMBER(cn),ISNUMBER(w)),(cn-w)*Wm_UF,"N/A")))')
    c.NumberFormat, c.HorizontalAlignment = S.FMT_BASIS, XL_RIGHT
    c.FormatConditions.Delete()
    cf(c, "=AND(ISNUMBER(B30),B30<=-Wm_Band)", fill=S.CHECK_GREEN_FILL, color=S.CHECK_GREEN_TEXT)
    cf(c, "=AND(ISNUMBER(B30),B30>=Wm_Band)", fill=RED_FILL, color=RED_TEXT)
    cf(c, '=B30="N/A"', color=S.HIST_GRAY)
    chk = last + 1
    ws.Range(f"A{chk}").Value = "CHECK"
    ws.Range(f"B{chk}").Formula2 = (
        f'="Spreads "&COUNT(B30:K{last})&"  ·  N/A "&COUNTIF(B30:K{last},"N/A")&"  ·  Canada cheaper "&'
        f'COUNTIF(B30:K{last},"<="&-Wm_Band)&"  ·  Canada premium "&COUNTIF(B30:K{last},">="&Wm_Band)')
    check_row(ws.Range(f"A{chk}:K{chk}"))
    for i, t in enumerate([
        "Spread = Canadian FOB − competitor FOB, same delivery month. Green = Canada cheaper by at least the band; "
        "red = Canada dearer by at least the band (Band ± cell).",
        "FOB-to-FOB ignores ocean freight: use Global_CIF_Matrix for the landed comparison at a destination.",
        "Quality and origin differ (e.g. CWRS 13.5 vs Russia 12.5): the spread is a price gap, not a like-for-like "
        "arbitrage.  Rules: README_FOB_Logic.md section 8.",
    ], start=chk + 2):
        note(ws.Range(f"A{i}"), t)
    widths(ws, {"A": 24, **{col: 13 for col in SLOT_COLS}, "L": 3, "M": 12, "N": 12, "O": 9, "P": 9, "Q": 9})


# --------------------------------------------------------------------------- #
# Global_CIF_Matrix
# --------------------------------------------------------------------------- #
def write_cif(wb) -> None:
    ws = wb.Worksheets(L.W_CIF)
    title(ws, "Global CIF Matrix", stale_subtitle('&"  ·  "&Wc_Com&" to "&Wc_Dest&"  ·  "&INDEX(Win_Label,Wc_K)&'
                                                  '"  ·  USD/MT"'))
    control(ws, "A4", "B4", "Commodity", "=WC_Commodities", "Wheat")
    control(ws, "C4", "D4", "Destination", "=WC_Dest", "China")
    control(ws, "E4", "F4", "Ref zone", "=Cfg_ZoneList", "SW SASK")
    control(ws, "G4", "H4", "Month", "=Win_Sel", "Month 2")
    control(ws, "J4", "K4", "Band ±", "", 5)
    ws.Range("K4").NumberFormat = "0.00"
    helper(ws.Range("I4"), "=INDEX(Win_Label,Wc_K)")
    for nm, cell in [("Com", "B4"), ("Dest", "D4"), ("Zone", "F4"), ("Month", "H4"), ("Band", "K4"), ("K", "M4"),
                     ("YM", "N4")]:
        wb.Names.Add("Wc_" + nm, f"='{L.W_CIF}'!${cell[0]}${cell[1:]}")
    helper(ws.Range("M4"), "=IFERROR(MATCH(Wc_Month,Win_Sel,0),1)")
    helper(ws.Range("N4"), "=INDEX(Win_YM,Wc_K)")

    ws.Range("B6").Formula2 = (f'=LET(s,FILTER(WC_Series,(WC_SCom=Wc_Com)*(COUNTIF(W_Series,WC_Series)>0),""),'
                               f'TRANSPOSE(INDEX(s,SEQUENCE(MIN(ROWS(s),{WORLD_SLOTS})))))')
    crops = ('LET(c,FILTER(WC_PeerCrop,WC_PeerCom=Wc_Com,""),r,Rt_Display,s,SEQUENCE(1,MIN(ROWS(c)*ROWS(r),'
             f'{CANADA_SLOTS})),')
    ws.Range("B7").Formula2 = "=" + crops + "INDEX(c,INT((s-1)/ROWS(r))+1))"
    ws.Range("B8").Formula2 = "=" + crops + "INDEX(r,MOD(s-1,ROWS(r))+1))"
    for r in (6, 7, 8):
        ws.Rows(r).Hidden = True

    # Block A: landed cost build-up. Rows 12-17 Canada, 18-27 world. Helpers: M id, N route, O freight key
    section(ws.Range("A10"), '="Landed cost build-up  ·  "&Wc_Com&" to "&Wc_Dest&"  ·  "&INDEX(Win_Label,Wc_K)')
    header(ws.Range("A11:G11"), ["Origin", "FOB (USD/MT)", "Freight proxy", "Vessel", "Freight (USD/MT)",
                                 "CIF (USD/MT)", "Notes"])
    can0, w0 = 12, 12 + CANADA_SLOTS
    fr_cond = "(F_OriginKey=$O{r})*(F_Dest=Wc_Dest)*(F_Pref=1)"
    for i in range(1, CANADA_SLOTS + 1):
        r = can0 + i - 1
        helper(ws.Range(f"M{r}"), f'=IFERROR(INDEX(B$7#,1,{i})&"","")')
        helper(ws.Range(f"N{r}"), f'=IF(LEN(M{r})=0,"",INDEX(B$8#,1,{i}))')
        helper(ws.Range(f"O{r}"), f'=IF(LEN(M{r})=0,"",XLOOKUP(N{r},WC_Route,WC_RouteFr,"")&"")')
        ws.Range(f"A{r}").Formula2 = f'=IF(LEN(M{r})=0,"",M{r}&" · "&SUBSTITUTE(N{r},"FOB ",""))'
        ws.Range(f"B{r}").Formula2 = (f'=IF(LEN(M{r})=0,"",LET(v,XLOOKUP(M{r}&"|"&Wc_Zone&"|"&N{r}&"|"&Wc_K,'
                                      f'Calc_Key,Calc_USDMT,"N/A"),IF(ISNUMBER(v),v,"N/A")))')
    for j in range(1, WORLD_SLOTS + 1):
        r = w0 + j - 1
        helper(ws.Range(f"M{r}"), f'=IFERROR(INDEX(B$6#,1,{j})&"","")')
        helper(ws.Range(f"O{r}"), f'=IF(LEN(M{r})=0,"",XLOOKUP(M{r},WC_Series,WC_FrKey,"")&"")')
        ws.Range(f"A{r}").Formula2 = f'=IF(LEN(M{r})=0,"",XLOOKUP(M{r},WC_Series,WC_Display,M{r}))'
        ws.Range(f"B{r}").Formula2 = (f'=IF(LEN(M{r})=0,"",'
                                      f'{wlk(f"(W_Series=M{r})*(W_YM=Wc_YM)", "W_USDMT")})')
    last = w0 + WORLD_SLOTS - 1
    r = can0
    ws.Range(f"C{can0}:C{last}").Formula2 = (
        f'=IF(LEN($M{r})=0,"",IF($O{r}="",IF($N{r}="","delivered price - no CIF","no Hammersmith route"),'
        f'XLOOKUP($O{r},WC_FrOriginKey,WC_FrOriginLabel,$O{r})))')
    ws.Range(f"D{can0}:D{last}").Formula2 = (
        f'=IF(OR(LEN($M{r})=0,$O{r}=""),"",LET(i,XMATCH(1,{fr_cond.format(r=r)}),IF(ISNA(i),"",'
        f'IF(INDEX(F_Vessel,i)="","size only",INDEX(F_Vessel,i)))))')
    ws.Range(f"E{can0}:E{last}").Formula2 = (
        f'=IF(LEN($M{r})=0,"",IF($O{r}="","N/A",{wlk(fr_cond.format(r=r), "F_USDMT")}))')
    ws.Range(f"F{can0}:F{last}").Formula2 = (
        f'=IF(LEN($M{r})=0,"",IF(AND(ISNUMBER($B{r}),ISNUMBER($E{r})),$B{r}+$E{r},"N/A"))')
    can_flags = (f'TEXTJOIN("; ",TRUE,IF(XLOOKUP($M{r}&"|"&Wc_Zone&"|"&$N{r}&"|"&Wc_K,Calc_Key,Calc_Source,"")='
                 f'"Manual","FOB = manual indication",""),IF({wlk(fr_cond.format(r=r), "F_Flag", chr(34)*2)}='
                 f'"OUTLIER","freight outlier (kept)",""))')
    ws.Range(f"G{can0}:G{w0 - 1}").Formula2 = f'=IF(LEN($M{r})=0,"",{can_flags})'
    r, blank = w0, chr(34) * 2
    w_cond = f"(W_Series=$M{r})*(W_YM=Wc_YM)"
    world_flags = (f'TEXTJOIN("; ",TRUE,LET(d,{wlk(w_cond, "W_Deriv", blank)},IF(OR(d="",d="QUOTED"),"",'
                   f'LOWER(SUBSTITUTE(d,"_"," ")))),IF({wlk(w_cond, "W_Flag", blank)}="OUTLIER","FOB outlier (kept)",'
                   f'""),IF({wlk(fr_cond.format(r=r), "F_Flag", blank)}="OUTLIER","freight outlier (kept)",""))')
    ws.Range(f"G{w0}:G{last}").Formula2 = f'=IF(LEN($M{r})=0,"",{world_flags})'
    for col in "BEF":
        ws.Range(f"{col}{can0}:{col}{last}").NumberFormat = S.FMT_CURRENCY
        ws.Range(f"{col}{can0}:{col}{last}").HorizontalAlignment = XL_RIGHT
    blk = ws.Range(f"A{can0}:G{last}")
    blk.FormatConditions.Delete()
    cf(ws.Range(f"B{can0}:F{last}"), f'=B{can0}="N/A"', color=S.HIST_GRAY)
    b = ws.Range(f"A{w0 - 1}:G{w0 - 1}").Borders(XL_EDGE_BOTTOM)
    b.LineStyle, b.Weight = XL_CONTINUOUS, XL_MEDIUM

    # Block B: CIF spread Canada - competitor
    top = last + 2
    section(ws.Range(f"A{top}"), '="CIF spread: Canada − competitor (USD/MT)  ·  "&Wc_Com&" to "&Wc_Dest')
    header(ws.Range(f"A{top + 1}:K{top + 1}"))
    ws.Range(f"A{top + 1}").Value = "Canada (crop · route)"
    for j, col in enumerate(SLOT_COLS, start=1):
        ws.Range(f"{col}{top + 1}").Formula2 = f"=$A${w0 + j - 1}&\"\""
    s0, s1 = top + 2, top + 1 + CANADA_SLOTS
    ws.Range(f"A{s0}:A{s1}").Formula2 = f"=A{can0}&\"\""
    for j, col in enumerate(SLOT_COLS, start=1):
        wr = w0 + j - 1
        ws.Range(f"{col}{s0}:{col}{s1}").Formula2 = (
            f'=IF(OR($A{s0}="",{col}${top + 1}=""),"",IF(AND(ISNUMBER($F{can0}),ISNUMBER($F${wr})),'
            f'$F{can0}-$F${wr},"N/A"))')
    c = ws.Range(f"B{s0}:K{s1}")
    c.NumberFormat, c.HorizontalAlignment = S.FMT_BASIS, XL_RIGHT
    c.FormatConditions.Delete()
    cf(c, f"=AND(ISNUMBER(B{s0}),B{s0}<=-Wc_Band)", fill=S.CHECK_GREEN_FILL, color=S.CHECK_GREEN_TEXT)
    cf(c, f"=AND(ISNUMBER(B{s0}),B{s0}>=Wc_Band)", fill=RED_FILL, color=RED_TEXT)
    cf(c, f'=B{s0}="N/A"', color=S.HIST_GRAY)
    chk = s1 + 1
    ws.Range(f"A{chk}").Value = "CHECK"
    ws.Range(f"B{chk}").Formula2 = (
        f'="Spreads "&COUNT(B{s0}:K{s1})&"  ·  N/A "&COUNTIF(B{s0}:K{s1},"N/A")&"  ·  Canada cheaper "&'
        f'COUNTIF(B{s0}:K{s1},"<="&-Wc_Band)&"  ·  Canada premium "&COUNTIF(B{s0}:K{s1},">="&Wc_Band)')
    check_row(ws.Range(f"A{chk}:K{chk}"))
    for i, t in enumerate([
        "CIF = FOB + ocean freight (USD/MT). Freight proxies: Vancouver = US PNW, Thunder Bay = US Lakes (Duluth) "
        "salties; Hammersmith has no St. Lawrence route, so STL Direct CIF is N/A.",
        "One freight row per origin and destination: Panamax first, then Supramax, size-only, Handysize, coaster; "
        "then the lower rate (Freight_Rates matrix). Black Sea feed grain and soybeans use Russian freight.",
        "Outliers (more than 15% weekly move, or outside 10-200 USD/MT freight) are flagged for review and KEPT in "
        "CIF.  Spread = Canada CIF − competitor CIF; green = Canada cheaper landed.",
    ], start=chk + 2):
        note(ws.Range(f"A{i}"), t)
    widths(ws, {"A": 24, "B": 13, "C": 20, "D": 13, "E": 13, "F": 13, "G": 30, "H": 13, "I": 13, "J": 13, "K": 13,
                "L": 3, "M": 12, "N": 12, "O": 16})


# --------------------------------------------------------------------------- #
# FOB_Indications and Freight_Rates (filtered lists)
# --------------------------------------------------------------------------- #
def _list_formula(block: str, fields: list[str], all_cols: list[str], cond: str, sort: str) -> str:
    idx = "{" + ",".join(str(all_cols.index(f) + 1) for f in fields) + "}"
    return (f'=LET(c,INDEX({block},SEQUENCE(ROWS({block})),{idx}),v,IF(c="","",c),'
            f'f,FILTER(v,{cond},"No rows"),IF(ROWS(f)=1,f,{sort}))')


def write_indications(wb) -> None:
    ws = wb.Worksheets(L.W_IND)
    title(ws, "FOB Indications (Hammersmith)", stale_subtitle(""))
    control(ws, "A4", "B4", "Commodity", "=WC_AllCom", "All")
    wb.Names.Add("Wi_Com", f"='{L.W_IND}'!$B$4")
    fields = ["commodity", "display_name", "series_id", "origin_country", "export_port", "delivery_ym",
              "period_role", "price_type", "derivation", "fob_usd_mt", "fob_usd_bu", "bid_usd_mt", "offer_usd_mt",
              "quality_flag", "canada_peer", "crop_note", "raw_quote", "review_note"]
    labels = ["Commodity", "Origin", "Series", "Country", "Port", "Delivery", "Role", "Type", "Derivation",
              "FOB (USD/MT)", "FOB (USD/bu)", "Bid", "Offer", "Flag", "Canada peer", "Crop note", "Raw quote",
              "Review note"]
    last_col = L.col_letter(len(fields))
    header(ws.Range(f"A6:{last_col}6"), labels)
    cond = '(INDEX(W_Block,0,1)<>"")*IF(Wi_Com="All",1,INDEX(W_Block,0,' + str(L.WORLD_COLS.index("commodity") + 1) + ')=Wi_Com)'
    sort = "SORTBY(f,INDEX(f,0,1),1,IFERROR(XMATCH(INDEX(f,0,3),WC_Series),99),1,INDEX(f,0,6),1)"
    ws.Range("A7").Formula2 = _list_formula("W_Block", fields, L.WORLD_COLS, cond, sort)
    body = ws.Range(f"A7:{last_col}{6 + L.WORLD_MAX}")
    for col in "JKLM":
        ws.Range(f"{col}7:{col}{6 + L.WORLD_MAX}").NumberFormat = S.FMT_CURRENCY
    ws.Range(f"K7:K{6 + L.WORLD_MAX}").NumberFormat = '#,##0.0000;(#,##0.0000);"-"'
    body.FormatConditions.Delete()
    cf(body, '=$N7="OUTLIER"', fill=RED_FILL, color=RED_TEXT)
    cf(body, '=$N7="NO_QUOTE"', color=S.HIST_GRAY)
    cf(body, '=$I7="INTERPOLATED"', color=S.MUTED_AXIS_GRAY, italic=True)
    cf(body, '=$I7="SPOT_PROXY"', italic=True)
    note(ws.Range("D4"), "Derivation: QUOTED = published price; INTERPOLATED = between near and far quotes; WINDOW = "
                         "flat window quote; SPOT_PROXY = spot used for the near month.  Red = outlier (kept).")
    widths(ws, {"A": 12, "B": 18, "C": 15, "D": 11, "E": 13, "F": 9, "G": 8, "H": 10, "I": 13, "J": 12, "K": 12,
                "L": 10, "M": 10, "N": 10, "O": 13, "P": 10, "Q": 28, "R": 40})


def write_freight(wb) -> None:
    ws = wb.Worksheets(L.W_FRT)
    title(ws, "Ocean Freight Rates (Hammersmith)", stale_subtitle('&"  ·  USD/MT, physical freight only"'))
    dests = list(hc.CIF_DESTINATIONS)
    origins = hc.FREIGHT_ORIGIN_KEYS
    last_dc = L.col_letter(1 + len(dests))
    section(ws.Range("A4"), "Freight matrix  ·  the route row Global_CIF_Matrix uses (preferred vessel)")
    header(ws.Range(f"A5:{last_dc}5"), ["Origin \\ Destination"] + dests)
    key_col = L.col_letter(len(dests) + 3)
    r0 = 6
    for i, (k, lab) in enumerate(origins):
        ws.Range(f"A{r0 + i}").Value = lab
        ws.Range(f"{key_col}{r0 + i}").Value = k
    font(ws.Range(f"{key_col}{r0}:{key_col}{r0 + len(origins) - 1}"), 10, italic=True, color=S.MUTED_AXIS_GRAY)
    r1 = r0 + len(origins) - 1
    m = ws.Range(f"B{r0}:{last_dc}{r1}")
    cond = f"(F_OriginKey=${key_col}{r0})*(F_Dest=B$5)*(F_Pref=1)"
    m.Formula2 = "=" + wlk(cond, "F_USDMT", chr(34) * 2)
    m.NumberFormat = S.FMT_CURRENCY
    m.FormatConditions.Delete()
    cf(m, f'=COUNTIFS(F_OriginKey,${key_col}{r0},F_Dest,B$5,F_Pref,1,F_Flag,"OUTLIER")>0', fill=RED_FILL,
       color=RED_TEXT)
    note(ws.Range(f"A{r1 + 1}"), "Blank = no Hammersmith route.  Red = outlier flagged for review (kept in CIF).  "
                                 "Multi-destination routes (e.g. Algeria/Tunisia) appear under each destination.")

    top = r1 + 3
    section(ws.Range(f"A{top}"), "Freight rows (filtered)")
    control(ws, f"A{top + 1}", f"B{top + 1}", "Origin", "=WC_FrOriginAll", "All")
    control(ws, f"C{top + 1}", f"D{top + 1}", "Destination", "=WC_DestAll", "All")
    control(ws, f"E{top + 1}", f"F{top + 1}", "Vessel", "=WC_Vessel", "All")
    for nm, cell in [("Origin", f"B{top + 1}"), ("Dest", f"D{top + 1}"), ("Vessel", f"F{top + 1}")]:
        wb.Names.Add("Wf_" + nm, f"='{L.W_FRT}'!${cell[0]}${cell[1:]}")
    fields = ["origin", "origin_country", "destination", "dest_port", "vessel_class", "vessel_size",
              "discharge_rate", "route_note", "freight_usd_mt", "low_usd_mt", "high_usd_mt", "change_usd_mt",
              "status_note", "quality_flag", "cif_pref", "destination_raw", "raw_label", "review_note"]
    labels = ["Origin", "Country", "Destination", "Dest. port", "Vessel", "Size (MT)", "Discharge", "Note",
              "Freight (USD/MT)", "Low", "High", "Change", "Status", "Flag", "Used in CIF", "Destination (raw)",
              "Raw label", "Review note"]
    last_col = L.col_letter(len(fields))
    hr = top + 3
    header(ws.Range(f"A{hr}:{last_col}{hr}"), labels)
    col = lambda f: f"INDEX(F_Block,0,{L.FREIGHT_COLS.index(f) + 1})"  # noqa: E731
    cond = (f'(F_Key<>"")*IF(Wf_Origin="All",1,F_OriginKey=XLOOKUP(Wf_Origin,WC_FrOriginLabel,WC_FrOriginKey,""))'
            f'*IF(Wf_Dest="All",1,F_Dest=Wf_Dest)*IF(Wf_Vessel="All",1,IF(Wf_Vessel="Size only",'
            f'{col("vessel_class")}="",{col("vessel_class")}=Wf_Vessel))')
    ws.Range(f"A{hr + 1}").Formula2 = _list_formula("F_Block", fields, L.FREIGHT_COLS, cond,
                                                    "SORTBY(f,INDEX(f,0,1),1,INDEX(f,0,2),1,INDEX(f,0,3),1)")
    end = hr + L.WORLD_MAX
    for c in "IJKL":
        ws.Range(f"{c}{hr + 1}:{c}{end}").NumberFormat = S.FMT_CURRENCY
    ws.Range(f"L{hr + 1}:L{end}").NumberFormat = S.FMT_BASIS
    body = ws.Range(f"A{hr + 1}:{last_col}{end}")
    body.FormatConditions.Delete()
    cf(body, f'=$N{hr + 1}="OUTLIER"', fill=RED_FILL, color=RED_TEXT)
    cf(body, f'=$N{hr + 1}="NO_QUOTE"', color=S.HIST_GRAY)
    widths(ws, {"A": 20, **{L.col_letter(c): 11 for c in range(2, 2 + len(dests))}, "H": 14, "P": 16, "Q": 34,
                "R": 34, key_col: 16})


# --------------------------------------------------------------------------- #
# Control tab block
# --------------------------------------------------------------------------- #
CONTROL_TOP = 33


def write_control(wb) -> None:
    ws = wb.Worksheets(L.CONTROL)
    t = CONTROL_TOP
    header(ws.Range(f"A{t}:C{t}"), ["World Data (Hammersmith)", "Value", "Status"])
    rows = [
        ("Hammersmith report date", "=IF(World_Date=\"\",\"-\",World_Date)", S.FMT_DATE,
         '=IF(World_Date="","No world data - run Refresh_FOB.bat","OK")'),
        ("Report age (days)", '=IF(World_Date="","-",World_Age)', "0",
         '=IF(World_Date="","-",IF(World_Age>WC_StaleDays,"STALE - no new weekly report","OK"))'),
        ("World step status", "=World_Status&\"\"", None,
         '=IF(World_Status="OK","OK","Previous drop kept - see run_pull messages")'),
        ("World FOB rows loaded", '=COUNTA(W_Key)', S.FMT_QTY, f'=IF(B{t + 4}>0,"OK","EMPTY")'),
        ("Freight rows loaded", '=COUNTA(F_Key)', S.FMT_QTY, f'=IF(B{t + 5}>0,"OK","EMPTY")'),
        ("Outliers flagged (kept)", '=COUNTIF(W_Flag,"OUTLIER")+COUNTIF(F_Flag,"OUTLIER")', S.FMT_QTY,
         f'=IF(B{t + 6}=0,"none","Review on FOB_Indications / Freight_Rates (red rows)")'),
        ("Unmapped labels", '=COUNTIF(W_Flag,"UNMAPPED")+COUNTIF(F_Flag,"UNMAPPED")', S.FMT_QTY,
         f'=IF(B{t + 7}=0,"OK","New Hammersmith label - add a rule in Hammersmith/hs_config.py")'),
    ]
    for i, (lab, fb, fmt, fc) in enumerate(rows, start=t + 1):
        ws.Range(f"A{i}").Value = lab
        font(ws.Range(f"A{i}:C{i}"))
        ws.Range(f"B{i}").Formula2 = fb
        ws.Range(f"B{i}").HorizontalAlignment = XL_RIGHT
        if fmt:
            ws.Range(f"B{i}").NumberFormat = fmt
        ws.Range(f"C{i}").Formula2 = fc
    chk = t + len(rows) + 1
    ws.Range(f"A{chk}").Value = "CHECK"
    ws.Range(f"B{chk}").Formula2 = (f'=IF(AND(B{t + 4}>0,B{t + 5}>0,B{t + 7}=0),IF(OR(World_Status<>"OK",'
                                    f'World_Age>WC_StaleDays),"PASS (review status)","PASS"),"REVIEW")')
    ws.Range(f"C{chk}").Formula2 = (f'=IF(LEFT(B{chk},4)="PASS","World data loaded; outliers stay in the data",'
                                    f'"See the Status column above")')
    check_row(ws.Range(f"A{chk}:C{chk}"))


def write(wb) -> None:
    write_config(wb)
    write_matrix(wb)
    write_cif(wb)
    write_indications(wb)
    write_freight(wb)
    write_control(wb)
    log.info("world tabs wired: %s", ", ".join(VIEW_TABS))
