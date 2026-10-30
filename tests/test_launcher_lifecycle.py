"""Regression tests for the Windows application lifecycle."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import launcher  # noqa: E402


class FakeBrowserProcess:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = list(polls)
        self.poll_count = 0
        self.returncode = 0
        self.pid = 1234

    def poll(self):
        self.poll_count += 1
        if self._polls:
            self.returncode = self._polls.pop(0)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class FakeScanManager:
    def __init__(self) -> None:
        self.stopped = False
        self.waited = False

    def stop(self) -> None:
        self.stopped = True

    def wait(self, timeout: float | None = None) -> None:
        self.waited = True


class FakeServer:
    def __init__(self) -> None:
        self.scan_manager = FakeScanManager()
        self.shutdown_called = False
        self.close_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True

    def server_close(self) -> None:
        self.close_called = True


class FakeThread:
    def join(self, timeout: float | None = None) -> None:
        return None


class LauncherLifecycleTests(unittest.TestCase):
    def test_application_waits_for_browser_instead_of_page_heartbeat(self) -> None:
        browser = FakeBrowserProcess([None, None, None, 0])
        server = FakeServer()
        with (
            mock.patch.object(launcher, "start_server", return_value=(server, FakeThread(), "http://127.0.0.1:1/")),
            mock.patch.object(launcher, "launch_app_window", return_value=(browser, None)),
            mock.patch.object(launcher.time, "sleep", return_value=None),
        ):
            self.assertEqual(launcher.run_application(), 0)

        self.assertGreaterEqual(browser.poll_count, 4)
        self.assertTrue(server.shutdown_called)
        self.assertTrue(server.close_called)
        self.assertTrue(server.scan_manager.stopped)
        self.assertTrue(server.scan_manager.waited)

    def test_browser_keeps_page_timers_active_when_backgrounded(self) -> None:
        browser = FakeBrowserProcess([0])
        with (
            mock.patch.object(launcher, "find_browser", return_value=r"C:\Edge\msedge.exe"),
            mock.patch.object(launcher.tempfile, "mkdtemp", return_value=r"C:\Temp\profile"),
            mock.patch.object(launcher.subprocess, "Popen", return_value=browser) as popen,
        ):
            process, profile = launcher.launch_app_window("http://127.0.0.1:1234/")

        self.assertIs(process, browser)
        self.assertEqual(str(profile), r"C:\Temp\profile")
        arguments = popen.call_args.args[0]
        self.assertIn("--disable-background-timer-throttling", arguments)
        self.assertIn("--disable-backgrounding-occluded-windows", arguments)
        self.assertIn("--disable-renderer-backgrounding", arguments)


if __name__ == "__main__":
    unittest.main()
