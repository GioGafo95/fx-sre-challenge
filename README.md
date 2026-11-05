# USD/MXN rates: SRE challenge

- `app/`    API + frontend + Dockerfile (see `app/README.md`)
- `infra/`  AWS CDK (Python): data, app and observability stacks, plus deploy scripts
- `docs/PROJECT_DOCUMENTATION.md`  scope, features, architecture, SLO/SLI/error budgets
- `docs/PIPELINE.md`  how to deploy: GitHub Actions + OIDC, `dev` then `prod` with approval (recommended)
- `docs/DEPLOYMENT.md`  manual deployment from a workstation (alternative)

Quick start: `cd app && PYTHONPATH=src pytest` and `cd infra && pytest tests`, then follow `docs/PIPELINE.md`.
