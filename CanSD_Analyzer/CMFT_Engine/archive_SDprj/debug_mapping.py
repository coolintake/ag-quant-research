import pandas as pd
df = pd.read_excel(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", sheet_name='Mapping_Dictionary', engine='openpyxl')

# Check Durum mappings
durum = df[df['Source Commodity'].str.strip() == 'Durum wheat']
print('Durum mappings:')
for _, row in durum.iterrows():
    print(f"  {row['StatCan_Raw_Metric']} -> {row['Tier1_Commodity_Metric']}")

print()
print('Industrial use in StatCan_Raw_Metric:', 'Industrial use' in df['StatCan_Raw_Metric'].values)
print('Industrial in Tier1_Commodity_Metric:', 'Industrial' in df['Tier1_Commodity_Metric'].values)

# Check what StatCan_Raw_Metric values exist for Durum
print()
print('Unique StatCan_Raw_Metric for Durum:')
for m in durum['StatCan_Raw_Metric'].unique():
    print(f"  '{m}'")

# Check S&D data raw metric names
print()
sd = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
durum_sd = sd[(sd['GEO'] == 'Canada') & (sd['Type of crop'] == 'Durum wheat')]
print('Unique S&D raw metrics for Durum wheat:')
for m in durum_sd['Supply and disposition of grains'].unique():
    print(f"  '{m}'")