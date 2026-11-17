"""Independent check of the app's revenue figures.

Deliberately does NOT import analytics.py: it reads the raw files with plain
pandas and recomputes the key numbers a different way (summing Sales directly
and mapping channel from the Stores file), so a bug in the app's join or
filter logic would show up as a mismatch.

Run from the repository folder, with the data files in the same folder or
passed as arguments:
    python validation/independent_check.py Sales.csv Support_EX_B_Stores.csv Categories.csv
"""
import sys

import pandas as pd

sales_path = sys.argv[1] if len(sys.argv) > 1 else 'Sales.csv'
stores_path = sys.argv[2] if len(sys.argv) > 2 else 'Support_EX_B_Stores.csv'
cats_path = sys.argv[3] if len(sys.argv) > 3 else 'Categories.csv'

sales = pd.read_csv(sales_path, parse_dates=['Date'])
stores = pd.read_csv(stores_path, sep='\t')
cats = pd.read_csv(cats_path)

# Channel straight from the Stores file: a lookup dictionary, not a join.
channel_of = dict(zip(stores['ID'], stores['StoreType']))
category_of = dict(zip(cats['Category ID'], cats['Category']))
sales['Channel'] = sales['Store ID'].map(channel_of)
sales['Category'] = sales['Category ID'].map(category_of)

money = lambda x: f'${x:,.0f}'
print(f'Rows read: {len(sales):,}')
print(f'Total revenue: {money(sales["Revenue"].sum())}')
print('\nBy channel:')
for k, v in sales.groupby('Channel')['Revenue'].sum().items():
    print(f'  {k}: {money(v)}')
print('\nBy year:')
for k, v in sales.groupby(sales['Date'].dt.year)['Revenue'].sum().items():
    print(f'  {k}: {money(v)}')
print('\nBy month (all years):')
m = sales.groupby(sales['Date'].dt.month)['Revenue'].sum()
for k, v in m.items():
    print(f'  {k:>2}: {money(v)}  ({v / m.sum() * 100:.1f}%)')
print('\nBy category:')
for k, v in sales.groupby('Category')['Revenue'].sum().sort_values(ascending=False).items():
    print(f'  {k}: {money(v)}')
print(f'\nWebsite, 2025 only: {money(sales.loc[(sales["Channel"] == "Website") & (sales["Date"].dt.year == 2025), "Revenue"].sum())}')