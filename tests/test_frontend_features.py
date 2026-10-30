"""Contract tests for user-facing productivity controls."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendFeatureTests(unittest.TestCase):
    def test_csv_export_control_and_keyboard_shortcut_exist(self) -> None:
        html = (ROOT / "src" / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "src" / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="exportButton"', html)
        self.assertIn("function exportCsv()", script)
        self.assertIn('event.key.toLowerCase() === "e"', script)
        self.assertIn("URL.createObjectURL", script)

    def test_heartbeat_cannot_terminate_the_application(self) -> None:
        launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
        server = (ROOT / "src" / "lanmacfinder" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("heartbeat expired; closing local server", launcher)
        self.assertNotIn("last_heartbeat", server)


if __name__ == "__main__":
    unittest.main()
