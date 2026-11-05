# USD/MXN Exchange Rate Website: Project Documentation

## 1. Objective and scope
A production-grade website that shows the official USD/MXN exchange rate from Banco de México (Banxico),
deployed with AWS CDK (which synthesizes CloudFormation) and operated against explicit SLOs.

In scope: the website and API, the data ingestion, the infrastructure as code, observability, SLO/SLI/error-budget
definitions, and a repeatable multi-account deployment. Out of scope: other currency pairs, user accounts, and
any write path from users.

Data source: Banxico SIE REST API, series **SF43718** (USD/MXN FIX rate, one value per business day, free token).

### How the objective is met
> Create a website to display essential currency exchange information, including current and historical USD exchange
> rates, along with defining Service Level Objectives (SLO), Service Level Indicators (SLI), and Error Budgets.

| Requirement | Delivered as | Where |
|---|---|---|
| Website | Static page on CloudFront + S3, one JSON API behind it, deployed to `dev` and `prod` by the pipeline | §3, `app/web/`, `docs/PIPELINE.md` |
| Current USD exchange rate | Latest Banxico FIX rate with its publication date, and a warning when it is more than 4 days old | §2, `GET /api/rates` → `current`, `stale` |
| Historical USD exchange rates | Every rate of the last 10 days (table + trend line) and the 15- and 30-day averages | §2, `GET /api/rates` → `history`, `average` |
| SLIs | Availability, latency, freshness and ingestion success, measured from outside through the public URL | §5, `infra/lambda/probe.py`, dashboard `fx-<env>-observability-slo` |
| SLOs | A target and a 30-day window for each SLI | §5 |
| Error budgets | Budget per SLO, burn-rate alerts that page or open a ticket, and a policy for what happens as it is spent | §5, `infra/fx_infra/observability_stack.py` |

## 2. Website features
| Feature | Definition |
|---|---|
| Current USD exchange | Most recent published FIX rate and its publication date. |
| Average, last 15 days | Mean of all published rates in the trailing 15 calendar days. |
| Average, last month | Mean of all published rates in the trailing 30 calendar days. |
| Past 10 days | Every published rate in the trailing 10 calendar days, newest first, plus a trend line. |

Notes. Banxico publishes on business days only, so a 10-day window holds about 7 or 8 rates. The page shows a warning
when the newest rate is more than 4 days old (a long weekend does not trigger it). If the data service fails, the page
shows an explicit error with a retry button.

API: `GET /api/rates` returns all of the above in one response, so a page view costs one request.

## 3. Architecture
```
Browser -> CloudFront (TLS, security headers, cache)
             |-- /*      -> S3 (static site, private, OAC)
             '-- /api/*  -> Lambda Function URL (IAM auth, OAC) -> API container (FastAPI) -> DynamoDB (read)

EventBridge (3 schedules) -> Ingest Lambda -> Banxico API -> DynamoDB (write)
                                  |-- token from Secrets Manager;  failures -> SQS DLQ -> alarm
EventBridge (every 5 min) -> Probe Lambda -> https://<cloudfront>/api/rates -> custom metrics (the SLIs)
CloudWatch: alarms (burn rate, latency, stale data, DLQ, errors) -> SNS -> email;  one dashboard
```

### Deviations from the suggested services, and why
The brief lists RDS, EC2, Lambda and VPC and invites changes where cost or performance improve.

| Suggested | Used | Reason |
|---|---|---|
| Aurora MySQL | DynamoDB on-demand | The dataset is about 30 rows. Aurora needs a VPC, subnets and (for outbound Banxico calls) a NAT gateway, and has a monthly floor cost. DynamoDB has no servers to patch, is multi-AZ by default, and has point-in-time recovery. |
| EC2 | Lambda (arm64 container image) | No instances to patch or scale. The same Docker image also runs on ECS/Fargate or locally, so the container requirement is kept. |
| VPC | None | Nothing here needs private networking. Origin access is controlled with IAM and CloudFront OAC instead, which removes NAT and endpoint costs. |
| (API Gateway) | Lambda Function URL + OAC | Same security (only CloudFront can invoke) with one fewer paid hop. |

If the reviewers want to see a relational database, the data layer is behind a small `store` interface;
an Aurora Serverless v2 implementation can be added without touching the API contract.

## 4. Production considerations
**Resilience**
- Every service used is multi-AZ by design. There are no single-instance components.
- CloudFront caches `/api/rates` for 5 minutes and serves stale data for up to 1 hour while revalidating and up to
  24 hours if the origin errors (`stale-while-revalidate`, `stale-if-error`). A short origin outage is invisible to users.
- The API never calls Banxico. Banxico being down only stops new data arriving; visitors keep getting the last rates
  with the stale flag raised after 4 days.
- Ingestion is idempotent (upserts keyed by date), retries twice, then lands in a dead-letter queue and alarms. It runs
  on three schedules a day (weekdays 12:30 and 15:30 Mexico City time, plus a daily 06:00 catch-up) and re-reads
  45 days each time, so a missed run heals itself.
- DynamoDB has point-in-time recovery, deletion protection and RETAIN. Stateful resources live in their own stack with
  termination protection, so an application change cannot affect data.
- Disaster recovery: the data is fully re-derivable from Banxico, so RPO is effectively zero and RTO is a redeploy plus
  one ingestion (about 15 minutes). A second region is not deployed because it would add cost for no benefit at this
  scale. See the suggestions section.

**Scalability.** Lambda, DynamoDB on-demand and CloudFront scale without configuration. Because responses are cached at
the edge, the origin sees roughly one request per 5 minutes per edge location, whatever the traffic.

**Security**
- No public origins. S3 and the Lambda URL accept traffic only from this CloudFront distribution (OAC); the Lambda URL
  requires SigV4.
- Least-privilege IAM: the API role can only read the table; the ingest role can only write it and read one secret.
- Banxico token in Secrets Manager, never in code, environment variables, images or templates. An existing secret can be
  referenced instead of created, so the token never passes through CloudFormation.
- HTTPS only, HSTS, a strict CSP (no inline scripts), nosniff, frame denial, no referrer. All storage is encrypted.
- Container runs as a non-root user; ECR scans images on push; dependencies are pinned.

**Cost (estimate, verify with the AWS pricing calculator).** Lambda, DynamoDB, S3 and CloudFront stay within or near
free tier at low traffic. The recurring cost is mostly CloudWatch: eleven alarms, three composite alarms, six custom
metrics and a dashboard, plus one secret. The one-minute probe stays within the Lambda free tier. Expect roughly **USD 5 to 8 per month**. Levers already used: arm64, on-demand
DynamoDB, no NAT/ALB/API Gateway, edge caching, 30-day log retention, CloudFront price class 100.

**Operability.** Everything is code. Structured logs, X-Ray tracing on all functions, one dashboard, and alarms with
runbook-style descriptions. Resource names are generated, so the same code deploys to any number of accounts without
name collisions.

**Delivery.** A GitHub Actions pipeline (`docs/PIPELINE.md`) tests and synthesizes every pull request, and every merge to
`main` deploys `dev`, then `prod` after a manual approval. It authenticates to AWS through OIDC, so no AWS keys are
stored in GitHub, and each environment can only be deployed by jobs running in its own GitHub Environment.

## 5. SLIs, SLOs and error budgets
Definitions. An **SLI** is a measured ratio of good events to valid events. An **SLO** is the target for that ratio over
a window. The **error budget** is `1 - SLO`: the amount of unreliability we are allowed to spend.

The user-facing SLIs come from a synthetic probe that calls the public URL **every minute**, through CloudFront, because
that is what a visitor experiences. Ingestion success comes from the ingestion runs themselves. All targets, thresholds
and alert rules are defined once, in `infra/fx_infra/observability_stack.py`.

| # | SLI: good event / valid events | SLO (30 days) | Error budget (30 days) | Alerting |
|---|---|---|---|---|
| 1 | **Availability**: probes that get HTTP 200 with a well-formed payload containing a current rate / all probes | 99.9% | 0.1%: about 43 failed probes (43 minutes) | Page (fast burn) and ticket (slow burn), below |
| 2 | **Latency**: successful probes with a round trip of at most 800 ms / successful probes | 99% | 1%: about 430 slow probes | Ticket when 6x the budget burns over both 6 h and 30 min |
| 3 | **Freshness**: probes that see a newest rate no older than 4 days / all probes | 99% | 1%: about 7 hours of stale data | Ticket after 3 hours stale |
| 4 | **Ingestion success**: ingestion runs that succeed, after retries / all runs (success + dead-letter queue) | 99% | 1%: with about 55 runs a month, one failed run spends the month's budget | Ticket on any message in the DLQ |

Notes.
- A failed probe counts against availability only; latency is measured over successful probes, so one incident is not
  counted twice.
- Ingestion is deliberately strict: every run re-reads 45 days, so one failed run causes no data loss, but it should
  still be looked at. Freshness (SLO 3) is the SLO that measures the user impact of ingestion problems.
- Freshness tolerates long weekends: Banxico does not publish on weekends or holidays, so data up to 4 days old is normal.
- The dashboard `fx-<env>-observability-slo` shows, for each SLO, the 30-day SLI and the **budget remaining**
  (`1 - spent / allowed`; 100% = untouched, 0% or below = exhausted).

### Burn-rate alerting for SLO 1
Burn rate is how fast the budget is being spent compared with spending it evenly over 30 days.

| Alert | Burn rate | Long window | Short window | Extra condition | Meaning | Action |
|---|---|---|---|---|---|---|
| Fast burn | 14.4x (error ratio above 1.44%) | 1 h | 5 min | 3 consecutive failed probes | 2% of the monthly budget gone in an hour | **Page** |
| Slow burn | 6x (error ratio above 0.6%) | 6 h | 30 min | none | 5% of the budget gone in 6 hours | Ticket |

Both windows must exceed the threshold (composite alarm). The long window avoids alerting on blips; the short window
makes the alert clear quickly once the problem is fixed. Missing probe data counts as failing, because being blind is an
incident.

Why the extra condition: with one probe per minute, a single failed probe is already 1/60 = 1.7% of the hour, above the
1.44% fast-burn threshold, so pure burn-rate maths would page on one blip. Requiring 3 consecutive failures means a
transient error never pages, while a real outage pages after about 3 minutes and has spent about 7% of the monthly
budget by then. The slow-burn ticket still catches repeated short blips.

### Error budget policy
- Budget remaining above 50%: ship normally.
- Below 50%: changes need a rollback plan and are deployed one at a time.
- Exhausted: freeze feature changes; only reliability work and fixes until the budget recovers.
- Every fast-burn page gets a short blameless review.

## 6. Suggestions beyond the brief
1. **WAF** with a rate-based rule on the distribution (about USD 6/month). Left out by default for cost; CloudFront
   caching and Lambda scaling already absorb ordinary load.
2. **Multi-region DR**: DynamoDB global table plus a second stack, with Route 53 failover. Worth it only if the
   business needs an RTO under minutes.
3. **`cdk-nag`** in CI, to fail pull requests that introduce insecure infrastructure (the OIDC deploy pipeline itself
   is already in place).
4. **Reserved concurrency** on the API once the account quota is raised above the new-account default of 10.
5. A second data source (for example another provider) to cross-check the Banxico rate and alert on divergence.
