"""Business logic. Keep Streamlit code OUT of this file so every function can be
tested on its own and reused by the assistant."""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field

import pandas as pd

EXCEL_EXT = ('.xlsx', '.xlsm', '.xls')
TEXT_EXT = ('.csv', '.txt', '.tsv')


# ---------------------------------------------------------------- loading
def excel_sheet_names(file_bytes: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def load_table(file_bytes: bytes, filename: str, sheet_name=0, header_hint: str | None = None) -> pd.DataFrame:
    """Read an uploaded CSV / TXT / TSV / Excel file into a DataFrame.

    header_hint: the name of a column we expect. Some sheets (the Survey workbook)
    have title rows above the real header, so we look for the row that contains
    this column name and use it as the header."""
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(EXCEL_EXT):
        header_row = 0
        if header_hint:
            peek = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, header=None, nrows=15)
            for i, row in peek.iterrows():
                if header_hint in [str(v).strip() for v in row.values]:
                    header_row = i
                    break
        df = pd.read_excel(buffer, sheet_name=sheet_name, header=header_row)
    elif name.endswith(TEXT_EXT):
        df = pd.read_csv(buffer, sep=None, engine='python')  # sniffs the delimiter
    else:
        raise ValueError(f'Unsupported file type: {filename}')
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how='all')  # drop fully blank rows (e.g. trailing lines in Excel)


def profile_table(df: pd.DataFrame) -> dict:
    """Basic profile: size, duplicates, missing values, data types."""
    missing = df.isna().sum()
    return {
        'rows': len(df),
        'columns': df.shape[1],
        'duplicate_rows': int(df.duplicated().sum()),
        'missing': pd.DataFrame({
            'column': missing.index,
            'missing': missing.values,
            'pct_missing': (missing.values / max(len(df), 1) * 100).round(2),
        }),
        'dtypes': pd.DataFrame({'column': df.columns, 'dtype': df.dtypes.astype(str).values}),
    }


# ---------------------------------------------------------- tool contract
@dataclass
class ToolResult:
    """Every analytical function returns this. The assistant may only narrate it.
    Keep `value` to plain Python types (numbers, strings, dicts, lists)."""
    tool: str
    value: object
    definition: str
    scope: dict
    rows_used: int
    caveats: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _error(tool: str, message: str, scope: dict | None = None) -> ToolResult:
    return ToolResult(tool=tool, value=None, definition='', scope=scope or {},
                      rows_used=0, caveats=[], error=message)


def _apply_filters(df: pd.DataFrame, filters: dict | None):
    """Validate filters against the LOADED data, then apply them."""
    for col, wanted in (filters or {}).items():
        if col not in df.columns:
            return df, f'Unknown column: {col}'
        wanted_list = list(wanted) if isinstance(wanted, (list, tuple, set)) else [wanted]
        valid = set(df[col].dropna().unique())
        bad = [w for w in wanted_list if w not in valid]
        if bad:
            examples = sorted(map(str, valid))[:20]
            return df, f'{bad} not found in column {col}. Valid values include: {examples}'
        df = df[df[col].isin(wanted_list)]
    return df, None


# ------------------------------------------------------ analytical tools
def get_total(df: pd.DataFrame, value_col: str, filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column, optionally filtered. EXAMPLE tool: replace the
    generic column names with your own definition of revenue."""
    name = 'get_total'
    if value_col not in df.columns:
        return _error(name, f'Unknown column: {value_col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    values = pd.to_numeric(subset[value_col], errors='coerce')
    n_bad = int(values.isna().sum())
    caveats = ['This is a sum of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) had a missing or non-numeric {value_col} and were excluded.')
    return ToolResult(tool=name, value=float(values.sum()), definition=f'Sum of {value_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(values.notna().sum()), caveats=caveats)


def group_total(df: pd.DataFrame, value_col: str, group_col: str,
                filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column by group (store, channel, year, ...)."""
    name = 'group_total'
    for col in (value_col, group_col):
        if col not in df.columns:
            return _error(name, f'Unknown column: {col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    work = subset.assign(**{value_col: pd.to_numeric(subset[value_col], errors='coerce')})
    n_bad = int(work[value_col].isna().sum())
    n_nogroup = int(work[group_col].isna().sum())
    grouped = work.dropna(subset=[value_col, group_col]).groupby(group_col)[value_col].sum()
    caveats = ['Sums of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) with missing or non-numeric {value_col} were excluded.')
    if n_nogroup:
        caveats.append(f'{n_nogroup} row(s) with a missing {group_col} were excluded.')
    return ToolResult(tool=name, value={str(k): float(v) for k, v in grouped.items()},
                      definition=f'Sum of {value_col} by {group_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(len(work) - n_bad - n_nogroup), caveats=caveats)


def data_quality_report(df: pd.DataFrame) -> ToolResult:
    """Row count, duplicate rows, and missing values per column."""
    missing = {c: int(n) for c, n in df.isna().sum().items() if n > 0}
    caveats = []
    if missing:
        caveats.append('Missing values may limit conclusions drawn from those columns.')
    return ToolResult(tool='data_quality_report',
                      value={'rows': len(df), 'duplicate_rows': int(df.duplicated().sum()),
                             'columns_with_missing': missing},
                      definition='Counts of duplicate rows and missing values in the loaded table',
                      scope={'filters': 'none (full table)'}, rows_used=len(df), caveats=caveats)


# ------------------------------------------------------------ merge check
def check_merge(left: pd.DataFrame, right: pd.DataFrame, on, value_col: str, how: str = 'left'):
    """Merge, and report whether the join changed the row count or the revenue sum.
    `value_col` must be a numeric column in `left`. Returns (merged, report)."""
    before_rows = len(left)
    before_sum = float(pd.to_numeric(left[value_col], errors='coerce').sum())
    merged = left.merge(right, on=on, how=how)
    after_rows = len(merged)
    after_sum = float(pd.to_numeric(merged[value_col], errors='coerce').sum())
    fan_out = after_rows > before_rows or abs(after_sum - before_sum) > 1e-9
    report = {
        'rows_before': before_rows, 'rows_after': after_rows,
        'sum_before': before_sum, 'sum_after': after_sum,
        'changed': fan_out,
        'warning': ('The join changed the row count or the total. Check the grain and keys '
                    'of both tables before using this result.') if fan_out else '',
    }
    return merged, report


# ======================================================= E-PRESSO DATA MODEL
# What each uploaded table must contain. Names match the exported source files.
# If an updated file is missing one of these columns, the app names it instead
# of crashing.
TABLE_SPECS = {
    'Sales': {
        'source': 'Sales table in Support_EX_B_Sales.accdb (exported to CSV/Excel)',
        'required': ['Sales ID', 'Date', 'Store ID', 'StoreYear', 'Category ID', 'Product ID', 'Revenue'],
        'key': ['Sales ID'],
        'grain': 'one product at one store (or the website) in one month',
        'has_revenue': True,
    },
    'Stores': {
        'source': 'Support_EX_B_Stores.csv',
        'required': ['ID', 'City', 'State', 'StoreType', 'Market', 'Location', 'Manager'],
        'key': ['ID'],
        'grain': 'one store (SH00 is the website)',
        'has_revenue': False,
    },
    'Products': {
        'source': 'Products table in Support_EX_B_Sales.accdb',
        'required': ['Product ID', 'Product'],
        'key': ['Product ID'],
        'grain': 'one product',
        'has_revenue': False,
    },
    'Categories': {
        'source': 'Categories table in Support_EX_B_Sales.accdb',
        'required': ['Category ID', 'Category'],
        'key': ['Category ID'],
        'grain': 'one product category',
        'has_revenue': False,
    },
    'Survey': {
        'source': 'Support_EX_B_Survey.xlsx, sheet "Survey Results"',
        'required': ['Survey ID', 'Store ID', 'Year', 'Satisfaction', 'Interest'],
        'key': ['Survey ID'],
        'grain': 'one store in one year (Survey ID = StoreID_YY)',
        'has_revenue': False,
    },
}
REVENUE_COL = 'Revenue'


def missing_columns(df: pd.DataFrame, table: str) -> list[str]:
    """Required columns that are not in the uploaded table."""
    return [c for c in TABLE_SPECS[table]['required'] if c not in df.columns]


def clean_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """Give Sales proper types. Bad dates/revenue become blank (NaN) and are
    reported by the data-quality check rather than silently dropped."""
    out = sales.copy()
    out['Date'] = pd.to_datetime(out['Date'], errors='coerce')
    out[REVENUE_COL] = pd.to_numeric(out[REVENUE_COL], errors='coerce')
    for c in ('Sales ID', 'Store ID', 'StoreYear', 'Category ID', 'Product ID'):
        out[c] = out[c].astype('string').str.strip()
    out['Year'] = out['Date'].dt.year.astype('Int64')
    out['Month'] = out['Date'].dt.month.astype('Int64')
    return out


def key_report(df: pd.DataFrame, table: str) -> dict:
    """Is the key column really unique and filled in? (Checks the grain.)"""
    key = TABLE_SPECS[table]['key']
    present = [k for k in key if k in df.columns]
    if not present:
        return {'key': key, 'blank_keys': None, 'duplicate_keys': None}
    return {
        'key': key,
        'blank_keys': int(df[present].isna().any(axis=1).sum()),
        'duplicate_keys': int(df.dropna(subset=present).duplicated(subset=present).sum()),
    }


def grain_table(tables: dict) -> pd.DataFrame:
    """The grain-statement table from the Project Guide, with live row counts."""
    rows = []
    for name, spec in TABLE_SPECS.items():
        df = tables.get(name)
        rows.append({
            'Table': name,
            'Source file / sheet': spec['source'],
            'One row =': spec['grain'],
            'Key column(s)': ', '.join(spec['key']),
            'Row count': len(df) if df is not None else None,
            'Contains revenue?': 'Yes' if spec['has_revenue'] else 'No',
        })
    return pd.DataFrame(rows)


def build_sales_model(tables: dict):
    """Join the lookup tables onto Sales, one at a time, checking each join.

    Sales is the 'many' side. Stores, Products and Categories are 'one' sides
    (one row per key), so a correct left join keeps the row count and the
    revenue total unchanged. The Survey is at a different grain (store-year),
    so it is NOT joined here; it is only used for store-year comparisons.
    Returns (model, merge_checks)."""
    sales = clean_sales(tables['Sales'])
    checks = []

    stores = tables['Stores'].rename(columns={'ID': 'Store ID'}).copy()
    stores['Store ID'] = stores['Store ID'].astype('string').str.strip()
    stores = stores.drop_duplicates(subset=['Store ID'])
    stores['Channel'] = stores['StoreType'].where(stores['StoreType'].notna(), 'Unknown')

    joins = [
        ('Sales + Stores', stores[['Store ID', 'City', 'State', 'StoreType', 'Channel',
                                   'Market', 'Location', 'Manager']], 'Store ID'),
        ('+ Products', tables['Products'][['Product ID', 'Product']], 'Product ID'),
        ('+ Categories', tables['Categories'][['Category ID', 'Category']], 'Category ID'),
    ]
    model = sales
    for label, right, key in joins:
        right = right.copy()
        right[key] = right[key].astype('string').str.strip()
        right = right.drop_duplicates(subset=[key])   # protect against fan-out
        unmatched = int((~model[key].isin(right[key])).sum())
        model, rep = check_merge(model, right, on=key, value_col=REVENUE_COL, how='left')
        checks.append({
            'Merge': label, 'Join key': key,
            'Rows before': rep['rows_before'], 'Rows after': rep['rows_after'],
            'Revenue sum before': rep['sum_before'], 'Revenue sum after': rep['sum_after'],
            'Sales rows with no match': unmatched,
            'Explanation': ('OK: many-to-one join, no rows or revenue added or lost.'
                            if not rep['changed'] else rep['warning']),
        })
    model['Channel'] = model['Channel'].fillna('Unknown')
    return model, pd.DataFrame(checks)


def total_revenue(model: pd.DataFrame) -> float:
    """Total revenue = sum of the Revenue column over every sales row loaded."""
    return float(pd.to_numeric(model[REVENUE_COL], errors='coerce').sum())


def reconciliation_summary(model: pd.DataFrame) -> dict:
    """Numbers to compare against Power BI before building anything else."""
    rev = pd.to_numeric(model[REVENUE_COL], errors='coerce')
    work = model.assign(**{REVENUE_COL: rev})
    return {
        'total': float(rev.sum()),
        'by_channel': work.groupby('Channel')[REVENUE_COL].sum(),
        'by_year': work.dropna(subset=['Year']).groupby('Year')[REVENUE_COL].sum(),
        'rows': len(model),
        'blank_revenue_rows': int(rev.isna().sum()),
    }


def sales_quality_issues(sales: pd.DataFrame, tables: dict) -> pd.DataFrame:
    """Problems specific to the E-Presso Sales table, in plain language."""
    s = clean_sales(sales)
    issues = []

    def add(check, n, note):
        issues.append({'Check': check, 'Rows affected': int(n), 'Note': note})

    add('Blank or non-numeric Revenue', s[REVENUE_COL].isna().sum(),
        'Excluded from revenue totals.')
    add('Negative or zero Revenue', (s[REVENUE_COL] <= 0).sum(), 'Worth checking; may be returns or errors.')
    add('Blank or invalid Date', s['Date'].isna().sum(), 'Excluded from year/month charts.')
    add('Duplicate Sales ID', s['Sales ID'].dropna().duplicated().sum(), 'Would double-count revenue.')
    stores = tables.get('Stores')
    if stores is not None and 'ID' in stores.columns:
        known = set(stores['ID'].astype(str).str.strip())
        add('Store ID not in Stores table', (~s['Store ID'].isin(known)).sum(),
            'Revenue counted, but store/channel shown as Unknown.')
    for tbl, key in (('Products', 'Product ID'), ('Categories', 'Category ID')):
        lk = tables.get(tbl)
        if lk is not None and key in lk.columns:
            known = set(lk[key].astype(str).str.strip())
            add(f'{key} not in {tbl} table', (~s[key].isin(known)).sum(),
                'Revenue counted, but name shown as blank.')
    bad_sy = s['StoreYear'] != (s['Store ID'] + '_' + (s['Year'] % 100).astype('string').str.zfill(2))
    add('StoreYear does not match Store ID + year', bad_sy.fillna(True).sum(),
        'StoreYear links Sales to the Survey; a mismatch breaks that link.')
    return pd.DataFrame(issues)