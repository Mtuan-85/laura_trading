from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from utils.atomic import atomic_write_json, read_json

ClipStatus = Literal["pending", "running", "done", "failed", "interrupted"]
FinalStatus = Literal["pending", "done", "failed"]


class ProjectInputs(BaseModel):
    ref_image: str
    prompts: str
    image_edit: str | None = None
    aspect: str = "9:16"
    duration: int = 10


class ClipState(BaseModel):
    status: ClipStatus = "pending"
    prompt: str = ""
    ref: str = ""
    clip: str | None = None
    frame: str | None = None
    refined_ref: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    reason: str | None = None


class FinalState(BaseModel):
    status: FinalStatus = "pending"
    path: str | None = None


class _ProjectStateFile(BaseModel):
    version: int = 1
    created_at: str
    updated_at: str
    inputs: ProjectInputs
    clips: dict[str, ClipState] = Field(default_factory=dict)
    final: FinalState = Field(default_factory=FinalState)


class ChainProject:
    def __init__(self, folder: Path, state: _ProjectStateFile):
        self._folder = Path(folder)
        self._state = state

    @classmethod
    def create(cls, folder: Path, inputs: ProjectInputs, prompt_ids: list[str]) -> ChainProject:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat()
        state = _ProjectStateFile(
            created_at=now,
            updated_at=now,
            inputs=inputs,
            clips={pid: ClipState() for pid in prompt_ids},
            final=FinalState(),
        )
        instance = cls(folder, state)
        instance.save()
        return instance

    @classmethod
    def load(cls, folder: Path) -> ChainProject:
        folder = Path(folder)
        path = folder / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"No state.json in {folder}")
        raw = read_json(path)
        return cls(folder, _ProjectStateFile.model_validate(raw))

    @property
    def folder(self) -> Path:
        return self._folder

    @property
    def clips(self) -> dict[str, ClipState]:
        return self._state.clips

    @property
    def final(self) -> FinalState:
        return self._state.final

    @property
    def inputs(self) -> ProjectInputs:
        return self._state.inputs

    def save(self) -> None:
        self._state.updated_at = datetime.now().astimezone().isoformat()
        atomic_write_json(self._folder / "state.json", self._state.model_dump(mode="json"))

    def update_clip(self, clip_id: str, **fields) -> None:
        if clip_id not in self._state.clips:
            raise KeyError(f"Unknown clip_id: {clip_id}")
        current = self._state.clips[clip_id].model_dump()
        current.update(fields)
        self._state.clips[clip_id] = ClipState.model_validate(current)
        self.save()

    def update_final(self, status: FinalStatus, path: str | None = None) -> None:
        self._state.final = FinalState(status=status, path=path)
        self.save()

    def pending_clip_ids(self) -> list[str]:
        return [
            cid for cid, c in self._state.clips.items()
            if c.status in ("pending", "failed", "interrupted", "running")
        ]
