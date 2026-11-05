# Deploying with GitHub Actions (recommended)

This is the normal way to deploy. `docs/DEPLOYMENT.md` describes the manual alternative from a workstation; both create
the same stacks, so pick one per environment and stick to it.

## How it works
```
pull request / any branch  -> ci: app tests, infra tests, full cdk synth        (no AWS access)
push to main               -> ci -> deploy dev -> (approval) -> deploy prod
```
- Each GitHub **Environment** (`dev`, `prod`, ...) maps to one AWS account and region and holds its own settings, so
  adding an environment means adding a GitHub Environment, not editing code. Stacks are named `fx-<ENV_NAME>-*`.
- Authentication is **OIDC**: GitHub proves who it is with a short-lived token, and AWS lets only this repository, running
  in that Environment, assume a role. There are no AWS keys stored in GitHub.
- The role can only assume the CDK bootstrap roles and read stack outputs. CDK itself does the deployment.
- A smoke test runs after every deploy: the page must return 200 and the API must return 200 (data) or 503 (reachable,
  no data yet). Anything else fails the pipeline.

## One-time setup per environment
Run steps 1, 2 and 5 with administrator credentials for the target account, from the repository root.
In Git Bash on Windows, run `export MSYS_NO_PATHCONV=1` first.

1. **Bootstrap CDK** (once per account and region; skip if `CDKToolkit` already exists):
   ```bash
   aws cloudformation describe-stacks --stack-name CDKToolkit --region <REGION>
   cdk bootstrap aws://<ACCOUNT>/<REGION>
   ```

2. **Create the deploy role**, one stack per environment so `dev` and `prod` do not overwrite each other:
   ```bash
   aws cloudformation deploy --stack-name fx-github-oidc-<env> --region <REGION> \
     --template-file infra/pipeline/github-oidc-role.yaml --capabilities CAPABILITY_IAM \
     --parameter-overrides GitHubOrg=<owner> GitHubRepo=<repo> EnvironmentName=<env> \
       GitHubOrgId=<owner-id> GitHubRepoId=<repo-id>
   aws cloudformation describe-stacks --stack-name fx-github-oidc-<env> --region <REGION> \
     --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text
   ```
   - Owner and repo names are **case-sensitive** and must match GitHub exactly.
   - `GitHubOrgId` / `GitHubRepoId`: this repository's OIDC `sub` claim includes numeric IDs
     (`repo:Owner@123/Repo@456:environment:dev`). Get them with `curl -s https://api.github.com/repos/<owner>/<repo>`
     (`owner.id` and `id`). Leave both out only if the claim is the classic `repo:Owner/Repo:environment:dev`.
   - An account can have only one GitHub OIDC provider: add `CreateOidcProvider=false` to every stack after the first
     one in the same account.

3. **Create the GitHub Environment**: **Settings -> Environments -> New environment** (`dev`, `prod`). Add these
   **variables** (not secrets):

   | Variable | Required | Value |
   |---|---|---|
   | `AWS_ROLE_ARN` | yes | the RoleArn from step 2 |
   | `AWS_REGION` | yes | for example `us-east-1` |
   | `ENV_NAME` | yes | `dev`, `prod`, ... The deploy stops if it is empty, so one environment can never overwrite another's stacks. |
   | `ALERT_EMAIL` | yes for `prod` | address subscribed to alarms. Without it alarms notify nobody (the deploy warns). Confirm the SNS email once after the first deploy. |
   | `BANXICO_TOKEN_SECRET_ARN` | no | an existing secret holding the token, instead of the one the data stack creates |

   On `prod`, also set:
   - **Required reviewers**: the pipeline waits for an approval before touching production.
   - **Deployment branches**: only `main`.

4. **Run the pipeline**: push to `main`, or **Actions -> deploy -> Run workflow**. Approve the `prod` job when asked.

5. **Store the Banxico token and run the first ingestion** (once per environment; the pipeline never sees the token).
   Until this is done the API returns 503 and the page shows "no data":
   ```bash
   read -rsp "Banxico token: " BANXICO_TOKEN; echo; export BANXICO_TOKEN
   ENV_NAME=<env> AWS_REGION=<REGION> ./infra/scripts/post-deploy.sh; unset BANXICO_TOKEN
   ```
   `post-deploy.sh` stores the token in Secrets Manager, invokes the ingest function and smoke-tests the site.

6. **Protect the one-off stacks** so a teardown cannot remove them:
   ```bash
   for s in CDKToolkit fx-github-oidc-<env>; do
     aws cloudformation update-termination-protection --enable-termination-protection --stack-name "$s" --region <REGION>
   done
   ```

## Day to day
- Open a pull request: tests and synthesis run automatically. Merge only when green.
- Rollback: revert the commit on `main`; the pipeline redeploys the previous version.

## Troubleshooting
| Symptom in the workflow log | Cause | Fix |
|---|---|---|
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The token's `sub` claim does not match the role's trust policy (name case, missing IDs, wrong Environment name), or `AWS_ROLE_ARN` points to an old role | Compare with the claim AWS received (`userName` in the output of the command below) and redeploy the role stack with matching parameters |
| `current credentials could not be used to assume 'cdk-hnb659fds-...-role'` | The account/region is not bootstrapped (`CDKToolkit` missing or deleted) | Step 1 |
| Stacks named `fx-prod-*` created by the `dev` job | `ENV_NAME` missing in that Environment (older pipeline versions fell back to `prod`) | Step 3 |
| Smoke test gets 503 from `/api/rates` | No data yet | Step 5 |

```bash
aws cloudtrail lookup-events --region <REGION> --max-results 3 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --query "Events[].CloudTrailEvent" --output text
```

## Notes and limits
- The arm64 image is built on an x86 runner through QEMU emulation, which is slower than a native arm64 runner
  (about 5 to 10 minutes). Switch `runs-on` to an arm64 runner if you have access.
- The pipeline deploys the application stacks only. Bootstrap and the OIDC role stay manual and one-off by design,
  because they need administrator rights.
