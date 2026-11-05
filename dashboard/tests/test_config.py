import re
from pathlib import Path

from dashboard.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_default_postgres_host_is_the_published_compose_address():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.search(r'"([0-9.]+):5432:5432"', compose).group(1)
    assert Settings.model_fields["postgres_host"].default == published


def test_container_shows_the_published_address_instead_of_looking_up_the_public_ip():
    # Without browser.serverAddress, a headless Streamlit asks an external service for the public IP.
    dockerfile = (ROOT / "dashboard" / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    shown = re.search(r'"--browser\.serverAddress=([0-9.]+)"', dockerfile)
    assert shown, "the dashboard image does not set browser.serverAddress"
    assert shown.group(1) == re.search(r'"([0-9.]+):8501:8501"', compose).group(1)
