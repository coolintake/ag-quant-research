import pandas as pd
xls = pd.ExcelFile(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", engine='openpyxl')
print('Sheets:', xls.sheet_names)