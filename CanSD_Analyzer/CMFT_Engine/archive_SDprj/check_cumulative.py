import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
wheat = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == 'Wheat, excluding durum')]
wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
wheat['Month'] = wheat['REF_DATE'].str[5:7].astype(int)

# Check cumulative YTD values for 2002-03 crop year
for metric in ['Human food', 'Industrial use', 'Seed requirements', 'Total exports']:
    subset = wheat[wheat['Supply and disposition of grains'] == metric]
    for _, row in subset.iterrows():
        if row['Year'] in [2002, 2003] and row['Month'] in [12, 3, 7]:
            cy = row['Year'] - 1 if row['Month'] < 8 else row['Year']
            if cy == 2002:
                print(f"{metric}: Year={row['Year']}, Month={row['Month']}, VALUE={row['VALUE']}")
    print()