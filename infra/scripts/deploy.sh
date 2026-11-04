#!/usr/bin/env bash
# Usage: scripts/deploy.sh <aws-profile> [region] [env_name]
# Example: scripts/deploy.sh client-prod us-east-2 prod
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: stop it rewriting "/..." arguments
cd "$(dirname "$0")/.."
PROFILE="${1:?usage: deploy.sh <aws-profile> [region] [env_name]}"
export AWS_PROFILE="$PROFILE"
export AWS_REGION="${2:-us-east-2}" AWS_DEFAULT_REGION="${2:-us-east-2}" CDK_DEFAULT_REGION="${2:-us-east-2}"
ENV_NAME="${3:-prod}"
export ENV_NAME

./scripts/preflight.sh

EXTRA=()
[ -n "${ALERT_EMAIL:-}" ] && EXTRA+=(-c "alert_email=$ALERT_EMAIL")
[ -n "${BANXICO_TOKEN_SECRET_ARN:-}" ] && EXTRA+=(-c "banxico_token_secret_arn=$BANXICO_TOKEN_SECRET_ARN")
[ -n "${DOMAIN_NAME:-}" ] && EXTRA+=(-c "domain_name=$DOMAIN_NAME" -c "certificate_arn=${CERTIFICATE_ARN:?set CERTIFICATE_ARN (us-east-1)}")

PY="$(command -v python || command -v python3)"   # Windows Git Bash: "python3" is often a Store stub
[ -d .venv ] || "$PY" -m venv .venv
# Windows venvs use Scripts/, Linux/macOS use bin/. cdk.json runs "python app.py": it must see aws-cdk-lib.
if [ -f .venv/Scripts/activate ]; then . .venv/Scripts/activate; else . .venv/bin/activate; fi
pip install -q -r requirements.txt

cdk deploy --all --require-approval broadening -c "env_name=$ENV_NAME" -c "region=$AWS_REGION" ${EXTRA[@]+"${EXTRA[@]}"}   # safe with empty arrays on macOS bash 3.2
./scripts/post-deploy.sh
