"""Main app. Upload the five E-Presso tables, profile them, join them, and
reconcile total revenue with Power BI.
Keep calculations in analytics.py and the assistant in agent.py."""
import pandas as pd
import streamlit as st

import agent
import analytics

MAX_QUESTIONS = 15  # per-session limit for the assistant

st.set_page_config(page_title='E-Presso BI Analyst', layout='wide')
st.title('E-Presso BI Analyst-Fatima-Aon-Nellie')
st.caption('Upload the five E-Presso tables in the sidebar. Every number on this page is '
           'calculated from the files you upload; nothing is typed into the code.')


@st.cache_data(show_spinner=False)
def cached_load(file_bytes: bytes, filename: str, sheet_name, header_hint):
    # The cache is keyed on the file CONTENTS, so a modified file is never
    # confused with the original one.
    return analytics.load_table(file_bytes, filename, sheet_name, header_hint)


# Which column tells us we found the header row (the Survey sheet has title rows on top)
HEADER_HINTS = {'Sales': 'Sales ID', 'Stores': 'ID', 'Products': 'Product ID',
                'Categories': 'Category ID', 'Survey': 'Survey ID'}
PREFERRED_SHEET = {'Survey': 'Survey Results'}
FILE_TYPES = ['csv', 'txt', 'tsv', 'xlsx', 'xls']

uploads, sheets = {}, {}
with st.sidebar:
    st.header('Data')
    st.caption('One file per table. CSV or Excel.')
    for table, spec in analytics.TABLE_SPECS.items():
        f = st.file_uploader(f'{table}  ({spec["source"]})', type=FILE_TYPES, key=f'up_{table}')
        uploads[table] = f
        sheets[table] = 0
        if f is not None and f.name.lower().endswith(analytics.EXCEL_EXT):
            names = analytics.excel_sheet_names(f.getvalue())
            default = names.index(PREFERRED_SHEET[table]) if PREFERRED_SHEET.get(table) in names else 0
            sheets[table] = st.selectbox(f'{table} sheet', names, index=default, key=f'sh_{table}')

missing_files = [t for t, f in uploads.items() if f is None]
if missing_files:
    st.info('Upload these tables in the sidebar to begin: ' + ', '.join(missing_files))
    st.stop()

# ---------------------------------------------------------------- load + validate
tables, problems = {}, []
for table, f in uploads.items():
    try:
        tables[table] = cached_load(f.getvalue(), f.name, sheets[table], HEADER_HINTS[table])
    except Exception as exc:  # readable message instead of a traceback
        problems.append(f'**{table}** ({f.name}): could not read the file ({exc}).')
        continue
    gone = analytics.missing_columns(tables[table], table)
    if gone:
        problems.append(f'**{table}** ({f.name}) is missing required column(s): {", ".join(gone)}.')
if problems:
    st.error('Some files cannot be used yet:\n\n' + '\n\n'.join(f'- {p}' for p in problems))
    st.stop()

# Not cached on purpose: the joins take well under a second and are always rebuilt
# from the tables just loaded, so a modified upload can never show stale numbers.
model, merge_checks = analytics.build_sales_model(tables)
recon = analytics.reconciliation_summary(model)

tab_data, tab_model, tab_dash, tab_assistant = st.tabs(
    ['Data and profile', 'Data model', 'Dashboard', 'Assistant'])

# ---------------------------------------------------------------- 1. profile
with tab_data:
    st.subheader('Tables loaded')
    st.dataframe(analytics.grain_table(tables), hide_index=True, use_container_width=True)
    for table, df in tables.items():
        profile = analytics.profile_table(df)
        keys = analytics.key_report(df, table)
        with st.expander(f'{table}: {profile["rows"]:,} rows x {profile["columns"]} columns'):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Rows', f"{profile['rows']:,}")
            c2.metric('Columns', profile['columns'])
            c3.metric('Duplicate rows', profile['duplicate_rows'])
            c4.metric(f'Duplicate keys ({", ".join(keys["key"])})', keys['duplicate_keys'])
            st.dataframe(df.head(20), use_container_width=True)
            left, right = st.columns(2)
            left.markdown('**Missing values**')
            left.dataframe(profile['missing'], hide_index=True)
            right.markdown('**Data types**')
            right.dataframe(profile['dtypes'], hide_index=True)
    st.subheader('Sales data-quality checks')
    st.dataframe(analytics.sales_quality_issues(tables['Sales'], tables),
                 hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- 2. joins
with tab_model:
    st.subheader('Grain statements')
    st.dataframe(analytics.grain_table(tables), hide_index=True, use_container_width=True)
    st.subheader('Merge checks')
    st.caption('Each lookup table is joined onto Sales one at a time. If the row count or '
               'revenue sum changes, the join is duplicating or dropping revenue.')
    fmt = merge_checks.copy()
    for c in ('Revenue sum before', 'Revenue sum after'):
        fmt[c] = fmt[c].map('${:,.2f}'.format)
    st.dataframe(fmt, hide_index=True, use_container_width=True)
    if (merge_checks['Rows before'] != merge_checks['Rows after']).any() or \
            (merge_checks['Revenue sum before'] - merge_checks['Revenue sum after']).abs().max() > 0.005:
        st.error('A join changed the row count or revenue total. Investigate before using the numbers.')
    else:
        st.success('All joins kept the same row count and revenue total.')
    st.markdown('**Survey** is one row per store per year, a different grain from Sales, so it is '
                'not joined to the sales rows. It links to Sales through `StoreYear` = `Survey ID` '
                'and will be compared at the store-year level.')

# ---------------------------------------------------------------- 3. revenue
with tab_dash:
    st.subheader('Step 3: reconcile with Power BI')
    k1, k2, k3 = st.columns(3)
    k1.metric('Total revenue (all years, all stores)', f"${recon['total']:,.2f}")
    k2.metric('Sales rows used', f"{recon['rows'] - recon['blank_revenue_rows']:,}")
    years = recon['by_year'].index
    k3.metric('Period covered', f'{years.min()}–{years.max()}' if len(years) else 'n/a')
    if recon['blank_revenue_rows']:
        st.warning(f"{recon['blank_revenue_rows']} row(s) have blank or non-numeric revenue "
                   'and are excluded from the total.')
    left, right = st.columns(2)
    left.markdown('**Revenue by channel**')
    left.dataframe(recon['by_channel'].map('${:,.2f}'.format).rename('Revenue'))
    right.markdown('**Revenue by year**')
    right.dataframe(recon['by_year'].map('${:,.2f}'.format).rename('Revenue'))
    st.info('Next (build step 4): filters and at least three charts go here.')

# ---------------------------------------------------------------- assistant
with tab_assistant:
    st.caption('Mode: rules-based starter. Describe what you actually built here.')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            st.markdown(f"**Scope:** {item['answer']['scope']}")
            st.markdown(f"**Findings:** {item['answer']['findings']}")
            if item['answer']['interpretation']:
                st.markdown(f"**Interpretation:** {item['answer']['interpretation']}")
            with st.expander('Tool-call log'):
                st.json(item['answer']['tool_log'])
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            answer = agent.answer_question(question, model, mode='rules')
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()