import pandas as pd
import numpy as np
from pathlib import Path

# Test the merge
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv", dtype=str, low_memory=False)
mapping = pd.read_excel(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx", sheet_name='Mapping_Dictionary', engine='openpyxl')

# Normalize function
def _normalize_commodity_name_fixed(name):
    name = str(name).strip()
    mapping_dict = {
        "All wheat": "Wheat", "Wheat, excluding durum": "Wheat", "Wheat, all": "Wheat",
        "Durum wheat": "Durum", "Wheat, durum": "Durum",
        "Canola (rapeseed)": "Canola", "Dry peas": "Dry peas", "Chickpeas": "Dry peas",
    }
    return mapping_dict.get(name, name)

# Process S&D data
df = df.rename(columns={
    'REF_DATE': 'Date', 'GEO': 'Geography', 'Type of crop': 'Commodity',
    'Supply and disposition of grains': 'StatCan_Raw_Metric',
    'UOM': 'Unit', 'SCALAR_FACTOR': 'Scalar_Factor', 'VALUE': 'Value'
})
df['Value'] = pd.to_numeric(df['Value'].replace(['..', '...', 'x', 'F', 'U', 'E'], np.nan), errors='coerce')
df['Commodity_Norm'] = df['Commodity'].apply(_normalize_commodity_name_fixed)

# Filter for Durum
durum = df[df['Commodity_Norm'] == 'Durum'].copy()
print(f"Durum S&D rows: {len(durum)}")
print(f"Unique StatCan_Raw_Metric: {durum['StatCan_Raw_Metric'].unique().tolist()}")

# Merge with mapping
map_cols = ["Source Commodity", "StatCan_Raw_Metric", "Tier1_Commodity_Metric", "Tier2_National_Metric"]
mapping_clean = mapping[map_cols].drop_duplicates()
mapping_clean['Source_Commodity_Norm'] = mapping_clean['Source Commodity'].apply(_normalize_commodity_name_fixed)

print(f"\nMapping rows for Durum:")
durum_map = mapping_clean[mapping_clean['Source_Commodity_Norm'] == 'Durum']
for _, row in durum_map.iterrows():
    print(f"  {row['StatCan_Raw_Metric']} -> {row['Tier1_Commodity_Metric']}")

# Do the merge
merged = durum.merge(
    mapping_clean,
    left_on=['Commodity_Norm', 'StatCan_Raw_Metric'],
    right_on=['Source_Commodity_Norm', 'StatCan_Raw_Metric'],
    how='left',
    indicator=True
)

print(f"\nMerged rows: {len(merged)}")
print(f"Matched: {(merged['_merge'] == 'both').sum()}")
print(f"Unmatched: {(merged['_merge'] == 'left_only').sum()}")

# Check if Tier1_Commodity_Metric is populated
print(f"\nTier1_Commodity_Metric values: {merged['Tier1_Commodity_Metric'].notna().sum()}")
print(f"Unique Tier1 metrics: {merged['Tier1_Commodity_Metric'].dropna().unique().tolist()}")

# Pivot
pivot = merged.dropna(subset=['Tier1_Commodity_Metric']).pivot_table(
    index=['Date', 'Commodity_Norm'],
    columns='Tier1_Commodity_Metric',
    values='Value',
    aggfunc='first'
).reset_index()

print(f"\nPivot columns: {pivot.columns.tolist()}")
print(f"Pivot shape: {pivot.shape}")