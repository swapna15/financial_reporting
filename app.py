import os
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from dotenv import load_dotenv

from data_loader import load_data, get_data_summary
from tools import get_client, run_conversation_turn, TOOLS
from guardrails import sanitize_input
from local_llm import run_local_turn, ollama_available, list_local_models, DEFAULT_MODEL

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title='SAP GL Account Analysis',
    page_icon='📊',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
.main-title { font-size: 1.8rem; font-weight: 700; color: #1f2937; margin-bottom: 0; }
.sub-title  { font-size: 0.95rem; color: #6b7280; margin-top: 0; }
.metric-box { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 16px; }
.sig-badge  { background: #fef3c7; color: #92400e; border-radius: 4px; padding: 2px 8px;
              font-size: 0.8rem; font-weight: 600; }
.stChatMessage { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if 'messages' not in st.session_state:
    st.session_state.messages = []          # display messages (with DataFrames)
if 'api_messages' not in st.session_state:
    st.session_state.api_messages = []      # Claude API messages (text only)
if 'df' not in st.session_state:
    st.session_state.df = None
if 'client' not in st.session_state:
    st.session_state.client = None

# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

COLORS = {
    'actual':      '#3b82f6',
    'plan':        '#f59e0b',
    'favorable':   '#10b981',
    'unfavorable': '#ef4444',
    'neutral':     '#6b7280',
}


def _bar_color(val):
    if pd.isna(val):
        return COLORS['neutral']
    return COLORS['unfavorable'] if val > 0 else COLORS['favorable']


def render_travel_report_chart(result: dict):
    df = result.get('df')
    if df is None or df.empty:
        return
    group_by = result.get('group_by', 'period')

    if group_by == 'period' and 'year' in df.columns and 'month' in df.columns:
        df = df.sort_values(['year', 'month'])
        x = df['fiscal_period']
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=df['actual'], name='Actual', line=dict(color=COLORS['actual'], width=2), mode='lines+markers'))
        fig.add_trace(go.Scatter(x=x, y=df['plan'],   name='Plan',   line=dict(color=COLORS['plan'],   width=2, dash='dash'), mode='lines+markers'))
        fig.update_layout(title=result.get('title', ''), xaxis_title='Period',
                          yaxis_title='Amount (Group Currency)', height=350,
                          legend=dict(orientation='h', y=-0.2), margin=dict(t=40, b=40))
    else:
        label_col = [c for c in df.columns if c not in ('actual', 'plan', 'variance_abs', 'variance_pct', 'significant')][0]
        fig = go.Figure()
        fig.add_trace(go.Bar(name='Actual', x=df[label_col], y=df['actual'], marker_color=COLORS['actual']))
        fig.add_trace(go.Bar(name='Plan',   x=df[label_col], y=df['plan'],   marker_color=COLORS['plan']))
        fig.update_layout(barmode='group', title=result.get('title', ''), height=350,
                          xaxis_title=label_col.replace('_', ' ').title(),
                          yaxis_title='Amount (Group Currency)',
                          margin=dict(t=40, b=40))

    st.plotly_chart(fig, use_container_width=True)


def render_variance_chart(result: dict):
    df = result.get('df')
    if df is None or df.empty:
        return
    label_col = [c for c in df.columns if c not in ('actual', 'plan', 'variance_abs', 'variance_pct', 'significant', 'year', 'month', 'fiscal_period')][0]
    plot_df = df.dropna(subset=['variance_pct']).head(15)
    colors = [_bar_color(v) for v in plot_df['variance_pct']]
    fig = go.Figure(go.Bar(
        y=plot_df[label_col].astype(str),
        x=plot_df['variance_pct'],
        orientation='h',
        marker_color=colors,
        text=plot_df['variance_pct'].apply(lambda v: f'{v:+.1f}%'),
        textposition='outside',
    ))
    fig.add_vline(x=0, line_width=1, line_color='#374151')
    fig.add_vline(x=20,  line_width=1, line_dash='dot', line_color='#ef4444', annotation_text='+20%')
    fig.add_vline(x=-20, line_width=1, line_dash='dot', line_color='#ef4444', annotation_text='-20%')
    fig.update_layout(title=f'Variance % — {result.get("title", "")}',
                      xaxis_title='Variance %', height=max(300, len(plot_df) * 32 + 80),
                      margin=dict(t=40, l=140, r=80))
    st.plotly_chart(fig, use_container_width=True)


def render_period_comparison_chart(result: dict):
    df = result.get('df')
    if df is None or df.empty:
        return
    curr_label = result.get('curr_label', 'Current')
    prior_label = result.get('prior_label', 'Prior')
    label_col = [c for c in df.columns if c not in (curr_label, prior_label, 'change_abs', 'change_pct', 'significant')][0]
    plot_df = df.head(12)
    fig = go.Figure()
    fig.add_trace(go.Bar(name=curr_label,  x=plot_df[label_col].astype(str), y=plot_df[curr_label],  marker_color=COLORS['actual']))
    fig.add_trace(go.Bar(name=prior_label, x=plot_df[label_col].astype(str), y=plot_df[prior_label], marker_color=COLORS['plan']))
    fig.update_layout(barmode='group', title=result.get('title', ''), height=370,
                      xaxis_title=label_col.replace('_', ' ').title(),
                      yaxis_title='Amount (Group Currency)', margin=dict(t=40, b=60))
    st.plotly_chart(fig, use_container_width=True)


def render_driver_chart(dim_name: str, dim_df: pd.DataFrame, variance_type: str):
    if dim_df is None or dim_df.empty:
        return
    label_col = dim_df.columns[0]
    val_col = 'variance_abs' if variance_type == 'plan_vs_actual' else 'change_abs'
    if val_col not in dim_df.columns:
        return
    plot_df = dim_df.head(8)
    colors = [_bar_color(v) for v in plot_df[val_col]]
    fig = go.Figure(go.Bar(
        y=plot_df[label_col].astype(str),
        x=plot_df[val_col],
        orientation='h',
        marker_color=colors,
        text=plot_df['contribution_pct'].apply(lambda v: f'{v:.0f}%'),
        textposition='outside',
    ))
    fig.add_vline(x=0, line_width=1, line_color='#374151')
    fig.update_layout(title=f'{dim_name}', height=max(250, len(plot_df) * 30 + 60),
                      xaxis_title='Variance (Group Currency)', margin=dict(t=35, l=120, r=80))
    st.plotly_chart(fig, use_container_width=True)


def render_tool_result(tool_name: str, result: dict):
    """Render a tool result as a table + chart inside the chat."""
    if 'error' in result:
        st.error(result['error'])
        return

    if tool_name == 'travel_expense_report':
        t = result.get('totals', {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Total Actual',  f"{t.get('total_actual', 0):,.0f}")
        c2.metric('Total Plan',    f"{t.get('total_plan', 0):,.0f}")
        c3.metric('Variance (Abs)', f"{t.get('total_variance_abs', 0):+,.0f}")
        pct = t.get('total_variance_pct')
        c4.metric('Variance %', f"{pct:+.1f}%" if pct is not None else 'N/A')
        if t.get('significant_count', 0) > 0:
            st.warning(f"⚠️  {t['significant_count']} period(s) with >20% variance detected")
        with st.expander('View Data Table', expanded=True):
            _render_df(result['df'])
        render_travel_report_chart(result)

    elif tool_name == 'actuals_vs_plan':
        t = result.get('totals', {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Total Actual',  f"{t.get('total_actual', 0):,.0f}")
        c2.metric('Total Plan',    f"{t.get('total_plan', 0):,.0f}")
        c3.metric('Variance (Abs)', f"{t.get('total_variance_abs', 0):+,.0f}")
        pct = t.get('total_variance_pct')
        c4.metric('Variance %', f"{pct:+.1f}%" if pct is not None else 'N/A')
        if t.get('significant_count', 0) > 0:
            st.warning(f"⚠️  {t['significant_count']} item(s) with >20% variance detected")
        col1, col2 = st.columns([3, 2])
        with col1:
            with st.expander('View Data Table', expanded=True):
                _render_df(result['df'])
        with col2:
            render_variance_chart(result)

    elif tool_name == 'period_comparison':
        t = result.get('totals', {})
        curr_label = result.get('curr_label', 'Current')
        prior_label = result.get('prior_label', 'Prior')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(curr_label,  f"{t.get('total_current', 0):,.0f}")
        c2.metric(prior_label, f"{t.get('total_prior', 0):,.0f}")
        c3.metric('Change (Abs)', f"{t.get('total_change_abs', 0):+,.0f}")
        pct = t.get('total_change_pct')
        c4.metric('Change %', f"{pct:+.1f}%" if pct is not None else 'N/A')
        if t.get('significant_count', 0) > 0:
            st.warning(f"⚠️  {t['significant_count']} item(s) with >20% change detected")
        col1, col2 = st.columns([2, 3])
        with col1:
            with st.expander('View Data Table', expanded=True):
                _render_df(result['df'])
        with col2:
            render_period_comparison_chart(result)

    elif tool_name == 'variance_driver_analysis':
        drivers = result.get('drivers', {})
        if result.get('total_variance') is not None:
            st.metric('Total Variance (Actual – Plan)', f"{result['total_variance']:+,.0f}")
        variance_type = result.get('variance_type', 'plan_vs_actual')
        for dim_name, dim_df in drivers.items():
            with st.expander(f'Driver: {dim_name}', expanded=True):
                col1, col2 = st.columns([2, 3])
                with col1:
                    _render_df(dim_df)
                with col2:
                    render_driver_chart(dim_name, dim_df, variance_type)


def _render_df(df: pd.DataFrame):
    if df is None or df.empty:
        st.info('No data.')
        return
    display = df.copy()
    # Format numeric columns
    for col in display.select_dtypes(include='number').columns:
        if 'pct' in col or 'contribution' in col:
            display[col] = display[col].apply(lambda v: f'{v:+.1f}%' if pd.notna(v) else 'N/A')
        elif col in ('year', 'month'):
            display[col] = display[col].astype(int).astype(str)
        else:
            display[col] = display[col].apply(lambda v: f'{v:,.0f}' if pd.notna(v) else 'N/A')
    # Highlight significant rows
    if 'significant' in display.columns:
        display['significant'] = display['significant'].apply(lambda v: '⚠️' if v else '')
    st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar(summary: dict) -> str:
    """Render sidebar and return the selected inference mode ('local' or 'cloud')."""
    with st.sidebar:
        # ---- Inference mode selector ----
        st.markdown('### Inference Mode')
        mode = st.radio(
            'Where should analysis run?',
            options=['local', 'cloud'],
            format_func=lambda m: '🔒 Local only (Ollama)' if m == 'local' else '☁️  Cloud (Anthropic API)',
            key='inference_mode',
            help=(
                'Local: all data and prompts stay on this machine (Ollama required).\n'
                'Cloud: summaries are sent to Anthropic API for narrative generation.'
            ),
        )

        if mode == 'local':
            is_up = ollama_available()
            if is_up:
                models = list_local_models()
                model_names = [m.split(':')[0] for m in models]  # strip tag for display
                st.success(f'Ollama running — {len(models)} model(s) available')
                if models:
                    chosen = st.selectbox('Model', options=models,
                                          index=0, key='ollama_model')
                    st.session_state['local_model'] = chosen
                else:
                    st.warning('No models pulled yet.\n`ollama pull llama3.2`')
            else:
                st.error('Ollama not running.')
                st.code('# Install:\nbrew install ollama\n\n# Start:\nollama serve\n\n# Pull a model:\nollama pull llama3.2', language='bash')
        else:
            api_key_set = bool(os.environ.get('ANTHROPIC_API_KEY'))
            if api_key_set:
                st.success('Anthropic API key found')
            else:
                st.error('ANTHROPIC_API_KEY not set')
                st.code('echo "ANTHROPIC_API_KEY=sk-ant-..." > .env', language='bash')

        st.divider()
        st.markdown('### 📊 Dataset Overview')
        st.markdown(f"""
| Field | Value |
|---|---|
| Records | {summary['total_records']:,} |
| Period | {summary['year_range']} |
| Fiscal Periods | {summary['periods']} |
| Company Codes | {', '.join(str(c) for c in summary['company_codes'])} |
| GL Accounts | {len(summary['gl_accounts'])} |
| Cost Centers | {len(summary['cost_centers'])} |
""")

        with st.expander('GL Account Reference'):
            for gl in summary['gl_reference']:
                badge = {'Revenue': '🟢', 'COGS': '🔴', 'OpEx': '🔵', 'R&D/CapEx': '🟣'}.get(gl['gl_type'], '⚪')
                st.markdown(f"{badge} **{gl['gl_account']}** — {gl['gl_account_desc']}")

        with st.expander('Functional Areas'):
            for fa in summary['functional_areas']:
                st.markdown(f"**{fa['functional_area']}** — {fa['functional_area_desc']}")

        st.markdown('---')
        st.markdown('### Example Queries')
        examples = [
            'Show travel expenses for 2024',
            'Compare actuals vs plan for all OpEx in 2025',
            'Year-over-year comparison for GL 620000',
            'Which cost centers are over budget in 2024?',
            'What is driving the travel expense variance in 2024?',
            'Flag all accounts with more than 20% variance vs plan',
            'Compare Jan 2025 vs Jan 2024 for IT expenses',
        ]
        for ex in examples:
            if st.button(ex, use_container_width=True, key=f'ex_{ex[:20]}'):
                st.session_state['prefill'] = ex

        if st.button('🗑️  Clear Chat', use_container_width=True):
            st.session_state.messages = []
            st.session_state.api_messages = []
            st.rerun()

    return mode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Header
    st.markdown('<p class="main-title">SAP GL Account Analysis</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">Conversational financial analysis — fully local or cloud</p>', unsafe_allow_html=True)
    st.divider()

    # Load data (always local — Excel file never leaves the machine)
    if st.session_state.df is None:
        with st.spinner('Loading SAP GL data…'):
            st.session_state.df = load_data()
    df = st.session_state.df
    summary = get_data_summary(df)

    # Sidebar — returns selected mode
    mode = render_sidebar(summary)

    # Mode indicator banner
    if mode == 'local':
        st.info('🔒 **Local mode** — all analysis and inference runs on this machine. No data is sent externally.')
    else:
        st.warning('☁️  **Cloud mode** — aggregated summaries are sent to Anthropic API for narrative generation.')

    # Cloud mode: validate API key and init client
    client = None
    if mode == 'cloud':
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            st.error('ANTHROPIC_API_KEY not set. Switch to Local mode or add the key to `.env`.')
            return
        if st.session_state.client is None:
            try:
                st.session_state.client = get_client()
            except ValueError as e:
                st.error(str(e))
                return
        client = st.session_state.client

    # Render existing messages
    for msg in st.session_state.messages:
        with st.chat_message(msg['role']):
            st.markdown(msg['content'])
            for tc in msg.get('tool_calls', []):
                render_tool_result(tc['tool_name'], tc['result'])

    # Handle prefilled example button
    prefill = st.session_state.pop('prefill', None)

    # Chat input
    user_input = st.chat_input('Ask about GL accounts, travel expenses, variances…') or prefill
    if not user_input:
        if not st.session_state.messages:
            st.info('👋  Start by typing a question, or pick an example from the sidebar.')
        return

    # Guardrail: sanitize input before anything is processed or sent
    is_safe, block_reason = sanitize_input(user_input)
    if not is_safe:
        st.warning(f'Input blocked: {block_reason}')
        return

    # Display user message
    with st.chat_message('user'):
        st.markdown(user_input)
    st.session_state.messages.append({'role': 'user', 'content': user_input})

    # -----------------------------------------------------------------------
    # LOCAL MODE — zero external network calls
    # -----------------------------------------------------------------------
    if mode == 'local':
        local_model = st.session_state.get('local_model', DEFAULT_MODEL)
        with st.chat_message('assistant'):
            with st.spinner(f'Running locally on {local_model}…'):
                try:
                    final_text, tool_calls = run_local_turn(
                        user_input,
                        st.session_state.messages[:-1],  # history excl. current
                        df,
                        model=local_model,
                    )
                except Exception as e:
                    st.error(f'Local inference error: {e}')
                    return
            for tc in tool_calls:
                render_tool_result(tc['tool_name'], tc['result'])
            st.markdown(final_text)

        st.session_state.messages.append({
            'role': 'assistant',
            'content': final_text,
            'tool_calls': tool_calls,
        })
        return

    # -----------------------------------------------------------------------
    # CLOUD MODE — aggregated summaries sent to Anthropic API
    # -----------------------------------------------------------------------
    st.session_state.api_messages.append({'role': 'user', 'content': user_input})

    with st.chat_message('assistant'):
        with st.spinner('Analyzing via Anthropic API…'):
            try:
                final_text, tool_calls, updated_api_messages = run_conversation_turn(
                    client,
                    st.session_state.api_messages[:-1] + [{'role': 'user', 'content': user_input}],
                    df,
                )
            except Exception as e:
                st.error(f'Error communicating with Claude: {e}')
                return

        for tc in tool_calls:
            render_tool_result(tc['tool_name'], tc['result'])
        st.markdown(final_text)

    st.session_state.messages.append({
        'role': 'assistant',
        'content': final_text,
        'tool_calls': tool_calls,
    })
    st.session_state.api_messages = updated_api_messages


if __name__ == '__main__':
    main()
