from __future__ import annotations

from typing import Any

from app.services import image_gen
from app.tools.base import Tool


class ImageGenerateTool(Tool):
    name = "image.generate"
    description = (
        "Generate an image from a text prompt using the configured text-to-image "
        "model (ModelScope). Use it to draw a console scene background, a pet "
        "avatar portrait, or any picture the user asks for. Returns the URL of the "
        "saved image; the desktop client can apply it as the current scene/avatar."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "A detailed description of the image to draw.",
            },
            "purpose": {
                "type": "string",
                "enum": ["scene", "avatar", "other"],
                "description": (
                    "Category: 'scene' for a console background, 'avatar' for a "
                    "pet portrait, otherwise 'other'."
                ),
            },
        },
        "required": ["prompt"],
    }

    def run(self, **kwargs: Any) -> str:  # pragma: no cover - async path used
        raise NotImplementedError("image.generate is async; use arun().")

    async def arun(self, **kwargs: Any) -> str:
        prompt = str(kwargs.get("prompt", "")).strip()
        purpose = str(kwargs.get("purpose", "other"))
        if not prompt:
            return "image.generate failed: a non-empty 'prompt' is required."
        try:
            record = await image_gen.generate_and_save(prompt, purpose)
        except image_gen.ImageGenerationError as exc:
            return f"image.generate failed: {exc}"
        except Exception as exc:  # noqa: BLE001 — surface any provider error as text
            return f"image.generate failed: {exc}"
        return (
            f"Generated a {record['purpose']} image (id={record['id']}), "
            f"saved at {record['url']}."
        )
