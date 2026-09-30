#!/usr/bin/env bash
# T-015: seeds LocalStack with 1 SQS queue + 1 SNS topic for mcp-sqs-sns dev/test
# (architecture.md "Hạ tầng dev/test": "LocalStack có 1 queue + 1 topic mẫu").
# LocalStack runs this automatically on startup (mounted at
# /etc/localstack/init/ready.d/init.sh).
set -euo pipefail

REGION="${DEFAULT_REGION:-ap-southeast-1}"
ENDPOINT="http://localhost:4566"

awslocal() {
  aws --endpoint-url="$ENDPOINT" --region="$REGION" "$@"
}

echo "[init.sh] creating dev queue mcp-dev-queue..."
awslocal sqs create-queue --queue-name mcp-dev-queue

echo "[init.sh] creating dev topic mcp-dev-topic..."
awslocal sns create-topic --name mcp-dev-topic

echo "[init.sh] done."
