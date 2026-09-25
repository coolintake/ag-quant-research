import openpyxl
from openpyxl.utils import get_column_letter

wb = openpyxl.load_workbook(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\olderfiles\CanadaSD_v01.xlsx", data_only=False)
print("Sheets:", wb.sheetnames)
print()

# Inspect Wheat Mo
ws_mo = wb['Wheat Mo']
print("=== WHEAT MO ===")
print(f"Dimensions: {ws_mo.dimensions}")
print(f"Max row: {ws_mo.max_row}, Max col: {ws_mo.max_column}")
print()

# Print headers (first few rows)
for row in range(1, min(5, ws_mo.max_row + 1)):
    vals = []
    for col in range(1, min(18, ws_mo.max_column + 1)):
        cell = ws_mo.cell(row=row, column=col)
        vals.append(f"{get_column_letter(col)}{row}={cell.value}")
    print(f"Row {row}: {vals}")

print()

# Find metric rows and their structure
print("Metric rows (first 30):")
for row in range(1, min(50, ws_mo.max_row + 1)):
    cell_b = ws_mo.cell(row=row, column=2)
    cell_c = ws_mo.cell(row=row, column=3)
    if cell_b.value:
        print(f"  Row {row}: A={ws_mo.cell(row=row, column=1).value}, B={cell_b.value}, C={cell_c.value}")

print()

# Check formulas in row for a specific metric
print("Sample formulas in Wheat Mo:")
for row in range(1, min(50, ws_mo.max_row + 1)):
    for col in range(4, min(18, ws_mo.max_column + 1)):
        cell = ws_mo.cell(row=row, column=col)
        if cell.value and isinstance(cell.value, str) and cell.value.startswith('='):
            print(f"  {get_column_letter(col)}{row}: {cell.value}")
            break
    else:
        continue
    break

print()

# Inspect Wheat Yr
ws_yr = wb['Wheat Yr']
print("=== WHEAT YR ===")
print(f"Dimensions: {ws_yr.dimensions}")
print(f"Max row: {ws_yr.max_row}, Max col: {ws_yr.max_column}")
print()

for row in range(1, min(5, ws_yr.max_row + 1)):
    vals = []
    for col in range(1, min(30, ws_yr.max_column + 1)):
        cell = ws_yr.cell(row=row, column=col)
        vals.append(f"{get_column_letter(col)}{row}={cell.value}")
    print(f"Row {row}: {vals}")

print()

print("Metric rows in Wheat Yr:")
for row in range(1, min(50, ws_yr.max_row + 1)):
    cell_b = ws_yr.cell(row=row, column=2)
    cell_c = ws_yr.cell(row=row, column=3)
    if cell_b.value:
        print(f"  Row {row}: A={ws_yr.cell(row=row, column=1).value}, B={cell_b.value}, C={cell_c.value}")

print()

print("Formulas in Wheat Yr:")
for row in range(1, min(50, ws_yr.max_row + 1)):
    for col in range(4, min(30, ws_yr.max_column + 1)):
        cell = ws_yr.cell(row=row, column=col)
        if cell.value and isinstance(cell.value, str) and cell.value.startswith('='):
            print(f"  {get_column_letter(col)}{row}: {cell.value}")

print()

# Check CHECK row
print("CHECK row formulas:")
for row in range(1, ws_yr.max_row + 1):
    cell_b = ws_yr.cell(row=row, column=2)
    if cell_b.value and str(cell_b.value).strip() == 'CHECK':
        print(f"  CHECK at row {row}:")
        for col in range(4, min(30, ws_yr.max_column + 1)):
            cell = ws_yr.cell(row=row, column=col)
            if cell.value and isinstance(cell.value, str) and cell.value.startswith('='):
                print(f"    {get_column_letter(col)}{row}: {cell.value}")