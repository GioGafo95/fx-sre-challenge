# syntax=docker/dockerfile:1
# One image, three ways to run: Lambda (via Web Adapter), ECS/Fargate, or locally.
FROM public.ecr.aws/docker/library/python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM public.ecr.aws/docker/library/python:3.12-slim
# Lambda Web Adapter: lets the same HTTP container run on Lambda with no code changes.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8080 AWS_LWA_PORT=8080 AWS_LWA_READINESS_CHECK_PATH=/healthz \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
COPY --from=build /install /usr/local
WORKDIR /app
COPY src ./src
COPY web ./web
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "fx.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
