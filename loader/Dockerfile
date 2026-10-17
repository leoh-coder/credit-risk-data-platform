FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 app
USER app

# Extensions are baked in, pinned with the duckdb wheel: runs need no network access.
RUN python -c "import duckdb; con = duckdb.connect(); [con.install_extension(e) for e in ('postgres', 'httpfs')]"

COPY src/ ./src/

ENTRYPOINT ["python", "-m", "loader.main"]
CMD ["run"]
