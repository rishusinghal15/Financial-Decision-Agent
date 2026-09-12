# Token Usage and Cost Analysis Report

## Summary

- **Total Requests Processed**: 250
- **Total Model Calls**: 459 (2 successful, 457 failed)
- **Total Input Tokens**: 821
- **Total Output Tokens**: 156
- **Total Combined Tokens**: 977
- **Average Input Tokens Per Request**: 3.28
- **Average Output Tokens Per Request**: 0.62
- **Average Total Tokens Per Request**: 3.91
- **Total Estimated Cost**: $0.000109 USD
- **Estimated Cost Per Request**: $0.000000 USD

## Model Breakdown

| Provider | Model Name | Calls | Input Tokens | Output Tokens | Total Tokens | Estimated Cost (USD) |
|---|---|---|---|---|---|---|
| Google Gemini | gemini-3.6-flash | 209 | 821 | 156 | 977 | $0.000109 |
| Google Gemini | gemini-2.5-flash | 250 | 0 | 0 | 0 | $0.000000 |

## Purpose Breakdown

| Purpose | Calls | Input Tokens | Output Tokens | Total Tokens | Estimated Cost (USD) |
|---|---|---|---|---|---|
| message_fact_extraction | 198 | 821 | 156 | 977 | $0.000109 |
| explanation_generation | 250 | 0 | 0 | 0 | $0.000000 |
| image_fact_extraction | 11 | 0 | 0 | 0 | $0.000000 |

## Efficiency & Optimization Notes

- Structured fact extraction runs strictly on relevant messages & images.
- Financial arithmetic, affordability, ranking, and scheduling are executed deterministically in Python with zero LLM token consumption.
- Explanations are compactly generated using deterministic decision traces as context.
