"""
Regression Tests for Centralized Gemini Model Configuration (Issue #2).
Verifies that all Gemini components use a single source of truth for model configuration.
"""

import os
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from instrumentation import get_gemini_model, DEFAULT_GEMINI_MODEL
from gemini_extractor import GeminiExtractor
from gemini_explainer import GeminiExplainer


class TestGeminiModelConfig(unittest.TestCase):
    def test_1_extractor_and_explainer_resolve_same_configured_model(self):
        """1. Prove the extractor and explainer resolve the same configured model."""
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-3.6-flash"}):
            os.environ.pop("GEMINI_API_KEY", None)

            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            self.assertEqual(extractor.model_name, explainer.model_name)
            self.assertEqual(extractor.model_name, "gemini-3.6-flash")
            self.assertEqual(explainer.model_name, "gemini-3.6-flash")

    def test_2_changing_gemini_model_env_updates_both_components(self):
        """2. Prove changing GEMINI_MODEL changes the model used by BOTH components."""
        test_model = "test-custom-model-v1"
        with patch.dict(os.environ, {"GEMINI_MODEL": test_model}):
            os.environ.pop("GEMINI_API_KEY", None)

            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            self.assertEqual(extractor.model_name, test_model)
            self.assertEqual(explainer.model_name, test_model)
            self.assertEqual(extractor.model_name, explainer.model_name)

    def test_3_neither_component_contains_stale_hardcoded_model(self):
        """3. Prove neither component contains a stale/deprecated hardcoded model identifier."""
        # When environment has gemini-3.6-flash, neither component should retain stale gemini-2.5-flash
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-3.6-flash"}):
            os.environ.pop("GEMINI_API_KEY", None)

            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            self.assertNotEqual(extractor.model_name, "gemini-2.5-flash")
            self.assertNotEqual(explainer.model_name, "gemini-2.5-flash")

        # When GEMINI_MODEL is unset, both fall back to the centralized DEFAULT_GEMINI_MODEL
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_MODEL", None)
            os.environ.pop("GEMINI_API_KEY", None)

            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            self.assertEqual(extractor.model_name, DEFAULT_GEMINI_MODEL)
            self.assertEqual(explainer.model_name, DEFAULT_GEMINI_MODEL)
            self.assertNotEqual(extractor.model_name, "gemini-2.5-flash")
            self.assertNotEqual(explainer.model_name, "gemini-2.5-flash")

    def test_4_no_real_gemini_api_calls_made(self):
        """4. Prove tests do not make real Gemini API calls."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            # Client remains None without API key -> zero network I/O
            self.assertIsNone(extractor.client)
            self.assertIsNone(explainer.client)

    def test_5_explicit_parameter_override_is_consistent(self):
        """5. Explicit parameter override works symmetrically across components."""
        with patch.dict(os.environ, {"GEMINI_MODEL": "env-model"}):
            os.environ.pop("GEMINI_API_KEY", None)

            extractor = GeminiExtractor(model_name="explicit-override")
            explainer = GeminiExplainer(model_name="explicit-override")

            self.assertEqual(extractor.model_name, "explicit-override")
            self.assertEqual(explainer.model_name, "explicit-override")


if __name__ == "__main__":
    unittest.main()
