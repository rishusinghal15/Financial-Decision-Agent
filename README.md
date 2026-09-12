# Financial Decision Agent

> **An AI-powered financial decision engine for cash-flow forecasting, affordability analysis, payment-plan evaluation, and safety-constrained spending decisions.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 50/50 Passed](https://img.shields.io/badge/tests-50%2F50%20passed-brightgreen.svg)]()
[![Validation: 0 Errors](https://img.shields.io/badge/validation-0%20errors-brightgreen.svg)]()

---

## 1. Project Overview

Making sound financial decisions requires more than just checking an account balance on payday. A purchase that looks affordable today can easily trigger a future liquidity crisis when upcoming rent, insurance premiums, utility bills, debt obligations, or essential living expenses come due before the next income credit.

The **Financial Decision Agent** is an autonomous financial intelligence system engineered to evaluate arbitrary purchase or payment requests. It reconstructs a user's multi-month financial trajectory from structured profiles, historical events, pending transactions, dated foreign-exchange rates, seller payment options, and unstructured multimodal evidence (messages, payslips, invoices).

The system determines with mathematical rigor whether a user should:
1. **Pay in full today** (`full_payment`)
2. **Make a partial payment now and clear the rest later** (`partial_payment`)
3. **Opt for a seller installment plan** (`installments`)
4. **Postpone the expense until a specific future date** (`wait`)
5. **Decline the expense entirely** (`not_recommended`)

---

## 2. Problem Statement

Given:
* **Requested Expense**: Purchase amount, request date, target completion deadline, and payment flexibility.
* **User Financial Profile**: Available balance, strict minimum balance constraint, protected expense categories, reducible/stoppable spending categories, and allowed payment methods.
* **Financial Ledger Events**: Historical, pending, scheduled, settled, failed, cancelled, and non-cash records.
* **Dated Exchange Rates**: Historical and future FX rates across global currencies.
* **Provider Payment Options**: Available single and multi-installment financing options with fees and schedules.
* **Multimodal Evidence**: Natural language messages (employer payroll notices, landlord updates, merchant alerts) and image documents (payslips, receipts).

The engine must evaluate whether the purchase is safe, compute the maximum amount safe to spend immediately without breaching safety thresholds over a 90-day horizon, select the optimal payment method, and construct an actionable financial schedule.

---

## 3. Core Objective

For every financial request, the engine answers seven critical questions:

| Question | Engine Output Field | Determination Method |
|---|---|---|
| **Can I afford this right now?** | `affordability_status` | 90-day daily balance simulation enforcing $\text{Balance}(t) \ge \text{MinBalance}$ |
| **How much can I safely pay today?** | `amount_safe_to_pay` | Monotonic binary-search simulation over $[0, \text{RequestedAmount}]$ |
| **Should I wait for a safer date?** | `recommended_payment_method` | Forward-scanning liquidity analysis across 90 projected days |
| **Can I safely use installments?** | `recommended_payment_method` | Simulation of all seller installment plans against user constraints |
| **Can I split this into a partial payment?** | `payment_plan` | 2-stage partial payment optimization (Safe Today + Remainder on Earliest Date) |
| **When is the earliest safe full payment date?** | `earliest_date_for_full_payment` | Chronological search for first date where a single full payment maintains safety |
| **What budget adjustments are needed?** | `spending_changes_needed` | Combinatorial exploration of permitted `reduce_to` and `stop` actions |

---

## 4. Key Features

* **AI-Assisted Multimodal Fact Extraction**: Leverages Google Gemini models (`gemini-3.6-flash`) to parse unstructured messages and visual documents (payslips, receipts) into strongly-typed fact schemas (`ExtractedFact`).
* **4-Tier Conflict Resolution & Event Reconciliation**: Reconciles contradicting ledger entries, pending authorizations, and AI-extracted amendments using strict provenance tracking and precedence hierarchies.
* **Dated Currency Normalization**: Converts multi-currency transactions into the user's home currency using exact settlement-date exchange rates without speculative interpolation.
* **90-Day Deterministic Cash-Flow Forecaster**: Reconstructs daily liquidity trajectories, modeling recurring payroll cycles, fixed living commitments, pending debit reservations, and essential spending.
* **Conservative Liquidity Invariants**: Guarantees that available balance never breaches `minimum_balance_to_keep` at any point in the 90-day forecast horizon under a debits-first daily accounting rule.
* **Binary-Search Safe-Amount Engine**: Computes the exact monetary amount safe to commit on `request_date` with sub-cent precision.
* **Forward-Scanning Earliest Full-Payment Engine**: Scans future dates to discover the earliest conservative day a full single payment is sustainable.
* **Exhaustive Candidate Plan Exploration**: Generates and validates full-payment, installment, partial-payment, wait-based, and spending-reduction candidate plans.
* **6-Key Deterministic Lexicographical Ranker**: Selects the optimal financial plan through a transparent priority hierarchy prioritizing user deadlines, budget preservation, and cost minimization.
* **Failure-Safe AI Architecture**: Gracefully handles API rate limits, timeouts, or network outages through deterministic rule fallbacks without halting pipeline execution or fabricating financial facts.
* **Full Telemetry & Auditability**: Instruments every decision with detailed decision traces, candidate rejection diagnostics, token consumption tracking, and cost estimates.

---

## 5. System Architecture

```
                  ┌────────────────────────────────────────┐
                  │    Raw Inputs (CSVs & Media Files)     │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       DataLoader & Type Enforcer       │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │    Dated Foreign Exchange Converter    │
                  └───────────────────┬────────────────────┘
                                      │
                     ┌────────────────┴────────────────┐
                     │                                 │
                     ▼                                 ▼
      ┌─────────────────────────────┐   ┌─────────────────────────────┐
      │  Structured Ledger Events   │   │ Gemini Multimodal Extractor │
      │  (Settled, Pending, Sched)  │   │   (Messages & Visual Docs)  │
      └──────────────┬──────────────┘   └──────────────┬──────────────┘
                     │                                 │
                     └────────────────┬────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ 4-Tier Conflict Resolution Engine      │
                  │ (Provenance, Precedence, Amendments)   │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ 90-Day Deterministic Forecaster        │
                  │ (Recurring Cycles, Debits-First Sim)   │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Candidate Generator & Safety Validator │
                  │ (Binary Search, Installment Simulation)│
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ 6-Key Deterministic Plan Ranker        │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Grounded Explainer & Output Validator  │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Verified Output (output.csv & Reports) │
                  └────────────────────────────────────────┘
```

---

## 6. AI + Deterministic Design Principle

### *"AI proposes facts; deterministic logic makes financial decisions."*

Financial software requires 100% auditability, zero arithmetic hallucinations, and mathematical reproducibility. Generative AI models excel at natural language parsing and visual document understanding, but cannot be trusted to perform multi-step arithmetic, binary searches, or constraint optimization.

```
┌──────────────────────────────────────┐     ┌──────────────────────────────────────┐
│       AI RESPONSIBILITIES (Gemini)   │     │    DETERMINISTIC PYTHON ENGINE       │
├──────────────────────────────────────┤     ├──────────────────────────────────────┤
│ • Extract salary updates from text   │     │ • Foreign exchange normalization     │
│ • Read payslips from PNG images      │     │ • 90-day daily balance simulation    │
│ • Detect invoice discount deadlines  │     │ • Minimum balance constraint checks  │
│ • Generate grounded user summary     │     │ • Binary search for safe amounts     │
│                                      │     │ • Candidate legality validation      │
│ ❌ NO arithmetic calculations        │     │ • 6-key plan ranking & selection     │
│ ❌ NO balance predictions            │     │ • Schema validation & output formatting│
└──────────────────────────────────────┘     └──────────────────────────────────────┘
```

---

## 7. Financial Forecasting & Decision Model

### 90-Day Cash Flow Simulation
The forecaster constructs a daily timeline $[t_0, t_0 + 90\text{ days}]$:
1. **Starting Point**: User's `current_available_balance`.
2. **Pending Debits**: Reserved on their settlement date (preventing double-counting).
3. **Pending Credits**: Excluded from available cash until officially settled (conservative baseline).
4. **Recurring Income / Salaries**: Detected across historical settlement intervals ($\sim 30$ days) and projected forward.
5. **Recurring Living Expenses**: Periodic groceries, utilities, and transport projected based on historical cadence.
6. **Debits-First Accounting**: On any date $t$, scheduled debits are deducted before credits are recognized to ensure solvency during intraday transactions:
   $$\text{Balance}(t) = \text{Balance}(t-1) + \sum \text{Credits}(t) - \sum \text{Debits}(t) - \text{CandidatePayment}(t)$$
   $$\forall t \in [t_0, t_0 + 90]: \quad \text{Balance}(t) \ge \text{minimum\_balance\_to\_keep}$$

### Affordability Status Matrix

```
                          ┌───────────────────────────┐
                          │  Can pay full requested   │
                          │   amount on request_date? │
                          └─────────────┬─────────────┘
                                        │
                       ┌────────────────┴────────────────┐
                      YES                               NO
                       │                                 │
                       ▼                                 ▼
             ┌───────────────────┐             ┌───────────────────┐
             │   affordable_now  │             │  Can complete by  │
             └───────────────────┘             │  deadline using   │
                                               │ plan or changes?  │
                                               └─────────┬─────────┘
                                                         │
                                        ┌────────────────┴────────────────┐
                                       YES                               NO
                                        │                                 │
                                        ▼                                 ▼
                             ┌─────────────────────┐            ┌───────────────────┐
                             │affordable_with_plan │            │  Can pay in full  │
                             └─────────────────────┘            │  at a later date  │
                                                                │  within 90 days?  │
                                                                └─────────┬─────────┘
                                                                          │
                                                         ┌────────────────┴────────────────┐
                                                        YES                               NO
                                                         │                                 │
                                                         ▼                                 ▼
                                               ┌───────────────────┐             ┌───────────────────┐
                                               │  affordable_later │             │   not_affordable  │
                                               └───────────────────┘             └───────────────────┘
```

---

## 8. Payment Method Specifications

| Payment Method | Activation Conditions | Required `payment_plan` Format |
|---|---|---|
| `full_payment` | Safe to pay 100% on `request_date` and allowed by user preferences. | `YYYY-MM-DD:amount` |
| `partial_payment` | `0 < safe_amount < requested_amount`, partial payment allowed by request and profile, and remainder payable on or before deadline. | `YYYY-MM-DD:safe_amt\|YYYY-MM-DD:remainder_amt` (Exactly 2 entries) |
| `installments` | Seller installment option safe, duration $\le \text{max\_installment\_months}$, and completes by deadline. | `YYYY-MM-DD:amt\|YYYY-MM-DD:amt\|...` (Matches option schedule) |
| `wait` | Request cannot complete safely by deadline, but single full payment is safe on a future date $t > t_{\text{request}}$. | `YYYY-MM-DD:requested_amount` |
| `not_recommended` | No safe payment strategy exists within 90 days without breaching minimum balance. | `none` |

---

## 9. Deterministic Plan Ranker (6-Key Lexicographical Ordering)

When multiple safe payment candidates exist, the `PlanRanker` selects the single optimal plan using a strict 6-key hierarchy:

1. **Deadline Compliance** (`completes_by_deadline == True` preferred).
2. **Spending Changes Avoided** ($\text{len}(\text{spending\_changes}) = 0$ preferred).
3. **Total Cost Minimized** ($\text{TotalPayableAmount}$ including financing fees).
4. **Earliest Execution Date** (Plans starting earlier preferred).
5. **Fewer Payment Installments** (Single payment preferred over multi-stage installments).
6. **Stable Tie-Breaking** (Option ID ordering).

---

## 10. Repository Structure

```
Financial-Decision-Agent/
├── code/
│   ├── main.py                     # Main pipeline orchestration entry point
│   ├── models.py                   # Strongly-typed dataclasses & schemas
│   ├── data_loader.py              # CSV loader & multi-relational indexer
│   ├── currency_normalizer.py      # Dated foreign exchange rate converter
│   ├── gemini_extractor.py         # Gemini multimodal fact extractor
│   ├── conflict_resolver.py        # 4-tier conflict resolution engine
│   ├── forecaster.py               # 90-day cash flow simulation engine
│   ├── decision_engine.py          # Candidate plan generation & safety validator
│   ├── ranker.py                   # 6-key deterministic lexicographical ranker
│   ├── gemini_explainer.py         # Grounded natural language explainer
│   ├── validator.py                # Strict 8-column output validator
│   ├── instrumentation.py          # Gemini token & cost telemetry logger
│   └── evaluation/
│       ├── main.py                 # Evaluation harness
│       ├── run_sample_pipeline.py  # 25-sample benchmark runner
│       ├── verify_output_csv.py    # Standalone output validator script
│       └── usage_report.md         # Final production token & cost telemetry
├── tests/
│   ├── test_phase1.py              # Models, loader & FX normalizer tests (12 tests)
│   ├── test_phase2.py              # Extraction, provenance & conflict tests (12 tests)
│   ├── test_phase3.py              # Forecaster, decision engine & ranker tests (14 tests)
│   └── test_phase4.py              # End-to-end integration & validation tests (12 tests)
├── .env.example                    # Configuration template for API keys
├── .gitignore                      # Clean Git exclusion rules
├── AGENTS.md                       # Machine-readable coding agent contract
├── LICENSE                         # MIT License
├── README.md                       # Comprehensive project documentation
├── requirements.txt                # Python package dependencies
└── walkthrough.md                  # Implementation walkthrough & forensic log
```

---

## 11. Technology Stack

* **Language**: Python 3.10+ / 3.13
* **Data Processing**: `pandas`, `numpy`, `python-dateutil`
* **Multimodal AI**: Google Gemini API via official `google-genai` SDK (`gemini-3.6-flash`)
* **Environment & Config**: `python-dotenv`
* **Testing & Quality Assurance**: Python standard `unittest` suite (50 unit and integration tests)

---

## 12. Installation & Setup

### 1. Clone Repository
```bash
git clone https://github.com/rishusinghal15/Financial-Decision-Agent.git
cd Financial-Decision-Agent
```

### 2. Create and Activate Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux (Bash):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and provide your Google Gemini API key:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash
```

---

## 13. How to Run

### Run Full Production Pipeline (250 Requests)
```bash
python code/main.py
```
* **Reads**: `dataset/requests.csv`, `financial_profiles.csv`, `financial_events.csv`, `exchange_rates.csv`, `messages.csv`, `images.csv`
* **Outputs**: `output.csv` (Root directory)
* **Validates**: Automatically executes `OutputValidator` on all 250 records.

### Run Sample Validation Benchmark (25 Public Requests)
```bash
python code/evaluation/run_sample_pipeline.py
```

### Run Output Validator Independently
```bash
python code/evaluation/verify_output_csv.py
```

---

## 14. How to Run Test Suite

Execute all 50 unit and integration tests:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

### Test Suite Summary

```text
tests/test_phase1.py  (12 tests) -> Data models, Decimal precision, dated FX normalization
tests/test_phase2.py  (12 tests) -> Conflict resolution, provenance tracking, message parsing
tests/test_phase3.py  (14 tests) -> 90-day forecaster, binary search safe amount, 6-key ranker
tests/test_phase4.py  (12 tests) -> End-to-end integration, partial payments, schema validation
----------------------------------------------------------------------
Ran 50 tests in 0.421s

OK
```

---

## 15. Output Schema & Example

The engine produces `output.csv` matching this exact 8-column specification:

```csv
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

### Output Columns

| Column | Type | Allowed Values / Format | Description |
|---|---|---|---|
| `request_id` | `str` | `request_01` .. `request_250` | Unique evaluation request identifier |
| `amount_safe_to_pay` | `Decimal` | `0` to `requested_amount` | Maximum safe expenditure on `request_date` |
| `affordability_status` | `str` | `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable` | Overall affordability status |
| `recommended_payment_method` | `str` | `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended` | Actionable payment recommendation |
| `payment_plan` | `str` | `YYYY-MM-DD:amount\|...` or `none` | Chronological payment schedule |
| `earliest_date_for_full_payment`| `date` | `YYYY-MM-DD` or empty | Earliest date one safe full payment is sustainable |
| `spending_changes_needed` | `str` | `none` or `stop:<id>\|reduce_to:<id>:<amt>` | Required budget adjustments |
| `decision_explanation` | `str` | Plain text | Concise, grounded financial explanation |

### Illustrative Example Row

```text
request_02,18376094.03,affordable_with_plan,installments,2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67,2025-09-15,none,"Use 3 installments of IDR 15,952,906.67, starting 2025-08-08. This leaves at least IDR 29,158,400 available."
```

---

## 16. Verification & Validation Results

* **Automated Unit Tests**: `50 / 50` passed (100%).
* **Production Dataset Evaluation**: `250 / 250` requests processed.
* **Unique ID Integrity**: `250 / 250` 1-to-1 match with `dataset/requests.csv`.
* **Output Schema Violations**: `0` errors.
* **Financial Invariant Violations**: `0` breaches of `minimum_balance_to_keep`.
* **Telemetry & Cost Tracking**: Fully documented in `code/evaluation/usage_report.md`.

---

## 17. Engineering Design Decisions

1. **Separation of Concerns**: Kept LLM extraction completely isolated from numerical simulation. If Gemini encounters API rate limits or returns malformed text, the deterministic engine falls back to known structured ledger events safely.
2. **Monotonic Binary Search**: Used binary search to solve for `amount_safe_to_pay` in $O(\log_2(\text{Amount}))$, evaluating 30 iterations to achieve sub-cent precision rather than linear probing.
3. **Strict Debits-First Accounting**: In liquidity simulations, all intraday debits are processed before credits on the same calendar date, ensuring users do not temporarily overdraw accounts before payroll deposits clear.
4. **Exact FX Dating**: Currency conversions enforce exact-day matching in `exchange_rates.csv` without speculative linear interpolation across missing dates.
5. **Zero Invented Facts**: Missing data (such as image-backed payslips during API failure) is treated conservatively as unconfirmed rather than hallucinating arbitrary numerical credits.

---

## 18. Challenge Origin

This project was originally developed for the **HackerRank Orchestrate (September 2026) — "Buy or Wait?"** engineering challenge. It is maintained and published here as an open-source reference architecture for safety-constrained financial reasoning agents.

---

## 19. Limitations & Future Roadmap

### Current Limitations
* **Batch Execution**: Operates on structured CSV and local image files rather than live banking APIs.
* **Deterministic Cash Flow**: Assumes recurring expense amounts remain bounded by historical medians rather than stochastic distributions.
* **Advisory Scope**: This system provides decision support analysis and does not constitute regulated financial or investment advice.

### Future Roadmap
* [ ] Open Banking (Plaid / Yodlee) live transaction synchronization.
* [ ] Monte Carlo probabilistic simulations for volatile variable spending.
* [ ] Interactive Web Dashboard (React + FastAPI) with visual cash-flow projections.
* [ ] Multi-currency multi-wallet asset rebalancing optimizer.

---

## 20. License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
