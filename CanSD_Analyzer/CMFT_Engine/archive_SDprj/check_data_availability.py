import pandas as pd

# Check StatCan S&D data availability
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
df['VALUE'] = pd.to_numeric(df['VALUE'], errors='coerce')
df['Year'] = df['REF_DATE'].str[:4].astype(int)
df['Month'] = df['REF_DATE'].str[5:7].astype(int)

commodities = ['Durum wheat', 'Canola', 'Barley', 'Oats', 'Dry peas', 'Lentils']
for c in commodities:
    subset = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == c)]
    metrics = subset['Supply and disposition of grains'].unique()
    print(f'{c}: {len(subset)} rows')
    print(f'  Metrics: {metrics}')
    print()

# Check planting data
df2 = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Planting_32100359-eng\32100359.csv", dtype=str, low_memory=False)
df2['VALUE'] = pd.to_numeric(df2['VALUE'], errors='coerce')
df2['Year'] = df2['REF_DATE'].str[:4].astype(int)

commodities2 = ['Durum wheat', 'Canola (rapeseed)', 'Barley', 'Oats', 'Peas, dry', 'Lentils']
for c in commodities2:
    subset = df2[(df2['GEO'] == 'Canada') & (df2['Type of crop'] == c)]
    metrics = subset['Harvest disposition'].unique()
    print(f'{c}: {len(subset)} rows')
    print(f'  Metrics: {metrics}')
    print()