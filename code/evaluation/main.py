"""
Evaluation utility to generate token usage and cost analysis report from logged Gemini calls.
"""

import os
import sys

# Add code directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from instrumentation import logger

def main():
    report_path = os.path.join(os.path.dirname(__file__), "usage_report.md")
    report = logger.generate_usage_report(total_requests=250, report_path=report_path)
    print(f"Generated usage report at: {report_path}")
    print(report)

if __name__ == "__main__":
    main()
