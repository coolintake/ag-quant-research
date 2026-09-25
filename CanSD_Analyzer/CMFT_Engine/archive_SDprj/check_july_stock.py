import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
wheat = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == 'Wheat, excluding durum')]
wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
wheat['Month'] = wheat['REF_DATE'].str[5:7].astype(int)
# Get July 2002 ending stocks
july_2002 = wheat[(wheat['Year'] == 2002) & (wheat['Month'] == 7) & (wheat['Supply and disposition of grains'] == 'Total ending stocks')]
print('July 2002 Total ending stocks:', july_2002['VALUE'].values)
# Also check 2001 July
july_2001 = wheat[(wheat['Year'] == 2001) & (wheat['Month'] == 7) & (wheat['Supply and disposition of grains'] == 'Total ending stocks')]
print('July 2001 Total ending stocks:', july_2001['VALUE'].values)