FROM python:3.12-slim AS deps

# DeveloperKit liegt in einem privaten GitHub-Repo. GITHUB_TOKEN wird nur in
# dieser Build-Stage verwendet, um es zu installieren, und landet dadurch
# nicht im finalen Image (siehe FROM weiter unten, das nur das fertige venv
# kopiert, nicht diese Stage). Gleiches Muster wie beim KleinvermieterLotse.
ARG GITHUB_TOKEN
ARG DEVELOPERKIT_VERSION=v0.1.9

WORKDIR /build

RUN apt-get update && \
    apt-get install -y git && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir "git+https://${GITHUB_TOKEN}@github.com/aluecke75/DeveloperKit.git@${DEVELOPERKIT_VERSION}#egg=developerkit"


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
