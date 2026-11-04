# Deploying with GitHub Actions

## How it works
```
pull request / any branch  -> ci: app tests, infra tests, full cdk synth        (no AWS access)
push to main               -> ci -> deploy dev -> (approval) -> deploy prod
```
- Each GitHub **Environment** (`dev`, `prod`, ...) maps to one AWS account and holds its own settings, so adding an
  account means adding an Environment, not editing code.
- Authentication is **OIDC**: GitHub proves who it is with a short-lived token, and AWS lets only this repository, running
  in that Environment, assume a role. There are no AWS keys stored in GitHub.
- The role can only assume the CDK bootstrap roles and read stack outputs. CDK itself does the deployment.
- A smoke test runs after every deploy: the page must return 200 and the API must return 200 (data) or 503 (reachable,
  no data yet). Anything else fails the pipeline.

## One-time setup per AWS account
1. Bootstrap CDK (already done for your test account): `cdk bootstrap aws://<ACCOUNT>/<REGION>`
2. Create the deploy role (run with admin credentials for that account):
   ```bash
   aws cloudformation deploy --stack-name fx-github-oidc \
     --template-file infra/pipeline/github-oidc-role.yaml --capabilities CAPABILITY_IAM \
     --parameter-overrides GitHubOrg=<org-or-user> GitHubRepo=<repo> EnvironmentName=<dev|prod>
   aws cloudformation describe-stacks --stack-name fx-github-oidc \
     --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text
   ```
   If the account already has a GitHub OIDC provider, add `CreateOidcProvider=false`. In Git Bash on Windows,
   run `export MSYS_NO_PATHCONV=1` first.
3. In GitHub: **Settings -> Environments -> New environment** (`dev`, `prod`). Add these **variables** (not secrets):

   | Variable | Value |
   |---|---|
   | `AWS_ROLE_ARN` | the RoleArn from step 2 |
   | `AWS_REGION` | for example `us-east-1` |
   | `ENV_NAME` | for example `prod` (stacks become `fx-<ENV_NAME>-*`) |
   | `ALERT_EMAIL` | optional |
   | `BANXICO_TOKEN_SECRET_ARN` | optional: an existing secret holding the token |

   On `prod`, enable **Required reviewers**: the pipeline then waits for a person before touching production.
4. Push the repository to GitHub. The first push to `main` runs the whole pipeline.
5. **Store the Banxico token once per account** (the pipeline never sees it) and run the first ingestion:
   ```bash
   aws secretsmanager put-secret-value --secret-id <TokenSecretArn> --secret-string file://token.tmp
   aws lambda invoke --function-name <IngestFunctionName> --payload '{}' --cli-binary-format raw-in-base64-out out.json
   ```

## Day to day
- Open a pull request: tests and synthesis run automatically. Merge only when green.
- Rollback: revert the commit on `main`; the pipeline redeploys the previous version.
- Pin the actions in `.github/workflows` to commit SHAs for supply-chain safety once the pipeline is stable.

## Notes and limits
- The arm64 image is built on an x86 runner through QEMU emulation, which is slower than a native arm64 runner
  (about 5 to 10 minutes). Switch `runs-on` to an arm64 runner if you have access.
- The pipeline deploys applications, not account bootstrap or the OIDC role: those stay manual and one-off by design,
  because they need administrator rights.
- GitLab CI or AWS CodePipeline versions follow the same structure; ask if you need one.
