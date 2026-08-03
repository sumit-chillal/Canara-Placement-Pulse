#!/usr/bin/env bash
# Build the AWS Lambda deployment zip.
#
# Usage:  ./scripts/build-lambda-zip.sh [output_path]
#         defaults to /app/tmp/pp-backend-lambda.zip
#
# Produces a zip with backend/ code + all Python deps at the root,
# ready for `aws lambda update-function-code --zip-file fileb://...`
# or Terraform / SAM deployment.
set -euo pipefail

OUT="${1:-/app/tmp/pp-backend-lambda.zip}"
STAGE="/app/tmp/lambda-stage"

mkdir -p "$(dirname "$OUT")"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "→ installing deps into $STAGE"
pip install --quiet --no-cache-dir \
  --platform manylinux2014_x86_64 \
  --target "$STAGE" \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  -r /app/backend/requirements.txt

echo "→ copying backend source"
cp -r /app/backend/*.py "$STAGE/"

# The service-account JSON is a secret and MUST NOT ship in the zip.
# In production, mount it from AWS Secrets Manager and set
# FIREBASE_SERVICE_ACCOUNT_PATH to the mount target (e.g.
# /tmp/firebase-admin.json written from a Secrets extension).
echo "→ stripping any accidental secrets"
find "$STAGE" -name "firebase-admin*.json" -delete
find "$STAGE" -name ".env*" -delete

echo "→ zipping"
rm -f "$OUT"
(cd "$STAGE" && zip -rq "$OUT" .)

SIZE=$(du -h "$OUT" | cut -f1)
echo "OK  $OUT  ($SIZE)"
