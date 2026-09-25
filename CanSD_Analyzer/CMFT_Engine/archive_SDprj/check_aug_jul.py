import pandas as pd
df = pd.read_excel(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", sheet_name='Mapping_Dictionary', engine='openpyxl')
# Filter for Aug-Jul commodities
aug_jul = df[df['Crop_Year'] == 'Aug-Jul']
print('Aug-Jul commodities:')
for _, row in aug_jul.iterrows():
    print(f"  {row['Source Commodity']} -> {row['Commodity']} ({row['Commodity_Group']})")