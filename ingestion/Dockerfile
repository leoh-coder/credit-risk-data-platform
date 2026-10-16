FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes,
# so editing the code does not trigger a full reinstall.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/

# Never run containers as root.
RUN useradd --create-home --uid 1000 app
USER app

ENTRYPOINT ["python", "-m", "ingestion.main"]
CMD ["run"]
