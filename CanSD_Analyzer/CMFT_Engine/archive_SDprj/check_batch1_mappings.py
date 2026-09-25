import pandas as pd
df = pd.read_excel(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", sheet_name='Mapping_Dictionary', engine='openpyxl')
commodities = ['Durum', 'Canola', 'Barley', 'Oats', 'Dry peas', 'Lentils']
for c in commodities:
    subset = df[df['Commodity'] == c]
    print(f'{c}: {len(subset)} mappings')
    print(f'  Tier1 metrics: {subset["Tier1_Commodity_Metric"].unique()}')
    print(f'  Source Commodity: {subset["Source Commodity"].unique()}')
    print(f'  StatCan_Raw_Metric: {subset["StatCan_Raw_Metric"].unique()}')
    print()