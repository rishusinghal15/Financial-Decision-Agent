"""
Runs all 25 sample requests through the complete end-to-end pipeline (Phases 1-4)
and performs a detailed field-by-field comparison against dataset/sample_requests.csv.
"""

import os
import sys
import pandas as pd
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import run_pipeline

dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dataset"))
sample_output_csv = os.path.abspath(os.path.join(os.path.dirname(__file__), "sample_pipeline_output.csv"))

print("Running 25-sample complete pipeline...")
df_generated = run_pipeline(
    dataset_dir=dataset_dir,
    output_path=sample_output_csv,
    is_sample_run=True,
    use_gemini=True
)

df_expected = pd.read_csv(os.path.join(dataset_dir, "sample_requests.csv"))

print("\n" + "=" * 100)
print("25-SAMPLE COMPLETE PIPELINE EVALUATION REPORT")
print("=" * 100)

exact_matches = 0
matches_by_field = {
    "status": 0,
    "method": 0,
    "plan": 0,
    "earliest_date": 0,
    "spending_changes": 0,
    "safe_amount": 0,
}

for idx, exp_row in df_expected.iterrows():
    req_id = exp_row['request_id']
    gen_row = df_generated[df_generated['request_id'] == req_id].iloc[0]

    exp_status = str(exp_row['affordability_status']).strip()
    exp_method = str(exp_row['recommended_payment_method']).strip()
    exp_plan = str(exp_row['payment_plan']).strip()
    exp_earliest = str(exp_row['earliest_date_for_full_payment']).strip() if pd.notna(exp_row['earliest_date_for_full_payment']) else ""
    exp_sc = str(exp_row['spending_changes_needed']).strip()
    exp_safe = str(exp_row['amount_safe_to_pay']).strip()

    gen_status = str(gen_row['affordability_status']).strip()
    gen_method = str(gen_row['recommended_payment_method']).strip()
    gen_plan = str(gen_row['payment_plan']).strip()
    gen_earliest = str(gen_row['earliest_date_for_full_payment']).strip() if pd.notna(gen_row['earliest_date_for_full_payment']) else ""
    gen_sc = str(gen_row['spending_changes_needed']).strip()
    gen_safe = str(gen_row['amount_safe_to_pay']).strip()
    gen_exp = str(gen_row['decision_explanation']).strip()

    diffs = []
    if gen_status == exp_status:
        matches_by_field["status"] += 1
    else:
        diffs.append(f"Status: Gen '{gen_status}' vs Exp '{exp_status}'")

    if gen_method == exp_method:
        matches_by_field["method"] += 1
    else:
        diffs.append(f"Method: Gen '{gen_method}' vs Exp '{exp_method}'")

    if gen_plan == exp_plan:
        matches_by_field["plan"] += 1
    else:
        diffs.append(f"Plan: Gen '{gen_plan}' vs Exp '{exp_plan}'")

    if gen_earliest == exp_earliest:
        matches_by_field["earliest_date"] += 1
    else:
        diffs.append(f"Earliest Date: Gen '{gen_earliest}' vs Exp '{exp_earliest}'")

    if gen_sc == exp_sc:
        matches_by_field["spending_changes"] += 1
    else:
        diffs.append(f"Spending Changes: Gen '{gen_sc}' vs Exp '{exp_sc}'")

    try:
        if abs(Decimal(gen_safe) - Decimal(exp_safe)) <= Decimal("0.05"):
            matches_by_field["safe_amount"] += 1
        else:
            diffs.append(f"Safe Amount: Gen '{gen_safe}' vs Exp '{exp_safe}'")
    except Exception:
        diffs.append(f"Safe Amount: Gen '{gen_safe}' vs Exp '{exp_safe}'")

    is_exact = len(diffs) == 0
    if is_exact:
        exact_matches += 1
        print(f"\n[EXACT MATCH] {req_id}: {gen_status} | {gen_method} | Plan: {gen_plan}")
    else:
        print(f"\n[DIFFERENCE]  {req_id}:")
        for d in diffs:
            print(f"    * {d}")
    print(f"    Explanation: {gen_exp}")

print("\n" + "=" * 100)
print(f"OVERALL SUMMARY: {exact_matches}/25 EXACT 100% FIELD-FOR-FIELD MATCHES")
print(f"FIELD MATCH RATES:")
for f_name, count in matches_by_field.items():
    print(f"  * {f_name:<20}: {count}/25 ({count/25*100:.1f}%)")
print("=" * 100)
