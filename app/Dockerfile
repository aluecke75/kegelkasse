# syntax=docker/dockerfile:1
FROM python:3.12-slim AS deps

# DeveloperKit liegt in einem privaten GitHub-Repo. Der Token wird per
# BuildKit-Secret eingebunden (--secret id=gh_token beim Build), landet
# dadurch weder im Image noch in der Build-Historie eines Layers - anders
# als ein ARG, das dauerhaft in den Layer-Metadaten sichtbar bliebe.
ARG DEVELOPERKIT_VERSION=v0.1.10

WORKDIR /build

RUN apt-get update && \
    apt-get install -y git && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN --mount=type=secret,id=gh_token \
    pip install --no-cache-dir "git+https://$(cat /run/secrets/gh_token)@github.com/aluecke75/DeveloperKit.git@${DEVELOPERKIT_VERSION}#egg=developerkit"


FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y wget && \
    rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

RUN mkdir -p \
    /app/database \
    /app/backups \
    /app/logs

EXPOSE 5000

CMD ["python", "run.py"]
