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


HEADER_HINTS = {'Sales': 'Sales ID', 'Stores': 'ID', 'Products': 'Product ID',
                'Categories': 'Category ID', 'Survey': 'Survey ID'}


def read_candidates(file_bytes: bytes, filename: str) -> list:
    """Every table-shaped thing in one uploaded file: one for a CSV, one per
    sheet for Excel (with the header row found automatically).
    Returns a list of (sheet_name_or_None, DataFrame)."""
    name = filename.lower()
    if name.endswith(TEXT_EXT):
        return [(None, load_table(file_bytes, filename))]
    if not name.endswith(EXCEL_EXT):
        raise ValueError(f'Unsupported file type: {filename}')
    out = []
    hints = set(HEADER_HINTS.values())
    for sheet in excel_sheet_names(file_bytes):
        peek = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=None, nrows=15)
        hint = next((str(v).strip() for _, row in peek.iterrows() for v in row.values
                     if str(v).strip() in hints), None)
        out.append((sheet, load_table(file_bytes, filename, sheet, hint)))
    return out


def identify_tables(files: list) -> tuple[dict, dict, list]:
    """Work out which uploaded file is which E-Presso table by its columns,
    so files can be uploaded together under any name.
    files: list of (filename, [(sheet, DataFrame), ...]) as from read_candidates.
    Returns (tables, sources, problems): tables maps table name to DataFrame,
    sources maps table name to 'file' or 'file / sheet', problems are plain messages."""
    tables, sources, problems, near = {}, {}, [], {}
    for filename, candidates in files:
        matched = False
        for sheet, df in candidates:
            cols = set(df.columns)
            full = [t for t, spec in TABLE_SPECS.items() if set(spec['required']) <= cols]
            if not full:
                for t, spec in TABLE_SPECS.items():
                    have = len(set(spec['required']) & cols) / len(spec['required'])
                    if have >= 0.5 and have > near.get(t, (0,))[0]:
                        near[t] = (have, filename, missing_columns(df, t))
                continue
            table = max(full, key=lambda t: len(TABLE_SPECS[t]['required']))  # most specific
            where = filename if sheet is None else f'{filename} / sheet "{sheet}"'
            if table in tables:
                problems.append(f'Two files look like the **{table}** table: {sources[table]} and {where}. '
                                'Remove one.')
            else:
                tables[table], sources[table] = df, where
            matched = True
        if not matched and not any(len(c[1].columns) for c in candidates):
            problems.append(f'**{filename}** is empty.')
    for t in TABLE_SPECS:
        if t in tables:
            continue
        if t in near:
            _, filename, gone = near[t]
            problems.append(f'**{filename}** looks like the **{t}** table but is missing required '
                            f'column(s): {", ".join(gone)}.')
        else:
            problems.append(f'No file for the **{t}** table yet.')
    return tables, sources, problems


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

    # Exact duplicate rows (every column identical) are double entries: keep one.
    # Rows sharing a Sales ID but with different values are kept and only flagged,
    # because we cannot tell which one is right.
    before_rows, before_sum = len(sales), float(sales[REVENUE_COL].sum())
    sales = sales.drop_duplicates()
    removed = before_rows - len(sales)
    checks.append({
        'Merge': 'Remove exact duplicate Sales rows', 'Join key': '(all columns)',
        'Rows before': before_rows, 'Rows after': len(sales),
        'Revenue sum before': before_sum, 'Revenue sum after': float(sales[REVENUE_COL].sum()),
        'Sales rows with no match': 0,
        'Explanation': (f'{removed} exact duplicate row(s) removed so revenue is not double-counted.'
                        if removed else 'OK: no exact duplicate rows.'),
    })

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
    return add_store_label(model), pd.DataFrame(checks)


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
    add('Duplicate Sales ID', s['Sales ID'].dropna().duplicated().sum(),
        'Exact copies are removed before totals (see Data model tab); rows with the same ID but different values are kept.')
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


# ======================================================= DASHBOARD (step 4)
# The sidebar filters map to these model columns. Options always come from
# the loaded data, never typed in.
FILTER_COLUMNS = {'Year': 'Year', 'Store': 'Store', 'Category': 'Category', 'Channel': 'Channel'}


def add_store_label(model: pd.DataFrame) -> pd.DataFrame:
    """Readable store name, e.g. 'SH11 - Chicago, IL' or 'SH00 - Website'."""
    out = model.copy()
    city = out['City'].astype('string').fillna('')
    state = out['State'].astype('string').fillna('')
    place = (city + ', ' + state).str.strip(', ')
    place = place.where(place != '', out['StoreType'].astype('string').fillna('Unknown'))
    out['Store'] = out['Store ID'].astype('string') + ' - ' + place
    out['Category'] = out['Category'].astype('string').fillna('Unknown')
    return out


def filter_options(model: pd.DataFrame) -> dict:
    """Sorted list of values for each filter, from the loaded data."""
    return {label: sorted(model[col].dropna().unique().tolist())
            for label, col in FILTER_COLUMNS.items()}


def filter_model(model: pd.DataFrame, selections: dict) -> pd.DataFrame:
    """Keep rows whose value is in the selected list for every filter.
    A filter that is missing or None means 'all values'."""
    mask = pd.Series(True, index=model.index)
    for label, chosen in selections.items():
        if chosen is None:
            continue
        mask &= model[FILTER_COLUMNS[label]].isin(chosen)
    return model[mask]


def revenue_by(model: pd.DataFrame, *cols: str) -> pd.DataFrame:
    """Revenue summed by one or more columns, as a tidy table."""
    rev = pd.to_numeric(model[REVENUE_COL], errors='coerce')
    work = model.assign(**{REVENUE_COL: rev}).dropna(subset=list(cols))
    return work.groupby(list(cols), as_index=False)[REVENUE_COL].sum()


def complete_years(model: pd.DataFrame) -> list[int]:
    """Years that have data in all 12 months. Comparing a full year with a
    partial one (e.g. one month of a new year) would distort growth."""
    months = model.dropna(subset=['Year', 'Month']).groupby('Year')['Month'].nunique()
    return sorted(int(y) for y, n in months.items() if n == 12)


def partial_years(model: pd.DataFrame) -> list[int]:
    years = sorted(int(y) for y in model['Year'].dropna().unique())
    full = set(complete_years(model))
    return [y for y in years if y not in full]


def kpis(model: pd.DataFrame) -> dict:
    """Headline numbers for whatever rows are currently selected."""
    rev = pd.to_numeric(model[REVENUE_COL], errors='coerce')
    by_year = revenue_by(model, 'Year').sort_values('Year')
    full = complete_years(model)
    by_year = by_year[by_year['Year'].isin(full)]   # growth uses full years only
    growth = None
    if len(by_year) >= 2 and by_year[REVENUE_COL].iloc[0] > 0:
        first, last = by_year.iloc[0], by_year.iloc[-1]
        growth = {'from': int(first['Year']), 'to': int(last['Year']),
                  'pct': float((last[REVENUE_COL] / first[REVENUE_COL] - 1) * 100)}
    web = float(rev[model['Channel'] == 'Website'].sum())
    total = float(rev.sum())
    return {
        'total': total,
        'rows': int(rev.notna().sum()),
        'website_share_pct': (web / total * 100) if total else None,
        'growth': growth,
        'partial_years': partial_years(model),
    }


# ======================================================= ASSISTANT TOOLS (step 7)
# Each tool takes explicit parameters, checks them against the LOADED data,
# and returns a ToolResult. None of them change the data.
REVENUE_DEF = 'Sum of the Revenue column on Sales rows (one product, one store, one month)'
BASE_CAVEATS = ['Revenue is not profit: the data has no costs, so profitability cannot be calculated.']
GROUPS = {'channel': 'Channel', 'store': 'Store', 'category': 'Category', 'year': 'Year'}


def _resolve_store(model: pd.DataFrame, store: str):
    """Accept 'SH11', 'SH11 - Chicago, IL' or 'Chicago'. Returns (label, error)."""
    labels = sorted(model['Store'].dropna().unique())
    s = str(store).strip().lower()
    exact = [l for l in labels if l.lower() == s or l.lower().split(' - ')[0] == s]
    if exact:
        return exact[0], None
    by_city = [l for l in labels if ' - ' in l and l.lower().split(' - ')[1].split(',')[0] == s]
    if len(by_city) == 1:
        return by_city[0], None
    if len(by_city) > 1:
        return None, f'"{store}" matches more than one store: {by_city}. Please name one.'
    return None, f'Store "{store}" not found. Valid stores: {labels}'


def _scoped(model: pd.DataFrame, year=None, channel=None, store=None, category=None):
    """Validate the filters and return (subset, scope_dict, error)."""
    scope, sub = {}, model
    checks = [('year', year, 'Year'), ('channel', channel, 'Channel'), ('category', category, 'Category')]
    for name, val, col in checks:
        if val is None:
            scope[name] = 'All'
            continue
        valid = sorted(model[col].dropna().unique().tolist())
        if name == 'year':
            try:
                val = int(val)
            except (TypeError, ValueError):
                return None, scope, f'Year "{val}" is not a number. Valid years: {valid}'
        if val not in valid:
            return None, scope, f'{name.title()} "{val}" not found in the loaded data. Valid values: {valid}'
        sub = sub[sub[col] == val]
        scope[name] = val
    if store is None:
        scope['store'] = 'All'
    else:
        label, err = _resolve_store(model, store)
        if err:
            return None, scope, err
        sub = sub[sub['Store'] == label]
        scope['store'] = label
    return sub, scope, None


def _rev(sub: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(sub[REVENUE_COL], errors='coerce')


def _missing_caveat(sub: pd.DataFrame) -> list:
    n = int(_rev(sub).isna().sum())
    return [f'{n} row(s) with blank revenue were excluded.'] if n else []


def get_total_revenue(model, year=None, channel=None, store=None, category=None) -> ToolResult:
    """Total revenue for an optional year / channel / store / category."""
    name = 'get_total_revenue'
    sub, scope, err = _scoped(model, year, channel, store, category)
    if err:
        return _error(name, err, scope)
    rev = _rev(sub)
    return ToolResult(tool=name, value=round(float(rev.sum()), 2), definition=REVENUE_DEF,
                      scope=scope, rows_used=int(rev.notna().sum()),
                      caveats=BASE_CAVEATS + _missing_caveat(sub))


def get_revenue_breakdown(model, by='channel', year=None, channel=None, store=None,
                          category=None) -> ToolResult:
    """Revenue and share of total by channel, store, category or year, largest first."""
    name = 'get_revenue_breakdown'
    if by not in GROUPS:
        return _error(name, f'Cannot group by "{by}". Choose one of {list(GROUPS)}.')
    sub, scope, err = _scoped(model, year, channel, store, category)
    if err:
        return _error(name, err, scope)
    scope['grouped_by'] = by
    work = sub.assign(**{REVENUE_COL: _rev(sub)})
    g = work.groupby(GROUPS[by])[REVENUE_COL].sum().sort_values(ascending=False)
    total = float(g.sum())
    value = {str(k): {'revenue': round(float(v), 2),
                      'share_pct': round(float(v) / total * 100, 2) if total else None}
             for k, v in g.items()}
    caveats = BASE_CAVEATS + _missing_caveat(sub) + [
        'Share = group revenue / total revenue within this scope.']
    if by == 'store':
        caveats.append('SH00 is the website, not a physical store.')
    return ToolResult(tool=name, value=value, definition=f'{REVENUE_DEF}, grouped by {by}',
                      scope=scope, rows_used=int(work[REVENUE_COL].notna().sum()), caveats=caveats)


def get_revenue_trend(model, by='channel', start_year=None, end_year=None, channel=None,
                      store=None, category=None) -> ToolResult:
    """Revenue per year for each group, and % change from start_year to end_year.
    Groups are sorted from biggest growth to biggest decline."""
    name = 'get_revenue_trend'
    if by not in GROUPS or by == 'year':
        return _error(name, f'Trend must be by one of {[g for g in GROUPS if g != "year"]}.')
    sub, scope, err = _scoped(model, None, channel, store, category)
    if err:
        return _error(name, err, scope)
    years = sorted(sub['Year'].dropna().unique().tolist())
    if len(years) < 2:
        return _error(name, 'At least two years of data are needed to measure a trend.', scope)
    full = complete_years(sub)
    default = full if len(full) >= 2 else years
    start = int(start_year) if start_year is not None else default[0]
    end = int(end_year) if end_year is not None else default[-1]
    for y in (start, end):
        if y not in years:
            return _error(name, f'Year {y} not found in the loaded data. Valid years: {years}', scope)
    if start >= end:
        return _error(name, 'start_year must be earlier than end_year.', scope)
    scope.update({'grouped_by': by, 'from_year': start, 'to_year': end})
    work = sub.assign(**{REVENUE_COL: _rev(sub)})
    table = work.pivot_table(index=GROUPS[by], columns='Year', values=REVENUE_COL,
                             aggfunc='sum', fill_value=0)
    rows = []
    for grp, r in table.iterrows():
        a, b = float(r.get(start, 0)), float(r.get(end, 0))
        rows.append({'group': str(grp), f'revenue_{start}': round(a, 2), f'revenue_{end}': round(b, 2),
                     'change': round(b - a, 2),
                     'pct_change': round((b / a - 1) * 100, 2) if a else None,
                     'by_year': {int(y): round(float(v), 2) for y, v in r.items()}})
    rows.sort(key=lambda x: (x['pct_change'] is None, -(x['pct_change'] or 0)))
    declined = [x['group'] for x in rows if x['pct_change'] is not None and x['pct_change'] < 0]
    partial = [y for y in years if y not in full]
    caveats = BASE_CAVEATS + _missing_caveat(sub) + [
        f'% change compares {end} with {start} only; years in between are shown in by_year.',
        *([f'Partial year(s) {partial} have fewer than 12 months of data'
           + (' and were not used as the default start or end.' if start not in partial and end not in partial
              else '; the comparison uses one of them, so treat it with caution.')] if partial else []),
        ('Groups that declined: ' + ', '.join(declined)) if declined else 'No group declined over this period.']
    return ToolResult(tool=name, value=rows, definition=f'{REVENUE_DEF}, per {by} per year',
                      scope=scope, rows_used=int(work[REVENUE_COL].notna().sum()), caveats=caveats)


def get_monthly_revenue_share(model, year=None, channel=None, store=None, category=None) -> ToolResult:
    """Revenue per calendar month and its % of the annual total.
    With no year, months are summed across all years in scope."""
    name = 'get_monthly_revenue_share'
    sub, scope, err = _scoped(model, year, channel, store, category)
    if err:
        return _error(name, err, scope)
    work = sub.assign(**{REVENUE_COL: _rev(sub)}).dropna(subset=['Month'])
    g = work.groupby('Month')[REVENUE_COL].sum()
    total = float(g.sum())
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    value = {months[int(m) - 1]: {'revenue': round(float(v), 2),
                                  'share_pct': round(float(v) / total * 100, 2) if total else None}
             for m, v in g.items()}
    denom = f'the {year} total' if year is not None else 'the total for all years in scope'
    return ToolResult(tool=name, value=value,
                      definition=f'{REVENUE_DEF}, per month, as a % of {denom}',
                      scope=scope, rows_used=int(work[REVENUE_COL].notna().sum()),
                      caveats=BASE_CAVEATS + _missing_caveat(sub) + [f'Denominator: {denom}.'])


def get_satisfaction_vs_revenue(model, survey) -> ToolResult:
    """Correlation between survey satisfaction and revenue, one point per store-year."""
    name = 'get_satisfaction_vs_revenue'
    if survey is None or missing_columns(survey, 'Survey'):
        return _error(name, 'The Survey table is not loaded or is missing required columns.')
    rev = (model.assign(**{REVENUE_COL: _rev(model)})
           .groupby(['Store ID', 'Year'], as_index=False)[REVENUE_COL].sum())
    s = survey.copy()
    s['Store ID'] = s['Store ID'].astype('string').str.strip()
    s['Year'] = pd.to_numeric(s['Year'].astype(str).str.extract(r'(\d{4})')[0], errors='coerce').astype('Int64')
    s = s.drop_duplicates(subset=['Store ID', 'Year'])
    rev['Year'] = rev['Year'].astype('Int64')
    joined = rev.merge(s[['Store ID', 'Year', 'Satisfaction', 'Interest']], on=['Store ID', 'Year'], how='left')
    usable = joined.dropna(subset=['Satisfaction', REVENUE_COL])
    excluded = len(joined) - len(usable)
    if len(usable) < 3:
        return _error(name, 'Fewer than 3 store-years have both revenue and a satisfaction score.')
    r = float(usable['Satisfaction'].corr(usable[REVENUE_COL]))
    # within-store: does a store earn more in its higher-satisfaction years?
    within = usable.groupby('Store ID').apply(
        lambda d: d['Satisfaction'].corr(d[REVENUE_COL]) if len(d) >= 3 else None,
        include_groups=False).dropna()
    value = {
        'correlation_satisfaction_revenue': round(r, 3),
        'store_years_used': int(len(usable)),
        'store_years_excluded_no_survey': int(excluded),
        'avg_within_store_correlation': round(float(within.mean()), 3) if len(within) else None,
        'stores_in_within_store_average': int(len(within)),
    }
    return ToolResult(
        tool=name, value=value,
        definition='Pearson correlation between Satisfaction (0-5 survey score) and that store\'s '
                   'revenue in the same year; one point per store-year',
        scope={'year': 'All', 'store': 'All with a survey score'},
        rows_used=int(len(usable)),
        caveats=['Correlation is not causation: other factors (store size, market, location) '
                 'can drive both satisfaction and revenue.',
                 f'{excluded} store-year(s) had no satisfaction score (the website has no survey) '
                 'and were excluded.',
                 'Revenue is not profit.'])


def get_data_quality_report(model, tables: dict | None = None) -> ToolResult:
    """Blank, duplicate and unmatched values in the loaded data."""
    name = 'get_data_quality_report'
    if tables:
        issues = sales_quality_issues(tables['Sales'], tables)
        value = {row['Check']: int(row['Rows affected']) for _, row in issues.iterrows()}
        survey = tables.get('Survey')
        if survey is not None and 'Satisfaction' in survey.columns:
            value['Survey rows with blank Satisfaction'] = int(survey['Satisfaction'].isna().sum())
    else:
        value = data_quality_report(model).value
    problems = {k: v for k, v in value.items() if v} if isinstance(value, dict) else {}
    return ToolResult(tool=name, value=value,
                      definition='Counts of rows affected by each data-quality check',
                      scope={'tables': 'all loaded tables'}, rows_used=len(model),
                      caveats=['Checks with a count above 0 need attention: ' + ', '.join(problems)]
                      if problems else ['No problems found by these checks.'])