FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY polymarket/requirements.txt /app/polymarket/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/polymarket/requirements.txt

COPY . /app

# run_live.sh expects $PYTHON; override the venv default with the system python
ENV PYTHON=/usr/local/bin/python

WORKDIR /app/polymarket

CMD ["bash", "run_live.sh"]
