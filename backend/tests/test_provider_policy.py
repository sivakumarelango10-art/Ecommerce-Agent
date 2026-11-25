import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_application_source_and_dependency_manifests_are_gemini_only():
    source_files = [
        path
        for path in ROOT.rglob("*.py")
        if ".git" not in path.parts
        and "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "alembic" not in path.parts
        and "providers" not in path.parts
        and not path.name.startswith("seed_ai_catalogue")
        and not path.name.startswith("ai_provider")
        and not path.name.startswith("ai_registry")
    ]
    provider_pattern = re.compile(r"\bopenai\b|\bgpt[-_][a-z0-9.-]+", re.IGNORECASE)

    for path in source_files:
        assert provider_pattern.search(path.read_text(encoding="utf-8")) is None, path

    for manifest in (ROOT / "requirements.txt", ROOT / "backend" / "requirements.txt"):
        packages = [
            line.split(";", maxsplit=1)[0].split("[", maxsplit=1)[0].split("=", maxsplit=1)[0]
            .split("<", maxsplit=1)[0]
            .split(">", maxsplit=1)[0]
            .strip()
            .lower()
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#") and not line.startswith("-r")
        ]
        assert "openai" not in packages, manifest
        assert "crewai" not in packages, manifest

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=" in env_example
    assert "GEMINI_MODEL=gemini-3.8-flash" in env_example
