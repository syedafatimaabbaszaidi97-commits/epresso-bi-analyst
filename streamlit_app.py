"""Main app. Upload the five E-Presso tables, profile them, join them, and
reconcile total revenue with Power BI.
Keep calculations in analytics.py and the assistant in agent.py."""
import pandas as pd
import plotly.express as px
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

# ---------------------------------------------------------------- 3. dashboard
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
CHANNEL_COLORS = {'Website': '#2a6fdb', 'Brick & Mortar': '#d9822b', 'Unknown': '#9aa0a6'}


def money(x):
    return f'${x:,.0f}'


with tab_dash:
    # Filters: options come from the loaded data. An empty box means "all".
    options = analytics.filter_options(model)
    st.subheader('Filters')
    st.caption('Leave a filter empty to include everything. Every number and chart below uses these filters.')
    f1, f2, f3, f4 = st.columns(4)
    chosen = {
        'Year': f1.multiselect('Year', options['Year'], key='f_year'),
        'Channel': f2.multiselect('Sales channel', options['Channel'], key='f_channel'),
        'Category': f3.multiselect('Product category', options['Category'], key='f_category'),
        'Store': f4.multiselect('Store', options['Store'], key='f_store'),
    }
    selections = {k: (v if v else None) for k, v in chosen.items()}
    view = analytics.filter_model(model, selections)
    scope = '; '.join(f'{k}: {", ".join(map(str, v))}' for k, v in selections.items() if v) \
        or 'all years, all stores, all categories, both channels'
    st.markdown(f'**Scope:** {scope}')

    if view.empty:
        st.warning('No sales match these filters. Clear one or more filters.')
    else:
        k = analytics.kpis(view)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Total revenue', money(k['total']))
        c2.metric('Website share of revenue',
                  f"{k['website_share_pct']:.1f}%" if k['website_share_pct'] is not None else 'n/a')
        if k['growth']:
            g = k['growth']
            c3.metric(f"Revenue growth {g['from']}→{g['to']}", f"{g['pct']:+.1f}%")
        else:
            c3.metric('Revenue growth', 'needs 2+ years')
        c4.metric('Sales rows (product-store-months)', f"{k['rows']:,}")

        # Chart 1: revenue by year, split by channel
        by_year = analytics.revenue_by(view, 'Year', 'Channel')
        by_year['Year'] = by_year['Year'].astype(str)
        fig1 = px.bar(by_year, x='Year', y='Revenue', color='Channel', barmode='group',
                      color_discrete_map=CHANNEL_COLORS,
                      title='Revenue by year and sales channel',
                      labels={'Revenue': 'Revenue ($)', 'Year': 'Year'})
        fig1.update_layout(yaxis_tickprefix='$', yaxis_tickformat=',.0f')
        st.plotly_chart(fig1, use_container_width=True)

        left, right = st.columns(2)
        # Chart 2: monthly pattern, one line per year
        by_month = analytics.revenue_by(view, 'Year', 'Month').sort_values(['Year', 'Month'])
        by_month['Month name'] = by_month['Month'].map(lambda m: MONTHS[int(m) - 1])
        by_month['Year'] = by_month['Year'].astype(str)
        fig2 = px.line(by_month, x='Month name', y='Revenue', color='Year', markers=True,
                       category_orders={'Month name': MONTHS},
                       title='Monthly revenue pattern (one line per year)',
                       labels={'Revenue': 'Revenue ($)', 'Month name': 'Month'})
        fig2.update_layout(yaxis_tickprefix='$', yaxis_tickformat=',.0f')
        left.plotly_chart(fig2, use_container_width=True)

        # Chart 3: revenue by category
        by_cat = analytics.revenue_by(view, 'Category').sort_values('Revenue')
        fig3 = px.bar(by_cat, x='Revenue', y='Category', orientation='h',
                      title='Revenue by product category',
                      labels={'Revenue': 'Revenue ($)', 'Category': ''})
        fig3.update_layout(xaxis_tickprefix='$', xaxis_tickformat=',.0f')
        right.plotly_chart(fig3, use_container_width=True)

        # Chart 4: revenue by store
        by_store = analytics.revenue_by(view, 'Store', 'Channel').sort_values('Revenue')
        fig4 = px.bar(by_store, x='Revenue', y='Store', color='Channel', orientation='h',
                      color_discrete_map=CHANNEL_COLORS,
                      title='Revenue by store (SH00 is the website)',
                      labels={'Revenue': 'Revenue ($)', 'Store': ''},
                      height=max(350, 28 * len(by_store)))
        fig4.update_layout(xaxis_tickprefix='$', xaxis_tickformat=',.0f')
        st.plotly_chart(fig4, use_container_width=True)

        with st.expander('Reconciliation figures for Power BI (unfiltered)'):
            a, b = st.columns(2)
            a.markdown('**Revenue by channel**')
            a.dataframe(recon['by_channel'].map('${:,.2f}'.format).rename('Revenue'))
            b.markdown('**Revenue by year**')
            b.dataframe(recon['by_year'].map('${:,.2f}'.format).rename('Revenue'))
            st.metric('Total revenue, all data', f"${recon['total']:,.2f}")

# ---------------------------------------------------------------- assistant
@st.cache_resource(show_spinner=False)
def gemini_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def secret(name: str, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:  # no secrets file at all (e.g. running locally without one)
        return default


with tab_assistant:
    st.subheader('Ask the analytical assistant')
    api_key = secret('GEMINI_API_KEY')
    modes = ['Gemini language model (Option A)', 'Rules-based (Option B)']
    mode = st.radio('Assistant mode', modes, index=0 if api_key else 1, horizontal=True,
                    disabled=not api_key, key='assistant_mode')
    if not api_key:
        st.caption('No GEMINI_API_KEY in Streamlit Secrets, so the rules-based assistant is used.')
    st.caption('**Gemini mode:** the model chooses one of six approved functions and explains the '
               'result; our code runs the function. **Rules-based mode:** keywords choose the function. '
               'If Gemini fails or its free quota is used up, the rules-based assistant answers instead. '
               'Answers use the full dataset; the dashboard filters do not apply here.')
    with st.expander('What can I ask?'):
        st.markdown('\n'.join(f'- {x}' for x in agent.CAN_ANSWER))
        st.markdown('Examples: *How much revenue did the website generate in 2028?* · '
                    '*Which store had the greatest percentage revenue decline?* · '
                    '*Which month is busiest?* · *Does customer satisfaction cause higher revenue?*')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    st.caption(f"Questions used this session: {st.session_state['n_questions']} of {MAX_QUESTIONS}")
    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            a = item['answer']
            st.caption(f"Answered by: {a.get('answered_by', 'Rules-based assistant')}")
            st.markdown(f"**Scope:** {a['scope']}")
            st.markdown(f"**Findings:** {a['findings']}")
            if a['interpretation']:
                st.markdown(f"**Interpretation:** {a['interpretation']}")
            if a['next_steps']:
                st.markdown(f"**Next steps:** {a['next_steps']}")
            with st.expander('Tool-call log (function, arguments, raw result)'):
                st.json(a['tool_log'])
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning(f'Question limit ({MAX_QUESTIONS}) reached for this session. '
                       'Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            data = {'model': model, 'tables': tables}
            if mode.startswith('Gemini') and api_key:
                try:
                    client = gemini_client(api_key)
                except Exception as exc:  # e.g. package missing: fall back, don't crash
                    answer = agent.answer_question(question, data, mode='rules')
                    answer['answered_by'] = f'Rules-based assistant (Gemini could not start: {exc})'
                else:
                    models = [m.strip() for m in str(secret('GEMINI_MODEL', '')).split(',') if m.strip()]
                    with st.spinner('Asking Gemini...'):
                        answer = agent.answer_question_llm(question, data, client,
                                                           models or agent.DEFAULT_MODELS)
            else:
                answer = agent.answer_question(question, data, mode='rules')
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()