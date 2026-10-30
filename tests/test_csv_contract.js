"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const csv = require("../src/frontend/csv.js");

test("CSV export has a UTF-8 BOM, stable columns, and safe escaping", () => {
  const output = csv.buildCsv([{
    interface: "Ethernet, primary",
    scan_range: "192.0.2.0/24",
    ip: "192.0.2.10",
    mac: "02-42-AC-11-00-10",
    state: "REACHABLE"
  }]);
  assert.ok(output.startsWith("\ufeff"));
  const lines = output.slice(1).split("\r\n");
  assert.equal(lines[0], "网卡,扫描范围,IP 地址,MAC 地址,状态");
  assert.match(lines[1], /^"Ethernet, primary",/);
  assert.equal(csv.escapeCell("=formula"), "'=formula");
});

test("dateStamp is deterministic in local time", () => {
  assert.equal(csv.dateStamp(new Date(2026, 9, 30, 9, 7, 5)), "20261030-090705");
});
