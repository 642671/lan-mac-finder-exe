"""Tests for Windows LAN discovery and MAC matching."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lanmacfinder.windows_network import (  # noqa: E402
    NetworkTarget,
    NeighborRecord,
    collect_devices,
    discover_networks,
    normalize_mac,
    normalize_mac_suffix,
    parse_arp_output,
    parse_ipv4_network,
)


class MacTests(unittest.TestCase):
    def test_suffix_formats(self) -> None:
        for value in ("a1-b2", "A1:B2", "a1B2"):
            self.assertEqual(normalize_mac_suffix(value), "A1B2")
        self.assertIsNone(normalize_mac_suffix("GG-12"))

    def test_mac_normalization(self) -> None:
        self.assertEqual(normalize_mac("02:42:ac:11:45:6a"), "02-42-AC-11-45-6A")
        self.assertIsNone(normalize_mac("bad"))


class DiscoveryTests(unittest.TestCase):
    def test_discover_physical_network_and_mac(self) -> None:
        payload = [
            {
                "interface_alias": "Ethernet",
                "interface_index": 7,
                "interface_description": "Intel Ethernet",
                "local_ip": "192.0.2.25",
                "prefix_length": 24,
                "gateway": "192.0.2.1",
                "local_mac": "02-42-AC-11-45-6A",
            }
        ]

        targets, warnings = discover_networks(
            adapter_reader=lambda: payload,
            max_hosts=1022,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].scan_range, "192.0.2.0/24")
        self.assertEqual(targets[0].local_mac, "02-42-AC-11-45-6A")

    def test_oversized_network_is_skipped(self) -> None:
        payload = [
            {
                "interface_alias": "Ethernet",
                "interface_index": 7,
                "interface_description": "Intel Ethernet",
                "local_ip": "10.20.30.40",
                "prefix_length": 16,
                "gateway": "10.20.0.1",
                "local_mac": "02-42-AC-11-45-6A",
            }
        ]
        targets, warnings = discover_networks(
            adapter_reader=lambda: payload,
            max_hosts=1022,
        )
        self.assertEqual(targets, [])
        self.assertTrue(any("超过安全上限" in warning for warning in warnings))

    def test_zero_prefix_network_is_skipped_without_expanding_hosts(self) -> None:
        payload = [
            {
                "interface_alias": "Ethernet",
                "interface_index": 7,
                "interface_description": "Intel Ethernet",
                "local_ip": "10.20.30.40",
                "prefix_length": 0,
                "gateway": "10.20.0.1",
                "local_mac": "02-42-AC-11-45-6A",
            }
        ]
        targets, warnings = discover_networks(
            adapter_reader=lambda: payload,
            max_hosts=1022,
        )
        self.assertEqual(targets, [])
        self.assertTrue(any("超过安全上限" in warning for warning in warnings))

    def test_unspecified_adapter_address_is_skipped(self) -> None:
        payload = [
            {
                "interface_alias": "WLAN",
                "interface_index": 8,
                "interface_description": "Wi-Fi",
                "local_ip": "0.0.0.0",
                "prefix_length": 0,
                "gateway": "192.0.2.1",
                "local_mac": "02-42-AC-11-45-6B",
            }
        ]
        targets, warnings = discover_networks(
            adapter_reader=lambda: payload,
            max_hosts=1022,
        )
        self.assertEqual(targets, [])
        self.assertTrue(any("不可扫描" in warning for warning in warnings))

    def test_known_devices_include_local_adapter(self) -> None:
        target = NetworkTarget(
            interface="Ethernet",
            interface_index=7,
            local_ip="192.0.2.25",
            prefix_length=24,
            gateway="192.0.2.1",
            network="192.0.2.0",
            broadcast="192.0.2.255",
            scan_range="192.0.2.0/24",
            host_count=254,
            local_mac="02-42-AC-11-45-6A",
        )
        neighbors = [
            NeighborRecord(
                interface="Ethernet",
                interface_index=7,
                ip="192.0.2.10",
                mac="02-42-AC-11-00-10",
                state="REACHABLE",
            )
        ]
        devices, warnings = collect_devices(
            [target],
            neighbor_reader=lambda _target: neighbors,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]["ip"], "192.0.2.10")
        self.assertEqual(devices[1]["state"], "LOCAL")

    def test_arp_output_parsing(self) -> None:
        target = NetworkTarget(
            interface="Ethernet",
            interface_index=7,
            local_ip="192.0.2.25",
            prefix_length=24,
            gateway="192.0.2.1",
            network="192.0.2.0",
            broadcast="192.0.2.255",
            scan_range="192.0.2.0/24",
            host_count=254,
            local_mac="02-42-AC-11-45-6A",
        )
        raw = """
Interface: 192.0.2.25 --- 0x7
  Internet Address      Physical Address      Type
  192.0.2.1             02-42-ac-11-00-01     dynamic
  invalid-line
"""
        records = parse_arp_output(raw, target)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].ip, "192.0.2.1")
        self.assertEqual(records[0].mac, "02-42-AC-11-00-01")

    def test_network_parsing(self) -> None:
        self.assertEqual(
            str(parse_ipv4_network("198.51.100.42", 23)),
            "198.51.100.0/23",
        )


if __name__ == "__main__":
    unittest.main()
