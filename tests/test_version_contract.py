"""Version synchronization contract for the Windows build."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VersionContractTests(unittest.TestCase):
    def test_version_is_synchronized(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        package = (ROOT / "src" / "lanmacfinder" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*"([^"]+)"', package)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), version)

        info = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        numeric = version.split(".")
        self.assertIn(f"filevers=({numeric[0]}, {numeric[1]}, {numeric[2]}, 0)", info)
        self.assertIn(f"StringStruct(u'FileVersion', u'{version}')", info)
        self.assertIn(f"StringStruct(u'ProductVersion', u'{version}')", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
