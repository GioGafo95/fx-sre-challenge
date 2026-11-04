#!/usr/bin/env bash
# Run before every deployment, once per account/region. Exits non-zero if something would make the deploy fail.
set -uo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows rewrites arguments starting with "/" into C:/... paths
fail=0
ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  warn  $*"; }
QUALIFIER="${CDK_QUALIFIER:-hnb659fds}"

echo "Tools"
for t in aws node docker cdk; do command -v "$t" >/dev/null && ok "$t found" || bad "$t not installed"; done
# The Windows Store "python3" alias is a stub that opens the Store: test that Python really runs.
PYV=$(python --version 2>&1 || python3 --version 2>&1); case "$PYV" in "Python 3."*) ok "$PYV";; *) bad "working Python 3 not found (install from python.org and tick Add to PATH)";; esac
command -v cdk >/dev/null && ok "cdk $(cdk --version | cut -d' ' -f1)"
if command -v docker >/dev/null; then
  docker info >/dev/null 2>&1 && ok "docker daemon running" || bad "docker daemon not running (needed to build the image)"
  docker buildx inspect 2>/dev/null | grep -q "linux/arm64" && ok "buildx can build linux/arm64" \
    || bad "no linux/arm64 support in buildx. On x86 run: docker run --privileged --rm tonistiigi/binfmt --install arm64"
fi

echo "AWS identity"
ident=$(aws sts get-caller-identity --output text --query '[Account,Arn]' 2>&1) || { bad "no valid AWS credentials: $ident"; exit 1; }
ACCOUNT=$(echo "$ident" | cut -f1); ok "account $ACCOUNT ($(echo "$ident" | cut -f2))"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null)}}"
[ -n "$REGION" ] && ok "region $REGION" || { bad "no region set (export AWS_REGION=...)"; exit 1; }

echo "Account readiness"
if aws ssm get-parameter --name "/cdk-bootstrap/$QUALIFIER/version" --region "$REGION" >/dev/null 2>&1; then
  ok "CDK bootstrapped (qualifier $QUALIFIER)"
else
  # Fallback: the SSM read can be denied by IAM even when bootstrap is fine, so check the stack itself.
  st=$(aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)
  case "$st" in
    CREATE_COMPLETE|UPDATE_COMPLETE) ok "CDK bootstrapped (CDKToolkit stack $st)";;
    *) bad "not bootstrapped (CDKToolkit status: ${st:-not found}): cdk bootstrap aws://$ACCOUNT/$REGION";;
  esac
fi
conc=$(aws lambda get-account-settings --region "$REGION" --query 'AccountLimit.ConcurrentExecutions' --output text 2>/dev/null || echo "")
if [ -z "$conc" ]; then warn "could not read Lambda quota"
elif [ "$conc" -lt 50 ]; then warn "Lambda concurrency quota is $conc (new-account default is 10). It works, but request an increase before real traffic."
else ok "Lambda concurrency quota $conc"; fi
aws cloudfront list-distributions --query 'DistributionList.Quantity' --output text >/dev/null 2>&1 \
  && ok "CloudFront accessible" || bad "CloudFront not accessible (new accounts may need verification, or an SCP blocks it)"

if [ -n "${BANXICO_TOKEN_SECRET_ARN:-}" ]; then
  aws secretsmanager describe-secret --secret-id "$BANXICO_TOKEN_SECRET_ARN" --region "$REGION" >/dev/null 2>&1 \
    && ok "existing token secret found" || bad "BANXICO_TOKEN_SECRET_ARN not found in $REGION"
fi
[ -n "${BANXICO_TOKEN:-}" ] && ok "BANXICO_TOKEN set (post-deploy will store it)" \
  || warn "BANXICO_TOKEN not set: set the secret manually after deploy (see docs/DEPLOYMENT.md step 6)"

echo; [ "$fail" -eq 0 ] && echo "Preflight passed." || echo "Preflight FAILED."; exit "$fail"
