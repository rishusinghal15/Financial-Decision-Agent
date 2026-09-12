import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from validator import OutputValidator
from data_loader import DataLoader

dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dataset"))
loader = DataLoader(dataset_dir)
c = loader.load_all()

output_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "output.csv"))
df = pd.read_csv(output_path)
print(f"File: {output_path}")
print(f"Total Rows: {len(df)}")
print(f"Columns: {list(df.columns)}")
print(f"Unique Request IDs: {df['request_id'].nunique()}")

validator = OutputValidator()
errors = validator.validate_dataset(df, expected_requests=c.requests)
print(f"Total Validation Errors: {len(errors)}")
if errors:
    for e in errors[:10]:
        print(f"  * Error: {e}")
else:
    print("ALL 250 ROWS & DATASET CONTRACT PASSED WITH 0 VALIDATION ERRORS!")
