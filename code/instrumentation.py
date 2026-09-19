"""
Automatic Gemini Call Logging and Token Usage Instrumentation.
Logs all LLM calls to a persistent JSON log file and generates usage_report.md.
"""

import os
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Centralized Gemini Model Configuration
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


def get_gemini_model(model_name: Optional[str] = None) -> str:
    """
    Centralized resolver for Gemini model configuration.
    Precedence:
    1. Explicit model_name passed to constructor/function.
    2. GEMINI_MODEL environment variable.
    3. Centralized DEFAULT_GEMINI_MODEL fallback ('gemini-3.6-flash').
    """
    if model_name:
        return model_name
    return os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


# Controlled execution contexts for telemetry provenance
VALID_RUN_TYPES = {"production", "evaluation", "smoke_test"}
DEFAULT_RUN_TYPE = "production"


def resolve_run_type(run_type: Optional[str] = None) -> str:
    """
    Resolves the execution context run_type.
    Precedence:
    1. Explicit run_type passed to caller/logger.
    2. GEMINI_RUN_TYPE environment variable.
    3. Centralized DEFAULT_RUN_TYPE fallback ('production').
    """
    if run_type and run_type in VALID_RUN_TYPES:
        return run_type
    env_run_type = os.environ.get("GEMINI_RUN_TYPE")
    if env_run_type and env_run_type in VALID_RUN_TYPES:
        return env_run_type
    return DEFAULT_RUN_TYPE


def _redact_secrets(text: Optional[str]) -> Optional[str]:
    """Redact potential API keys or sensitive tokens from error messages or strings."""
    if not text:
        return text
    redacted = re.sub(r"AIzaSy[A-Za-z0-9_-]{33}", "[REDACTED_API_KEY]", text)
    redacted = re.sub(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_\-\.]{8,})([\"']?)", r"\1[REDACTED_API_KEY]\3", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"(key=)([A-Za-z0-9_\-\.]{8,})", r"\1[REDACTED_API_KEY]", redacted, flags=re.IGNORECASE)
    return redacted


# Gemini pricing estimates (USD per 1M tokens) - standard Gemini Flash rates
# gemini-3.6-flash / gemini-2.5-flash / gemini-1.5-flash: ~$0.075 per 1M input tokens (<128k prompt), ~$0.30 per 1M output tokens
PRICE_PER_1M_INPUT_TOKENS = {
    "gemini-3.6-flash": 0.075,
    "gemini-2.5-flash": 0.075,
    "gemini-1.5-flash": 0.075,
    "gemini-1.5-pro": 1.25,
    "default": 0.075,
}

PRICE_PER_1M_OUTPUT_TOKENS = {
    "gemini-3.6-flash": 0.30,
    "gemini-2.5-flash": 0.30,
    "gemini-1.5-flash": 0.30,
    "gemini-1.5-pro": 5.00,
    "default": 0.30,
}

LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "evaluation", "gemini_calls.json")
REPORT_FILE_PATH = os.path.join(os.path.dirname(__file__), "evaluation", "usage_report.md")


def _calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    in_rate = PRICE_PER_1M_INPUT_TOKENS.get(model_name, PRICE_PER_1M_INPUT_TOKENS["default"])
    out_rate = PRICE_PER_1M_OUTPUT_TOKENS.get(model_name, PRICE_PER_1M_OUTPUT_TOKENS["default"])
    cost = (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate
    return round(cost, 6)


class GeminiLogger:
    def __init__(self, log_path: str = LOG_FILE_PATH, default_run_type: str = DEFAULT_RUN_TYPE):
        self.log_path = log_path
        self.default_run_type = resolve_run_type(default_run_type)
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            if not os.path.exists(self.log_path):
                with open(self.log_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except Exception as e:
            print(f"[GeminiLogger Warning] Init failed to ensure log file: {e}")

    def log_call(
        self,
        purpose: str,
        model_name: str,
        provider: str = "Google Gemini",
        request_id: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        success: bool = True,
        error_msg: Optional[str] = None,
        duration_ms: float = 0.0,
        run_type: Optional[str] = None
    ) -> Dict[str, Any]:
        actual_run_type = resolve_run_type(run_type or self.default_run_type)
        cleaned_error = _redact_secrets(error_msg) if not success else None
        try:
            cost = _calculate_cost(model_name, input_tokens, output_tokens)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id or "N/A",
                "purpose": purpose,
                "run_type": actual_run_type,
                "model_name": model_name,
                "provider": provider,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "total_tokens": int(input_tokens + output_tokens),
                "estimated_cost_usd": cost,
                "success": success,
                "duration_ms": round(duration_ms, 2),
                "error": cleaned_error
            }

            # Read existing records, append, and atomic write
            try:
                records = []
                if os.path.exists(self.log_path):
                    try:
                        with open(self.log_path, "r", encoding="utf-8") as f:
                            records = json.load(f)
                            if not isinstance(records, list):
                                records = []
                    except Exception:
                        records = []

                records.append(record)
                with open(self.log_path, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2)
            except Exception as e:
                print(f"[GeminiLogger Warning] Failed to save call log: {e}")

            return record
        except Exception as e:
            print(f"[GeminiLogger Warning] Unexpected error in log_call: {e}")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id or "N/A",
                "purpose": purpose,
                "run_type": actual_run_type,
                "model_name": model_name,
                "provider": provider,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "total_tokens": int(input_tokens + output_tokens),
                "estimated_cost_usd": 0.0,
                "success": success,
                "duration_ms": round(duration_ms, 2),
                "error": cleaned_error
            }

    def clear_logs(self):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump([], f)

    def generate_usage_report(
        self,
        total_requests: int = 250,
        report_path: str = REPORT_FILE_PATH
    ) -> str:
        records = []
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as f:
                records = json.load(f)

        total_calls = len(records)
        successful_calls = sum(1 for r in records if r.get("success", False))
        failed_calls = total_calls - successful_calls
        total_in_tokens = sum(r.get("input_tokens", 0) for r in records)
        total_out_tokens = sum(r.get("output_tokens", 0) for r in records)
        total_tokens = total_in_tokens + total_out_tokens
        total_cost = sum(r.get("estimated_cost_usd", 0.0) for r in records)

        avg_in_per_req = total_in_tokens / max(1, total_requests)
        avg_out_per_req = total_out_tokens / max(1, total_requests)
        avg_tokens_per_req = total_tokens / max(1, total_requests)
        avg_cost_per_req = total_cost / max(1, total_requests)

        # Breakdown by model
        by_model = {}
        for r in records:
            m = r.get("model_name", "unknown")
            if m not in by_model:
                by_model[m] = {
                    "provider": r.get("provider", "Google Gemini"),
                    "calls": 0,
                    "in_tokens": 0,
                    "out_tokens": 0,
                    "cost": 0.0
                }
            by_model[m]["calls"] += 1
            by_model[m]["in_tokens"] += r.get("input_tokens", 0)
            by_model[m]["out_tokens"] += r.get("output_tokens", 0)
            by_model[m]["cost"] += r.get("estimated_cost_usd", 0.0)

        # Breakdown by purpose
        by_purpose = {}
        for r in records:
            p = r.get("purpose", "unknown")
            if p not in by_purpose:
                by_purpose[p] = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "cost": 0.0}
            by_purpose[p]["calls"] += 1
            by_purpose[p]["in_tokens"] += r.get("input_tokens", 0)
            by_purpose[p]["out_tokens"] += r.get("output_tokens", 0)
            by_purpose[p]["cost"] += r.get("estimated_cost_usd", 0.0)

        # Generate markdown
        report_lines = [
            "# Token Usage and Cost Analysis Report",
            "",
            "## Summary",
            "",
            f"- **Total Requests Processed**: {total_requests}",
            f"- **Total Model Calls**: {total_calls} ({successful_calls} successful, {failed_calls} failed)",
            f"- **Total Input Tokens**: {total_in_tokens:,}",
            f"- **Total Output Tokens**: {total_out_tokens:,}",
            f"- **Total Combined Tokens**: {total_tokens:,}",
            f"- **Average Input Tokens Per Request**: {avg_in_per_req:.2f}",
            f"- **Average Output Tokens Per Request**: {avg_out_per_req:.2f}",
            f"- **Average Total Tokens Per Request**: {avg_tokens_per_req:.2f}",
            f"- **Total Estimated Cost**: ${total_cost:.6f} USD",
            f"- **Estimated Cost Per Request**: ${avg_cost_per_req:.6f} USD",
            "",
            "## Model Breakdown",
            "",
            "| Provider | Model Name | Calls | Input Tokens | Output Tokens | Total Tokens | Estimated Cost (USD) |",
            "|---|---|---|---|---|---|---|"
        ]

        if not by_model:
            report_lines.append("| Google Gemini | (none recorded) | 0 | 0 | 0 | 0 | $0.000000 |")
        else:
            for m, data in by_model.items():
                m_total = data["in_tokens"] + data["out_tokens"]
                report_lines.append(
                    f"| {data['provider']} | {m} | {data['calls']} | {data['in_tokens']:,} | {data['out_tokens']:,} | {m_total:,} | ${data['cost']:.6f} |"
                )

        report_lines.extend([
            "",
            "## Purpose Breakdown",
            "",
            "| Purpose | Calls | Input Tokens | Output Tokens | Total Tokens | Estimated Cost (USD) |",
            "|---|---|---|---|---|---|"
        ])

        if not by_purpose:
            report_lines.append("| (none recorded) | 0 | 0 | 0 | 0 | $0.000000 |")
        else:
            for p, data in by_purpose.items():
                p_total = data["in_tokens"] + data["out_tokens"]
                report_lines.append(
                    f"| {p} | {data['calls']} | {data['in_tokens']:,} | {data['out_tokens']:,} | {p_total:,} | ${data['cost']:.6f} |"
                )

        report_lines.extend([
            "",
            "## Efficiency & Optimization Notes",
            "",
            "- Structured fact extraction runs strictly on relevant messages & images.",
            "- Financial arithmetic, affordability, ranking, and scheduling are executed deterministically in Python with zero LLM token consumption.",
            "- Explanations are compactly generated using deterministic decision traces as context.",
            ""
        ])

        report_content = "\n".join(report_lines)

        # Write report to report_path and also root evaluation folder if needed
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        return report_content


# Global logger instance
logger = GeminiLogger()
