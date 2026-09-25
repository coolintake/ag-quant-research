import pandas as pd
import numpy as np

df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
durum = df[(df['GEO'] == 'Canada') & (df['Type of crop'] == 'Durum wheat')]
durum['VALUE'] = pd.to_numeric(durum['VALUE'].replace(['..', '...', 'x', 'F', 'U', 'E'], np.nan), errors='coerce')

# Check what months have data for Production
prod = durum[durum['Supply and disposition of grains'] == 'Production']
print("Production dates:")
print(sorted(prod['REF_DATE'].unique()))

# Check what months have data for Industrial use
ind = durum[durum['Supply and disposition of grains'] == 'Industrial use']
print("\nIndustrial use dates:")
print(sorted(ind['REF_DATE'].unique()))

# Check all unique dates
print("\nAll unique dates:")
print(sorted(durum['REF_DATE'].unique()))