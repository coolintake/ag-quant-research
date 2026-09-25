import pandas as pd
df = pd.read_excel(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", sheet_name='Mapping_Dictionary', engine='openpyxl')
print('Mapping_Dictionary columns:', df.columns.tolist())
print('Shape:', df.shape)
print()
print(df.to_string())