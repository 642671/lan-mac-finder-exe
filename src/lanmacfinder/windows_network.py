"""Windows network discovery, neighbor lookup, and bounded scan state."""

from __future__ import annotations

import base64
import concurrent.futures
import ctypes
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import winreg
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable

DEFAULT_MAX_HOSTS = 1022
DEFAULT_CONCURRENCY = 64
MAC_SUFFIX_PATTERN = re.compile(
    r"^(?:[0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}|[0-9A-Fa-f]{4})$"
)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _IP_ADDR_STRING(ctypes.Structure):
    pass


_IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADDR_STRING)),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", ctypes.c_ulong),
]


class _IP_ADAPTER_INFO(ctypes.Structure):
    pass


_IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADAPTER_INFO)),
    ("ComboIndex", ctypes.c_ulong),
    # The documented constants include four bytes reserved for a suffix.
    # Omitting them shifts every following field on 64-bit Windows.
    ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132),
    ("AddressLength", ctypes.c_uint),
    ("Address", ctypes.c_ubyte * 8),
    ("Index", ctypes.c_ulong),
    ("Type", ctypes.c_uint),
    ("DhcpEnabled", ctypes.c_uint),
    ("CurrentIpAddress", ctypes.POINTER(_IP_ADDR_STRING)),
    ("IpAddressList", _IP_ADDR_STRING),
    ("GatewayList", _IP_ADDR_STRING),
    ("DhcpServer", _IP_ADDR_STRING),
    ("HaveWins", ctypes.c_int),
    ("PrimaryWinsServer", _IP_ADDR_STRING),
    ("SecondaryWinsServer", _IP_ADDR_STRING),
    # time_t is 64-bit in supported 64-bit Windows Python builds.
    ("LeaseObtained", ctypes.c_longlong),
    ("LeaseExpires", ctypes.c_longlong),
]


class CommandError(RuntimeError):
    """Raised when a required Windows network command fails."""


class ScanCancelled(RuntimeError):
    """Internal cancellation signal."""


@dataclass(frozen=True)
class NetworkTarget:
    interface: str
    interface_index: int
    local_ip: str
    prefix_length: int
    gateway: str
    network: str
    broadcast: str
    scan_range: str
    host_count: int
    local_mac: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NeighborRecord:
    interface: str
    interface_index: int
    ip: str
    mac: str
    state: str


def normalize_mac_suffix(value: str) -> str | None:
    text = str(value or "").strip()
    if MAC_SUFFIX_PATTERN.fullmatch(text) is None:
        return None
    return re.sub(r"[-:]", "", text).upper()


def normalize_mac(value: str) -> str | None:
    text = str(value or "").strip().replace("-", "").replace(":", "")
    if re.fullmatch(r"[0-9A-Fa-f]{12}", text) is None:
        return None
    upper = text.upper()
    return "-".join(upper[index : index + 2] for index in range(0, 12, 2))


def parse_ipv4_network(address: str, prefix_length: int) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(f"{address}/{int(prefix_length)}", strict=False)
    except ValueError as exc:
        raise CommandError(f"invalid IPv4 network {address}/{prefix_length}: {exc}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise CommandError(f"not an IPv4 network: {address}/{prefix_length}")
    return network


def network_hosts(network: ipaddress.IPv4Network) -> list[ipaddress.IPv4Address]:
    if network.prefixlen <= 30:
        return list(network.hosts())
    if network.prefixlen == 31:
        return list(network)
    return [network.network_address]


def _decode_output(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16-le", "mbcs"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_powershell(script: str, timeout: float = 20.0) -> str:
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if not os.path.isfile(powershell):
        raise CommandError("Windows PowerShell 5.1 is unavailable")
    preamble = (
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
        "$OutputEncoding=[Console]::OutputEncoding;"
    )
    encoded = base64.b64encode((preamble + script).encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"PowerShell network query failed: {exc}") from exc
    stdout = _decode_output(result.stdout).strip()
    stderr = _decode_output(result.stderr).strip()
    if result.returncode != 0:
        raise CommandError(stderr or stdout or f"PowerShell exited {result.returncode}")
    return stdout


def _json_items(raw: str, label: str) -> list[dict]:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(f"{label} returned invalid JSON: {exc}") from exc
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    raise CommandError(f"{label} returned an unexpected JSON value")


def _is_virtual_name(alias: str, description: str) -> bool:
    text = f"{alias} {description}".lower()
    markers = (
        "vmware",
        "hyper-v",
        "vethernet",
        "loopback",
        "tailscale",
        "wireguard",
        "openvpn",
        "tap-",
        "tun",
        "docker",
        "wsl",
        "virtualbox",
        "bluetooth",
    )
    return any(marker in text for marker in markers)


def _adapter_friendly_name(adapter_name: str) -> str:
    """Read the Windows connection name for the adapter GUID when available."""
    if not adapter_name:
        return ""
    key_path = (
        r"SYSTEM\CurrentControlSet\Control\Network"
        r"\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        rf"\{adapter_name}\Connection"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "Name")
    except OSError:
        return ""
    return str(value).strip()


def read_windows_adapters() -> list[dict]:
    if os.name != "nt":
        raise CommandError("GetAdaptersInfo is only available on Windows")
    iphlpapi = ctypes.windll.iphlpapi
    iphlpapi.GetAdaptersInfo.argtypes = [
        ctypes.POINTER(_IP_ADAPTER_INFO),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    iphlpapi.GetAdaptersInfo.restype = ctypes.c_ulong
    size = ctypes.c_ulong(15 * 1024)
    error_buffer_overflow = 111
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = iphlpapi.GetAdaptersInfo(
            ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_INFO)),
            ctypes.byref(size),
        )
        if result == 0:
            break
        if result != error_buffer_overflow:
            raise CommandError(f"GetAdaptersInfo failed with error {result}")
    else:
        raise CommandError("GetAdaptersInfo buffer sizing failed")

    names: dict[int, str] = {}
    try:
        names = {index: name for index, name in socket.if_nameindex()}
    except OSError:
        pass

    entries: list[dict] = []
    current = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_INFO))
    while current:
        adapter = current.contents
        description = adapter.Description.decode("mbcs", errors="replace").strip()
        adapter_name = adapter.AdapterName.decode("ascii", errors="ignore").strip()
        alias = (
            _adapter_friendly_name(adapter_name)
            or names.get(int(adapter.Index))
            or description
            or f"Adapter {adapter.Index}"
        )
        mac = "-".join(
            f"{adapter.Address[index]:02X}"
            for index in range(min(int(adapter.AddressLength), 8))
        )
        address = ctypes.pointer(adapter.IpAddressList)
        while address:
            item = address.contents
            local_ip = item.IpAddress.decode("ascii", errors="ignore").strip()
            mask = item.IpMask.decode("ascii", errors="ignore").strip()
            if local_ip and mask:
                try:
                    prefix_length = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
                    ipaddress.IPv4Address(local_ip)
                except ValueError:
                    pass
                else:
                    gateway = adapter.GatewayList.IpAddress.decode(
                        "ascii",
                        errors="ignore",
                    ).strip()
                    if gateway and gateway != "0.0.0.0":
                        entries.append(
                            {
                                "interface_alias": alias,
                                "interface_index": int(adapter.Index),
                                "interface_description": description,
                                "local_ip": local_ip,
                                "prefix_length": prefix_length,
                                "gateway": gateway,
                                "local_mac": mac,
                            }
                        )
                    break
            address = item.Next
        current = adapter.Next
    return entries


def _powershell_adapter_entries(
    powershell_runner: Callable[[str], str],
) -> list[dict]:
    script = r"""
$rows = Get-NetIPConfiguration | Where-Object {
    $_.IPv4Address -and
    $_.IPv4DefaultGateway -and
    $_.NetAdapter.Status -eq 'Up' -and
    $_.NetAdapter.HardwareInterface -eq $true
} | ForEach-Object {
    $v4 = $_.IPv4Address | Select-Object -First 1
    $gateway = $_.IPv4DefaultGateway | Select-Object -First 1
    $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
    [pscustomobject]@{
        interface_alias = [string]$_.InterfaceAlias
        interface_index = [int]$_.InterfaceIndex
        interface_description = [string]$_.InterfaceDescription
        local_ip = [string]$v4.IPAddress
        prefix_length = [int]$v4.PrefixLength
        gateway = [string]$gateway.NextHop
        local_mac = [string]$adapter.MacAddress
    }
}
ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
"""
    return _json_items(powershell_runner(script), "Get-NetIPConfiguration")


def discover_networks(
    *,
    adapter_reader: Callable[[], list[dict]] | None = None,
    powershell_runner: Callable[[str], str] = run_powershell,
    max_hosts: int | None = None,
) -> tuple[list[NetworkTarget], list[str]]:
    if adapter_reader is not None:
        entries = adapter_reader()
    else:
        try:
            entries = read_windows_adapters()
            if not entries:
                entries = _powershell_adapter_entries(powershell_runner)
        except Exception:
            entries = _powershell_adapter_entries(powershell_runner)
    limit = max(1, int(max_hosts or os.environ.get("SCAN_MAX_HOSTS", DEFAULT_MAX_HOSTS)))
    targets: list[NetworkTarget] = []
    warnings: list[str] = []
    seen: set[tuple[int, str]] = set()

    for entry in entries:
        alias = str(entry.get("interface_alias") or "")
        description = str(entry.get("interface_description") or "")
        if _is_virtual_name(alias, description):
            continue
        try:
            interface_index = int(entry["interface_index"])
            local_ip = str(entry["local_ip"])
            prefix_length = int(entry["prefix_length"])
            network = parse_ipv4_network(local_ip, prefix_length)
        except (KeyError, TypeError, ValueError, CommandError) as exc:
            warnings.append(f"{alias or 'unknown adapter'}: {exc}")
            continue
        key = (interface_index, str(network))
        if key in seen:
            continue
        hosts = network_hosts(network)
        if len(hosts) > limit:
            warnings.append(
                f"{alias} 的 {network} 包含 {len(hosts)} 台主机，"
                f"超过安全上限 {limit}，已跳过"
            )
            continue
        seen.add(key)
        targets.append(
            NetworkTarget(
                interface=alias,
                interface_index=interface_index,
                local_ip=local_ip,
                prefix_length=network.prefixlen,
                gateway=str(entry.get("gateway") or ""),
                network=str(network.network_address),
                broadcast=str(network.broadcast_address),
                scan_range=str(network),
                host_count=len(hosts),
                local_mac=normalize_mac(str(entry.get("local_mac") or "")) or "",
            )
        )
    targets.sort(key=lambda item: (item.interface, item.scan_range))
    if not targets and not warnings:
        warnings.append("未找到带 IPv4 默认网关的物理网卡")
    return targets, warnings


ARP_LINE_PATTERN = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})\s+\S+",
    re.MULTILINE,
)


def parse_arp_output(raw: str, target: NetworkTarget) -> list[NeighborRecord]:
    records: list[NeighborRecord] = []
    for match in ARP_LINE_PATTERN.finditer(raw):
        ip_value, raw_mac = match.groups()
        mac = normalize_mac(raw_mac)
        if mac is None:
            continue
        try:
            address = ipaddress.ip_address(ip_value)
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv4Address):
            continue
        records.append(
            NeighborRecord(
                interface=target.interface,
                interface_index=target.interface_index,
                ip=str(address),
                mac=mac,
                state="ARP",
            )
        )
    return records


def read_neighbors(target: NetworkTarget) -> list[NeighborRecord]:
    arp = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "arp.exe",
    )
    if not os.path.isfile(arp):
        raise CommandError("Windows arp.exe is unavailable")
    try:
        result = subprocess.run(
            [arp, "-a", "-N", target.local_ip],
            capture_output=True,
            check=False,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"arp.exe failed: {exc}") from exc
    if result.returncode != 0:
        detail = _decode_output(result.stderr or result.stdout).strip()
        raise CommandError(detail or f"arp.exe exited {result.returncode}")
    return parse_arp_output(_decode_output(result.stdout), target)


def _local_device(target: NetworkTarget) -> dict | None:
    mac = normalize_mac(target.local_mac)
    if mac is None:
        return None
    return {
        "interface": target.interface,
        "scan_range": target.scan_range,
        "ip": target.local_ip,
        "mac": mac,
        "state": "LOCAL",
    }


def _sort_devices(items: Iterable[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        try:
            ip_value = int(ipaddress.ip_address(item.get("ip", "")))
        except ValueError:
            ip_value = 0
        return (item.get("interface", ""), ip_value, item.get("mac", ""))

    return sorted(items, key=key)


def collect_devices(
    targets: Iterable[NetworkTarget],
    *,
    neighbor_reader: Callable[[NetworkTarget], list[NeighborRecord]] = read_neighbors,
) -> tuple[list[dict], list[str]]:
    devices: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for target in targets:
        local = _local_device(target)
        if local:
            key = (local["interface"], local["ip"], local["mac"])
            seen.add(key)
            devices.append(local)
        try:
            neighbors = neighbor_reader(target)
        except CommandError as exc:
            warnings.append(f"{target.interface} 邻居表读取失败：{exc}")
            continue
        network = parse_ipv4_network(target.local_ip, target.prefix_length)
        for record in neighbors:
            try:
                address = ipaddress.ip_address(record.ip)
            except ValueError:
                continue
            if not isinstance(address, ipaddress.IPv4Address) or address not in network:
                continue
            key = (record.interface, record.ip, record.mac)
            if key in seen:
                continue
            seen.add(key)
            devices.append(
                {
                    "interface": record.interface,
                    "scan_range": target.scan_range,
                    "ip": record.ip,
                    "mac": record.mac,
                    "state": record.state,
                }
            )
    return _sort_devices(devices), warnings


def resolve_ping_command() -> str:
    path = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "ping.exe",
    )
    if not os.path.isfile(path):
        raise CommandError("Windows ping.exe is unavailable")
    return path


def _ping_host(
    ping_command: str,
    address: ipaddress.IPv4Address,
    source_ip: str,
    timeout_ms: int,
) -> bool:
    try:
        result = subprocess.run(
            [
                ping_command,
                "-n",
                "1",
                "-w",
                str(max(100, timeout_ms)),
                "-4",
                "-S",
                source_ip,
                str(address),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(2.0, timeout_ms / 1000.0 + 1.5),
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def scan_hosts(
    target: NetworkTarget,
    cancel_event: threading.Event,
    progress: Callable[[int], None] | None = None,
    *,
    timeout_ms: int | None = None,
    concurrency: int | None = None,
    ping_command: str | None = None,
) -> None:
    network = parse_ipv4_network(target.local_ip, target.prefix_length)
    hosts = network_hosts(network)
    timeout_value = int(
        timeout_ms
        if timeout_ms is not None
        else os.environ.get("SCAN_PING_TIMEOUT_MS", "650")
    )
    batch_size = max(
        1,
        min(
            128,
            int(
                concurrency
                if concurrency is not None
                else os.environ.get("SCAN_CONCURRENCY", DEFAULT_CONCURRENCY)
            ),
        ),
    )
    ping_path = ping_command or resolve_ping_command()
    completed = 0
    for start in range(0, len(hosts), batch_size):
        if cancel_event.is_set():
            raise ScanCancelled("scan stopped")
        batch = hosts[start : start + batch_size]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(batch),
            thread_name_prefix="lan-ping",
        ) as executor:
            futures = [
                executor.submit(
                    _ping_host,
                    ping_path,
                    address,
                    target.local_ip,
                    timeout_value,
                )
                for address in batch
            ]
            for future in concurrent.futures.as_completed(futures):
                if cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    raise ScanCancelled("scan stopped")
                try:
                    future.result()
                except Exception:
                    pass
        completed += len(batch)
        if progress:
            progress(completed)
    time.sleep(0.15)


def _match_records(records: Iterable[dict], suffix: str) -> list[dict]:
    normalized = normalize_mac_suffix(suffix)
    if normalized is None:
        raise ValueError("invalid MAC suffix")
    return [
        record
        for record in records
        if record["mac"].replace("-", "").endswith(normalized)
    ]


class ScanManager:
    """Run one non-blocking scan at a time."""

    def __init__(
        self,
        *,
        discover: Callable[..., tuple[list[NetworkTarget], list[str]]] = discover_networks,
        neighbor_reader: Callable[[NetworkTarget], list[NeighborRecord]] = read_neighbors,
        sweep: Callable[..., None] = scan_hosts,
    ) -> None:
        self._discover = discover
        self._neighbor_reader = neighbor_reader
        self._sweep = sweep
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {
            "status": "idle",
            "running": False,
            "stopping": False,
            "job_id": "",
            "suffix": "",
            "started_at": None,
            "finished_at": None,
            "networks": [],
            "warnings": [],
            "total_hosts": 0,
            "probed_hosts": 0,
            "progress_percent": 0,
            "current_interface": "",
            "current_range": "",
            "results": [],
            "error": "",
        }

    def status(self) -> dict:
        with self._lock:
            snapshot = dict(self._state)
            snapshot["networks"] = [dict(item) for item in self._state["networks"]]
            snapshot["warnings"] = list(self._state["warnings"])
            snapshot["results"] = [dict(item) for item in self._state["results"]]
            return snapshot

    def start(self, suffix: str) -> bool:
        normalized = normalize_mac_suffix(suffix)
        if normalized is None:
            raise ValueError("MAC suffix must be AA-BB, AA:BB, or AABB")
        with self._lock:
            if self._state["running"]:
                return False
            self._cancel = threading.Event()
            self._state = self._idle_state()
            self._state.update(
                {
                    "status": "discovering",
                    "running": True,
                    "job_id": uuid.uuid4().hex,
                    "suffix": normalized,
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(self._state["job_id"], normalized, self._cancel),
                name="lan-mac-scan",
                daemon=True,
            )
            self._thread.start()
        return True

    def stop(self) -> bool:
        with self._lock:
            if not self._state["running"]:
                return False
            self._state["stopping"] = True
            self._cancel.set()
            return True

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._thread
        if thread:
            thread.join(timeout)

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _append_results(self, values: Iterable[dict]) -> None:
        with self._lock:
            unique: dict[tuple[str, str, str], dict] = {}
            for item in list(self._state["results"]) + list(values):
                unique[(item["interface"], item["ip"], item["mac"])] = item
            self._state["results"] = _sort_devices(unique.values())

    def _run(
        self,
        job_id: str,
        suffix: str,
        cancel_event: threading.Event,
    ) -> None:
        try:
            targets, warnings = self._discover()
            if cancel_event.is_set():
                raise ScanCancelled("scan stopped")
            if not targets:
                self._update(
                    status="error",
                    running=False,
                    warnings=warnings,
                    error=warnings[0] if warnings else "没有可扫描网段",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                return
            total = sum(item.host_count for item in targets)
            self._update(
                status="scanning",
                networks=[item.as_dict() for item in targets],
                warnings=warnings,
                total_hosts=total,
            )
            probed_before = 0
            for target in targets:
                if cancel_event.is_set():
                    raise ScanCancelled("scan stopped")
                self._update(
                    current_interface=target.interface,
                    current_range=target.scan_range,
                )

                def on_progress(completed: int, offset: int = probed_before) -> None:
                    done = min(total, offset + completed)
                    self._update(
                        probed_hosts=done,
                        progress_percent=int(done * 100 / total) if total else 0,
                    )

                self._sweep(target, cancel_event, on_progress)
                devices, read_warnings = collect_devices(
                    [target],
                    neighbor_reader=self._neighbor_reader,
                )
                if read_warnings:
                    self._update(warnings=list(self.status()["warnings"]) + read_warnings)
                self._append_results(_match_records(devices, suffix))
                probed_before += target.host_count
            if cancel_event.is_set():
                raise ScanCancelled("scan stopped")
            self._update(
                status="completed",
                running=False,
                stopping=False,
                probed_hosts=total,
                progress_percent=100,
                current_interface="",
                current_range="",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        except ScanCancelled:
            self._update(
                status="stopped",
                running=False,
                stopping=False,
                current_interface="",
                current_range="",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            self._update(
                status="error",
                running=False,
                stopping=False,
                current_interface="",
                current_range="",
                error=str(exc),
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        finally:
            with self._lock:
                if self._state["job_id"] == job_id:
                    self._state["running"] = False
