import json
import os
import anthropic
import pandas as pd
from analysis import (
    travel_expense_report,
    actuals_vs_plan,
    period_comparison,
    variance_driver_analysis,
)
from guardrails import minimize_for_api, log_api_call, log_data_access, audit

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert SAP financial analyst assistant. You analyze GL account data and provide clear, concise financial insights.

DATA OVERVIEW:
- 1,200 records | 60 fiscal periods (01/2021 – 12/2025)
- Company Codes: 1000, 2000, 3000, 4000
- 20 GL accounts across Revenue, COGS, OpEx, R&D/CapEx, and Balance Sheet categories
- 10 Cost Centers | 7 Functional Areas (Administration, Sales, Production, Research, Distribution, Finance, IT Services)
- Currencies: USD, EUR, GBP, CHF, JPY — all comparisons use Group Currency

KEY GL ACCOUNTS:
620000=Travel & Entertainment | 600000=Salaries & Wages | 610000=Employee Benefits
630000=Office Supplies | 640000=IT & Software Expenses | 650000=Depreciation
660000=Rent & Utilities | 670000=Marketing & Advertising | 680000=Professional Services
700000=R&D Expenses | 710000=Capital Expenditure | 400000=Revenue-Products
400100=Revenue-Services | 500000=COGS | 510000=Direct Labor | 520000=Manufacturing Overhead

ANALYSIS RULES:
- Always use Group Currency for cross-entity comparisons
- For EXPENSE accounts: positive variance (actual > plan) = UNFAVORABLE (over budget)
- For REVENUE accounts: positive variance (actual > plan) = FAVORABLE
- Flag |variance %| > 20% as SIGNIFICANT — always prefix with ⚠️
- Default travel GL account = 620000 (Travel & Entertainment)
- When year/period is unspecified, use the most recent available (2025)

RESPONSE FORMAT:
1. ALWAYS call the appropriate tool first to retrieve data
2. Write a 2–4 sentence narrative: what was found, magnitude, key driver
3. Call out ⚠️ significant variances explicitly
4. Mention the top contributor if identifiable
5. Suggest one relevant follow-up analysis

Keep responses concise and use financial terminology. Numbers should be formatted with commas and 2 decimal places."""

# ---------------------------------------------------------------------------
# Tool definitions (Claude tool-use schema)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "travel_expense_report",
        "description": (
            "Generate a travel expense report showing actuals, plan, and variance by period. "
            "Use for any query about travel spending, T&E (Travel & Entertainment), GL 620000, "
            "or general expense reports for a specific GL account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_start":       {"type": "integer", "description": "Start year e.g. 2024"},
                "year_end":         {"type": "integer", "description": "End year e.g. 2024"},
                "month_start":      {"type": "integer", "description": "Start month 1–12"},
                "month_end":        {"type": "integer", "description": "End month 1–12"},
                "gl_account":       {"type": "string",  "description": "GL account number. Defaults to 620000 for travel."},
                "cost_center":      {"type": "string",  "description": "Filter by cost center code e.g. CC2001"},
                "functional_area":  {"type": "string",  "description": "Filter by functional area code e.g. FA02"},
                "company_code":     {"type": "string",  "description": "Filter by company code e.g. 1000"},
                "group_by":         {
                    "type": "string",
                    "enum": ["period", "cost_center", "functional_area", "profit_center", "company_code"],
                    "description": "Primary grouping dimension. Default: period"
                },
            },
        },
    },
    {
        "name": "actuals_vs_plan",
        "description": (
            "Compare actual spend against planned amounts and calculate variances. "
            "Use for budget variance analysis, over/under budget queries, or plan vs actual comparisons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_start":       {"type": "integer"},
                "year_end":         {"type": "integer"},
                "month_start":      {"type": "integer"},
                "month_end":        {"type": "integer"},
                "gl_account":       {"type": "string",  "description": "GL account number or omit for all"},
                "gl_type":          {"type": "string",  "description": "Filter by type: Revenue, COGS, OpEx, R&D/CapEx, Below-the-line, Balance Sheet"},
                "cost_center":      {"type": "string"},
                "functional_area":  {"type": "string"},
                "company_code":     {"type": "string"},
                "group_by":         {
                    "type": "string",
                    "enum": ["gl_account", "cost_center", "functional_area", "profit_center", "company_code", "period"],
                    "description": "Primary grouping dimension. Default: gl_account"
                },
            },
        },
    },
    {
        "name": "period_comparison",
        "description": (
            "Compare spending between two periods — month-over-month (MoM) or year-over-year (YoY). "
            "Use for 'compare with previous month', 'prior year', 'MoM', 'YoY', 'how did X change' queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comparison_type":  {
                    "type": "string",
                    "enum": ["MoM", "YoY"],
                    "description": "MoM = month-over-month, YoY = year-over-year"
                },
                "year":             {"type": "integer", "description": "Current year to compare FROM"},
                "month":            {"type": "integer", "description": "Current month (required for MoM, optional for YoY)"},
                "gl_account":       {"type": "string"},
                "cost_center":      {"type": "string"},
                "functional_area":  {"type": "string"},
                "company_code":     {"type": "string"},
                "group_by":         {
                    "type": "string",
                    "enum": ["gl_account", "cost_center", "functional_area", "profit_center", "period"],
                    "description": "Primary grouping dimension. Default: gl_account"
                },
            },
            "required": ["comparison_type"],
        },
    },
    {
        "name": "variance_driver_analysis",
        "description": (
            "Identify and rank the key factors/drivers contributing to a variance. "
            "Use when asked 'what is driving', 'explain the variance', 'root cause', 'what factors', "
            "or when a significant variance needs investigation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_start":       {"type": "integer"},
                "year_end":         {"type": "integer"},
                "month_start":      {"type": "integer"},
                "month_end":        {"type": "integer"},
                "gl_account":       {"type": "string"},
                "variance_type":    {
                    "type": "string",
                    "enum": ["plan_vs_actual", "MoM", "YoY"],
                    "description": "Type of variance to analyze drivers for"
                },
                "company_code":     {"type": "string"},
            },
            "required": ["variance_type"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(tool_name: str, tool_input: dict, df: pd.DataFrame) -> dict:
    """Execute the named tool with given inputs against the DataFrame."""
    fn_map = {
        'travel_expense_report':    travel_expense_report,
        'actuals_vs_plan':          actuals_vs_plan,
        'period_comparison':        period_comparison,
        'variance_driver_analysis': variance_driver_analysis,
    }
    fn = fn_map.get(tool_name)
    if fn is None:
        return {'error': f'Unknown tool: {tool_name}'}
    result = fn(df, **{k: v for k, v in tool_input.items() if v is not None})
    row_count = len(result.get('df', pd.DataFrame()) or pd.DataFrame())
    log_data_access(tool_name, tool_input, row_count)
    return result


def _fmt(val, is_pct=False):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 'N/A'
    if is_pct:
        sign = '+' if val > 0 else ''
        flag = ' ⚠️' if abs(val) > 20 else ''
        return f'{sign}{val:.1f}%{flag}'
    return f'{val:,.0f}'


def _df_to_text(df: pd.DataFrame) -> str:
    """Serialize a minimized DataFrame for the Claude API payload (data minimization applied)."""
    if df is None or df.empty:
        return 'No data.'
    # Apply data minimization — only the most significant rows go to the external API
    api_df = minimize_for_api(df).copy()
    for col in api_df.select_dtypes(include='number').columns:
        api_df[col] = api_df[col].round(2)
    try:
        text = api_df.to_markdown(index=False)
    except Exception:
        text = api_df.to_string(index=False)
    if len(df) > len(api_df):
        text += f'\n\n*(Top {len(api_df)} of {len(df)} rows sent to analysis model — full table shown in UI)*'
    return text


def format_tool_result(result: dict) -> str:
    """Convert a tool result dict to a text string suitable for Claude's tool_result message."""
    if 'error' in result:
        return f"Error: {result['error']}"

    lines = []

    # --- travel_expense_report / actuals_vs_plan ---
    if 'df' in result and result['df'] is not None:
        if 'title' in result:
            lines.append(f"### {result['title']}\n")

        t = result.get('totals', {})
        if t:
            lines.append(f"**Total Actual:** {_fmt(t.get('total_actual'))}")
            lines.append(f"**Total Plan:** {_fmt(t.get('total_plan'))}")
            lines.append(f"**Total Variance:** {_fmt(t.get('total_variance_abs'))} ({_fmt(t.get('total_variance_pct'), is_pct=True)})")
            lines.append(f"**Significant Variances (>20%):** {t.get('significant_count', 0)}\n")

        lines.append(_df_to_text(result['df']))

    # --- period_comparison ---
    elif 'curr_label' in result and 'df' in result:
        lines.append(f"### {result.get('title', '')}\n")
        t = result.get('totals', {})
        if t:
            lines.append(f"**{result['curr_label']}:** {_fmt(t.get('total_current'))}")
            lines.append(f"**{result['prior_label']}:** {_fmt(t.get('total_prior'))}")
            lines.append(f"**Change:** {_fmt(t.get('total_change_abs'))} ({_fmt(t.get('total_change_pct'), is_pct=True)})")
            lines.append(f"**Significant Variances (>20%):** {t.get('significant_count', 0)}\n")
        lines.append(_df_to_text(result['df']))

    # --- variance_driver_analysis ---
    elif 'drivers' in result and result['drivers']:
        lines.append(f"### {result.get('title', 'Driver Analysis')}\n")
        if result.get('total_variance') is not None:
            lines.append(f"**Total Variance (Actual – Plan):** {_fmt(result['total_variance'])}\n")
        for dim, dim_df in result['drivers'].items():
            lines.append(f"\n**{dim}:**")
            lines.append(_df_to_text(dim_df, max_rows=10))

    return '\n'.join(lines) if lines else 'No results.'


# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError('ANTHROPIC_API_KEY environment variable is not set.')
    return anthropic.Anthropic(api_key=api_key)


def run_conversation_turn(
    client: anthropic.Anthropic,
    api_messages: list,
    df: pd.DataFrame,
) -> tuple[str, list[dict], list]:
    """
    Run one user turn through the tool-calling loop.

    Returns:
        final_text      – Claude's final narrative response
        tool_calls      – list of {tool_name, tool_input, result} dicts (for UI rendering)
        updated_messages – updated api_messages list (append before next turn)
    """
    messages = list(api_messages)  # copy
    tool_calls = []

    while True:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == 'tool_use':
            # Collect all tool calls in this response
            tool_results_content = []
            assistant_content = response.content  # list of blocks

            for block in response.content:
                if block.type == 'tool_use':
                    result = dispatch_tool(block.name, block.input, df)
                    tool_calls.append({
                        'tool_name': block.name,
                        'tool_input': block.input,
                        'result': result,
                    })
                    tool_results_content.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': format_tool_result(result),
                    })

            # Add assistant turn + tool results to message history
            messages.append({'role': 'assistant', 'content': assistant_content})
            messages.append({'role': 'user', 'content': tool_results_content})

        else:
            # Final text response
            final_text = ''.join(
                block.text for block in response.content if hasattr(block, 'text')
            )
            messages.append({'role': 'assistant', 'content': final_text})
            # Audit: record metadata of what was sent externally (no financial content)
            user_text = next(
                (m['content'] for m in reversed(api_messages) if m.get('role') == 'user'), ''
            )
            log_api_call(user_text if isinstance(user_text, str) else '', tool_calls, 'claude-sonnet-4-6')
            return final_text, tool_calls, messages
