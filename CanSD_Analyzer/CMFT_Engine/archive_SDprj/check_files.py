import os
ds = 'data_store'
if os.path.exists(ds):
    print(f'data_store: {os.listdir(ds)}')
else:
    print('data_store: NOT FOUND')

csp = r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CanadaSD_Prj"
if os.path.exists(csp):
    print(f'CanadaSD_Prj: {os.listdir(csp)}')
else:
    print('CanadaSD_Prj: NOT FOUND')