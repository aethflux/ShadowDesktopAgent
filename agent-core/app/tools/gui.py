from __future__ import annotations

from typing import Any

import pyautogui

from app.tools.base import Tool


class GuiAutomationTool(Tool):
    name = "gui.act"
    description = "Simulate mouse and keyboard actions on the desktop."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["click", "double_click", "write", "hotkey", "move", "scroll"]},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "text": {"type": "string"},
            "keys": {"type": "array", "items": {"type": "string"}},
            "amount": {"type": "integer"}
        },
        "required": ["action"],
    }

    def run(self, **kwargs: Any) -> str:
        action = kwargs["action"]
        if action == "click":
            pyautogui.click(x=kwargs.get("x"), y=kwargs.get("y"))
            return f"Clicked at ({kwargs.get('x')}, {kwargs.get('y')})."
        if action == "double_click":
            pyautogui.doubleClick(x=kwargs.get("x"), y=kwargs.get("y"))
            return f"Double clicked at ({kwargs.get('x')}, {kwargs.get('y')})."
        if action == "write":
            pyautogui.write(kwargs.get("text", ""), interval=0.02)
            return "Typed requested text."
        if action == "hotkey":
            keys = kwargs.get("keys", [])
            pyautogui.hotkey(*keys)
            return f"Pressed hotkey: {'+'.join(keys)}."
        if action == "move":
            pyautogui.moveTo(kwargs.get("x"), kwargs.get("y"), duration=0.15)
            return f"Moved cursor to ({kwargs.get('x')}, {kwargs.get('y')})."
        if action == "scroll":
            pyautogui.scroll(kwargs.get("amount", -500))
            return f"Scrolled by {kwargs.get('amount', -500)}."
        return f"Unsupported GUI action: {action}"
