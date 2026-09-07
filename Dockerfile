FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY output ./output
COPY tests/fixtures ./tests/fixtures

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 \
    FLOWPII_HOST=0.0.0.0 \
    FLOWPII_PORT=8000

EXPOSE 8000

CMD ["uvicorn", "flowpii.api:app", "--host", "0.0.0.0", "--port", "8000"]
