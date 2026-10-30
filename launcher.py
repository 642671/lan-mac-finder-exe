"""Windows executable entry point for LAN MAC Finder."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from lanmacfinder import APP_NAME, APP_ID, __version__
from lanmacfinder.server import start_server
from lanmacfinder.windows_network import discover_networks


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "LanMacFinder"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging() -> Path:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    return log_file


def show_error(message: str) -> None:
    logging.error(message)
    try:
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        print(message, file=sys.stderr)


def find_browser() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        os.path.join(program_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(program_files, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(program_files, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(program_files_x86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def launch_app_window(url: str) -> tuple[subprocess.Popen, Path]:
    browser = find_browser()
    if not browser:
        raise RuntimeError(
            "未找到 Microsoft Edge 或 Google Chrome。"
            "请安装 Edge/Chrome 后重新打开应用。"
        )
    profile_dir = Path(tempfile.mkdtemp(prefix="lan-mac-finder-browser-"))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            browser,
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-background-mode",
            # Edge/Chrome throttles page timers when the app window is
            # minimized. Keep the local UI responsive instead of treating a
            # delayed heartbeat as a closed browser window.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-component-update",
            "--window-size=1180,760",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    logging.info("browser=%s pid=%s url=%s", browser, process.pid, url)
    return process, profile_dir


def run_selftest(result_file: str) -> int:
    result_path = Path(result_file).resolve()
    result: dict = {
        "app": APP_ID,
        "name": APP_NAME,
        "version": __version__,
        "python": sys.version.split()[0],
        "status": "error",
    }
    try:
        targets, warnings = discover_networks()
        result.update(
            {
                "status": "ok",
                "network_count": len(targets),
                "networks": [target.as_dict() for target in targets],
                "warnings": warnings,
            }
        )
        exit_code = 0
    except Exception as exc:
        result["error"] = str(exc)
        logging.exception("selftest failed")
        exit_code = 1
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return exit_code


def run_server_only(port: int, ready_file: str, duration: float) -> int:
    server, thread, url = start_server(port)
    ready_path = Path(ready_file).resolve()
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(
        json.dumps(
            {
                "url": url,
                "port": server.server_address[1],
                "pid": os.getpid(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logging.info("serve-only url=%s duration=%s", url, duration)
    try:
        if duration > 0:
            time.sleep(duration)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
    return 0


def run_application() -> int:
    server, thread, url = start_server(0)
    browser_process: subprocess.Popen | None = None
    profile_dir: Path | None = None
    try:
        browser_process, profile_dir = launch_app_window(url)
        while True:
            if browser_process.poll() is not None:
                logging.info("browser closed; exit_code=%s", browser_process.returncode)
                break
            time.sleep(0.75)
    finally:
        if browser_process is not None and browser_process.poll() is None:
            try:
                browser_process.terminate()
                browser_process.wait(timeout=5)
            except Exception:
                pass
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        server.scan_manager.stop()
        server.scan_manager.wait(2)
        if profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--result-file")
    parser.add_argument("--serve-only", action="store_true")
    parser.add_argument("--ready-file")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    log_file = configure_logging()
    logging.info(
        "starting version=%s frozen=%s log=%s",
        __version__,
        bool(getattr(sys, "frozen", False)),
        log_file,
    )
    if args.selftest:
        if not args.result_file:
            show_error("--selftest requires --result-file")
            return 2
        return run_selftest(args.result_file)
    if args.serve_only:
        if not args.ready_file:
            show_error("--serve-only requires --ready-file")
            return 2
        return run_server_only(args.port, args.ready_file, args.duration)
    try:
        return run_application()
    except Exception as exc:
        show_error(f"应用启动失败：{exc}")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
