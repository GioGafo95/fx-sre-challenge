# USD/MXN rates: SRE challenge

- `app/`    API + frontend + Dockerfile (see `app/README.md`)
- `infra/`  AWS CDK (Python): data, app and observability stacks, plus deploy scripts
- `docs/PROJECT_DOCUMENTATION.md`  scope, features, architecture, SLO/SLI/error budgets
- `docs/DEPLOYMENT.md`  step-by-step runbook for each AWS account (manual)
- `docs/PIPELINE.md`  automated deployment with GitHub Actions + OIDC

Quick start: `cd app && PYTHONPATH=src pytest` and `cd infra && pytest tests`, then follow `docs/DEPLOYMENT.md`.
