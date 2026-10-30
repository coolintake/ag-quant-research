# Relative Cash RPA Index — Executive Summary

*Canada Planting Model, V2 · October 2026 · Technical detail: `SPECIFICATION_V2.md`*

## The question it answers

Before spring planting, Prairie farmers decide how to split a fixed land base between wheat, canola, durum and peas. This framework asks one question each winter: **is each crop more or less profitable *relative to wheat* than it usually is, and has that edge grown or shrunk since last year?** It then checks how well that signal lined up with the acres farmers actually planted, according to Statistics Canada.

## Economic intuition

- **Farmers compare crops, not absolute prices.** In 2022 every crop's margin doubled or tripled, yet total cropland didn't change, because acres can only move *between* crops. So the framework measures each crop against a common benchmark, **CWRS spring wheat**: the most widely grown crop and the default choice on most Prairie farms.
- **Margins, not prices.** The measure is return per acre: expected yield × the cash price farmers could lock in, minus seed, fertilizer and chemicals from the provincial crop budgets. A canola rally means less if canola's input bill rose faster.
- **Only what farmers knew at the time.** Expected yield is a trend fitted on 2005 to last year, never the yield actually harvested that fall. The 2021 drought, for example, doesn't distort the 2021 spring signal. Prices are the cash bids posted on PDQ during each pre-plant window.
- **Liquid markets only.** The framework covers CWRS, CPSR, Durum, Canola and Yellow Peas, the crops with daily PDQ cash bids. Barley, oats, lentils, soybeans and flax have no comparable daily price series, so they're left out rather than filled in with static budget prices. The covered crops are about 75% of Saskatchewan cropland, 69% of Alberta's and 60% of Manitoba's.

## The PerfChart concept

StockCharts.com's **PerfChart** plots several stocks rebased to a common starting point, so you see *relative* performance at a glance. V2 applies the same idea to crop margins:

1. Each crop's return per acre is rebased so its **2019–2024 average for the same pricing window = 100**.
2. That index is divided by the CWRS index, so **CWRS is the flat line at 100**.
3. A crop line **above 100** means it's more profitable relative to wheat than is normal for that time of the season. **Below 100** means wheat has the edge.

There is **one workbook per pricing window** (R1 mid-December to mid-January, R2 mid-January to mid-February, R3 mid-February to mid-March). Each is indexed to its own window's history. This matters because some relationships are seasonal. Durum's margin relative to wheat is typically 12–16% higher in December–January than in February–March, so a single base would make every January reading look unusually strong for Durum.

*Reading the Saskatchewan 2026 charts:* by the R3 (mid-March) workbook, Canola sits at about 121, while Yellow Peas (about 43) and Durum (about 63) are far below their usual edge over wheat. The R2 workbook already showed the same picture in mid-February (Canola 119, Durum 59, Peas 42). CANSIM's preliminary 2026 numbers show SK canola acres up 10%, durum down 9% and peas down 7%.

## How the forecast is made

The forecast works as **expert-gated updates**. Plan-B gives an automatic starting point (the prior) for every crop and province, and the analyst's Plan-A updates it. Where the analyst enters a call, that call is the forecast. Where they don't, Plan-B stands as the fallback.

- **Plan-B, the automated prior and fallback.** A single rule shared by all provinces and crops:

  > **% change in acres = 0.098 × % change in relative profitability** (current Feb–Mar estimate)

  A crop whose margin edge over wheat improves 10% on last year is expected to gain about 1% in acres. If the edge doesn't change, acres don't change. The coefficient is re-estimated every year for each pricing window (currently 0.093 Dec–Jan, 0.080 Jan–Feb). It's never clamped or tuned, and it's tested only on years it hasn't seen.
- **Plan-A, analyst-led (primary).** The analyst starts from the Plan-B prior and adjusts it with the relative index, last year's change and outside context (futures spreads, specialty bids, rotations). Calls go in `inputs/plan_a_assignments.csv` with a short rationale. A call that points the opposite way from Plan-B, or departs from it by more than that crop's usual range, gets a second review. Both plans are scored against Statistics Canada each year, so the value of analyst judgement is measured.

## First results (2020–2026)

| What we measured | Result |
|---|---|
| Direction: relative signal and acres moved the same way | **~70%** of 91 province-crop-years, in every window (Feb–Mar 70%) |
| Ranking: crops with the biggest signal gains also gained most acres | Mean rank correlation **+0.41** (Feb–Mar), +0.40 (Dec–Jan) |
| 2026, Feb–Mar window | Direction right for **12 of 13** provincial crop lines. The miss is SK CPSR (+81% acres on a falling signal), which looks like a shift between wheat classes rather than a price response. |
| Plan-B (no-intercept rule) | Coefficient **0.098** (Feb–Mar, p ≈ 0.08). Tested only on years it hadn't seen, it beats a no-change forecast in **4 of 5 years in every window** (2022–2024 and 2026; not 2025). Feb–Mar error is 6.4% vs 8.1% in 2026. |
| Plan-B alternatives tested | Province-crop fixed effects, separate slopes per crop tier and mixed (partial-pooling) models all failed to improve. The data shows no detectable differences in response between crops or provinces, so one shared coefficient is the right level of detail. |

**Takeaway:** the signal is good at direction and ranking, and weak on size. A 50% swing in relative margin usually moves acres by single digits, because of rotations, contracts and machinery. This supports making the analyst's judgement (Plan-A) the primary forecast, with Plan-B as the automated prior and fallback.

## Audit log — what changed from the legacy model

| # | Legacy log-log model (archived) | Problem | V2 fix |
|---|---|---|---|
| 1 | One regression per crop pair, province and window, each on **6 data points** | 13 of 69 fits had the wrong sign and 19 hit the forced 0.10 floor, so many "elasticities" were policy floors rather than estimates | No per-pair fitting. The analyst leads, and the automated fallback is **one pooled regression on ~78 points**, never clamped |
| 2 | Elasticities clamped to [0.10, 2.00] | Clamping hid the absence of a signal | Nothing is clamped. A non-economic slope is flagged and the window isn't used |
| 3 | Guide prices for barley, oats, lentils, soybeans and flax | The same price in every window, so the "signal" came only from wheat or canola | **Liquid PDQ crops only**, and crops without daily bids are left out |
| 4 | The 2026 run mixed guide prices with a prompt-price history | Signal mixed two price sources | One price source throughout (PDQ prompt bids) |
| 5 | Summer fallow absorbed every gap in the land balance | Implied SK fallow was 1.09M ac vs 613k actual | V2 is a signal framework; there's no forced land balancing |
| 6 | Draft base-100 idea: index on **2019 alone**, using **actual** yields | Thin 2019 margins inflated the index (SK peas 576), and the 2021 drought drove SK durum's index to 0 | **2019–2024 average base**, and **2005..T-1 trend yields** for every year |
| 7 | Draft: absolute profitability index | All crops rose together in 2022 | **Relative to CWRS** (PerfChart) |
| 8 | Draft: level index compared with year-on-year acres | Mismatched measures | **Year-on-year vs year-on-year** |
| 9 | "Spring wheat excl. durum" acreage | Includes CPSR, so double counting | CANSIM's **CWRS class series** |
| 10 | — | PDQ has no CPSR bids in Manitoba | **MB CPSR excluded** (like MB Durum) |
| 11 | — | A 2026 PDQ export quoted $/tonne and inflated prices ~40× | **$/bu range check** and a **10-day minimum** per window |
| 12 | Dual-axis charts proposed | Line-ups depend on arbitrary axis scales | **Stacked panels** sharing the year axis |
| 13 | V2 draft: one Run 3 base for all windows | Seasonal patterns read as signal (Durum's Run 1 index overstated by 12–16%) | **Window-matched base** and **one workbook per window** |
| 14 | V2 Plan-B with a drift intercept (a + b·x) | An insignificant +2% drift pushed most forecasts upward; directional accuracy about 50% | **Intercept forced to zero** (b·x): Feb–Mar error 5.2% vs 6.0% out-of-sample, hit rate 69% vs 49%, most stable coefficient (backtest in `output/backtest/`) |

Carried over from the legacy work:
- budget costs checked against the master workbook's `Historical_Ratios` (35 of 35 match);
- StatCan's zero yields (no area seeded) treated as missing;
- the Durum price routed to the Durum belt (S ALTA, SW SASK).

## Limitations

- **Small sample:** 2020–2026 gives 7 changes per series, enough for direction but not for precise magnitudes.
- **2026 CANSIM acres are preliminary** (extract dated 2026-09-10).
- **Saskatchewan has no CPS budget,** so SK CPSR uses CWRS costs.
- **CPSR acreage also moves with wheat-class switching** (e.g. CNHR to CPSR), which price ratios can't capture.
- **About a quarter to two-fifths of provincial cropland is out of scope.**

## Where to look

| File | Use |
|---|---|
| `output/Prairie_RPA_Acreage_Diagnostic_R1.xlsx` / `_R2.xlsx` / `_R3.xlsx` | One per release window: province and Prairie tabs (data blocks, PerfChart, per-crop panels), the **Plan-B Forecast** tab (the no-intercept rule, the release forecast with Plan-A where entered and Plan-B otherwise, review gates, and comparison with CANSIM), and evaluation for that window. Styled to The Desk standard. |
| `output/v2_diagnostics.md` | One-page numeric summary |
| `output/backtest/backtest_plan_b_report.md` | Plan-B specification backtest (legacy, no-intercept, fixed effects, tier slopes, mixed models) |
| `output/v2_panel.csv` | All numbers, one row per province × crop × window × year |
| `inputs/plan_a_assignments.csv` | Analyst calls (Plan-A) |
