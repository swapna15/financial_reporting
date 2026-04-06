import pandas as pd
import numpy as np
from pathlib import Path
import streamlit as st

DATA_FILE = Path(__file__).parent / "SAP_GL_Account_Data.xlsx"

COLUMNS = [
    'company_code', 'fiscal_period', 'cost_center', 'functional_area',
    'functional_area_desc', 'gl_account', 'gl_account_desc', 'wbs_element',
    'wbs_element_desc', 'resp_cost_center', 'resp_cost_center_desc',
    'internal_order', 'internal_order_desc', 'profit_center',
    'profit_center_desc', 'currency', 'amount_local', 'amount_group', 'planned_amount'
]

GL_TYPES = {
    range(400000, 410001): 'Revenue',
    range(500000, 520001): 'COGS',
    range(600000, 680001): 'OpEx',
    range(700000, 710001): 'R&D/CapEx',
    range(720000, 730001): 'Below-the-line',
    range(800000, 800001): 'Balance Sheet',
}


def classify_gl(gl: int) -> str:
    for r, label in GL_TYPES.items():
        if gl in r:
            return label
    return 'Other'


@st.cache_data
def load_data() -> pd.DataFrame:
    df = pd.read_excel(DATA_FILE, sheet_name="GL Data")
    df.columns = COLUMNS

    # Parse fiscal period into month / year / sortable date
    parts = df['fiscal_period'].str.split('/', expand=True)
    df['month'] = parts[0].astype(int)
    df['year'] = parts[1].astype(int)
    df['period_date'] = pd.to_datetime(
        df['year'].astype(str) + '-' + df['month'].astype(str).str.zfill(2) + '-01'
    )

    # Numeric columns
    for col in ('amount_local', 'amount_group', 'planned_amount'):
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    df['gl_account'] = df['gl_account'].astype(str)
    df['gl_type'] = df['gl_account'].astype(int).apply(classify_gl)

    return df


def get_data_summary(df: pd.DataFrame) -> dict:
    return {
        'total_records': len(df),
        'year_range': f"{df['year'].min()} – {df['year'].max()}",
        'periods': df['fiscal_period'].nunique(),
        'company_codes': sorted(df['company_code'].unique().tolist()),
        'gl_accounts': sorted(df['gl_account'].unique().tolist()),
        'cost_centers': sorted(df['cost_center'].unique().tolist()),
        'functional_areas': df[['functional_area', 'functional_area_desc']].drop_duplicates().sort_values('functional_area').to_dict('records'),
        'gl_reference': df[['gl_account', 'gl_account_desc', 'gl_type']].drop_duplicates().sort_values('gl_account').to_dict('records'),
        'latest_year': int(df['year'].max()),
        'latest_month': int(df[df['year'] == df['year'].max()]['month'].max()),
    }
