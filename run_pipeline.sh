#!/bin/bash
set -e

echo "Running 04_supplier_features.py..."
python3 pipeline/04_supplier_features.py

echo "Running 05_build_dataset.py..."
python3 pipeline/05_build_dataset.py

echo "Running models/train.py..."
python3 models/train.py

echo "Running models/calibrate.py..."
python3 models/calibrate.py

echo "Running models/recalibrate_threshold.py..."
python3 models/recalibrate_threshold.py

echo "Pipeline complete."
