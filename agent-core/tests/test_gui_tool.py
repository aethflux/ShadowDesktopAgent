from __future__ import annotations

import pytest

from app.tools.gui import GuiAutomationTool


def test_gui_tool_reports_unavailable_when_pyautogui_cannot_load(monkeypatch) -> None:
    monkeypatch.setattr("app.tools.gui.importlib.import_module", lambda name: (_ for _ in ()).throw(RuntimeError("boom")))

    tool = GuiAutomationTool()

    with pytest.raises(RuntimeError, match="GUI automation is unavailable"):
        tool.run(action="click", x=1, y=2)
