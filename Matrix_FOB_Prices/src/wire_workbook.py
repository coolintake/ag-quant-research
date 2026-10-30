"""Step 3 of the build: wire Matrix_FOB_Calculator.xlsx through Excel itself (COM).

  * Power Query: Q_PDQ, Q_FX, Q_History (CSV drops) and Q_Costing (CAN_Costing_Input.xlsx / Raw),
    each loaded to a table on its Data_* tab (overwrite-in-place, synchronous refresh)
  * every formula, entered with Range.Formula2 so dynamic arrays (FILTER/XLOOKUP/LET/LAMBDA) work
  * manual-indication markers (conditional formatting) and the Historical_Charts line chart bound to
    spilling ranges, styled per the Design folder
  * the world (Hammersmith) tabs - Q_World / Q_Freight queries plus World_FOB_Matrix, Global_CIF_Matrix,
    FOB_Indications, Freight_Rates and the hidden World_Config (wire_world.py)
  * Refresh All, full recalculation, save

Idempotent: re-running updates queries/formulas and rebuilds the chart; data and Manual_Inputs stay.
The workbook must be closed in Excel while this runs. A timestamped backup is written to cache/backup first.

    python src/wire_workbook.py
"""

from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime

import config
import fob_styles as S
import layout as L
import wire_world
from excel_com import find_open_workbook

log = logging.getLogger("wire")
XL_SRC_EXTERNAL, XL_YES, XL_CMD_SQL, XL_OVERWRITE = 0, 1, 2, 0
XL_EXPRESSION, XL_LINE, XL_TIMESCALE, XL_UPWARD, XL_LEGEND_RIGHT, XL_MARKER_NONE = 2, 4, 3, -4171, -4152, -4142


# --------------------------------------------------------------------------- #
# Power Query (M)
# --------------------------------------------------------------------------- #
def m_csv(path, columns: list[str], types: dict[str, str]) -> str:
    cols = "{" + ", ".join(f'"{c}"' for c in columns) + "}"
    tt = "{" + ", ".join(f'{{"{c}", {types.get(c, "type text")}}}' for c in columns) + "}"
    return f'''let
    Source = Csv.Document(File.Contents("{path}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Selected = Table.SelectColumns(Promoted, {cols}, MissingField.UseNull),
    Typed = Table.TransformColumnTypes(Selected, {tt}, "en-US"),
    Clean = Table.ReplaceErrorValues(Typed, List.Transform({cols}, each {{_, null}}))
in
    Clean'''


def m_costing() -> str:
    raw_routes = "{" + ", ".join(f'"{r["raw"]}"' for r in config.ROUTES) + "}"
    ren = ('{{"Commodity","commodity"},{"PDQ Zone","zone"},{"Route","route"},{"Primary Elevation ($/MT)","primary"},'
           '{"Rail Freight ($/MT)","rail"},{"Port Elevation ($/MT)","port_elev"},{"Export Margin ($/MT)","margin"},'
           '{"Other Fees ($/MT)","other_fees"}}')
    nums = "{" + ", ".join(f'{{"{c}", each try Number.From(_) otherwise null, type nullable number}}'
                           for c in config.COST_COMPONENTS) + "}"
    order = "{" + ", ".join(f'"{c}"' for c in L.COST_COLS) + "}"
    return f'''let
    Source = Excel.Workbook(File.Contents("{config.COSTING_XLSX}"), null, true),
    Raw = Source{{[Item="{config.COSTING_SHEET}", Kind="Sheet"]}}[Data],
    Skipped = Table.Skip(Raw, 1),
    Promoted = Table.PromoteHeaders(Skipped, [PromoteAllScalars=true]),
    Rows = Table.SelectRows(Promoted, each List.Contains({raw_routes}, [Route])),
    Selected = Table.SelectColumns(Rows, {{"Commodity","PDQ Zone","Route","Primary Elevation ($/MT)","Rail Freight ($/MT)",
        "Port Elevation ($/MT)","Export Margin ($/MT)","Other Fees ($/MT)"}}),
    Renamed = Table.RenameColumns(Selected, {ren}),
    Numbers = Table.TransformColumns(Renamed, {nums}),
    Keyed = Table.AddColumn(Numbers, "key", each Text.From([commodity]) & "|" & Text.From([zone]) & "|" & Text.From([route]), type text),
    Ordered = Table.SelectColumns(Keyed, {order})
in
    Ordered'''


def queries() -> dict[str, str]:
    n, d, t = "type number", "type date", "type text"
    return {
        "Q_PDQ": m_csv(config.PDQ_LATEST_CSV, L.PDQ_COLS, {"price_date": d, "cash_cad_mt": n, "futures_px": n,
                                                          "local_basis": n}),
        "Q_FX": m_csv(config.FX_CSV, L.FX_COLS, {"as_of": d, "run_month": d, "pdq_date": d, "pdq_age_days": "Int64.Type",
                                                  "fx_date": d, "usdcad": n, "world_report_date": d,
                                                  **{c: "Int64.Type" for c in L.FX_COLS[11:] if c != "world_status"}}),
        "Q_Costing": m_costing(),
        "Q_History": m_csv(config.HISTORY_CSV, L.HIST_COLS, {
            "week": d, "horizon": "Int64.Type", **{c: n for c in L.HIST_COLS[5:19] if c != "futures_code"}}),
        "Q_World": m_csv(wire_world.hc.WORLD_FOB_CSV, L.WORLD_COLS, {
            "report_date": d, **{c: n for c in ("protein_pct", "bid_usd_mt", "offer_usd_mt", "fob_usd_mt",
                                                "fob_usd_bu")}}),
        "Q_Freight": m_csv(wire_world.hc.WORLD_FREIGHT_CSV, L.FREIGHT_COLS, {
            "report_date": d, "cif_pref": "Int64.Type", **{c: n for c in ("low_usd_mt", "high_usd_mt",
                                                                          "freight_usd_mt", "change_usd_mt")}}),
    }


def wire_queries(wb) -> None:
    existing = {q.Name: q for q in wb.Queries}
    for qname, m in queries().items():
        if qname in existing:
            existing[qname].Formula = m
        else:
            wb.Queries.Add(qname, m)
        sheet, tname = L.QUERIES[qname]
        ws = wb.Worksheets(sheet)
        if ws.ListObjects.Count == 0:
            src = (f"OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location={qname};"
                   'Extended Properties=""')
            lo = ws.ListObjects.Add(XL_SRC_EXTERNAL, src, True, XL_YES, ws.Range("$A$1"))
            qt = lo.QueryTable
            qt.CommandType = XL_CMD_SQL
            qt.CommandText = f"SELECT * FROM [{qname}]"
            qt.RowNumbers = False
            qt.FillAdjacentFormulas = False
            qt.PreserveFormatting = True
            qt.RefreshOnFileOpen = False
            qt.BackgroundQuery = False
            qt.RefreshStyle = XL_OVERWRITE
            qt.SavePassword = False
            qt.SaveData = True
            qt.AdjustColumnWidth = True
            qt.PreserveColumnInfo = True
            lo.DisplayName = tname
            lo.TableStyle = "TableStyleLight1"
        lo = ws.ListObjects(1)
        lo.QueryTable.Refresh(False)
        hdr = lo.HeaderRowRange
        hdr.Interior.Color = S.rgb_int(S.HEADER_BLACK)
        hdr.Font.Color = S.rgb_int(S.HEADER_TEXT_WHITE)
        hdr.Font.Bold = True
        log.info("query %-9s -> %s.%s: %d rows", qname, sheet, tname, lo.ListRows.Count)
    for nm, ref in L.data_names().items():      # table inserts shift names; pin them back
        wb.Names.Add(nm, ref)


# --------------------------------------------------------------------------- #
# Formulas
# --------------------------------------------------------------------------- #
def lk(key: str, keys: str, col: str) -> str:
    """Lookup that returns "" for a missing key OR an empty cell (XLOOKUP alone would return 0)."""
    return f'LET(i,XMATCH({key},{keys}),IF(ISNA(i),"",IF(INDEX({col},i)="","",INDEX({col},i))))'


def calc_formulas() -> dict[str, str]:
    r = L.CALC_FIRST
    manual = (f'LET(f,LAMBDA(k,LET(d,MAXIFS(Man_Date,Man_Key,k,Man_Valid,TRUE),IF(d=0,"",'
              f'XLOOKUP(1,(Man_Key=k)*(Man_Date=d)*(Man_Valid=TRUE),Man_PriceMT,"",0,-1)))),'
              f'v,f(A{r}&"|"&B{r}&"|"&K{r}),IF(ISNUMBER(v),v,f(A{r}&"|ALL|"&K{r})))')
    cost_key = f'$G{r}&"|"&$B{r}&"|"&$Q{r}'
    pdq_key = f'F{r}&"|"&B{r}&"|"&K{r}'
    return {
        "E": f'=A{r}&"|"&B{r}&"|"&C{r}&"|"&D{r}',
        "F": f'=LET(p,XLOOKUP(A{r},Cfg_Crop,Cfg_PDQ,"none"),IF(p="none","",p))',
        "G": f'=XLOOKUP(A{r},Cfg_Crop,Cfg_CostCom,"")',
        "H": f'=XLOOKUP(A{r},Cfg_Crop,Cfg_Bu,"")',
        "I": f'=XLOOKUP(A{r},Cfg_Crop,Cfg_BasisType,"NONE")',
        "J": f'=LET(az,XLOOKUP(A{r},Cfg_Crop,Cfg_Zones,"ALL"),OR(az="ALL",ISNUMBER(SEARCH(";"&B{r}&";",";"&az&";"))))',
        "K": f'=INDEX(Win_YM,D{r})',
        "L": f'=INDEX(Win_Date,D{r})',
        "M": f'=IF(F{r}="","",{lk(pdq_key, "PDQ_Key", "PDQ_Cash")})',
        "N": f"={manual}",
        "O": f'=IF(NOT(J{r}),"Excluded",IF(ISNUMBER(M{r}),M{r},IF(ISNUMBER(N{r}),N{r},"N/A")))',
        "P": f'=IF(NOT(J{r}),"Excluded",IF(ISNUMBER(M{r}),"PDQ",IF(ISNUMBER(N{r}),"Manual","N/A")))',
        "Q": f'=XLOOKUP(C{r},Rt_Display,Rt_Raw,"")',
        "R": "=" + lk(cost_key, "Cost_Key", "Cost_Primary"),
        "S": "=" + lk(cost_key, "Cost_Key", "Cost_Rail"),
        "T": "=" + lk(cost_key, "Cost_Key", "Cost_Port"),
        "U": "=" + lk(cost_key, "Cost_Key", "Cost_Margin"),
        "V": "=" + lk(cost_key, "Cost_Key", "Cost_Other"),
        "W": f'=IF(COUNT(R{r}:V{r})=5,SUM(R{r}:V{r}),"n/a")',
        "X": f'=IF(AND(ISNUMBER(O{r}),ISNUMBER(W{r})),O{r}+W{r},"N/A")',
        "Y": f'=IF(ISNUMBER(X{r}),X{r}/FX_USDCAD,"N/A")',
        "Z": f'=IF(ISNUMBER(Y{r}),Y{r}/H{r},"N/A")',
        "AA": f'=IF(OR(I{r}="NONE",F{r}=""),"",{lk(pdq_key, "PDQ_Key", "PDQ_FutCode")})',
        "AB": f'=IF(AA{r}="","",{lk(pdq_key, "PDQ_Key", "PDQ_FutPx")})',
        "AC": f'=IF(OR(AB{r}="",NOT(ISNUMBER(X{r}))),"N/A",IF(I{r}="USD_BU",Y{r}/H{r}-AB{r},X{r}-AB{r}))',
        "AD": f'=IF(I{r}="USD_BU","USD/bu",IF(I{r}="CAD_MT","CAD/MT","-"))',
        "AE": f'=IF(AND(I{r}="CAD_MT",ISNUMBER(AC{r})),AC{r}/FX_USDCAD,"")',
    }


def manual_formulas() -> dict[str, str]:
    r = L.MAN_FIRST
    return {
        "I": f'=IF(OR(E{r}="",F{r}=""),"",IF(F{r}="CAD/bu",E{r}*XLOOKUP(B{r},Cfg_Crop,Cfg_Bu,""),E{r}))',
        "J": (f'=IF(D{r}="","",LET(ym,IF(ISNUMBER(D{r}),TEXT(D{r},"yyyy-mm"),XLOOKUP(D{r},Win_Label,Win_YM,"")),'
              f'IF(ISNUMBER(MATCH(ym,Win_YM,0)),ym,"OUT")))'),
        "K": f'=IF(OR(B{r}="",C{r}="",J{r}="",J{r}="OUT"),"",B{r}&"|"&C{r}&"|"&J{r})',
        "L": (f'=IF(COUNTA(A{r}:F{r})=0,"",IF(COUNTA(A{r}:F{r})<6,"INCOMPLETE",IF(J{r}="OUT","OUT OF WINDOW",'
              f'IF(NOT(ISNUMBER(I{r})),"INVALID",IF(AsOfDate-A{r}>Set_ManualStale,"STALE","OK")))))'),
        "M": f'=OR(L{r}="OK",L{r}="STALE")',
    }


def write_formulas(wb) -> None:
    cfg = wb.Worksheets(L.CFG)
    w1, w2 = 5, 4 + config.WINDOW_MONTHS
    cfg.Range(f"Q{w1}:Q{w2}").Formula2 = f"=EDATE(RunMonth,O{w1}-1)"
    cfg.Range(f"R{w1}:R{w2}").Formula2 = f'=TEXT(Q{w1},"yyyy-mm")'
    # label is deliberately NOT date-like ("Oct-26 delivery"): Excel converts "Oct-26" into a date on entry
    cfg.Range(f"S{w1}:S{w2}").Formula2 = f'=TEXT(Q{w1},"mmm-yy")&" delivery"'

    calc = wb.Worksheets(L.CALC)
    for col, f in calc_formulas().items():
        calc.Range(f"{col}{L.CALC_FIRST}:{col}{L.CALC_LAST}").Formula2 = f
    man = wb.Worksheets(L.MANUAL)
    for col, f in manual_formulas().items():
        man.Range(f"{col}{L.MAN_FIRST}:{col}{L.MAN_LAST}").Formula2 = f

    ctl = wb.Worksheets(L.CONTROL)
    status = {
        5: ("=AsOfDate", S.FMT_DATE, ""), 6: ("=RunMonth", "mmm-yyyy", ""), 7: ("=PDQ_Date", S.FMT_DATE, ""),
        8: ("=PDQ_Age", "0", '=IF(PDQ_Age>Set_PDQStale,"STALE - download a new PDQ CSV","OK")'),
        9: ("=FX_USDCAD", S.FMT_FX, ""), 10: ("=FX_Date", S.FMT_DATE,
                                             '=IF(PDQ_Date-FX_Date>4,"FX older than price date","OK")'),
        11: ("=FX_Source", None, ""), 12: ("=Costing_Modified", None, ""), 13: ("=Drop_Generated", None, ""),
        16: ("=COUNTA(PDQ_Key)", S.FMT_QTY, '=IF(B16>0,"OK","EMPTY - run run_pull.py")'),
        17: ("=COUNTA(Cost_Key)", S.FMT_QTY, '=IF(B17>0,"OK","EMPTY - check CAN_Costing_Input.xlsx")'),
        18: ("=COUNTA(H_Week)", S.FMT_QTY, '=IF(B18>0,"OK","EMPTY - run run_historical_drop.py")'),
        19: ('=COUNTIF(Calc_Source,"PDQ")', S.FMT_QTY, ""),
        20: ('=COUNTIF(Calc_Source,"Manual")', S.FMT_QTY, ""),
        21: ('=COUNTIF(Calc_Source,"N/A")+COUNTIF(Calc_Source,"Excluded")', S.FMT_QTY, ""),
        22: ('=COUNTIF(Man_Status,"OK")', S.FMT_QTY,
             '="OK "&COUNTIF(Man_Status,"OK")&" / stale "&COUNTIF(Man_Status,"STALE")&" / rejected "&'
             '(COUNTIF(Man_Status,"INCOMPLETE")+COUNTIF(Man_Status,"INVALID")+COUNTIF(Man_Status,"OUT OF WINDOW"))'),
        23: ("=SUMPRODUCT(--ISERROR(Calc_Cash))+SUMPRODUCT(--ISERROR(Calc_USDMT))+SUMPRODUCT(--ISERROR(Calc_XBasis))",
             S.FMT_QTY, '=IF(B23=0,"OK","ERRORS - see Calc")'),
        24: ('=IF(AND(B16>0,B17>0,B18>0,B23=0),IF(PDQ_Age>Set_PDQStale,"PASS (stale PDQ)","PASS"),"REVIEW")', None,
             '=IF(LEFT(B24,4)="PASS","All inputs loaded, no formula errors","See the Status column above")'),
    }
    for row, (fb, fmt, fc) in status.items():
        ctl.Range(f"B{row}").Formula2 = fb
        if fmt:
            ctl.Range(f"B{row}").NumberFormat = fmt
        if fc:
            ctl.Range(f"C{row}").Formula2 = fc
    ctl.Range("B11:B13").HorizontalAlignment = -4152

    subtitle = ('="As of "&TEXT(AsOfDate,"yyyy-mm-dd")&"  ·  PDQ "&TEXT(PDQ_Date,"yyyy-mm-dd")&" ("&PDQ_Age&'
                '" days old)  ·  USD/CAD "&TEXT(FX_USDCAD,"0.0000")&" ("&TEXT(FX_Date,"yyyy-mm-dd")&")"')
    fob = wb.Worksheets(L.FOB)
    cr = L.FOB_CTRL_ROW
    fob.Range("A2").Formula2 = subtitle + '&"  ·  "&Sel_Unit&"  ·  "&INDEX(Win_Label,Sel_K)'
    fob.Range(f"C{cr}").Formula2 = "=INDEX(Win_Label,Sel_K)"
    fob.Range(f"H{cr}").Formula2 = "=MATCH(Sel_Month,Win_Sel,0)"
    fob.Range(f"I{cr}").Formula2 = "=MATCH(Sel_Unit,Cfg_FOBUnits,0)"
    n = len(config.CROPS)
    for b in range(len(config.ROUTES)):
        top = L.FOB_BLOCK_START + b * L.FOB_BLOCK_SIZE
        a, z = top + 2, top + 1 + n
        key = f'$A{a}&"|"&B${top + 1}&"|"&$A${top}&"|"&Sel_K'
        rng = fob.Range(f"B{a}:J{z}")
        rng.Formula2 = (f'=LET(v,XLOOKUP({key},Calc_Key,CHOOSE(Sel_UnitIdx,Calc_USDMT,Calc_USDBU,Calc_CADMT),"N/A"),'
                        f'IF(ISNUMBER(v),v,"N/A"))')
        rng.FormatConditions.Delete()
        fc = rng.FormatConditions.Add(XL_EXPRESSION, None, f'=XLOOKUP({key},Calc_Key,Calc_Source,"")="Manual"')
        fc.NumberFormat = S.FMT_CURRENCY_MANUAL
        fc.Font.Italic = True
        chk = z + 1
        fob.Range(f"B{chk}").Formula2 = (f'="Priced "&COUNT(B{a}:J{z})&"  ·  manual "&COUNTIFS(Calc_Route,$A${top},'
                                         f'Calc_K,Sel_K,Calc_Source,"Manual")&"  ·  N/A "&COUNTIF(B{a}:J{z},"N/A")')

    bas = wb.Worksheets(L.BASIS)
    bas.Range("A2").Formula2 = subtitle + '&"  ·  "&INDEX(Win_Label,Sel_BK)'
    bas.Range(f"C{cr}").Formula2 = "=INDEX(Win_Label,Sel_BK)"
    bas.Range(f"E{cr}").Formula2 = "=MATCH(Sel_BMonth,Win_Sel,0)"
    col_name = {"AC": "Calc_XBasis", "AE": "Calc_XBasisUSD"}
    for b in range(len(config.ROUTES)):
        top = L.BASIS_BLOCK_START + b * L.BASIS_BLOCK_SIZE
        for i, (_, crop, col, _) in enumerate(L.BASIS_LINES):
            rr = top + 2 + i
            bas.Range(f"C{rr}:K{rr}").Formula2 = (
                f'=LET(v,XLOOKUP("{crop}|"&C${top + 1}&"|"&$A${top}&"|"&Sel_BK,Calc_Key,{col_name[col]},"N/A"),'
                f'IF(ISNUMBER(v),v,"N/A"))')
            pick = f'(Calc_Crop="{crop}")*(Calc_K=Sel_BK)*(Calc_FutCode<>"")'
            bas.Range(f"L{rr}").Formula2 = f'=IFERROR(INDEX(FILTER(Calc_FutCode,{pick}),1),"-")'
            bas.Range(f"M{rr}").Formula2 = f'=IFERROR(INDEX(FILTER(Calc_FutPx,{pick}),1),"-")'
        a, z = top + 2, top + 1 + len(L.BASIS_LINES)
        bas.Range(f"C{z + 1}").Formula2 = f'="Priced "&COUNT(C{a}:K{z})&"  ·  N/A "&COUNTIF(C{a}:K{z},"N/A")'


def write_history(wb) -> None:
    ws = wb.Worksheets(L.HIST)
    helpers = {
        4: '=IF(Hc_RangeYears>=99,DATE(2019,1,1),EDATE(MAX(H_Week),-12*Hc_RangeYears))',
        5: "=XLOOKUP(Hc_H,Hz_List,Hz_Val,0)",
        6: "=XLOOKUP(Hc_Crop,Cfg_Crop,Cfg_Bu,1)",
        7: '=XLOOKUP(Hc_Crop,Cfg_Crop,Cfg_BasisType,"NONE")',
        8: "=XMATCH(Hc_Metric,Mt_Name)",
        9: '=INDEX(Mt_PerBu,Hc_MRow)="Y"',
        10: "=INDEX(Mt_VC,Hc_MRow)>0",
        11: "=XLOOKUP(Hc_Range,Rg_List,Rg_Years,99)",
    }
    for row, f in helpers.items():
        ws.Range(f"Q{row}").Formula2 = f
    ws.Range("P12").Value = "Chart title"
    ws.Range("Q12").Formula2 = '=Hc_Title&IF(Hc_RouteDep,CHAR(10)&"pro-forma at current costing","")'
    wb.Names.Add("Hc_ChartTitle", f"='{L.HIST}'!$Q$12")
    ws.Range(L.HC_TITLE_CELL).Formula2 = (
        '=Hc_Crop&"  ·  "&Hc_Metric&IF(Hc_RouteDep,"  ·  "&IF(Hc_Cmp="Routes","all routes",Hc_Route),"")&"  ·  "&'
        'IF(Hc_Cmp="Zones","all zones (focus "&Hc_Zone&")",Hc_Zone)&"  ·  "&Hc_H&"  ·  "&Hc_Range')
    ws.Range(L.HC_UNIT_CELL).Formula2 = ('=INDEX(IF(Hc_Type="USD_BU",Mt_UnitW,IF(Hc_Type="CAD_MT",Mt_UnitC,Mt_UnitO)),'
                                         'Hc_MRow)')

    first, last = L.HC_SLOT_COLS[0], L.HC_SLOT_COLS[-1]
    sr, zr, rr, nr, cr, pr = (L.HC_SLOT_ROW, L.HC_ZONE_ROW, L.HC_ROUTE_ROW, L.HC_NAME_ROW, L.HC_COLIDX_ROW,
                              L.HC_SPILL_ROW)
    ws.Range(f"{first}{zr}:{last}{zr}").Formula2 = (
        f'=LET(s,{first}${sr},oz,FILTER(Cfg_ZoneList,Cfg_ZoneList<>Hc_Zone),IF(Hc_Cmp="Zones",IF(s=1,Hc_Zone,'
        f'INDEX(oz,s-1)),IF(OR(s=1,AND(Hc_Cmp="Routes",s<=ROWS(Rt_Display))),Hc_Zone,"")))')
    ws.Range(f"{first}{rr}:{last}{rr}").Formula2 = (
        f'=LET(s,{first}${sr},orr,FILTER(Rt_Display,Rt_Display<>Hc_Route),IF({first}${zr}="","",'
        f'IF(Hc_Cmp="Routes",IF(s=1,Hc_Route,INDEX(orr,s-1)),Hc_Route)))')
    ws.Range(f"{first}{nr}:{last}{nr}").Formula2 = (
        f'=IF({first}${zr}=""," ",IF(Hc_Cmp="Zones",{first}${zr},IF(Hc_Cmp="Routes",{first}${rr},'
        f'{first}${zr}&" · "&{first}${rr})))')
    ws.Range(f"{first}{cr}:{last}{cr}").Formula2 = (
        f'=IF({first}${zr}="","",IF(Hc_RouteDep,INDEX(CHOOSE(XMATCH({first}${rr},Rt_Display),Mt_VC,Mt_TBAY,Mt_STL),'
        f'Hc_MRow),INDEX(Mt_Base,Hc_MRow)))')
    hc = L.HC_HELP_COL
    ws.Range(f"{hc}{pr}").Formula2 = '=SORT(UNIQUE(FILTER(H_Week,(H_Week<>"")*(H_Week>=Hc_Start),"")))'
    wb.Names.Add("HC_Weeks", f"='{L.HIST}'!${hc}${pr}#")
    for s, col in enumerate(L.HC_SLOT_COLS, start=1):
        ws.Range(f"{col}{pr}").Formula2 = (
            f'=LET(w,HC_Weeks,blank,IF(SEQUENCE(ROWS(w)),NA()),IF({col}${cr}="",blank,'
            f'LET(c,INDEX(H_Block,0,{col}${cr}),m,(H_Crop=Hc_Crop)*(H_Zone={col}${zr})*(H_H=Hc_HVal)*'
            f'(H_Week>=Hc_Start)*(c<>""),IF(SUM(m)=0,blank,'
            f'XLOOKUP(w,FILTER(H_Week,m),FILTER(c,m)/IF(Hc_PerBu,Hc_Bu,1),NA())))))')
        wb.Names.Add(f"HC_S{s}", f"='{L.HIST}'!${col}${pr}#")
    ws.Range(f"{hc}{pr}:{hc}{pr + 500}").NumberFormat = S.FMT_DATE
    ws.Range(f"{first}{pr}:{last}{pr + 500}").NumberFormat = "#,##0.00"
    ws.Range(L.HC_NOTE_CELL).Formula2 = (
        '=IF(Hc_RouteDep,"Pro-forma at current costing: historical cash + today\'s CAN_Costing_Input costs"&'
        'IF(COUNTIF(H_CostBasis,"recorded")>0,"; recorded costs from "&TEXT(MINIFS(H_Week,H_CostBasis,"recorded"),'
        '"yyyy-mm-dd"),"")&". ","")&IF(COUNT(HC_S1)=0,"No data for this selection (non-hedgeable crop, route not '
        'costed, or zone not quoted).","")&IF(Hc_Cmp="Routes"," Comparing routes: cash/futures/local-basis metrics are '
        'route-independent, so the lines coincide.","")')


def build_chart(wb) -> None:
    ws = wb.Worksheets(L.HIST)
    for co in list(ws.ChartObjects()):
        co.Delete()
    a, b = ws.Range(L.HC_CHART_ANCHOR[0]), ws.Range(f"{L.HC_CHART_ANCHOR[0]}:{L.HC_CHART_ANCHOR[1]}")
    shp = ws.Shapes.AddChart2(-1, XL_LINE, a.Left, a.Top, b.Width, b.Height)
    ch = shp.Chart
    while ch.SeriesCollection().Count:
        ch.SeriesCollection(1).Delete()
    book = wb.Name
    for s, col in enumerate(L.HC_SLOT_COLS, start=1):
        ser = ch.SeriesCollection().NewSeries()
        ser.Formula = (f"=SERIES('{L.HIST}'!${col}${L.HC_NAME_ROW},'{book}'!HC_Weeks,'{book}'!HC_S{s},{s})")
        color, width, dash = S.LINE_SLOTS[s - 1]
        ser.MarkerStyle = XL_MARKER_NONE
        ser.Smooth = False
        ln = ser.Format.Line
        ln.Visible = True
        ln.ForeColor.RGB = S.rgb_int(color)
        ln.Weight = width
        ln.DashStyle = dash
    gray = S.rgb_int(S.MUTED_AXIS_GRAY)
    ch.HasTitle = True
    ch.ChartTitle.Format.TextFrame2.TextRange.Font.Size = S.CHART_TITLE_PT
    ch.ChartTitle.Format.TextFrame2.TextRange.Font.Bold = False
    ch.ChartTitle.Format.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = gray
    ch.ChartTitle.Format.TextFrame2.TextRange.Font.Name = S.FONT
    cat, val = ch.Axes(1), ch.Axes(2)
    cat.CategoryType = XL_TIMESCALE
    cat.TickLabels.NumberFormat = "mmm-yy"
    cat.TickLabels.Orientation = XL_UPWARD
    cat.HasMajorGridlines = False
    val.HasMajorGridlines = True
    val.MajorGridlines.Format.Line.ForeColor.RGB = S.rgb_int(S.GRIDLINE_GRAY)
    val.TickLabels.NumberFormat = "#,##0.00"
    val.HasTitle = True
    for ax in (cat, val):
        ax.TickLabels.Font.Size = S.AXIS_PT
        ax.TickLabels.Font.Color = gray
        ax.TickLabels.Font.Name = S.FONT
    val.AxisTitle.Format.TextFrame2.TextRange.Font.Size = S.AXIS_PT
    val.AxisTitle.Format.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = gray
    ch.HasLegend = True
    ch.Legend.Position = XL_LEGEND_RIGHT
    ch.Legend.Font.Size = S.AXIS_PT
    ch.Legend.Font.Color = gray
    ch.PlotArea.Format.Fill.ForeColor.RGB = S.rgb_int("FFFFFF")
    ch.ChartArea.Format.Fill.ForeColor.RGB = S.rgb_int("FFFFFF")
    ch.ChartArea.Format.Line.Visible = True
    ch.ChartArea.Format.Line.ForeColor.RGB = S.rgb_int(S.HEADER_BLACK)
    ch.ChartArea.Format.Line.Weight = 0.75
    # Link titles LAST: restyling title text afterwards would replace the link with static text.
    ch.ChartTitle.Formula = f"='{L.HIST}'!$Q$12"                                   # Hc_ChartTitle
    val.AxisTitle.Formula = f"='{L.HIST}'!${L.HC_UNIT_CELL[0]}${L.HC_UNIT_CELL[1:]}"  # Hc_Unit
    shp.Name = "FOB_History_Chart"


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    if not config.WORKBOOK.exists():
        log.error("run build_template.py first")
        return 1
    if find_open_workbook(config.WORKBOOK) is not None:
        log.error("close %s in Excel before wiring", config.WORKBOOK.name)
        return 1
    for p in (config.PDQ_LATEST_CSV, config.FX_CSV, config.HISTORY_CSV, wire_world.hc.WORLD_FOB_CSV,
              wire_world.hc.WORLD_FREIGHT_CSV):
        if not p.exists():
            log.error("missing %s - run run_pull.py and run_historical_drop.py first", p.name)
            return 1
    if (config.WORKBOOK.parent / f"~${config.WORKBOOK.name}").exists():
        log.error("Excel lock file ~$%s exists - close the workbook (or delete the lock if Excel is not running)",
                  config.WORKBOOK.name)
        return 1
    backup = config.CACHE_DIR / "backup" / f"{config.WORKBOOK.stem}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.WORKBOOK, backup)
    log.info("backup: %s", backup)
    import win32com.client
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = False
    xl.DisplayAlerts = False
    xl.ScreenUpdating = False
    wb = xl.Workbooks.Open(str(config.WORKBOOK))
    try:
        xl.Calculation = -4135          # manual while writing
        wire_world.ensure_sheets(wb)
        wire_queries(wb)
        write_formulas(wb)
        write_history(wb)
        wire_world.write(wb)
        xl.Calculation = -4105          # automatic
        xl.CalculateFull()
        build_chart(wb)
        xl.CalculateFull()
        wb.Worksheets(L.CONTROL).Activate()
        wb.Save()
        log.info("wired and saved %s", config.WORKBOOK.name)
    finally:
        wb.Close(SaveChanges=False)
        xl.Quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
