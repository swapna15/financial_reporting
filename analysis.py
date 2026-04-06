import pandas as pd
import numpy as np
from typing import Optional

SIGNIFICANT_THRESHOLD = 20.0  # percent

GROUP_COLS_MAP = {
    'period':           ['fiscal_period', 'year', 'month'],
    'gl_account':       ['gl_account', 'gl_account_desc', 'gl_type'],
    'cost_center':      ['cost_center'],
    'functional_area':  ['functional_area', 'functional_area_desc'],
    'profit_center':    ['profit_center', 'profit_center_desc'],
    'company_code':     ['company_code'],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_df(
    df: pd.DataFrame,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    month_start: Optional[int] = None,
    month_end: Optional[int] = None,
    gl_account: Optional[str] = None,
    gl_type: Optional[str] = None,
    cost_center: Optional[str] = None,
    functional_area: Optional[str] = None,
    company_code: Optional[str] = None,
    profit_center: Optional[str] = None,
) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)

    if year_start is not None and month_start is not None:
        mask &= df['period_date'] >= pd.Timestamp(year_start, month_start, 1)
    elif year_start is not None:
        mask &= df['year'] >= year_start

    if year_end is not None and month_end is not None:
        mask &= df['period_date'] <= pd.Timestamp(year_end, month_end, 1)
    elif year_end is not None:
        mask &= df['year'] <= year_end

    if gl_account and gl_account.lower() not in ('all', ''):
        mask &= df['gl_account'] == str(gl_account)
    if gl_type:
        mask &= df['gl_type'] == gl_type
    if cost_center:
        mask &= df['cost_center'] == cost_center
    if functional_area:
        mask &= df['functional_area'] == functional_area
    if company_code:
        mask &= df['company_code'] == str(company_code)
    if profit_center:
        mask &= df['profit_center'] == profit_center

    return df[mask].copy()


def _variance_cols(result: pd.DataFrame, actual_col='actual', plan_col='plan') -> pd.DataFrame:
    result['variance_abs'] = result[actual_col] - result[plan_col]
    result['variance_pct'] = np.where(
        result[plan_col].abs() > 0,
        (result[actual_col] - result[plan_col]) / result[plan_col].abs() * 100,
        np.nan
    )
    result['variance_pct'] = result['variance_pct'].round(2)
    result['significant'] = result['variance_pct'].abs() > SIGNIFICANT_THRESHOLD
    return result


def _aggregate(filtered: pd.DataFrame, group_by: str) -> pd.DataFrame:
    cols = [c for c in GROUP_COLS_MAP.get(group_by, GROUP_COLS_MAP['gl_account']) if c in filtered.columns]
    result = filtered.groupby(cols, dropna=False).agg(
        actual=('amount_group', 'sum'),
        plan=('planned_amount', 'sum')
    ).reset_index()
    result = _variance_cols(result)
    if 'year' in result.columns and 'month' in result.columns:
        result = result.sort_values(['year', 'month'])
    else:
        result = result.sort_values('variance_pct', ascending=False, key=lambda s: s.abs())
    return result


def _totals(result: pd.DataFrame) -> dict:
    total_actual = result['actual'].sum()
    total_plan = result['plan'].sum()
    total_var_abs = result['variance_abs'].sum()
    total_var_pct = (total_var_abs / abs(total_plan) * 100) if total_plan != 0 else None
    return {
        'total_actual': round(total_actual, 2),
        'total_plan': round(total_plan, 2),
        'total_variance_abs': round(total_var_abs, 2),
        'total_variance_pct': round(total_var_pct, 2) if total_var_pct is not None else None,
        'significant_count': int(result['significant'].sum()),
        'row_count': len(result),
    }


# ---------------------------------------------------------------------------
# Feature 1 — Travel Expense Report
# ---------------------------------------------------------------------------

def travel_expense_report(
    df: pd.DataFrame,
    year_start: int = None,
    year_end: int = None,
    month_start: int = None,
    month_end: int = None,
    gl_account: str = '620000',
    cost_center: str = None,
    functional_area: str = None,
    company_code: str = None,
    group_by: str = 'period',
) -> dict:
    gl = gl_account or '620000'
    filtered = _filter_df(df, year_start, year_end, month_start, month_end,
                          gl_account=gl, cost_center=cost_center,
                          functional_area=functional_area, company_code=company_code)

    if filtered.empty:
        return {'error': 'No data found for the specified filters.', 'df': None}

    result = _aggregate(filtered, group_by)
    gl_desc = filtered['gl_account_desc'].iloc[0]

    return {
        'title': f'Travel Expense Report — GL {gl} ({gl_desc})',
        'df': result,
        'totals': _totals(result),
        'group_by': group_by,
        'gl_account': gl,
        'gl_desc': gl_desc,
    }


# ---------------------------------------------------------------------------
# Feature 2 — Actuals vs Plan
# ---------------------------------------------------------------------------

def actuals_vs_plan(
    df: pd.DataFrame,
    year_start: int = None,
    year_end: int = None,
    month_start: int = None,
    month_end: int = None,
    gl_account: str = None,
    gl_type: str = None,
    cost_center: str = None,
    functional_area: str = None,
    company_code: str = None,
    group_by: str = 'gl_account',
) -> dict:
    filtered = _filter_df(df, year_start, year_end, month_start, month_end,
                          gl_account=gl_account, gl_type=gl_type,
                          cost_center=cost_center, functional_area=functional_area,
                          company_code=company_code)

    if filtered.empty:
        return {'error': 'No data found for the specified filters.', 'df': None}

    result = _aggregate(filtered, group_by)
    t = _totals(result)

    period_desc = ''
    if year_start and year_end and year_start != year_end:
        period_desc = f'{year_start}–{year_end}'
    elif year_start:
        period_desc = str(year_start)

    return {
        'title': f'Actuals vs Plan{" — " + period_desc if period_desc else ""}',
        'df': result,
        'totals': t,
        'group_by': group_by,
        'over_budget': result[result['variance_pct'] > SIGNIFICANT_THRESHOLD].sort_values('variance_pct', ascending=False),
        'under_budget': result[result['variance_pct'] < -SIGNIFICANT_THRESHOLD].sort_values('variance_pct'),
    }


# ---------------------------------------------------------------------------
# Feature 3 — Period Comparison (MoM / YoY)
# ---------------------------------------------------------------------------

def period_comparison(
    df: pd.DataFrame,
    comparison_type: str = 'YoY',
    year: int = None,
    month: int = None,
    gl_account: str = None,
    cost_center: str = None,
    functional_area: str = None,
    company_code: str = None,
    group_by: str = 'gl_account',
) -> dict:
    latest_year = int(df['year'].max())
    year = year or latest_year

    if comparison_type == 'MoM':
        month = month or int(df[df['year'] == year]['month'].max())
        prior_year = year if month > 1 else year - 1
        prior_month = month - 1 if month > 1 else 12
        curr_filter = df[(df['year'] == year) & (df['month'] == month)].copy()
        prior_filter = df[(df['year'] == prior_year) & (df['month'] == prior_month)].copy()
        curr_label = f"{str(month).zfill(2)}/{year}"
        prior_label = f"{str(prior_month).zfill(2)}/{prior_year}"
    else:  # YoY
        if month:
            curr_filter = df[(df['year'] == year) & (df['month'] == month)].copy()
            prior_filter = df[(df['year'] == year - 1) & (df['month'] == month)].copy()
            curr_label = f"{str(month).zfill(2)}/{year}"
            prior_label = f"{str(month).zfill(2)}/{year - 1}"
        else:
            curr_filter = df[df['year'] == year].copy()
            prior_filter = df[df['year'] == year - 1].copy()
            curr_label = str(year)
            prior_label = str(year - 1)

    # Apply extra filters
    extra = dict(gl_account=gl_account, cost_center=cost_center,
                 functional_area=functional_area, company_code=company_code)
    for key, val in extra.items():
        if val:
            if key == 'gl_account':
                curr_filter = curr_filter[curr_filter['gl_account'] == str(val)]
                prior_filter = prior_filter[prior_filter['gl_account'] == str(val)]
            elif key == 'cost_center':
                curr_filter = curr_filter[curr_filter['cost_center'] == val]
                prior_filter = prior_filter[prior_filter['cost_center'] == val]
            elif key == 'functional_area':
                curr_filter = curr_filter[curr_filter['functional_area'] == val]
                prior_filter = prior_filter[prior_filter['functional_area'] == val]
            elif key == 'company_code':
                curr_filter = curr_filter[curr_filter['company_code'] == str(val)]
                prior_filter = prior_filter[prior_filter['company_code'] == str(val)]

    if curr_filter.empty and prior_filter.empty:
        return {'error': 'No data found for either comparison period.', 'df': None}

    cols = [c for c in GROUP_COLS_MAP.get(group_by, GROUP_COLS_MAP['gl_account']) if c in df.columns]

    def _grp(fdf, label):
        if fdf.empty:
            return pd.DataFrame(columns=cols + [label])
        g = fdf.groupby(cols, dropna=False)['amount_group'].sum().reset_index()
        return g.rename(columns={'amount_group': label})

    curr_agg = _grp(curr_filter, curr_label)
    prior_agg = _grp(prior_filter, prior_label)

    result = pd.merge(curr_agg, prior_agg, on=cols, how='outer').fillna(0)
    result['change_abs'] = result[curr_label] - result[prior_label]
    result['change_pct'] = np.where(
        result[prior_label].abs() > 0,
        (result[curr_label] - result[prior_label]) / result[prior_label].abs() * 100,
        np.nan
    ).round(2)
    result['significant'] = result['change_pct'].abs() > SIGNIFICANT_THRESHOLD
    result = result.sort_values('change_pct', ascending=False, key=lambda s: s.abs())

    total_curr = result[curr_label].sum()
    total_prior = result[prior_label].sum()
    total_chg = total_curr - total_prior
    total_pct = (total_chg / abs(total_prior) * 100) if total_prior != 0 else None

    return {
        'title': f'{comparison_type}: {curr_label} vs {prior_label}',
        'df': result,
        'curr_label': curr_label,
        'prior_label': prior_label,
        'comparison_type': comparison_type,
        'group_by': group_by,
        'totals': {
            'total_current': round(total_curr, 2),
            'total_prior': round(total_prior, 2),
            'total_change_abs': round(total_chg, 2),
            'total_change_pct': round(total_pct, 2) if total_pct is not None else None,
            'significant_count': int(result['significant'].sum()),
        },
    }


# ---------------------------------------------------------------------------
# Feature 4+5 — Variance Driver Analysis
# ---------------------------------------------------------------------------

def variance_driver_analysis(
    df: pd.DataFrame,
    year_start: int = None,
    year_end: int = None,
    month_start: int = None,
    month_end: int = None,
    gl_account: str = None,
    variance_type: str = 'plan_vs_actual',
    company_code: str = None,
) -> dict:
    latest_year = int(df['year'].max())
    year_start = year_start or latest_year
    year_end = year_end or year_start

    filtered = _filter_df(df, year_start, year_end, month_start, month_end,
                          gl_account=gl_account, company_code=company_code)

    if filtered.empty:
        return {'error': 'No data found for the specified filters.', 'drivers': None}

    DIMENSIONS = {
        'GL Account':       ('gl_account', 'gl_account_desc'),
        'Cost Center':      ('cost_center', None),
        'Functional Area':  ('functional_area', 'functional_area_desc'),
        'Profit Center':    ('profit_center', 'profit_center_desc'),
        'Company Code':     ('company_code', None),
    }

    drivers = {}

    if variance_type == 'plan_vs_actual':
        for dim_name, (col, desc_col) in DIMENSIONS.items():
            cols = [c for c in [col, desc_col] if c and c in filtered.columns]
            grp = filtered.groupby(cols, dropna=False).agg(
                actual=('amount_group', 'sum'),
                plan=('planned_amount', 'sum')
            ).reset_index()
            grp = _variance_cols(grp)
            total_abs = grp['variance_abs'].abs().sum()
            grp['contribution_pct'] = (
                (grp['variance_abs'].abs() / total_abs * 100).round(1) if total_abs > 0 else 0
            )
            drivers[dim_name] = grp.sort_values('variance_abs', ascending=False, key=lambda s: s.abs()).head(10)

    else:  # MoM or YoY period comparison drivers
        if variance_type == 'YoY':
            curr = filtered[filtered['year'] == year_end]
            prior = _filter_df(df, year_end - 1, year_end - 1, month_start, month_end,
                               gl_account=gl_account, company_code=company_code)
        else:  # MoM
            last_row = filtered.sort_values(['year', 'month']).iloc[-1]
            cy, cm = int(last_row['year']), int(last_row['month'])
            py, pm = (cy, cm - 1) if cm > 1 else (cy - 1, 12)
            curr = filtered[(filtered['year'] == cy) & (filtered['month'] == cm)]
            prior = df[(df['year'] == py) & (df['month'] == pm)]
            if gl_account:
                prior = prior[prior['gl_account'] == str(gl_account)]

        for dim_name, (col, desc_col) in DIMENSIONS.items():
            cols = [c for c in [col, desc_col] if c and c in curr.columns]
            curr_g = curr.groupby(cols, dropna=False)['amount_group'].sum().reset_index().rename(columns={'amount_group': 'current'})
            prior_g = prior.groupby(cols, dropna=False)['amount_group'].sum().reset_index().rename(columns={'amount_group': 'prior'})
            grp = pd.merge(curr_g, prior_g, on=cols, how='outer').fillna(0)
            grp['change_abs'] = grp['current'] - grp['prior']
            grp['change_pct'] = np.where(
                grp['prior'].abs() > 0,
                (grp['current'] - grp['prior']) / grp['prior'].abs() * 100,
                np.nan
            ).round(2)
            grp['significant'] = grp['change_pct'].abs() > SIGNIFICANT_THRESHOLD
            total_abs = grp['change_abs'].abs().sum()
            grp['contribution_pct'] = (
                (grp['change_abs'].abs() / total_abs * 100).round(1) if total_abs > 0 else 0
            )
            drivers[dim_name] = grp.sort_values('change_abs', ascending=False, key=lambda s: s.abs()).head(10)

    total_var = round(filtered['amount_group'].sum() - filtered['planned_amount'].sum(), 2) if variance_type == 'plan_vs_actual' else None

    return {
        'title': f'Variance Driver Analysis — {variance_type} ({year_start}{"–" + str(year_end) if year_end != year_start else ""})',
        'drivers': drivers,
        'total_variance': total_var,
        'variance_type': variance_type,
        'period': f'{year_start}–{year_end}',
    }
