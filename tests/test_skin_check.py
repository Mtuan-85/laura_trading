from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core import skin_check
from core.skin_check import SkinResult, check_skin, stitch_side_by_side


# -- _parse_result ------------------------------------------------------


def test_parse_result_pass_above_threshold() -> None:
    raw = '{"skin_similarity": 98, "lighting_similarity": 88, "verdict": "pass", "rationale": "match"}'
    r = skin_check._parse_result(raw, threshold=95)
    assert r.skin == 98
    assert r.lighting == 88
    assert r.verdict == "pass"


def test_parse_result_ask_below_threshold() -> None:
    raw = '{"skin_similarity": 40, "lighting_similarity": 80, "verdict": "pass", "rationale": "wrong"}'
    r = skin_check._parse_result(raw, threshold=95)
    # Rule overrides declared verdict when they disagree.
    assert r.verdict == "ask"
    assert r.skin == 40


def test_parse_result_handles_markdown_fence() -> None:
    raw = '```json\n{"skin_similarity": 99, "lighting_similarity": 95, "verdict": "pass", "rationale": "ok"}\n```'
    r = skin_check._parse_result(raw, threshold=95)
    assert r.verdict == "pass"
    assert r.skin == 99


def test_parse_result_extracts_json_from_surrounding_text() -> None:
    raw = 'Sure! Here:\n{"skin_similarity": 50, "lighting_similarity": 60, "verdict": "ask", "rationale": "diff"}\nThanks!'
    r = skin_check._parse_result(raw, threshold=95)
    assert r.verdict == "ask"
    assert r.skin == 50


def test_parse_result_malformed_json_falls_back_to_ask() -> None:
    r = skin_check._parse_result("not json at all", threshold=95)
    assert r.verdict == "ask"
    assert r.skin == 0


def test_parse_result_clamps_out_of_range() -> None:
    raw = '{"skin_similarity": 250, "lighting_similarity": -7, "verdict": "pass", "rationale": "x"}'
    r = skin_check._parse_result(raw, threshold=95)
    assert r.skin == 100
    assert r.lighting == 0
    assert r.verdict == "pass"


# -- check_skin (CLI subprocess mocked) ---------------------------------


def test_check_skin_returns_ask_when_stitched_missing(tmp_path: Path) -> None:
    r = check_skin(tmp_path / "nope.jpg")
    assert r.verdict == "ask"
    assert r.skin == 0


def test_check_skin_returns_ask_when_cli_missing(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "stitched.jpg"
    img.write_bytes(b"FAKE")
    monkeypatch.setattr(
        skin_check, "_run_claude_cli_blocking",
        lambda instr, t: (-1, ""),
    )
    r = check_skin(img)
    assert r.verdict == "ask"
    assert "not installed" in r.rationale.lower()


def test_check_skin_returns_ask_on_timeout(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "stitched.jpg"
    img.write_bytes(b"FAKE")
    monkeypatch.setattr(
        skin_check, "_run_claude_cli_blocking",
        lambda instr, t: (-2, ""),
    )
    r = check_skin(img, timeout_s=42)
    assert r.verdict == "ask"
    assert "timeout" in r.rationale.lower()


def test_check_skin_pass_when_claude_returns_high_skin(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "stitched.jpg"
    img.write_bytes(b"FAKE")
    monkeypatch.setattr(
        skin_check, "_run_claude_cli_blocking",
        lambda instr, t: (
            0,
            '{"skin_similarity": 97, "lighting_similarity": 92, "verdict": "pass", "rationale": "great match"}',
        ),
    )
    r = check_skin(img, threshold=95)
    assert r.verdict == "pass"
    assert r.skin == 97
    assert r.lighting == 92


def test_check_skin_clears_anthropic_env(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "stitched.jpg"
    img.write_bytes(b"FAKE")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-cleared")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    captured: dict = {}

    from core import claude_cli

    class FakeProc:
        returncode = 0
        def communicate(self, input: str, timeout: int) -> tuple[str, str]:
            return (
                '{"skin_similarity": 99, "lighting_similarity": 90, "verdict": "pass", "rationale": "ok"}',
                "",
            )

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(claude_cli, "find_claude_exe", lambda: r"C:\fake\claude.cmd")
    monkeypatch.setattr(claude_cli.subprocess, "Popen", fake_popen)
    r = check_skin(img)
    assert r.verdict == "pass"
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "HTTPS_PROXY" not in captured["env"]


# -- stitch_side_by_side (real ffmpeg) ---------------------------------


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_solid_jpg(path: Path, color: str, ffmpeg: str = "ffmpeg") -> None:
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:size=128x128:duration=0.04",
        "-frames:v", "1",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_stitch_side_by_side_produces_wider_image(tmp_path: Path) -> None:
    ref = tmp_path / "ref.jpg"
    refined = tmp_path / "refined.jpg"
    out = tmp_path / "stitched.jpg"
    _make_solid_jpg(ref, "red")
    _make_solid_jpg(refined, "blue")
    stitch_side_by_side(ref, refined, out)
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_stitch_side_by_side_raises_on_missing_input(tmp_path: Path) -> None:
    out = tmp_path / "stitched.jpg"
    with pytest.raises(RuntimeError, match="ffmpeg"):
        stitch_side_by_side(
            tmp_path / "no_such_ref.jpg",
            tmp_path / "no_such_refined.jpg",
            out,
        )
