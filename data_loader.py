import io
import os
import pandas as pd
import numpy as np
from pathlib import Path
import streamlit as st

DATA_FILE = Path(__file__).parent / "SAP_GL_Account_Data.xlsx"

# S3 is used when S3_BUCKET_NAME is set; cache TTL allows dynamic data refresh
_USE_S3 = bool(os.environ.get('S3_BUCKET_NAME'))
_cache_kwargs = {'ttl': 3600, 'show_spinner': 'Loading data from S3…'} if _USE_S3 else {}

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


def _load_excel_source():
    """Return a file-like object (S3) or Path (local) for the Excel data file."""
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        return DATA_FILE

    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    key = os.environ.get('S3_DATA_KEY', 'SAP_GL_Account_Data.xlsx')
    region = os.environ.get('AWS_REGION', 'us-east-1')

    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=region,
        )
        response = s3.get_object(Bucket=bucket, Key=key)
        return io.BytesIO(response['Body'].read())

    except NoCredentialsError:
        st.error(
            "**S3 credentials missing.** "
            "Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in your app secrets.",
            icon="🔑",
        )
        st.stop()

    except ClientError as e:
        code = e.response['Error']['Code']
        if code == 'NoSuchBucket':
            st.error(
                f"**S3 bucket `{bucket}` does not exist** (region: `{region}`).  \n"
                "Create the bucket in the AWS console and upload your Excel file, "
                "or check that `S3_BUCKET_NAME` in your app secrets is spelled correctly.",
                icon="🪣",
            )
        elif code == 'NoSuchKey':
            st.error(
                f"**File `{key}` not found in bucket `{bucket}`.**  \n"
                "Upload `SAP_GL_Account_Data.xlsx` to the bucket, "
                "or update `S3_DATA_KEY` in your app secrets.",
                icon="📄",
            )
        elif code in ('InvalidAccessKeyId', 'SignatureDoesNotMatch'):
            st.error(
                "**AWS credentials are invalid.** "
                "Double-check `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in your app secrets.",
                icon="🔑",
            )
        elif code == 'AccessDenied':
            st.error(
                f"**Access denied to `s3://{bucket}/{key}`.**  \n"
                "Make sure the IAM user has `s3:GetObject` permission on this bucket.",
                icon="🚫",
            )
        else:
            st.error(f"**S3 error ({code}):** {e.response['Error']['Message']}", icon="⚠️")
        st.stop()


@st.cache_data(**_cache_kwargs)
def load_data() -> pd.DataFrame:
    df = pd.read_excel(_load_excel_source(), sheet_name="GL Data")
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
