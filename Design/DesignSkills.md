---
name: the-desk-style
description: Uncompromising visual design, typography, color palette, and formatting specifications for all tables (Excel/openpyxl) and charts generated for The Desk | Chilmeran Research.
---

# Design & Branding System: The Desk | Chilmeran Research

This document defines the strict, non-negotiable styling parameters for all data tables (Excel, HTML, pandas) and charts (Matplotlib, openpyxl, Plotly) generated for "The Desk" publication.

---

## 1. Official Brand Color Palette

| Token Name | HEX Code | RGB Code | Intended Usage |
| :--- | :--- | :--- | :--- |
| **Header Black** | `#000000` | `RGB(0, 0, 0)` | Table header row backgrounds, main sheet title text |
| **Header Text White** | `#FFFFFF` | `RGB(255, 255, 255)` | Text inside black table header cells |
| **Check Green Fill** | `#C6EFCE` | `RGB(198, 239, 206)` | Highlight background for validation/CHECK rows |
| **Check Green Text** | `#006100` | `RGB(0, 97, 0)` | Text color inside CHECK green rows |
| **Bar Fill Green** | `#D4F1D6` | `RGB(212, 241, 214)` | Primary bar fill color in Excel charts |
| **Bar Border Black** | `#000000` | `RGB(0, 0, 0)` | Thin black stroke around chart bars |
| **Muted Axis Gray** | `#595959` | `RGB(89, 89, 89)` | Chart title, tick labels, and axis titles |
| **Gridline Gray** | `#E5E5E5` | `RGB(229, 229, 229)` | Major horizontal chart gridlines |

---

## 2. Table Specifications (Excel / openpyxl / pandas)

### Typography & Fonts
- **Font Family:** `Calibri` across all cells.
- **Main Sheet Title (Cell A1):** Size `18pt`, **Bold**, Left-aligned.
- **Table Headers (Row 2):** Size `11pt`, **Bold**, Text color `#FFFFFF`, Fill `#000000`, Center-aligned vertically.
- **Row Labels (Cols A-C):** Size `11pt`, Left-aligned. Key summary metrics (**Total Supply**, **Total Disposition**, **Ending Stocks**, **CHECK**) MUST be **Bold**.
- **Data Cells (Cols D+):** Size `11pt`, Right-aligned.

### Separator Lines & Borders
- **Top Border on Summary Rows:** Thin top border above **Total Supply** and **Total Disposition**.
- **Bottom Border on Summary Rows:** Medium/thick bottom line beneath **Total Supply** and **Total Disposition**.
- **CHECK Row (Validation):**
  - Fill color: `#C6EFCE` across all populated data columns.
  - Text color: `#006100`, **Bold**.

### Number Formatting
- **Quantities (KMT / MMT):** Right-aligned, formatted with comma separator: `#,##0` (e.g., `26,340`, `9,140`).
- **Acreage / Yields (MLN AC / MT/AC):** Formatted with 3 decimals: `0.000` (e.g., `43.952`, `0.794`).
- **Percentages (% / Stocks-to-Use):** Formatted to 1 decimal place: `0.0%` (e.g., `27.3%`).
- **Ratio Rows:** Formatted as integers: `0%` (e.g., `13%`, `50%`).

---

## 3. Chart Specifications (Excel Charts & Matplotlib)

### Dimensions & Structure
- **Aspect Ratio:** Widescreen clean layout.
- **Chart Area Border:** Clean single black outline (`#000000`).
- **Plot Area:** White background (`#FFFFFF`) with subtle horizontal gridlines only (`#E5E5E5`). No vertical gridlines.

### Typography & Color Styling
- **Chart Title:** Centered above plot area, Muted Gray (`#595959`), size `18pt` or `20pt`, Regular weight (e.g., `Canada Cereals Ending Stocks KMT`).
- **X-Axis (Marketing Years):** Size `10pt`, vertical rotation (90° upward), dark gray text (`#595959`).
- **Y-Axis (Values):** Size `10pt`, formatted with commas (`#,##0`), dark gray text (`#595959`).
- **Y-Axis Title:** Placed vertically on the left, size `10pt`, uppercase (`KMT` or `MMT`).

### Data Series (Bars)
- **Fill Color:** Signature Soft Light Green (`#D4F1D6` or `#C6EFCE`).
- **Border/Outline:** Solid thin black border (`#000000`, `width=0.75pt`) around every bar.
- **Gap Width:** Set to `80% - 100%` (bars should be distinct columns, not thin sticks).
- **Legend:** Placed on the right side if multiple series exist; hidden if single-series with descriptive title.

---
### Line Chart Rules & Color Hierarchy

#### Single Line Charts
- **Primary Series:** Solid Black (`#000000`), `width=2.25pt`.
- **Data Markers:** None (clean line) or solid black filled circles (`size=4`).

#### Multiple Line Charts (Comparative Analysis)
When plotting multiple series (e.g., Current Year vs. Last Year vs. 5-Year Average), follow this strict visual hierarchy:

1. **Current Crop Year / Focus Asset (Hero Line):**
   - **Color:** Solid Black (`#000000`)
   - **Style:** Solid line, `width=2.5pt`
   - **Role:** Immediately draws the reader's eye as the primary data story.

2. **Previous Crop Year (LY):**
   - **Color:** Dark Slate / Gray (`#595959`)
   - **Style:** Solid or Long Dash (`--`), `width=1.5pt`
   - **Role:** Direct prior-period comparison without competing with the main line.

3. **5-Year / Historical Average or Range:**
   - **Color:** Muted Medium Gray (`#A6A6A6` or `#D9D9D9`)
   - **Style:** Short Dash (`---`) or Dotted (`...`), `width=1.25pt`
   - **Role:** Contextual benchmark in the background.

4. **Secondary / Highlight Series (e.g., Specific Regional Line):**
   - **Color:** Signature Green Fill (`#C6EFCE` / `#16A34A` for high contrast on lines)
   - **Style:** Solid line, `width=2.0pt`
   
_- - -
   
## 4. Execution Rules for Claude Code

When writing Python scripts using `openpyxl`, `xlsxwriter`, `matplotlib`, or `seaborn`:
1. **Never use default Excel chart themes or default blue bars.**
2. Always apply `PatternFill(start_color="000000", end_color="000000", fill_type="solid")` to table headers.
3. Always apply `PatternFill(start_color="C6EFCE", fill_type="solid")` to validation/CHECK rows.
4. Always set chart bar fills explicitly using RGB/HEX codes specified in Section 1.