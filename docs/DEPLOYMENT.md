# Deployment runbook (repeat for every AWS account)

Stacks per account/region: `fx-<env>-data` (table, secret) -> `fx-<env>-app` (Lambdas, CloudFront, S3) ->
`fx-<env>-observability` (probe, alarms, dashboard). Default `<env>` is `prod`, default region `us-east-2`.

## Prerequisites on the machine doing the deploy
Node 20+, Python 3.12+, AWS CLI v2, AWS CDK CLI (`npm i -g aws-cdk`), Docker with linux/arm64 support
(Docker Desktop has it; on x86 Linux run `docker run --privileged --rm tonistiigi/binfmt --install arm64`).
A free Banxico API token: https://www.banxico.org.mx/SieAPIRest/service/v1/token

## Steps
1. **Credentials.** Configure a profile for the target account and check it:
   `aws sts get-caller-identity --profile <profile>`. Permissions: administrator, or equivalent CDK deploy rights.
2. **Bootstrap (once per account and region).**
   `cdk bootstrap aws://<ACCOUNT_ID>/<REGION> --profile <profile>`
   Container images are published to the bootstrap ECR repository automatically during deploy.
3. **Choose parameters** (all optional; environment variables read by `scripts/deploy.sh`):

   | Variable | Purpose | Default |
   |---|---|---|
   | `ALERT_EMAIL` | Address subscribed to alarms (confirm the SNS email) | none |
   | `BANXICO_TOKEN` | Token stored in Secrets Manager by post-deploy | set manually later |
   | `BANXICO_TOKEN_SECRET_ARN` | Use an existing secret instead of creating one | create |
   | `DOMAIN_NAME` + `CERTIFICATE_ARN` | Custom domain; the ACM certificate must be in **us-east-1** | CloudFront domain |
   | `CDK_QUALIFIER` | Only if the account was bootstrapped with a custom qualifier | `hnb659fds` |

   Probe frequency: `-c probe_rate_minutes=10` lowers cost slightly.
4. **Deploy.**
   ```bash
   cd infra
   export ALERT_EMAIL=oncall@example.com BANXICO_TOKEN=<token>
   ./scripts/deploy.sh <profile> <region> prod
   ```
   The script runs `preflight.sh` first and stops on any failure, then `cdk deploy --all`, then `post-deploy.sh`.
   Preflight checks: tools, Docker arm64, credentials, region, bootstrap version, Lambda quota, CloudFront access,
   token secret.
5. **Review before deploying** (optional, as the brief describes): `cdk synth` writes the CloudFormation templates to
   `infra/cdk.out/`; `cdk diff` shows changes against a deployed account.
6. **Token** (only if `BANXICO_TOKEN` was not set in step 4):
   `aws secretsmanager put-secret-value --secret-id <TokenSecretArn output> --secret-string file://token.txt`
   then `./scripts/post-deploy.sh`.
7. **Verify.** The site URL is printed at the end. Check that `/api/rates` returns data, the dashboard
   `fx-<env>-observability-slo` shows probe metrics within about 10 minutes, and the SNS subscription is confirmed.

## Rollback and teardown
- Rollback: redeploy the previous git revision with the same command (CloudFormation rolls back failed updates itself).
- Teardown: `cdk destroy fx-<env>-observability fx-<env>-app`. The data stack has termination protection and a
  retained table by design; remove it deliberately (disable protection, then delete) only when the data is not needed.

## Common failures
| Symptom | Cause | Fix |
|---|---|---|
| "bootstrap stack version" or missing SSM parameter | Account/region not bootstrapped | Step 2 |
| Docker build fails with "exec format error" | No arm64 emulation | Install binfmt (see prerequisites) |
| CloudFront "account must be verified" | New AWS account | Contact AWS Support to enable CloudFront |
| Lambda throttles under load | Quota of 10 concurrent executions (new accounts) | Request a quota increase |
| Ingestion fails with HTTP 401 | Placeholder or wrong token | Step 6 |
| Custom domain deploy fails | Certificate not in us-east-1, or not validated | Issue it in us-east-1 |
| Deploy denied | SCP or missing IAM permission in the target account | Check the CloudFormation event for the denied action |
| Alarm "no data" right after deploy | Probe has not run yet | Wait 10 minutes |
