from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_stage_auth_mutations_use_canonical_app_source_sha():
    for name in ("repair-stage-admin.yml", "stage-auth-acceptance.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "app-source-sha.txt" in text
        assert "org.opencontainers.image.revision" not in text
        assert "requested SHA does not match app-source-sha.txt" in text
