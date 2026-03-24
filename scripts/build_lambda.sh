#!/usr/bin/env bash
# Build Lambda zip for Terraform deploy (pattern aligned with policy-agent/scripts/build_lambda.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$PROJECT_ROOT/terraform/build"
PACKAGE_DIR="$OUTPUT_DIR/package"

rm -rf "$PACKAGE_DIR" "$OUTPUT_DIR/lambda.zip"
mkdir -p "$PACKAGE_DIR"

echo "==> pip install requirements → package (this may take a few minutes)..."
python3 -m pip install -q -r "$PROJECT_ROOT/requirements.txt" -t "$PACKAGE_DIR" --upgrade

echo "==> Copy application code..."
cp -R "$PROJECT_ROOT/src" "$PACKAGE_DIR/"
cp -R "$PROJECT_ROOT/lambda" "$PACKAGE_DIR/"

# Drop dev-only / bulky paths if present inside package
rm -rf "$PACKAGE_DIR"/tests "$PACKAGE_DIR"/__pycache__ 2>/dev/null || true
find "$PACKAGE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "==> Zip..."
(cd "$PACKAGE_DIR" && zip -rq "$OUTPUT_DIR/lambda.zip" .)
echo "Built: $OUTPUT_DIR/lambda.zip"
ls -lh "$OUTPUT_DIR/lambda.zip"
