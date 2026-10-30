"""Tests for local HTTP route normalization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lanmacfinder.server import route_parts  # noqa: E402


class RouteTests(unittest.TestCase):
    def test_proxy_routes(self) -> None:
        self.assertEqual(route_parts("/health"), ["health"])
        self.assertEqual(
            route_parts("/v2/proxy/lan-mac-finder/health"),
            ["health"],
        )
        self.assertEqual(
            route_parts("/lan-mac-finder/api/scan/status"),
            ["scan", "status"],
        )


if __name__ == "__main__":
    unittest.main()
