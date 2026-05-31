"""Tests for the image-generation service.

The ModelScope HTTP call is mocked, so these verify the parts we own — the
save / index / list / delete pipeline and the guard rails — independently of
the live provider (which needs a key + quota and can't run in CI).
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import image_gen


@pytest.fixture
def gen_dir(tmp_path, monkeypatch):
    """Point the generated-images dir at a tmp location and enable the feature."""
    shots = tmp_path / "artifacts" / "screenshots"
    shots.mkdir(parents=True)
    monkeypatch.setattr(settings, "screenshots_dir", shots)
    monkeypatch.setattr(settings, "enable_image_generation", True)
    monkeypatch.setattr(settings, "modelscope_api_key", "test-key")
    return tmp_path / "artifacts" / "generated"


@pytest.mark.asyncio
async def test_generate_and_save_persists_and_indexes(gen_dir, monkeypatch) -> None:
    async def fake_ms(prompt):
        assert prompt == "a neon city"
        return b"\x89PNG fake-bytes", "image/png"

    monkeypatch.setattr(image_gen, "_modelscope_generate", fake_ms)

    record = await image_gen.generate_and_save("a neon city", "scene")

    assert record["purpose"] == "scene"
    assert record["url"] == f"/artifacts/generated/{record['filename']}"
    saved = gen_dir / record["filename"]
    assert saved.exists() and saved.read_bytes() == b"\x89PNG fake-bytes"

    gallery = image_gen.list_gallery()
    assert len(gallery) == 1 and gallery[0]["id"] == record["id"]


@pytest.mark.asyncio
async def test_delete_removes_file_and_index(gen_dir, monkeypatch) -> None:
    async def fake_ms(prompt):
        return b"img", "image/png"

    monkeypatch.setattr(image_gen, "_modelscope_generate", fake_ms)
    record = await image_gen.generate_and_save("x", "other")
    assert (gen_dir / record["filename"]).exists()

    assert image_gen.delete_image(record["id"]) is True
    assert not (gen_dir / record["filename"]).exists()
    assert image_gen.list_gallery() == []
    assert image_gen.delete_image("does-not-exist") is False


@pytest.mark.asyncio
async def test_empty_prompt_rejected(gen_dir) -> None:
    with pytest.raises(image_gen.ImageGenerationError):
        await image_gen.generate_and_save("   ", "scene")


@pytest.mark.asyncio
async def test_disabled_feature_rejected(gen_dir, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_image_generation", False)
    with pytest.raises(image_gen.ImageGenerationError):
        await image_gen.generate_and_save("draw something", "scene")


def test_availability_requires_key(gen_dir, monkeypatch) -> None:
    assert image_gen.image_generation_available() is True
    monkeypatch.setattr(settings, "modelscope_api_key", None)
    assert image_gen.image_generation_available() is False
