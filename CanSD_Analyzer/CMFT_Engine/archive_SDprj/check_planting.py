import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Planting_32100359-eng\32100359.csv", dtype=str, low_memory=False)
print('Unique GEO:', df['GEO'].unique()[:10])
print('Unique Type of crop:', df['Type of crop'].unique())
print('Unique Harvest disposition:', df['Harvest disposition'].unique())