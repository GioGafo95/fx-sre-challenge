#!/usr/bin/env bash
# Stores the Banxico token (if BANXICO_TOKEN is set), runs the first ingestion and smoke-tests the site.
set -euo pipefail
export MSYS_NO_PATHCONV=1
ENV_NAME="${ENV_NAME:-prod}"; P="fx-$ENV_NAME"
# Git Bash maps /tmp to a Windows folder that the native aws.exe cannot see: hand it a Windows-style path.
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }

SECRET_ARN=$(out "$P-data" TokenSecretArn)
FN=$(out "$P-app" IngestFunctionName)
URL=$(out "$P-app" SiteUrl)

if [ -n "${BANXICO_TOKEN:-}" ]; then
  tmp=$(mktemp); chmod 600 "$tmp"; trap 'rm -f "$tmp"' EXIT
  printf '%s' "$BANXICO_TOKEN" > "$tmp"             # file:// keeps the token out of the process list
  aws secretsmanager put-secret-value --secret-id "$SECRET_ARN" --secret-string "file://$(winpath "$tmp")" >/dev/null
  echo "Token stored in Secrets Manager."
else
  echo "BANXICO_TOKEN not set. Store it with:"
  echo "  aws secretsmanager put-secret-value --secret-id $SECRET_ARN --secret-string file://token.txt"
  echo "then re-run this script."; exit 0
fi

echo "Running first ingestion (backfills 45 days)..."
RESP=$(mktemp); trap 'rm -f "$RESP" "${tmp:-}"' EXIT
aws lambda invoke --function-name "$FN" --payload '{}' --cli-binary-format raw-in-base64-out "$(winpath "$RESP")" \
  --query FunctionError --output text | grep -qv '^Unhandled$' || { cat "$RESP"; echo "Ingestion failed: check the token and the Lambda logs."; exit 1; }
cat "$RESP"; echo

echo "Smoke test: $URL/api/rates"
curl -fsS "$URL/api/rates" | head -c 300; echo
echo "Site: $URL"
