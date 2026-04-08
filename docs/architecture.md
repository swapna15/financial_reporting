# SAP OpEx Analysis — Architecture

## System Architecture

```mermaid
flowchart TB
    %% ── External Data ──────────────────────────────────────────────────────
    subgraph AWS["☁️  AWS"]
        S3[(S3 Bucket\nfinancial-reporting-1\nSAP_GL_Account_Data.xlsx)]
    end

    %% ── CI/CD ──────────────────────────────────────────────────────────────
    subgraph CICD["🔄  CI/CD"]
        GH[GitHub\nswapna15/financial_reporting]
        GHA[GitHub Actions\ndeploy.yml]
        GH -->|push to main| GHA
    end

    %% ── Deployment ─────────────────────────────────────────────────────────
    subgraph DEPLOY["🚀  Deployment"]
        SCC[Streamlit Community Cloud\nCloud Mode]
        GHA -->|auto-deploy| SCC
    end

    %% ── Local Dev ──────────────────────────────────────────────────────────
    subgraph LOCAL_DEV["💻  Local Development"]
        DOCKER[Docker\nmake run]
        OLLAMA[Ollama · llama3.2\nlocalhost:11434]
        DOCKER --> OLLAMA
    end

    %% ── Application ────────────────────────────────────────────────────────
    subgraph APP["📊  Streamlit Application"]
        direction TB
        UI[app.py\nChat UI · Charts · Sidebar]

        subgraph CORE["Core Modules"]
            DL[data_loader.py\nS3 download · Cache · Parse]
            GR[guardrails.py\nInput sanitization · Audit log]
            AN[analysis.py\nPandas · Travel · Variance · Drivers]
        end

        subgraph INFER["Inference Layer"]
            TOOLS[tools.py\nClaude tool dispatcher]
            LLM[local_llm.py\nIntent detection · Ollama client]
        end

        UI --> GR
        GR --> UI
        UI --> DL
        UI --> AN
        AN --> TOOLS
        AN --> LLM
    end

    %% ── AI Backend (Cloud) ─────────────────────────────────────────────────
    subgraph AI["🤖  AI Backend"]
        CLAUDE[Anthropic Claude API\nclaude-sonnet-4-6]
    end

    %% ── Cross-boundary connections ─────────────────────────────────────────
    S3 -->|boto3 · download at startup| DL
    SCC --> APP
    DOCKER --> APP
    TOOLS -->|tool-use API| CLAUDE
    LLM -->|HTTP| OLLAMA
```

---

## Request Flow

```mermaid
sequenceDiagram
    actor User
    participant UI   as app.py (Streamlit UI)
    participant GR   as guardrails.py
    participant DL   as data_loader.py
    participant AN   as analysis.py
    participant AI   as Claude API / Ollama

    User->>UI: Natural language query
    UI->>GR: sanitize_input()
    GR-->>UI: safe / blocked

    alt First load
        UI->>DL: load_data()
        DL->>S3: boto3.download_file()
        S3-->>DL: SAP_GL_Account_Data.xlsx
        DL-->>UI: DataFrame (cached 1 hr)
    end

    UI->>AN: travel_expense_report() / actuals_vs_plan() /<br/>period_comparison() / variance_driver_analysis()
    AN-->>UI: result {df, totals, title}

    alt Cloud Mode (Streamlit Community Cloud)
        UI->>AI: Claude API · tool-use · top 8 rows only
        AI-->>UI: narrative text
    else Local Mode (Docker)
        UI->>AI: Ollama HTTP · aggregated stats only
        AI-->>UI: narrative text
    end

    UI-->>User: Charts + data table + narrative
```

---

## Component Responsibilities

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI, chat loop, chart rendering, mode switching |
| `data_loader.py` | Downloads Excel from S3 at startup, parses & caches DataFrame |
| `analysis.py` | Pure-pandas analysis: travel reports, variance, period comparison, driver analysis |
| `tools.py` | Claude API client, tool dispatcher, serialises results for API payload |
| `local_llm.py` | Regex intent detection, Ollama narrative generation |
| `guardrails.py` | Prompt injection defence, scope enforcement, audit logging |

## Deployment Topology

| Component | Platform | Purpose |
|---|---|---|
| Streamlit App | Streamlit Community Cloud | Hosts the UI — auto-deploys on push to `main` |
| Local Dev | Docker + Ollama | Full local mode, no API costs |
| Data | AWS S3 (`financial-reporting-1`) | Single source of truth for Excel data |
| CI/CD | GitHub Actions | Triggers SCC auto-deploy on push to `main` |
