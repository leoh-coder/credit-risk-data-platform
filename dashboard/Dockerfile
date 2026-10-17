FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/dashboard/src:/app/ml/src \
    ARTIFACTS_DIR=/artifacts

# Repo layout, so `-r ../ml/requirements.txt` resolves; ml comes from the `ml` additional build context.
WORKDIR /app/dashboard
COPY --from=ml requirements.txt /app/ml/requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 app
USER app

COPY --from=ml src/ /app/ml/src/
COPY src/ ./src/

EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=2)"

CMD ["python", "-m", "streamlit", "run", "src/dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
