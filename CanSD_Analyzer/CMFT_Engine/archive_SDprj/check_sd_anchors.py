import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
wheat = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == 'Wheat, excluding durum')]
print('Unique months:', sorted(wheat['REF_DATE'].unique()))
print()
# Get ending stocks for each crop year
for metric in ['Total ending stocks', 'Ending stocks on farms', 'Ending stocks in commercial positions']:
    subset = wheat[wheat['Supply and disposition of grains'] == metric]
    print(f'{metric}:')
    for _, row in subset.iterrows():
        print(f'  {row["REF_DATE"]}: {row["VALUE"]}')
    print()