# SAP OpEx Analysis — Claude Application Specs

## Overview

A conversational AI application powered by Claude that allows finance users to query and analyze SAP GL Account data via natural language prompts. The application loads `SAP_GL_Account_Data.xlsx` as its data source and responds to context-aware questions about travel expenses, actuals vs. plan variances, period-over-period comparisons, and variance driver analysis.

---

## Data Source

**File:** `SAP_GL_Account_Data.xlsx`
**Sheet:** `GL Data` (1,200 rows)

### Column Schema

| Column | Field | Description |
|---|---|---|
| A | Company Code | 1000, 2000, 3000, 4000 |
| B | Fiscal Period/Year | Format: `MM/YYYY` (01/2021 – 12/2025, 60 periods) |
| C | Cost Center | CC1001–CC5001 (10 unique) |
| D | Functional Area | FA01–FA07 |
| E | Functional Area Description | Administration, Sales, Production, Research, Distribution, Finance, IT Services |
| F | GL Account | 6-digit code (20 unique accounts) |
| G | GL Account Description | Salaries & Wages, Travel & Entertainment, Rent & Utilities, etc. |
| H | WBS Element | Project code |
| I | WBS Element Description | Project name |
| J | Responsible Cost Center | RCC code |
| K | Responsible Cost Center Description | Dept head name |
| L | Internal Order | IO code |
| M | Internal Order Description | Program name |
| N | Profit Center | PC-AMER, PC-EMEA, etc. |
| O | Profit Center Description | Americas, Europe Middle East Africa, etc. |
| P | Currency | USD, EUR, GBP, CHF, JPY |
| Q | Amount in Local Currency | Actual spend (numeric) |
| R | Amount in Group Currency | Actual spend converted to group currency (numeric) |
| S | Planned Amount | Budget/plan amount in local currency (numeric) |

### Key GL Accounts for Travel Expense Reports
- `620000` — Travel & Entertainment (primary travel account)

### Date Range
- **Months:** 01–12
- **Years:** 2021–2025

---

## Application Requirements

### 1. Travel Expenses Report per GL Account

**Trigger phrases:** "travel expense report", "travel spending", "show travel costs", "T&E report"

**Behavior:**
- Filter data to GL Account `620000` (Travel & Entertainment) by default
- Allow user to specify any GL account by name or number
- Report columns: Company Code, Cost Center, Functional Area, Period, Actual Amount (Group Currency), Planned Amount, Variance, Variance %
- Support filtering by: period/year, cost center, functional area, profit center, company code
- Default grouping: by GL Account, then by period (monthly)
- Output format: tabular summary + narrative explanation

**Example prompts:**
- "Show me travel expenses for 2024"
- "Travel expense report for cost center CC2001 in Q3 2024"
- "Which functional area had the highest travel spend in 2023?"

---

### 2. Actuals vs. Plan Variance Analysis

**Trigger phrases:** "compare actuals", "plan vs actual", "budget variance", "over/under plan"

**Behavior:**
- Compare `Amount in Group Currency` (actual) against `Planned Amount` for any GL account or group
- Calculate:
  - **Variance (Absolute):** `Actual - Plan`
  - **Variance (%):** `(Actual - Plan) / Plan × 100`
- Positive variance = over plan (unfavorable for expenses, favorable for revenue)
- Negative variance = under plan
- Support filtering by period, GL account, cost center, functional area, company code
- Output: table + plain-language summary of over/under performance

**Example prompts:**
- "Compare actuals vs plan for GL 620000 in 2024"
- "Which cost centers are over budget this year?"
- "Show budget variance for all OpEx accounts in Jan 2025"

---

### 3. Period-over-Period Comparisons

**Trigger phrases:** "compare with previous month", "month over month", "year over year", "prior year", "MoM", "YoY"

**Behavior:**
- **Month-over-Month (MoM):** Compare selected period to the immediately preceding calendar month
- **Year-over-Year (YoY):** Compare selected period/month to the same period in the prior year
- Calculate for each:
  - Absolute change: `Current - Prior`
  - Percentage change: `(Current - Prior) / Prior × 100`
- Support multi-period comparisons (e.g., rolling 3-month, full year)
- Apply to any GL account, cost center, functional area, or company code
- When no period is specified, default to the most recent available period

**Example prompts:**
- "How did travel expenses change vs last month?"
- "Year over year comparison of all OpEx for 2024 vs 2023"
- "Show me Q1 2025 vs Q1 2024 for GL 660000"

---

### 4. Significant Variance Highlighting (>20% threshold)

**Trigger phrases:** "significant variance", "flag variances", "highlight anomalies", "what's over 20%"

**Behavior:**
- Automatically flag any record where `|Variance %|` exceeds **20%** (configurable threshold)
- Applied to both:
  - **Plan vs. Actual** variance
  - **Period-over-Period** variance
- Highlight indicators:
  - Flag with `⚠️ HIGH VARIANCE` label in output
  - Separate summary section listing only flagged items
  - Sort flagged items by variance % descending
- Threshold is 20% by default; user can override ("flag variances over 30%")
- Distinguish direction: over-budget vs. under-budget, increase vs. decrease

**Example prompts:**
- "Flag all GL accounts with more than 20% variance vs plan"
- "Show me where spend jumped more than 20% vs last year"
- "Any significant variances in IT expenses this quarter?"

---

### 5. Variance Driver / Factor Analysis

**Trigger phrases:** "why is there a variance", "what's driving", "explain the variance", "root cause", "factors", "drivers"

**Behavior:**
- When a significant variance is identified, analyze contributing dimensions:

| Dimension | How to Analyze |
|---|---|
| **GL Account Mix** | Which specific accounts contributed most to the total variance |
| **Cost Center** | Which cost centers are driving the spend change |
| **Functional Area** | Which business function (Sales, IT, Production, etc.) is driving variance |
| **Profit Center** | Regional breakdown (Americas, EMEA, etc.) |
| **Company Code** | Entity-level contribution |
| **Seasonality** | Compare same month across multiple years to detect seasonal patterns |
| **WBS / Project** | Project-related spend spikes |
| **Internal Order** | Program-level cost drivers |

- Rank contributors by absolute variance impact (highest to lowest)
- Express each driver as % of total variance
- Provide plain-language narrative: *"The 28% increase in Travel & Entertainment is primarily driven by CC3001 (Sales dept), which accounts for 62% of the variance, likely due to increased field sales activity in Q3."*

**Example prompts:**
- "What's driving the travel expense variance in 2024?"
- "Explain why actuals are 35% over plan for GL 600000"
- "What factors caused the year-over-year increase in IT expenses?"

---

## Conversational Context Handling

### Context Retention
- Maintain conversation context across follow-up questions
- Resolve pronouns and references: "that account", "those cost centers", "the same period"
- Support drill-down: user asks high-level, then narrows with follow-ups

**Example conversation:**
```
User: Show travel expenses for 2024
Claude: [shows full 2024 T&E report]

User: Which month had the highest spend?
Claude: [answers in context of the 2024 T&E report]

User: Compare that month to the same month last year
Claude: [compares identified month vs same month in 2023]

User: What's driving the difference?
Claude: [performs driver analysis on the variance]
```

### Ambiguity Resolution
- If a query is ambiguous, ask one clarifying question
- Offer suggestions when a GL account name is partially matched
- Default to Group Currency (column R) for cross-currency comparisons
- When year is not specified, default to the most recent full year available

### Supported Filter Combinations
Users can combine any of the following in a single query:
- Period range (single month, quarter, full year, custom range)
- GL Account (by name or number)
- Cost Center (single or multiple)
- Functional Area (by name or code)
- Company Code
- Profit Center / Region

---

## Output Format Standards

### Tables
- Always include: Period, GL Account, Description, Actual, Plan, Variance (Abs), Variance (%)
- Sort by Variance % descending when highlighting variances
- Include totals row for numeric columns

### Variance Formatting
- Favorable variance (under budget for expenses): display in green / prefix with `▼`
- Unfavorable variance (over budget for expenses): display in red / prefix with `▲`
- >20% variance: prefix with `⚠️`

### Narrative Summary
- Every report must end with a 2–4 sentence plain-language summary
- Include: what was found, magnitude, key driver(s), recommendation if applicable

---

## Technical Implementation Notes

### Data Loading
- Load `SAP_GL_Account_Data.xlsx` at application startup using `openpyxl` or `pandas`
- Cache the parsed DataFrame in memory for the session
- Parse `Fiscal Period/Year` as `MM/YYYY` → split into `month` (int) and `year` (int) columns for easy filtering
- Normalize currency to Group Currency (column R) for all cross-entity comparisons

### Claude Integration
- Use the Anthropic Claude API (claude-sonnet-4-6 or claude-opus-4-6)
- Pass the relevant filtered data subset (not full 1,200 rows) as context in the system prompt
- System prompt must include the column schema and business rules (e.g., expense vs. revenue account types)
- Keep conversation history for multi-turn context

### Variance Calculation Rules
```
Actual          = Amount in Group Currency (col R)
Plan            = Planned Amount (col S)
Variance_Abs    = Actual - Plan
Variance_Pct    = (Actual - Plan) / Plan * 100   [handle Plan=0 as N/A]
MoM_Change_Pct  = (Current_Month - Prior_Month) / |Prior_Month| * 100
YoY_Change_Pct  = (Current_Year - Prior_Year) / |Prior_Year| * 100
Significant     = |Variance_Pct| > 20
```

### GL Account Classification
| Range | Type |
|---|---|
| 400000–410000 | Revenue |
| 500000–520000 | Cost of Goods Sold |
| 600000–680000 | Operating Expenses (OpEx) |
| 700000–710000 | R&D / CapEx |
| 720000–730000 | Below-the-line (Interest, Tax) |
| 800000 | Balance Sheet (Inventory) |

---

## Out of Scope (v1)

- Writing back to SAP or Excel
- Real-time data refresh
- User authentication / multi-user sessions
- Currency conversion beyond the pre-converted Group Currency column
- Forecasting or predictive analytics
