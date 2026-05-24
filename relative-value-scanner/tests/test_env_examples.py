from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER_ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


SECRETISH_VALUE_RE = re.compile(
    r"^[A-Z0-9_]*(KEY|TOKEN|SECRET|PRIVATE|PASSWORD|CREDENTIAL)[A-Z0-9_]*="
    r"[A-Za-z0-9_\-]{12,}$"
)


def _active_assignment_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0])
    return names


def test_env_examples_exist_and_do_not_require_real_env_files() -> None:
    assert SCANNER_ENV_EXAMPLE.exists()


def test_env_examples_do_not_contain_obvious_secret_values() -> None:
    for line in SCANNER_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        assert not SECRETISH_VALUE_RE.match(line.strip()), f"secret-looking value in {SCANNER_ENV_EXAMPLE}: {line}"


def test_scanner_env_example_covers_source_readiness_env_and_boundaries() -> None:
    text = SCANNER_ENV_EXAMPLE.read_text(encoding="utf-8")
    names = _active_assignment_names(SCANNER_ENV_EXAMPLE)

    assert {
        "THE_ODDS_API_KEY",
        "KALSHI_BASE_URL",
        "POLYMARKET_GAMMA_BASE_URL",
        "SX_BET_BASE_URL",
        "IBKR_HOST",
        "PROPHETX_API_KEY",
        "CRYPTO_COM_API_KEY",
        "ROBINHOOD_ENABLED",
    }.issubset(names)
    assert "REFERENCE_ONLY" in text
    assert "READ-ONLY" in text
    assert "PLANNED_NOT_IMPLEMENTED" in text
    assert "DO_NOT_USE_YET" in text
    assert "wallet" in text.lower()
    assert "private-key" in text.lower()
