import re
from pathlib import Path

from dashboard.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_default_postgres_host_is_the_published_compose_address():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.search(r'"([0-9.]+):5432:5432"', compose).group(1)
    assert Settings.model_fields["postgres_host"].default == published
