import re
from pathlib import Path

from ml.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_default_postgres_host_is_the_published_compose_address():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.search(r'"([0-9.]+):5432:5432"', compose).group(1)
    assert Settings.model_fields["postgres_host"].default == published


def test_split_periods_reach_the_ml_container_with_the_code_defaults():
    # The container has no .env: a setting the compose file does not pass is silently ignored.
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    ml_service = re.search(r"^  ml:\n(.*?)(?=^  \S)", compose, re.M | re.S).group(1)
    for name in ("VALIDATION_MONTHS", "TEST_MONTHS"):
        passed = re.search(rf"^\s+{name}: \$\{{{name}:-(\d+)\}}$", ml_service, re.M)
        assert passed, f"docker-compose.yml does not pass {name} to the ml job"
        assert int(passed.group(1)) == Settings.model_fields[name.lower()].default
