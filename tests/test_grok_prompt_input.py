from __future__ import annotations

import asyncio

from engines.grok import actions


class _FakeLocator:
    @property
    def first(self):
        return self

    async def click(self):
        return None


class _FakeKeyboard:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def press(self, key: str) -> None:
        self.calls.append(("press", key))

    async def type(self, text: str) -> None:
        self.calls.append(("type", text))

    async def insert_text(self, text: str) -> None:
        self.calls.append(("insert_text", text))


class _FakePage:
    def __init__(self) -> None:
        self.keyboard = _FakeKeyboard()

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator()


def test_hybrid_type_prompt_types_character_and_inserts_suffix(monkeypatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(actions.asyncio, "sleep", no_sleep)
    page = _FakePage()

    asyncio.run(
        actions.hybrid_type_prompt(
            page,
            typed_prefix="Lisa",
            paste_suffix="Action: points\n\nCamera: close-up",
        )
    )

    assert ("type", "L") in page.keyboard.calls
    assert ("type", "i") in page.keyboard.calls
    assert ("insert_text", "Action: points") in page.keyboard.calls
    assert ("insert_text", "Camera: close-up") in page.keyboard.calls
