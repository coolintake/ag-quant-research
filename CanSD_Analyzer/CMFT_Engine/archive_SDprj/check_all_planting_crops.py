import pandas as pd

# Check all unique crop names in planting data
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Planting_32100359-eng\32100359.csv", dtype=str, low_memory=False)
print("Unique crop names in planting data:")
for c in sorted(df['Type of crop'].unique()):
    count = len(df[(df['GEO'] == 'Canada') & (df['Type of crop'] == c)])
    if count > 0:
        print(f"  {c}: {count} rows")