"""Assistant: a dispatcher plus a router.
The router (rules now, a language model later) only CHOOSES a tool and its
arguments. Our code RUNS the tool. Nothing here executes model-written code,
and no tool can change the loaded data."""
from __future__ import annotations

import re

import analytics
from analytics import ToolResult

# The ONLY things the assistant may do. 'needs' says which loaded data the
# dispatcher hands to the function; the router can never pass data itself.
TOOLS = {
    'get_total_revenue': {
        'fn': analytics.get_total_revenue, 'needs': 'model',
        'params': {'year', 'channel', 'store', 'category'},
        'description': 'Total revenue, optionally for one year, channel, store or category.',
    },
    'get_revenue_breakdown': {
        'fn': analytics.get_revenue_breakdown, 'needs': 'model',
        'params': {'by', 'year', 'channel', 'store', 'category'},
        'description': 'Revenue and % share by channel, store, category or year.',
    },
    'get_revenue_trend': {
        'fn': analytics.get_revenue_trend, 'needs': 'model',
        'params': {'by', 'start_year', 'end_year', 'channel', 'store', 'category'},
        'description': 'Revenue per year and % change for each channel, store or category.',
    },
    'get_monthly_revenue_share': {
        'fn': analytics.get_monthly_revenue_share, 'needs': 'model',
        'params': {'year', 'channel', 'store', 'category'},
        'description': 'Revenue per month as a % of the annual total.',
    },
    'get_satisfaction_vs_revenue': {
        'fn': analytics.get_satisfaction_vs_revenue, 'needs': 'survey',
        'params': set(),
        'description': 'Correlation between customer satisfaction and store revenue.',
    },
    'get_data_quality_report': {
        'fn': analytics.get_data_quality_report, 'needs': 'tables',
        'params': set(),
        'description': 'Blank, duplicate and unmatched values in the loaded data.',
    },
}

CAN_ANSWER = [
    'total revenue (for a year, channel, store or category)',
    'revenue by channel, store, category or year',
    'growth or decline by channel, store or category',
    'monthly revenue pattern',
    'customer satisfaction vs. revenue',
    'data quality',
]
UNSUPPORTED = ('I can only answer questions that my approved analytical functions can calculate '
               'from the loaded data. I can answer questions about: ' + '; '.join(CAN_ANSWER) + '.')

# For the language-model upgrade: keep these rules in the system prompt.
SYSTEM_PROMPT = """You are an analytical assistant for E-Presso managers.
Rules:
- Only state numbers that appear in a tool result. Never estimate or recall figures.
- Always call a tool before making a numerical claim.
- State the scope (which filters/period) of every answer.
- Separate Findings (from tool results), Interpretation, and Next steps.
- Revenue is not profit. Association is not causation.
- If the data cannot support a conclusion, say so and name the missing information.
- Never ask for, run, or write code. Never modify data."""


# ------------------------------------------------------------------ dispatcher
def run_tool(name: str, args: dict, data: dict, log: list) -> ToolResult:
    """The dispatcher: validate the request, run the function, log the call.
    `data` holds what the app loaded: {'model': ..., 'tables': {...}}."""
    if name not in TOOLS:
        result = analytics._error(name, f'Unknown tool: {name}')
    else:
        unexpected = set(args) - TOOLS[name]['params']
        if unexpected:
            result = analytics._error(name, f'Unexpected argument(s): {sorted(unexpected)}')
        else:
            spec = TOOLS[name]
            model = data['model']
            tables = data.get('tables') or {}
            try:
                if spec['needs'] == 'survey':
                    result = spec['fn'](model, tables.get('Survey'), **args)
                elif spec['needs'] == 'tables':
                    result = spec['fn'](model, tables or None, **args)
                else:
                    result = spec['fn'](model, **args)
            except Exception as exc:  # a tool bug becomes an error result, not a crash
                result = analytics._error(name, f'The calculation failed: {exc}')
    log.append({'tool': name, 'arguments': args, 'result': result.to_dict()})
    return result


# ------------------------------------------------------------------ rules router
def _find_params(q: str, model) -> dict:
    """Pull year / channel / category / store out of the question, using only
    values that exist in the loaded data."""
    args = {}
    years = [int(y) for y in re.findall(r'\b(20\d\d)\b', q)]
    if years:
        args['years'] = years
    if re.search(r'\b(website|online|web|e-?commerce)\b', q):
        args['channel'] = 'Website'
    elif re.search(r'brick|physical|in-store|bricks', q):
        args['channel'] = 'Brick & Mortar'
    for cat in model['Category'].dropna().unique():
        c = str(cat).lower()
        if c in q or c.rstrip('s') in q.split():
            args['category'] = cat
            break
    stores = model[['Store ID', 'City']].drop_duplicates()
    for _, row in stores.iterrows():
        sid, city = str(row['Store ID']).lower(), str(row['City']).lower()
        if re.search(rf'\b{sid}\b', q) or (city not in ('nan', '<na>', '') and city in q):
            args['store'] = row['Store ID'] if re.search(rf'\b{sid}\b', q) else row['City']
            break
    return args


def _group(q: str, default: str = 'channel') -> str:
    if re.search(r'\bstores?\b|location|shop', q):
        return 'store'
    if re.search(r'categor|product', q):
        return 'category'
    if re.search(r'channel|website|online|brick', q):
        return 'channel'
    if re.search(r'\byears?\b|annual', q):
        return 'year'
    return default


def rules_router(question: str, model):
    """Option B router. Returns a dict with the tool to run, its arguments, and
    any limitation the answer must state; or None if unsupported."""
    q = question.lower()
    p = _find_params(q, model)
    years = p.pop('years', [])
    filt = {k: v for k, v in p.items() if k in ('channel', 'store', 'category')}

    if re.search(r'missing|duplicate|quality|blank|error in the data', q):
        return {'tool': 'get_data_quality_report', 'args': {}}

    if re.search(r'satisf|survey|happy|customer experience', q):
        return {'tool': 'get_satisfaction_vs_revenue', 'args': {},
                'limitation': 'causation' if re.search(r'caus|lead to|drive|because|effect|impact', q) else None}

    if re.search(r'profit|margin|cost|expense|earnings|net income', q):
        return {'tool': 'get_revenue_breakdown', 'args': {'by': _group(q, 'store'), **filt},
                'limitation': 'profit'}

    if re.search(r'\bclose\b|shut|should (we|e-presso)|invest|open a new|recommend', q):
        args = {'by': 'store' if 'store' in filt or re.search(r'stores?\b', q) else _group(q)}
        args.update({k: v for k, v in filt.items() if not (k == 'store')})
        return {'tool': 'get_revenue_trend', 'args': args, 'limitation': 'judgment',
                'focus': filt.get('store')}

    if re.search(r'caus|why did|why is|because', q):
        return {'tool': 'get_revenue_trend', 'args': {'by': _group(q), **filt}, 'limitation': 'causation'}

    if re.search(r'grow|grew|gain|lost|declin|decreas|increas|trend|change|drop|fell|rose|over time', q):
        by = _group(q)
        if by == 'year':
            by = 'channel'
        args = {'by': by, **{k: v for k, v in filt.items() if k != by}}
        if len(years) >= 2:
            args['start_year'], args['end_year'] = min(years), max(years)
        return {'tool': 'get_revenue_trend', 'args': args}

    if re.search(r'month|season|busiest|quarter', q):
        args = dict(filt)
        if years:
            args['year'] = years[0]
        return {'tool': 'get_monthly_revenue_share', 'args': args}

    wants_breakdown = re.search(r'\bby\b|which|compare|comparison|split|share|vs\.?|versus|best|worst|top|highest|lowest|most|least|rank', q)
    if wants_breakdown and re.search(r'revenue|sales|sell|earn|made|perform', q):
        by = _group(q)
        args = {'by': by, **{k: v for k, v in filt.items() if k != by}}
        if years:
            args['year'] = years[0]
        return {'tool': 'get_revenue_breakdown', 'args': args}

    if re.search(r'revenue|sales|how much|total|earn|made|sold', q):
        args = dict(filt)
        if years:
            args['year'] = years[0]
        return {'tool': 'get_total_revenue', 'args': args}
    return None


def llm_router(question: str, model):
    """Kept for reference: Option A is implemented in answer_question_llm below."""
    raise NotImplementedError('Use answer_question_llm for the language-model assistant.')


# ------------------------------------------------------------------ answer text
def _money(x):
    return f'${x:,.2f}'


def _scope_text(scope: dict) -> str:
    if not scope:
        return 'n/a (the request could not be run)'
    parts = [f'{k.replace("_", " ")}: {v}' for k, v in scope.items()]
    return ', '.join(parts) + ' (full loaded dataset; dashboard filters are not applied)'


def format_findings(result: ToolResult, focus=None) -> str:
    """Turn a tool result into sentences. Every number comes from result.value."""
    if result.error:
        return f'The request could not be completed: {result.error}'
    v, t = result.value, result.tool
    if t == 'get_total_revenue':
        return f'Total revenue: **{_money(v)}** ({result.rows_used:,} sales rows).'
    if t == 'get_revenue_breakdown':
        lines = [f'- {k}: {_money(d["revenue"])} ({d["share_pct"]}%)' for k, d in v.items()]
        top = next(iter(v))
        return f'Highest: **{top}**.\n\n' + '\n'.join(lines)
    if t == 'get_revenue_trend':
        s = result.scope
        a, b = s['from_year'], s['to_year']
        lines = []
        for row in v:
            pct = f'{row["pct_change"]:+.2f}%' if row['pct_change'] is not None else 'n/a (no base-year revenue)'
            mark = ' ⬅' if focus and str(focus).lower() in row['group'].lower() else ''
            lines.append(f'- {row["group"]}: {_money(row[f"revenue_{a}"])} → '
                         f'{_money(row[f"revenue_{b}"])} ({pct}){mark}')
        best, worst = v[0], v[-1]
        head = ''
        if focus:
            hit = [(i, r) for i, r in enumerate(v) if str(focus).lower() in r['group'].lower()]
            if hit:
                i, r = hit[0]
                pct = f'{r["pct_change"]:+.2f}%' if r['pct_change'] is not None else 'n/a'
                head = (f'**{r["group"]}**: {_money(r[f"revenue_{a}"])} in {a} → {_money(r[f"revenue_{b}"])} '
                        f'in {b} ({pct}), ranked {i + 1} of {len(v)} by growth.\n\n')
        if len(v) == 1:
            head += f'From {a} to {b}, **{best["group"]}** changed by {lines[0].split("(")[-1].rstrip(")")}.'
        elif best['pct_change'] is not None and best['pct_change'] < 0:
            head += (f'From {a} to {b}, every group declined. The smallest decline was **{best["group"]}** '
                     f'({best["pct_change"]:+.2f}%); the largest was **{worst["group"]}** ({worst["pct_change"]:+.2f}%).')
        else:
            head += f'From {a} to {b}, the largest growth was **{best["group"]}** ({best["pct_change"]:+.2f}%).'
            if worst['pct_change'] is not None and worst['pct_change'] < 0:
                head += f' The largest decline was **{worst["group"]}** ({worst["pct_change"]:+.2f}%).'
            else:
                head += ' No group declined over this period.'
        return head + '\n\n' + '\n'.join(lines)
    if t == 'get_monthly_revenue_share':
        top = max(v.items(), key=lambda kv: kv[1]['revenue'])
        low = min(v.items(), key=lambda kv: kv[1]['revenue'])
        lines = [f'- {m}: {_money(d["revenue"])} ({d["share_pct"]}%)' for m, d in v.items()]
        return (f'Highest month: **{top[0]}** ({top[1]["share_pct"]}%). Lowest: **{low[0]}** '
                f'({low[1]["share_pct"]}%).\n\n' + '\n'.join(lines))
    if t == 'get_satisfaction_vs_revenue':
        w = v['avg_within_store_correlation']
        return (f'Across {v["store_years_used"]} store-years, the correlation between satisfaction and '
                f'revenue is **{v["correlation_satisfaction_revenue"]}**. Within the same store over time, '
                f'the average correlation is **{w}** ({v["stores_in_within_store_average"]} stores). '
                f'{v["store_years_excluded_no_survey"]} store-years had no survey score and were excluded.')
    if t == 'get_data_quality_report':
        return '\n'.join(f'- {k}: {n}' for k, n in v.items())
    return str(v)


LIMITS = {
    'profit': ('**This data cannot measure profit.** It has revenue only, with no costs, rent, staffing '
               'or cost of goods. The figures above rank stores by revenue, which is not the same as '
               'profitability.',
               'To answer this, E-Presso would need operating costs, cost of goods sold, and rent by store.'),
    'causation': ('**These numbers show association, not cause.** Many factors (store size, market, '
                  'location, pricing, local competition) could drive both measures, and this data cannot '
                  'separate them.',
                  'A causal answer would need a controlled comparison, such as a pilot in some stores with '
                  'matched control stores, or data on the other factors.'),
    'judgment': ('**The data cannot support this decision on its own.** Revenue trends show performance '
                 'direction, but not whether a store is profitable or worth keeping.',
                 'No recommendation is supported by this data. Management would also need store costs and '
                 'profitability, lease terms, staffing, local market conditions, and whether customers would '
                 'move to another store or the website.'),
}


def _interpretation(result: ToolResult, route: dict) -> str:
    if result.error:
        return ''
    t, v = result.tool, result.value
    if t == 'get_satisfaction_vs_revenue':
        r = v['correlation_satisfaction_revenue']
        strength = 'weak' if abs(r) < 0.3 else 'moderate' if abs(r) < 0.6 else 'strong'
        direction = 'positive' if r > 0 else 'negative'
        text = (f'Across stores this is a {strength} {direction} association: stores with higher satisfaction '
                f'tend to have {"higher" if r > 0 else "lower"} revenue.')
        w = v['avg_within_store_correlation']
        if w is not None and (w > 0) != (r > 0):
            text += (f' But within the same store over time the relationship runs the other way ({w}), '
                     'so the overall pattern likely reflects differences between stores (size, market) '
                     'rather than satisfaction itself.')
        return text + ' Neither figure shows that satisfaction causes revenue.'
    if t == 'get_revenue_trend':
        return 'Percentage change compares only the first and last year; check the yearly values for the path in between.'
    if t == 'get_revenue_breakdown' and result.scope.get('grouped_by') == 'store':
        return 'Store revenue reflects store size and market as well as performance.'
    return ''


def answer_question(question: str, data, mode: str = 'rules') -> dict:
    """Route, run, and write the answer in Findings / Interpretation / Next steps."""
    if not isinstance(data, dict):  # allow passing a bare model DataFrame
        data = {'model': data, 'tables': {}}
    log: list = []
    try:
        route = rules_router(question, data['model']) if mode == 'rules' else llm_router(question, data['model'])
    except NotImplementedError as exc:
        return {'scope': 'n/a', 'findings': str(exc), 'interpretation': '', 'next_steps': '', 'tool_log': log}
    if route is None:
        return {'scope': 'n/a', 'findings': UNSUPPORTED, 'interpretation': '',
                'next_steps': 'Try rephrasing with words like revenue, growth, channel, store, category or month.',
                'tool_log': log, 'answered_by': 'Rules-based assistant'}
    result = run_tool(route['tool'], route['args'], data, log)
    limit = LIMITS.get(route.get('limitation'))
    interp = ' '.join(x for x in [limit[0] if limit else '', _interpretation(result, route)] if x)
    caveats = ' '.join(result.caveats)
    if result.error:
        next_steps = 'Ask again using one of the valid values listed above.'
    elif limit:
        next_steps = limit[1]
    else:
        next_steps = 'No recommendation is supported by this data alone; this is a descriptive figure.'
    return {
        'scope': _scope_text(result.scope),
        'findings': format_findings(result, route.get('focus')),
        'interpretation': ' '.join(x for x in [interp, f'Caveats: {caveats}' if caveats else ''] if x),
        'next_steps': next_steps,
        'tool_log': log,
        'answered_by': 'Rules-based assistant',
    }


# ================================================================ OPTION A: GEMINI
# The model (1) chooses ONE tool from TOOLS with arguments limited to values that
# exist in the loaded data, and (2) explains the tool result. Our code runs the
# tool. Findings are written by our code from the result; the model writes only
# Interpretation and Next steps, and any number it uses must appear in the result.
DEFAULT_MODELS = ['gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3-flash-preview', 'gemini-flash-latest']


def tool_schemas(model) -> list[dict]:
    """JSON-schema declarations for Gemini. Allowed values come from the loaded data."""
    years = [str(y) for y in sorted(model['Year'].dropna().unique())]
    enums = {
        'year': years, 'start_year': years, 'end_year': years,
        'channel': sorted(model['Channel'].dropna().astype(str).unique()),
        'store': sorted(model['Store'].dropna().astype(str).unique()),
        'category': sorted(model['Category'].dropna().astype(str).unique()),
    }
    help_text = {
        'year': 'Limit to one year.', 'start_year': 'First year of the comparison.',
        'end_year': 'Last year of the comparison.', 'channel': 'Sales channel.',
        'store': 'One store. SH00 is the website.', 'category': 'Product category.',
    }
    by_values = {'get_revenue_breakdown': ['channel', 'store', 'category', 'year'],
                 'get_revenue_trend': ['channel', 'store', 'category']}
    out = []
    for name, spec in TOOLS.items():
        props = {}
        for p in sorted(spec['params']):
            if p == 'by':
                props[p] = {'type': 'string', 'enum': by_values[name], 'description': 'What to group by.'}
            else:
                props[p] = {'type': 'string', 'enum': enums[p], 'description': help_text[p]}
        decl = {'name': name, 'description': spec['description']}
        if props:  # functions with no parameters get no schema at all
            decl['parameters'] = {'type': 'object', 'properties': props}
        out.append(decl)
    return out


CHOOSER_PROMPT = SYSTEM_PROMPT + """
Your only job in this step is to call exactly ONE function that best answers the
manager's question. Leave out any parameter the question does not specify.
For questions about profit, call get_revenue_breakdown (by store) so revenue can be shown.
For questions about causes or whether to close/open a store, call the most relevant
revenue function; the limitation will be explained afterwards.
If no function is relevant, do not call any function and reply with one short sentence."""

EXPLAIN_PROMPT = SYSTEM_PROMPT + """
You receive a manager's question and the JSON result of the function that was run.
The Findings are already shown to the manager. Write ONLY:
- "interpretation": 2-4 sentences on what the result means, its limits and caveats.
- "next_steps": 1-3 sentences on what to check next, or exactly
  "No recommendation is supported by this data." when a decision is asked for.
Use only numbers that appear in the result. Do not mention profit as if known.
Return JSON: {"interpretation": "...", "next_steps": "..."}"""


def _gemini_choose_tool(client, model_name: str, question: str, schemas: list[dict]):
    """Call 1: the model picks a function. Returns (name, args) or None."""
    from google.genai import types
    decls = [types.FunctionDeclaration(name=s['name'], description=s['description'],
                                       parameters_json_schema=s.get('parameters')) for s in schemas]
    config = types.GenerateContentConfig(
        system_instruction=CHOOSER_PROMPT,
        tools=[types.Tool(function_declarations=decls)],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode='AUTO')),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # WE run tools
        temperature=0,
    )
    resp = client.models.generate_content(model=model_name, contents=question, config=config)
    calls = resp.function_calls or []
    if not calls:
        return None
    return calls[0].name, dict(calls[0].args or {})


def _gemini_explain(client, model_name: str, question: str, result: dict) -> dict:
    """Call 2: the model explains the tool result. Returns {'interpretation', 'next_steps'}."""
    import json
    from google.genai import types
    config = types.GenerateContentConfig(system_instruction=EXPLAIN_PROMPT,
                                         response_mime_type='application/json', temperature=0.2)
    prompt = f'Question: {question}\n\nFunction result (JSON):\n{json.dumps(result, default=str)}'
    resp = client.models.generate_content(model=model_name, contents=prompt, config=config)
    text = (resp.text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)  # some models wrap JSON in a code fence
    data = json.loads(text)
    return {'interpretation': str(data.get('interpretation', '')).strip(),
            'next_steps': str(data.get('next_steps', '')).strip()}


def _clean_args(args: dict) -> dict:
    """Years arrive as strings (schema enums are strings); convert them back."""
    out = {}
    for k, v in args.items():
        if v in (None, '', 'All'):
            continue
        out[k] = int(v) if k in ('year', 'start_year', 'end_year') and str(v).isdigit() else v
    return out


def _numbers(text: str) -> list[float]:
    found = re.findall(r'-?\d[\d,]*\.?\d*', text or '')
    return [float(n.replace(',', '')) for n in found if n.replace(',', '').replace('.', '').strip('-')]


def unsupported_numbers(text: str, result: dict) -> list[float]:
    """Numbers in the model's text that cannot be traced to the tool result.
    Allowed: values in the result (rounded to 0-2 decimals, or in millions/thousands),
    years, and small counts (<= 12)."""
    import json
    allowed = set()
    for x in _numbers(json.dumps(result, default=str)):
        for v in (x, abs(x)):
            for scale in (1, 1e3, 1e6):
                for d in (0, 1, 2):
                    allowed.add(round(v / scale, d))
    bad = []
    for n in _numbers(text):
        if abs(n) <= 12 or 2000 <= n <= 2100:
            continue
        if not any(round(abs(n), d) in allowed for d in (0, 1, 2)):
            bad.append(n)
    return bad


def _limitation(question: str):
    q = question.lower()
    if re.search(r'profit|margin|cost|expense|net income', q):
        return 'profit'
    if re.search(r'\bclose\b|shut|should (we|e-presso)|invest|open a new|recommend', q):
        return 'judgment'
    if re.search(r'caus|why did|why is|because|lead to|drive', q):
        return 'causation'
    return None


RETRY_WAIT_SECONDS = 2  # pause before retrying a busy model


def _retryable(exc) -> bool:
    """Errors worth trying again or trying another model for: model missing (404),
    server busy (503/500), or that model's free quota used up (429)."""
    t = str(exc).lower()
    return any(k in t for k in ('404', 'not found', '503', 'unavailable', 'overloaded',
                                'high demand', '500', 'internal', '429', 'resource_exhausted', 'quota'))


def _call_with_fallback(fn, model_names, log, step):
    """Run fn(model_name) on each model in turn. A busy model (503) gets one retry
    after a short pause. Returns (result, model_used) or raises the last error."""
    import time
    last = None
    for name in model_names:
        for attempt in (1, 2):
            try:
                return fn(name), name
            except Exception as exc:
                last = exc
                log.append({'gemini_retry': step, 'model': name, 'attempt': attempt,
                            'error': f'{type(exc).__name__}: {str(exc)[:120]}'})
                if not _retryable(exc):
                    raise
                busy = any(k in str(exc).lower() for k in ('503', 'unavailable', 'high demand', 'overloaded'))
                if attempt == 1 and busy:
                    time.sleep(RETRY_WAIT_SECONDS)
                    continue
                break  # missing model, quota, or still busy: try the next model
    raise last


def answer_question_llm(question: str, data: dict, client, model_names=None) -> dict:
    """Option A. Falls back to the rules-based assistant if Gemini is unavailable."""
    model_names = model_names or DEFAULT_MODELS
    schemas = tool_schemas(data['model'])
    retries: list = []
    used_model = None
    try:
        picked, used_model = _call_with_fallback(
            lambda name: _gemini_choose_tool(client, name, question, schemas), model_names, retries, 'choose')
    except Exception as exc:
        last_error = exc
    if used_model is None:
        out = answer_question(question, data, mode='rules')
        out['answered_by'] = (f'Rules-based assistant (Gemini unavailable: '
                              f'{type(last_error).__name__}: {str(last_error)[:160]})')
        out['tool_log'] = retries + out['tool_log']
        return out

    if picked is None:
        return {'scope': 'n/a', 'findings': UNSUPPORTED, 'interpretation': '',
                'next_steps': 'Try asking about revenue, growth, channels, stores, categories or months.',
                'tool_log': retries + [{'router': f'gemini ({used_model})', 'chosen_tool': None}],
                'answered_by': f'Gemini ({used_model}): no approved function fits this question'}

    tool, raw_args = picked
    args = _clean_args(raw_args)
    log = retries + [{'router': f'gemini ({used_model})', 'chosen_tool': tool, 'raw_arguments': raw_args}]
    result = run_tool(tool, args, data, log)
    limit = LIMITS.get(_limitation(question))
    findings = format_findings(result)
    caveats = ' '.join(result.caveats)

    if result.error:
        return {'scope': _scope_text(result.scope), 'findings': findings, 'interpretation': '',
                'next_steps': 'Ask again using one of the valid values listed above.',
                'tool_log': log, 'answered_by': f'Gemini ({used_model})'}

    note = ''
    try:
        # start with the model that chose the tool, then the others in the list
        order = [used_model] + [m for m in model_names if m != used_model]
        expl, explained_by = _call_with_fallback(
            lambda name: _gemini_explain(client, name, question, result.to_dict()), order, log, 'explain')
        if explained_by != used_model:
            note = f' (explanation by {explained_by})'
        bad = unsupported_numbers(expl['interpretation'] + ' ' + expl['next_steps'], result.to_dict())
        if bad:
            log.append({'guard': 'explanation withheld', 'numbers_not_in_tool_result': bad,
                        'model_text': expl})
            note = ' (model explanation withheld: it used numbers not in the tool result)'
            expl = None
    except Exception as exc:
        log.append({'guard': 'explanation failed', 'error': f'{type(exc).__name__}: {str(exc)[:160]}'})
        note = ' (model explanation unavailable; standard explanation shown)'
        expl = None

    if expl:
        interp = ' '.join(x for x in [limit[0] if limit else '', expl['interpretation']] if x)
        next_steps = expl['next_steps'] or (limit[1] if limit else '')
        if limit and limit[1] not in next_steps:
            next_steps = (next_steps + ' ' + limit[1]).strip()
    else:
        interp = ' '.join(x for x in [limit[0] if limit else '', _interpretation(result, {})] if x)
        next_steps = limit[1] if limit else 'No recommendation is supported by this data alone; this is a descriptive figure.'
    return {
        'scope': _scope_text(result.scope),
        'findings': findings,
        'interpretation': ' '.join(x for x in [interp, f'Caveats: {caveats}' if caveats else ''] if x),
        'next_steps': next_steps,
        'tool_log': log,
        'answered_by': f'Gemini ({used_model}){note}',
    }