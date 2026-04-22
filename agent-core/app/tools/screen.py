from __future__ import annotations

from datetime import datetime

import mss

from app.config import settings
from app.tools.base import Tool


class ScreenReaderTool(Tool):
    name = "screen.capture"
    description = "Capture the current screen and return the saved file path."
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = settings.screenshots_dir / f"screen-{timestamp}.png"
        with mss.mss() as sct:
            sct.shot(output=str(output_path))
        return str(output_path)
