"""Automated Acceptance Test for Acceptance Criterion CT-02: Zero Hardcoded Secrets Enforcement.

Verifies that:
1. docker-compose.yml defines all services without hardcoded plaintext secrets.
   All sensitive credentials must be injected dynamically via environment variables (${...}).
2. .env.example strictly contains placeholder values ('changeme') for secrets.
3. .gitignore excludes .env to prevent local credential leakage into version control.
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"

SENSITIVE_KEYWORDS = ("PASSWORD", "SECRET", "KEY", "PASS")


def test_docker_compose_enforces_zero_hardcoded_secrets() -> None:
    """CT-02: Assert all sensitive environment values in docker-compose.yml use interpolation."""
    assert COMPOSE_PATH.is_file(), f"docker-compose.yml not found at {COMPOSE_PATH}"

    with open(COMPOSE_PATH, encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    assert len(services) > 0, "No services defined in docker-compose.yml"

    for service_name, service_cfg in services.items():
        env = service_cfg.get("environment", {})
        if isinstance(env, dict):
            env_items = env.items()
        elif isinstance(env, list):
            env_items = [item.split("=", 1) if "=" in item else (item, "") for item in env]
        else:
            continue

        for key, val in env_items:
            key_upper = key.upper()
            if any(keyword in key_upper for keyword in SENSITIVE_KEYWORDS):
                val_str = str(val).strip()
                # Must be an interpolated variable like ${VAR:?error} or ${VAR:-default}
                assert val_str.startswith("${") and val_str.endswith("}"), (
                    f"CT-02 Violation in service '{service_name}': sensitive variable '{key}' "
                    f"has hardcoded plaintext value '{val_str}' instead of interpolation."
                )


def test_env_example_uses_safe_placeholders_for_secrets() -> None:
    """CT-02: Assert .env.example template uses safe 'changeme' placeholders for sensitive keys."""
    assert ENV_EXAMPLE_PATH.is_file(), f".env.example not found at {ENV_EXAMPLE_PATH}"

    content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")
    ]

    for line in lines:
        if "=" in line:
            key, val = line.split("=", 1)
            key_upper = key.strip().upper()
            val_clean = val.strip().strip("'\"")

            if any(keyword in key_upper for keyword in SENSITIVE_KEYWORDS):
                assert "CHANGEME" in val_clean.upper(), (
                    f"CT-02 Violation in .env.example: sensitive key '{key}' "
                    f"has value '{val_clean}' which is not a safe placeholder ('changeme')."
                )


def test_gitignore_enforces_env_exclusion() -> None:
    """CT-02: Assert .gitignore explicitly excludes .env to prevent secret leakage."""
    assert GITIGNORE_PATH.is_file(), f".gitignore not found at {GITIGNORE_PATH}"

    content = GITIGNORE_PATH.read_text(encoding="utf-8")
    ignored_patterns = [
        line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")
    ]

    has_env_ignore = any(re.match(r"^\.?env(\..*)?$", p) or p == ".env" for p in ignored_patterns)
    assert has_env_ignore, "CT-02 Violation: .gitignore must explicitly ignore .env"
