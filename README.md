# LAN MAC Finder for Windows

Portable Windows executable for finding LAN devices by the last four
hexadecimal characters of their MAC address.

## Run

Double-click `LanMacFinder.exe`.

The executable starts a local-only service on `127.0.0.1` and opens the WebUI
in a dedicated Edge or Chrome application window without an address bar.
Python does not need to be installed.

## Features

- Detect physical Windows adapters with an IPv4 default gateway.
- Exclude common Hyper-V, VMware, VPN, TAP/TUN, Docker, WSL, and loopback
  adapters.
- Display the current Windows neighbor table as the default device overview.
- Match `AA-BB`, `AA:BB`, and `AABB` suffixes case-insensitively.
- Copy either the selected IP address or full MAC address.
- Export the current device or match table to UTF-8 CSV for Excel.
- Include the selected adapter's own MAC with a `LOCAL` state.
- Liquid-glass light and dark themes based on the Windows color scheme.
- Stop, refresh, clear-search, keyboard Enter/Escape, `/`, `Ctrl+E`, and double-click copy.
- Keep the window alive while minimized or backgrounded.

## Requirements

- Windows 10 or Windows 11 x64.
- Microsoft Edge or Google Chrome.
- Windows PowerShell 5.1 and the built-in `ping.exe`.

## Safety and limits

The app only reads local adapter, neighbor, and routing data. It does not change
Windows network settings.

Only devices in the same layer-2 broadcast domain can be discovered. Devices
behind another router or VLAN normally appear only as the gateway MAC.

## Local source and build

Source code:

```text
D:\111\dev-test\lan-mac-finder-exe
```

Build:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

Outputs:

```text
release\LanMacFinder.exe
release\LanMacFinder-Windows-x64-1.1.0.zip
release\LanMacFinder.exe.sha256
```

Runtime logs are written under:

```text
%LOCALAPPDATA%\LanMacFinder\logs\app.log
```

## Version 1.1.0 reliability fix

The previous build stopped itself when the page heartbeat was delayed by Edge/Chrome
background timer throttling. Version 1.1.0 no longer uses page heartbeat age as an
exit signal; the bundled browser process is now the source of truth for the app
window lifetime. Browser background throttling is also disabled for the dedicated
app window.

See [CHANGELOG.md](CHANGELOG.md) for the release notes.

Source and releases: `https://github.com/642671/lan-mac-finder-exe`
