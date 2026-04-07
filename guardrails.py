"""
Guardrails — data protection, prompt sanitization, and audit logging.

Three layers:
  1. Input guardrails  — block prompt injection before it reaches Claude
  2. Data minimization — limit what financial data is included in API payloads
  3. Audit logging     — local-only record of every external API call
"""

import re
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Audit logger — writes only to local disk, never to any external service
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "audit_logs"
LOG_DIR.mkdir(exist_ok=True)

_audit_logger = logging.getLogger('sap_audit')
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False  # prevent root logger from re-emitting

_log_file = LOG_DIR / f"audit_{datetime.now().strftime('%Y%m%d')}.log"
_fh = logging.FileHandler(_log_file)
_fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
_audit_logger.addHandler(_fh)


def audit(event: str, **kwargs):
    """Write a structured audit record."""
    _audit_logger.info(f"{event} | " + " | ".join(f"{k}={v}" for k, v in kwargs.items()))


# ---------------------------------------------------------------------------
# 1. Input guardrails — prompt injection + scope enforcement
# ---------------------------------------------------------------------------

# Patterns that indicate prompt injection or data exfiltration attempts
_INJECTION_PATTERNS = [
    (r'ignore\s+(all\s+)?previous\s+instructions?', 'instruction override'),
    (r'forget\s+(everything|all|prior)', 'memory wipe'),
    (r'(you\s+are\s+now|pretend\s+to\s+be|act\s+as)\s+', 'role override'),
    (r'(jailbreak|dan\s+mode|developer\s+mode)', 'jailbreak attempt'),
    (r'(reveal|show|print|dump|export)\s+(the\s+)?(full\s+)?(system\s+)?prompt', 'system prompt extraction'),
    (r'(dump|export|download|send)\s+(all\s+)?(the\s+)?(raw\s+)?data', 'data exfiltration'),
    (r'show\s+all\s+\d+\s*rows?', 'bulk data extraction'),
    (r'(send|email|post|upload)\s+(data|file|excel)', 'data upload attempt'),
    (r'<script[\s>]', 'script injection'),
    (r'(http|https|ftp)s?://', 'URL in prompt'),
    (r'(curl|wget|requests?\.get)', 'HTTP call attempt'),
]

# Queries must be related to financial analysis
_ALLOWED_TOPICS = re.compile(
    r'(travel|expense|budget|plan|actual|variance|gl\s*(account)?|cost\s*center|'
    r'functional\s*area|revenue|opex|cogs|depreciation|salary|rent|marketing|'
    r'month|year|quarter|period|compare|report|analysis|spend|driver|factor|'
    r'highlight|significant|over|under|increase|decrease|profit\s*center)',
    re.IGNORECASE
)

MAX_INPUT_LENGTH = 500  # characters


def sanitize_input(text: str) -> tuple[bool, str]:
    """
    Validate a user prompt before it is sent to Claude.

    Returns:
        (True, '')           — safe to proceed
        (False, reason_msg)  — blocked; reason_msg is shown to the user
    """
    if not text or not text.strip():
        return False, 'Empty query.'

    if len(text) > MAX_INPUT_LENGTH:
        return False, f'Query exceeds {MAX_INPUT_LENGTH} characters. Please be more concise.'

    text_lower = text.lower()
    for pattern, label in _INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            audit('INPUT_BLOCKED', reason=label,
                  hash=hashlib.sha256(text.encode()).hexdigest()[:16])
            return False, (
                'That query cannot be processed. '
                'Please ask a financial analysis question about the GL data.'
            )

    if not _ALLOWED_TOPICS.search(text):
        audit('INPUT_BLOCKED', reason='off_topic',
              hash=hashlib.sha256(text.encode()).hexdigest()[:16])
        return False, (
            'Please ask a question related to GL account analysis — '
            'e.g. travel expenses, budget variance, period comparisons.'
        )

    return True, ''


# ---------------------------------------------------------------------------
# 2. Data minimization — limit financial data sent to Claude API
# ---------------------------------------------------------------------------

# How many rows (at most) are included in a tool-result payload sent to Claude.
# The full DataFrame is always displayed locally in the UI; only this truncated
# version is included in the API request.
API_MAX_ROWS = 8


def minimize_for_api(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """
    Return a reduced DataFrame for inclusion in the Claude API payload.

    Strategy:
      • If there are significant-variance rows, send those first (they carry
        the most narrative value).
      • Fill remaining slots with the highest-absolute-variance rows.
      • Never send more than API_MAX_ROWS rows to the external API.
    """
    if df is None or df.empty:
        return df

    rows = []
    budget = API_MAX_ROWS

    if 'significant' in df.columns:
        sig = df[df['significant'] == True]
        rows.append(sig.head(budget))
        budget -= min(len(sig), budget)

    if budget > 0:
        rest_col = None
        for col in ('variance_pct', 'change_pct', 'variance_abs', 'change_abs'):
            if col in df.columns:
                rest_col = col
                break
        if rest_col:
            rest = df[df.get('significant', pd.Series(False, index=df.index)) != True]
            rest = rest.sort_values(rest_col, ascending=False, key=lambda s: s.abs())
            rows.append(rest.head(budget))
        else:
            rows.append(df.head(budget))

    result = pd.concat(rows).drop_duplicates() if rows else df.head(API_MAX_ROWS)
    return result.head(API_MAX_ROWS)


# ---------------------------------------------------------------------------
# 3. Audit logging — record every external API call (metadata only)
# ---------------------------------------------------------------------------

def log_api_call(user_input: str, tool_calls: list, model: str):
    """
    Record metadata about each outbound Claude API call.
    Stores: timestamp, model, input length, input hash, tool names.
    Does NOT store: actual prompt text, financial amounts, raw data rows.
    """
    audit(
        'CLAUDE_API_CALL',
        model=model,
        input_len=len(user_input),
        input_hash=hashlib.sha256(user_input.encode()).hexdigest()[:16],
        tools=[tc.get('tool_name') for tc in tool_calls],
    )


def log_data_access(tool_name: str, tool_input: dict, row_count: int):
    """
    Record each local data access (which tool, which filters, row count).
    Financial amounts are never logged.
    """
    safe_input = {
        k: v for k, v in tool_input.items()
        if k not in ('amount', 'value', 'planned_amount', 'amount_group')
    }
    audit(
        'DATA_ACCESS',
        tool=tool_name,
        filters=json.dumps(safe_input, default=str),
        rows=row_count,
    )
