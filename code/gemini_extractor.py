"""
Gemini Structured Fact Extractor for Messages and Images.
Extracts factual updates strictly conforming to the ExtractedFact schema.
All LLM interactions pass through the automatic instrumentation logger.
"""

import json
import os
import re
import time
from typing import List, Optional, Dict, Any

from models import Message, ImageMapping, FinancialEvent, ExtractedFact
from instrumentation import logger as global_logger, GeminiLogger, get_gemini_model, resolve_run_type

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


VALID_OPERATIONS = {"amend", "cancel", "confirm", "new"}


def _clean_json_text(text: str) -> str:
    """Strip markdown code fences and whitespace from JSON response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class GeminiExtractor:
    """Extracts structured financial facts from messages and images using Google Gemini."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        provider: str = "Google Gemini",
        logger: Optional[GeminiLogger] = None,
        run_type: Optional[str] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = get_gemini_model(model_name)
        self.provider = provider
        self.logger = logger or global_logger
        self.run_type = resolve_run_type(run_type)
        self.client = None

        if self.api_key and genai is not None:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[GeminiExtractor Warning] Failed to initialize genai client: {e}")
                self.client = None

    def extract_all(
        self,
        messages: Optional[List[Message]] = None,
        images: Optional[List[ImageMapping]] = None,
        events_by_id: Optional[Dict[str, FinancialEvent]] = None
    ) -> List[ExtractedFact]:
        """Convenience method to extract all facts from a list of messages and images."""
        facts: List[ExtractedFact] = []
        for msg in (messages or []):
            facts.extend(self.extract_from_message(msg))
        for img in (images or []):
            rel_ev = events_by_id.get(img.related_event_id) if events_by_id else None
            facts.extend(self.extract_from_image(img, related_event=rel_ev))
        return facts

    def extract_from_message(self, message: Message) -> List[ExtractedFact]:
        """
        Extract structured financial facts from a message.
        Failure-safe: returns [] on any failure or missing credentials.
        """
        if self.client is None:
            return []

        prompt = f"""You are an accurate financial fact extraction system.
Analyze the following message and extract any factual financial updates, amendments, cancellations, or confirmations.

Context:
- User ID: {message.user_id}
- Source Type: {message.source_type}
- Sent At: {message.sent_at.isoformat()}
- Related Event ID (if known): {message.related_event_id or "Unknown"}

Message Text:
\"\"\"{message.message_text}\"\"\"

Extraction Rules:
1. Extract only explicit, grounded financial facts directly stated in the text.
2. If the message confirms a salary change, extract the new amount, effective date, and currency.
3. If the message mentions an unapproved or pending bonus without a confirmed amount/date, do NOT treat it as confirmed income.
4. If the message cancels or amends an expense/subscription/refund, extract operation, field, value, currency, and date.
5. If there are no factual changes, return an empty JSON array [].

Return ONLY a valid JSON array of objects with these exact keys:
[
  {{
    "event_id": "{message.related_event_id or ''}",
    "operation": "amend" | "cancel" | "confirm" | "new",
    "field": "amount" | "settlement_date" | "status" | "category" | "description",
    "value": "string value of the new fact",
    "currency": "3-letter currency code or empty string",
    "effective_date": "YYYY-MM-DD or empty string",
    "source": "{message.message_id}"
  }}
]
"""
        start_time = time.time()
        req_id = message.request_id or f"msg_{message.message_id}"

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json"
                ) if types else None
            )

            duration_ms = (time.time() - start_time) * 1000.0

            # Extract token usage from response metadata if available
            in_tokens = 0
            out_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                try:
                    in_tokens = int(getattr(response.usage_metadata, "prompt_token_count", 0) or 0)
                except Exception:
                    in_tokens = 0
                try:
                    out_tokens = int(getattr(response.usage_metadata, "candidates_token_count", 0) or 0)
                except Exception:
                    out_tokens = 0

            raw_text = response.text if hasattr(response, "text") and response.text else ""
            cleaned = _clean_json_text(raw_text)
            parsed_data = json.loads(cleaned)

            facts = self._validate_and_build_facts(parsed_data, default_source=message.message_id)

            try:
                self.logger.log_call(
                    purpose="message_fact_extraction",
                    model_name=self.model_name,
                    provider=self.provider,
                    request_id=req_id,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    success=True,
                    duration_ms=duration_ms,
                    run_type=self.run_type
                )
            except Exception as log_err:
                print(f"[GeminiExtractor Warning] Telemetry logging failed: {log_err}")
            return facts

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000.0
            try:
                self.logger.log_call(
                    purpose="message_fact_extraction",
                    model_name=self.model_name,
                    provider=self.provider,
                    request_id=req_id,
                    input_tokens=0,
                    output_tokens=0,
                    success=False,
                    error_msg=str(e),
                    duration_ms=duration_ms,
                    run_type=self.run_type
                )
            except Exception as log_err:
                print(f"[GeminiExtractor Warning] Telemetry logging failed: {log_err}")
            return []

    def extract_from_image(
        self,
        image_mapping: ImageMapping,
        related_event: Optional[FinancialEvent] = None
    ) -> List[ExtractedFact]:
        """
        Extract net/total payable amount, currency, and date from an image receipt/bill/pay-slip.
        Failure-safe: returns [] on any failure or missing credentials.
        """
        if self.client is None or not os.path.exists(image_mapping.file_path):
            return []

        start_time = time.time()
        req_id = image_mapping.request_id or f"img_{image_mapping.image_id}"

        event_desc = related_event.description if related_event else "Unknown document"
        event_curr = related_event.currency if related_event else ""
        event_date_str = related_event.event_date.isoformat() if related_event else ""

        prompt = f"""You are an accurate financial document parser.
Inspect this image ({event_desc}) and extract the final total/net payable monetary amount, currency, and document date.

Context:
- Associated Event ID: {image_mapping.related_event_id}
- Expected Document Type: {event_desc}
- Currency Hint: {event_curr}
- Approximate Date: {event_date_str}

Rules:
1. Extract the exact final total amount due / net paid amount from the image.
2. Extract the 3-letter currency code (e.g. INR, USD, EUR, IDR, ZAR).
3. Extract the document date in YYYY-MM-DD format if visible.
4. Do not guess or estimate. Extract only clear printed values.

Return ONLY a valid JSON array with exactly one object:
[
  {{
    "event_id": "{image_mapping.related_event_id}",
    "operation": "amend",
    "field": "amount",
    "value": "string numeric amount, e.g. 1425.50",
    "currency": "3-letter currency",
    "effective_date": "YYYY-MM-DD",
    "source": "{image_mapping.image_id}"
  }}
]
"""
        try:
            with open(image_mapping.file_path, "rb") as f:
                image_bytes = f.read()

            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/png"
            ) if types else image_bytes

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[image_part, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json"
                ) if types else None
            )

            duration_ms = (time.time() - start_time) * 1000.0

            in_tokens = 0
            out_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                try:
                    in_tokens = int(getattr(response.usage_metadata, "prompt_token_count", 0) or 0)
                except Exception:
                    in_tokens = 0
                try:
                    out_tokens = int(getattr(response.usage_metadata, "candidates_token_count", 0) or 0)
                except Exception:
                    out_tokens = 0

            raw_text = response.text if hasattr(response, "text") and response.text else ""
            cleaned = _clean_json_text(raw_text)
            parsed_data = json.loads(cleaned)

            facts = self._validate_and_build_facts(
                parsed_data,
                default_source=image_mapping.image_id,
                default_event_id=image_mapping.related_event_id
            )

            try:
                self.logger.log_call(
                    purpose="image_fact_extraction",
                    model_name=self.model_name,
                    provider=self.provider,
                    request_id=req_id,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    success=True,
                    duration_ms=duration_ms,
                    run_type=self.run_type
                )
            except Exception as log_err:
                print(f"[GeminiExtractor Warning] Telemetry logging failed: {log_err}")
            return facts

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000.0
            try:
                self.logger.log_call(
                    purpose="image_fact_extraction",
                    model_name=self.model_name,
                    provider=self.provider,
                    request_id=req_id,
                    input_tokens=0,
                    output_tokens=0,
                    success=False,
                    error_msg=str(e),
                    duration_ms=duration_ms,
                    run_type=self.run_type
                )
            except Exception as log_err:
                print(f"[GeminiExtractor Warning] Telemetry logging failed: {log_err}")
            return []

    def _validate_and_build_facts(
        self,
        raw_items: Any,
        default_source: str,
        default_event_id: str = ""
    ) -> List[ExtractedFact]:
        """Validate raw dictionary items against ExtractedFact schema."""
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            return []

        validated = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue

            op = str(item.get("operation", "")).strip().lower()
            if op not in VALID_OPERATIONS:
                continue

            field_name = str(item.get("field", "")).strip().lower()
            val = str(item.get("value", "")).strip()
            if not field_name or not val:
                continue

            event_id = str(item.get("event_id", "")).strip() or default_event_id
            curr = str(item.get("currency", "")).strip().upper()
            eff_date = str(item.get("effective_date", "")).strip()
            src = str(item.get("source", "")).strip() or default_source

            fact = ExtractedFact(
                event_id=event_id,
                operation=op,
                field=field_name,
                value=val,
                currency=curr,
                effective_date=eff_date,
                source=src
            )
            validated.append(fact)

        return validated
