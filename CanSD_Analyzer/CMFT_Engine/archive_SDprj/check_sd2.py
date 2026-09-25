import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
wheat = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == 'Wheat, excluding durum')]
print('Unique REF_DATE:', sorted(wheat['REF_DATE'].unique()))
print('Unique Supply and disposition:', wheat['Supply and disposition of grains'].unique())
print()
# Check a few rows for Total ending stocks
ending = wheat[wheat['Supply and disposition of grains'] == 'Total ending stocks']
print('Ending stocks sample:')
print(ending[['REF_DATE', 'VALUE']].head(20).to_string())