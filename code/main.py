"""
Main Pipeline Orchestration for Buy or Wait? Financial Decision Agent.
Executes the locked end-to-end architecture across all requests in dataset/requests.csv:
1. Data loading & FX normalization
2. Gemini message & image fact extraction
3. 4-tier conflict resolution & event reconciliation
4. 90-day deterministic cash flow forecasting
5. Decision engine & candidate generation
6. 6-key deterministic ranking
7. Gemini grounded explanation generation (with deterministic fallback)
8. Output validation
9. Writes root output.csv and generates usage_report.md
"""

import os
import sys
import time
import pandas as pd
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure code directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from data_loader import DataLoader, DatasetContainer
from models import Request, UserProfile, FinancialEvent, ReconciledEvent, DecisionTrace
from gemini_extractor import GeminiExtractor
from conflict_resolver import ConflictResolver
from forecaster import FinancialForecaster
from ranker import PlanRanker
from decision_engine import DecisionEngine
from gemini_explainer import GeminiExplainer
from validator import OutputValidator, ValidationError
from instrumentation import logger as global_logger


def run_pipeline(
    dataset_dir: Optional[str] = None,
    output_path: Optional[str] = None,
    is_sample_run: bool = False,
    use_gemini: bool = True
) -> pd.DataFrame:
    """
    Executes the complete Buy or Wait pipeline.
    """
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dataset_dir = dataset_dir or os.path.join(root_dir, "dataset")
    output_path = output_path or os.path.join(root_dir, "output.csv")

    print("=" * 80)
    print(f"STARTING BUY OR WAIT? FINANCIAL DECISION AGENT PIPELINE")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Dataset Dir: {dataset_dir}")
    print(f"Target Output: {output_path}")
    print(f"Mode: {'25-SAMPLE VALIDATION RUN' if is_sample_run else 'FULL 250-REQUEST DATASET RUN'}")
    print("=" * 80)

    # Reset telemetry logger to ensure clean run-specific metrics
    global_logger.clear_logs()

    # 1. Load dataset
    print("\n[Step 1/8] Loading dataset and constructing multi-relational indices...")
    loader = DataLoader(dataset_dir)
    container = loader.load_all()

    # Select target requests
    target_requests: List[Request] = (
        container.sample_requests if is_sample_run else container.requests
    )
    print(f"Loaded {len(target_requests)} evaluation requests.")

    # 2. Initialize components
    print("\n[Step 2/8] Initializing pipeline components...")
    extractor = GeminiExtractor() if use_gemini else None
    resolver = ConflictResolver()
    forecaster = FinancialForecaster(container.rate_table)
    ranker = PlanRanker()
    engine = DecisionEngine(forecaster, ranker)
    explainer = GeminiExplainer()
    validator = OutputValidator()

    output_rows: List[Dict[str, Any]] = []
    start_total_time = time.time()

    # 3. Process requests
    print("\n[Step 3/8] Processing requests through the locked pipeline...")
    for idx, req in enumerate(target_requests):
        req_id = req.request_id
        u_id = req.user_id
        profile = container.profiles_by_user_id[u_id]
        user_events = container.events_by_user_id.get(u_id, [])
        opts = container.payment_options_by_request_id.get(req_id, [])

        # A. Gather relevant messages & images for this user and request
        req_msgs = [m for m in container.messages if m.user_id == u_id or m.request_id == req_id]
        req_imgs = [im for im in container.images if im.user_id == u_id or im.request_id == req_id]

        # B. Gemini fact extraction (Phase 2)
        extracted_facts = []
        if extractor and (req_msgs or req_imgs):
            try:
                events_by_id = {e.event_id: e for e in user_events}
                extracted_facts = extractor.extract_all(
                    messages=req_msgs,
                    images=req_imgs,
                    events_by_id=events_by_id
                )
            except Exception as ex:
                print(f"[{req_id}] Warning: Extraction error ({ex}), falling back to structured baseline.")
                extracted_facts = []

        # C. Conflict Resolution & Event Reconciliation
        reconciled_events = resolver.reconcile_events(
            events=user_events,
            facts=extracted_facts,
            messages=req_msgs
        )

        # D. 90-Day Forecaster, Decision Engine & 6-Key Ranker (Phase 3)
        trace: DecisionTrace = engine.evaluate_request(
            request=req,
            user_profile=profile,
            reconciled_events=reconciled_events,
            payment_options=opts
        )

        # E. Gemini Grounded Explanation (Phase 4)
        explanation = explainer.generate_explanation(
            request=req,
            profile=profile,
            trace=trace,
            winning_plan=trace.selected_plan
        )

        # Format earliest date string
        earliest_str = (
            trace.earliest_date_for_full_payment.isoformat()
            if trace.earliest_date_for_full_payment else ""
        )

        # F. Build output row
        row_dict = {
            "request_id": req_id,
            "amount_safe_to_pay": str(trace.amount_safe_to_pay),
            "affordability_status": trace.affordability_status,
            "recommended_payment_method": trace.recommended_payment_method,
            "payment_plan": trace.payment_plan,
            "earliest_date_for_full_payment": earliest_str,
            "spending_changes_needed": trace.spending_changes_needed,
            "decision_explanation": explanation,
        }

        # G. Strict single-row validation
        row_errors = validator.validate_row(row_dict, req, trace, opts)
        if row_errors:
            print(f"[{req_id}] VALIDATION FAILED on single row:")
            for err in row_errors:
                print(f"   * {err}")
            raise ValidationError(f"Output row validation failed for {req_id}: {row_errors}")

        output_rows.append(row_dict)

        if (idx + 1) % 25 == 0 or (idx + 1) == len(target_requests):
            elapsed = time.time() - start_total_time
            print(f"   Processed {idx + 1}/{len(target_requests)} requests ({elapsed:.1f}s elapsed)...")

    # 4. Validate complete output dataset
    print("\n[Step 4/8] Validating complete output dataset structure and integrity...")
    dataset_errors = validator.validate_dataset(output_rows, target_requests)
    if dataset_errors:
        print("DATASET VALIDATION ERRORS DETECTED:")
        for err in dataset_errors[:10]:
            print(f"   * {err}")
        raise ValidationError(f"Dataset validation failed with {len(dataset_errors)} errors.")
    print("Dataset validation passed 100% cleanly.")

    # 5. Write final output CSV
    print(f"\n[Step 5/8] Writing predictions to {output_path}...")
    df_out = pd.DataFrame(output_rows, columns=[
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    ])
    df_out.to_csv(output_path, index=False)
    print(f"Successfully wrote {len(df_out)} rows to {output_path}.")

    # 6. Generate usage report
    print("\n[Step 6/8] Generating token usage and cost report...")
    usage_md = global_logger.generate_usage_report(total_requests=len(target_requests))
    print("Usage report successfully written to code/evaluation/usage_report.md.")

    print("\n" + "=" * 80)
    print("PIPELINE EXECUTION COMPLETE")
    print("=" * 80)

    return df_out


if __name__ == "__main__":
    # If run directly, process the complete 250-request dataset
    run_pipeline()
