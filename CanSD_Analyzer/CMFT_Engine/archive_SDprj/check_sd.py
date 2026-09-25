import pandas as pd
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
print('Unique Type of crop:', df['Type of crop'].unique())
print('Unique Supply and disposition:', df['Supply and disposition of grains'].unique())
print('Unique SCALAR_FACTOR:', df['SCALAR_FACTOR'].unique())
print('Unique UOM:', df['UOM'].unique())