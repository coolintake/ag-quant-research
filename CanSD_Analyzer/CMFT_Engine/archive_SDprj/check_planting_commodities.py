import pandas as pd

# Check planting data availability
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Planting_32100359-eng\32100359.csv", dtype=str, low_memory=False)
df['VALUE'] = pd.to_numeric(df['VALUE'], errors='coerce')
df['Year'] = df['REF_DATE'].str[:4].astype(int)

commodities = ['Durum wheat', 'Canola (rapeseed)', 'Barley', 'Oats', 'Peas, dry', 'Lentils']
for c in commodities:
    subset = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == c)]
    metrics = subset['Harvest disposition'].unique()
    print(f'{c}: {len(subset)} rows')
    print(f'  Metrics: {metrics}')
    print()