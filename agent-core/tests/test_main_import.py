from __future__ import annotations


def test_main_module_imports_cleanly() -> None:
    import app.main as main

    assert main.app.title
