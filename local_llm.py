"""
Local LLM mode — fully air-gapped operation.

Architecture:
  1. Intent detection  — pure Python regex, determines which analysis to run
  2. Analysis          — runs entirely in analysis.py (pandas, no network)
  3. Narrative         — Ollama local LLM writes the summary from pre-computed stats

Nothing leaves the machine. No data, no summaries, no row counts.

Setup:
  brew install ollama
  ollama pull llama3.2          # ~2 GB, good quality, fast on Apple Silicon
  # or: ollama pull qwen2.5     # alternative
  ollama serve                  # starts at http://localhost:11434
"""

import re
import json
import requests
from typing import Optional
import pandas as pd

from analysis import (
    travel_expense_report,
    actuals_vs_plan,
    period_comparison,
    variance_driver_analysis,
    SIGNIFICANT_THRESHOLD,
)
from guardrails import log_data_access, audit

OLLAMA_BASE = 'http://localhost:11434'
DEFAULT_MODEL = 'llama3.2'   # change to any model you have pulled

NARRATIVE_SYSTEM = (
    'You are a concise SAP financial analyst. '
    'Given pre-computed analysis results, write a 3–5 sentence narrative. '
    'Be specific with numbers, highlight ⚠️ variances over 20%, '
    'name the top driver, and suggest one follow-up question. '
    'Do not repeat the numbers in a table — just write the narrative.'
)


# ---------------------------------------------------------------------------
# Ollama connectivity
# ---------------------------------------------------------------------------

def ollama_available() -> bool:
    try:
        r = requests.get(f'{OLLAMA_BASE}/api/tags', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def list_local_models() -> list[str]:
    try:
        r = requests.get(f'{OLLAMA_BASE}/api/tags', timeout=3)
        data = r.json()
        return [m['name'] for m in data.get('models', [])]
    except Exception:
        return []


def _ollama_chat(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Send a single prompt to Ollama and return the response text."""
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': NARRATIVE_SYSTEM},
            {'role': 'user',   'content': prompt},
        ],
        'stream': False,
        'options': {'temperature': 0.3},  # low temp for factual financial text
    }
    try:
        r = requests.post(f'{OLLAMA_BASE}/api/chat', json=payload, timeout=60)
        r.raise_for_status()
        return r.json()['message']['content']
    except requests.exceptions.ConnectionError:
        return (
            '_Ollama is not running. Start it with `ollama serve` in a terminal._\n\n'
            '*(Analysis data is shown above — narrative unavailable in offline mode.)*'
        )
    except Exception as e:
        return f'_Local LLM error: {e}_'


# ---------------------------------------------------------------------------
# Intent detection — pure Python, no LLM
# ---------------------------------------------------------------------------

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5,  'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10,'nov': 11, 'dec': 12,
}

def _extract_years(text: str, default_year: int) -> tuple[Optional[int], Optional[int]]:
    years = sorted(set(int(y) for y in re.findall(r'\b(202[1-5])\b', text)))
    if len(years) >= 2:
        return years[0], years[-1]
    if len(years) == 1:
        return years[0], years[0]
    return default_year, default_year


def _extract_month(text: str) -> Optional[int]:
    # Try month name first
    for name, num in MONTH_MAP.items():
        if re.search(rf'\b{name}', text.lower()):
            return num
    # Try Q1/Q2/Q3/Q4
    q = re.search(r'\bq([1-4])\b', text.lower())
    if q:
        return (int(q.group(1)) - 1) * 3 + 1   # first month of quarter
    # Try bare number with context
    m = re.search(r'\bmonth\s+(\d{1,2})\b|\b(\d{1,2})/\d{4}\b', text.lower())
    if m:
        val = int(m.group(1) or m.group(2))
        if 1 <= val <= 12:
            return val
    return None


def _extract_gl(text: str) -> Optional[str]:
    m = re.search(r'\b([4-8]\d{5})\b', text)
    return m.group(1) if m else None


def _extract_cost_center(text: str) -> Optional[str]:
    m = re.search(r'\b(CC\d{4})\b', text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _extract_functional_area(text: str) -> Optional[str]:
    m = re.search(r'\b(FA0[1-7])\b', text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _extract_company_code(text: str) -> Optional[str]:
    # Match "company 1000" or "company code 2000" or standalone 1000/2000/3000/4000
    m = re.search(r'company\s+(?:code\s+)?([1-4]000)\b', text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\b([1-4]000)\b', text)
    return m.group(1) if m else None


def _extract_group_by(text: str, default: str) -> str:
    t = text.lower()
    if re.search(r'by (cost\s*center|cc)', t):           return 'cost_center'
    if re.search(r'by (functional\s*area|fa\d)', t):     return 'functional_area'
    if re.search(r'by (profit\s*center|region)', t):     return 'profit_center'
    if re.search(r'by (company|entity)', t):             return 'company_code'
    if re.search(r'by (gl|account)', t):                 return 'gl_account'
    if re.search(r'by (period|month|year|time)', t):     return 'period'
    return default


def detect_intent(text: str, default_year: int) -> tuple[str, dict]:
    """
    Returns (tool_name, kwargs_dict) by parsing the user's query.

    Tools:
      travel_expense_report   — travel / T&E / GL 620000
      actuals_vs_plan         — budget / plan vs actual / over budget
      period_comparison       — MoM / YoY / compare months or years
      variance_driver_analysis — driver / factor / why / root cause
    """
    t = text.lower()
    year_start, year_end = _extract_years(text, default_year)
    month = _extract_month(text)
    gl = _extract_gl(text)
    cc = _extract_cost_center(text)
    fa = _extract_functional_area(text)
    co = _extract_company_code(text)

    # --- variance driver ---
    if re.search(r'driver|factor|what.{0,20}(driv|caus)|explain.{0,20}varianc|root.cause|why.{0,20}(varianc|over|under|higher|lower)', t):
        vtype = 'YoY' if re.search(r'year|yoy', t) else ('MoM' if re.search(r'month|mom', t) else 'plan_vs_actual')
        return 'variance_driver_analysis', dict(
            year_start=year_start, year_end=year_end,
            month_start=month, month_end=month,
            gl_account=gl, variance_type=vtype, company_code=co,
        )

    # --- period comparison ---
    if re.search(r'(mom|month.{0,10}(over|vs|versus|compar)|previous\s+month|last\s+month|prior\s+month)', t):
        return 'period_comparison', dict(
            comparison_type='MoM', year=year_start, month=month,
            gl_account=gl, cost_center=cc, functional_area=fa, company_code=co,
            group_by=_extract_group_by(t, 'gl_account'),
        )
    if re.search(r'(yoy|year.{0,10}(over|vs|versus|compar)|previous\s+year|last\s+year|prior\s+year|\bvs\s+20[0-9]{2})', t):
        return 'period_comparison', dict(
            comparison_type='YoY', year=year_end, month=month,
            gl_account=gl, cost_center=cc, functional_area=fa, company_code=co,
            group_by=_extract_group_by(t, 'gl_account'),
        )

    # --- travel expense report ---
    if re.search(r'travel|t\s*&\s*e|entertainment|620000', t):
        return 'travel_expense_report', dict(
            year_start=year_start, year_end=year_end,
            month_start=month, month_end=month,
            gl_account=gl or '620000',
            cost_center=cc, functional_area=fa, company_code=co,
            group_by=_extract_group_by(t, 'period'),
        )

    # --- actuals vs plan (default for budget / variance / spend queries) ---
    return 'actuals_vs_plan', dict(
        year_start=year_start, year_end=year_end,
        month_start=month, month_end=month,
        gl_account=gl, cost_center=cc, functional_area=fa, company_code=co,
        group_by=_extract_group_by(t, 'gl_account'),
    )


# ---------------------------------------------------------------------------
# Result → narrative prompt builder
# ---------------------------------------------------------------------------

def _fmt(val, pct=False) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 'N/A'
    if pct:
        flag = ' ⚠️ SIGNIFICANT' if abs(val) > SIGNIFICANT_THRESHOLD else ''
        return f'{val:+.1f}%{flag}'
    return f'{val:,.0f}'


def _build_narrative_prompt(tool_name: str, result: dict) -> str:
    """
    Build a compact text prompt for the local LLM from pre-computed analysis results.
    Only aggregated statistics are included — no raw rows.
    """
    lines = [f'TOOL: {tool_name}', f'TITLE: {result.get("title", "")}', '']

    if tool_name in ('travel_expense_report', 'actuals_vs_plan'):
        t = result.get('totals', {})
        lines += [
            f'Total Actual:   {_fmt(t.get("total_actual"))}',
            f'Total Plan:     {_fmt(t.get("total_plan"))}',
            f'Variance (Abs): {_fmt(t.get("total_variance_abs"))}',
            f'Variance (%):   {_fmt(t.get("total_variance_pct"), pct=True)}',
            f'Items >20% var: {t.get("significant_count", 0)}',
            '',
        ]
        df = result.get('df')
        if df is not None and not df.empty:
            lines.append('TOP SIGNIFICANT ITEMS (sorted by |variance %|):')
            top = df.dropna(subset=['variance_pct']).sort_values('variance_pct', key=abs, ascending=False).head(5)
            label_col = [c for c in top.columns if c not in ('actual', 'plan', 'variance_abs', 'variance_pct', 'significant', 'year', 'month', 'gl_type')][0]
            for _, row in top.iterrows():
                flag = '⚠️' if row.get('significant') else '  '
                lines.append(f'  {flag} {row[label_col]}: actual={_fmt(row["actual"])}, plan={_fmt(row["plan"])}, var={_fmt(row["variance_pct"], pct=True)}')

    elif tool_name == 'period_comparison':
        t = result.get('totals', {})
        cl, pl = result.get('curr_label', 'Current'), result.get('prior_label', 'Prior')
        lines += [
            f'{cl} total:   {_fmt(t.get("total_current"))}',
            f'{pl} total:   {_fmt(t.get("total_prior"))}',
            f'Change (Abs):    {_fmt(t.get("total_change_abs"))}',
            f'Change (%):      {_fmt(t.get("total_change_pct"), pct=True)}',
            f'Items >20% chg:  {t.get("significant_count", 0)}',
            '',
        ]
        df = result.get('df')
        if df is not None and not df.empty:
            lines.append('TOP MOVERS:')
            top = df.dropna(subset=['change_pct']).sort_values('change_pct', key=abs, ascending=False).head(5)
            label_col = [c for c in top.columns if c not in (cl, pl, 'change_abs', 'change_pct', 'significant')][0]
            for _, row in top.iterrows():
                flag = '⚠️' if row.get('significant') else '  '
                lines.append(f'  {flag} {row[label_col]}: {cl}={_fmt(row[cl])}, {pl}={_fmt(row[pl])}, chg={_fmt(row["change_pct"], pct=True)}')

    elif tool_name == 'variance_driver_analysis':
        if result.get('total_variance') is not None:
            lines.append(f'Total Plan vs Actual Variance: {_fmt(result["total_variance"])}')
        lines.append('')
        for dim_name, dim_df in (result.get('drivers') or {}).items():
            if dim_df is None or dim_df.empty:
                continue
            top_row = dim_df.iloc[0]
            label_col = dim_df.columns[0]
            val_col = 'variance_abs' if 'variance_abs' in dim_df.columns else 'change_abs'
            if val_col in dim_df.columns:
                lines.append(
                    f'Top {dim_name}: {top_row[label_col]} '
                    f'({_fmt(top_row.get("contribution_pct"))}% of total variance, '
                    f'amount={_fmt(top_row.get(val_col))})'
                )

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main entry point — mirrors run_conversation_turn in tools.py
# ---------------------------------------------------------------------------

def run_local_turn(
    user_input: str,
    conversation_history: list[dict],
    df: pd.DataFrame,
    model: str = DEFAULT_MODEL,
) -> tuple[str, list[dict]]:
    """
    Process one user turn entirely locally.

    Returns:
        narrative_text  – LLM-generated narrative (string)
        tool_calls      – list of {tool_name, tool_input, result} for UI rendering
    """
    default_year = int(df['year'].max())

    # Build context from conversation history for follow-up detection
    context_text = user_input
    if conversation_history:
        last_user = next(
            (m['content'] for m in reversed(conversation_history) if m.get('role') == 'user'), ''
        )
        if last_user and last_user != user_input:
            # Merge last exchange for context on follow-up pronouns
            context_text = f'{last_user} {user_input}'

    tool_name, kwargs = detect_intent(context_text, default_year)

    # Remove None values before calling analysis
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    fn_map = {
        'travel_expense_report':    travel_expense_report,
        'actuals_vs_plan':          actuals_vs_plan,
        'period_comparison':        period_comparison,
        'variance_driver_analysis': variance_driver_analysis,
    }

    result = fn_map[tool_name](df, **kwargs)

    if 'error' in result:
        return f"No data found: {result['error']}", []

    row_count = len(result.get('df') if result.get('df') is not None else pd.DataFrame())
    log_data_access(tool_name, kwargs, row_count)
    audit('LOCAL_INFERENCE', model=model, tool=tool_name, rows=row_count)

    # Build a minimal stats-only prompt for the local LLM (no raw rows)
    narrative_prompt = _build_narrative_prompt(tool_name, result)
    narrative = _ollama_chat(narrative_prompt, model=model)

    tool_calls = [{'tool_name': tool_name, 'tool_input': kwargs, 'result': result}]
    return narrative, tool_calls
