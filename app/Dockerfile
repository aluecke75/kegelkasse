FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y wget && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p \
    /app/database \
    /app/backups \
    /app/logs

EXPOSE 5000

CMD ["python", "app.py"]