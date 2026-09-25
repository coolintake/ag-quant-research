import pandas as pd

# Check crush data
df = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Crush_32100352-eng\32100352.csv", dtype=str, low_memory=False)
print('=== CRUSH DATA ===')
print('UOM unique:', df['UOM'].unique())
print('SCALAR_FACTOR unique:', df['SCALAR_FACTOR'].unique())
print()
canola = df[df['Commodity'] == 'Canola (rapeseed)']
crushed = canola[canola['Process'] == 'Seed crushed']
print('Canola Seed crushed:')
print(crushed[['REF_DATE', 'Commodity', 'Process', 'UOM', 'SCALAR_FACTOR', 'VALUE']].head(10).to_string())

print()
print('=== FEEDSTOCK DATA ===')
df2 = pd.read_csv(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\Feedstock_25100082-eng\25100082.csv", dtype=str, low_memory=False)
print('UOM unique:', df2['UOM'].unique())
print('SCALAR_FACTOR unique:', df2['SCALAR_FACTOR'].unique())
print()
# Check vegetable oils
veg = df2[df2['Products'].str.contains('Vegetable oil', case=False, na=False)]
print('Vegetable oils:')
print(veg[['REF_DATE', 'Products', 'Supply and disposition', 'UOM', 'SCALAR_FACTOR', 'VALUE']].head(10).to_string())