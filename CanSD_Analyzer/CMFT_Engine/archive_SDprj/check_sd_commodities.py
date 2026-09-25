import pandas as pd

# Check S&D data availability
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