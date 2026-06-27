from __future__ import annotations

from pathlib import Path


def test_skin_profile_runtime_dependencies_are_declared() -> None:
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    normalized = {
        line.strip().lower().split("==", 1)[0].split(">=", 1)[0]
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "numpy" in normalized
    assert "opencv-python" in normalized
