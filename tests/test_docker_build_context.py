"""Case 10: ``.env`` must be excluded from the Docker build context.

The Dockerfile copies the whole context (``COPY . .``), so any secret in a local
``.env`` would be baked into the image unless ``.dockerignore`` excludes it. This
guards that exclusion so it cannot be silently removed.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _dockerignore_patterns() -> set[str]:
    dockerignore = _REPO_ROOT / ".dockerignore"
    assert dockerignore.is_file(), (
        ".dockerignore is missing; .env would be copied into the Docker image."
    )
    return {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def test_case10_env_excluded_from_docker_build_context():
    patterns = _dockerignore_patterns()
    assert ".env" in patterns, ".env must be excluded from the Docker build context."


def test_case10_env_variants_excluded_from_docker_build_context():
    patterns = _dockerignore_patterns()
    assert ".env.*" in patterns, (
        ".env.* variants (e.g. .env.local) must be excluded from the Docker build "
        "context."
    )
