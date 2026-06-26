# Home Remote Desktop

A small Python remote desktop client/server for two Windows 11 machines on the same home network.

It supports:

- UDP broadcast discovery on the LAN.
- TCP screen streaming as JPEG frames.
- Mouse movement, clicks, wheel scrolling, and basic keyboard forwarding.
- A per-session passcode printed by the server.

This is intended for trusted home networks. It is not encrypted and should not be exposed to the internet.

## Install

Install Python 3.11 or newer from <https://www.python.org/downloads/windows/>. During install, enable **Add python.exe to PATH**.

Download the repository:

```powershell
git clone https://github.com/endrawes0/home-remote-desktop.git
cd home-remote-desktop
```

On both Windows machines, open PowerShell in this folder and run:

```powershell
.\install.bat
```

## Run The Server

On the Windows 11 machine you want to control:

```powershell
.\run-server.bat
```

The server prints a six-digit passcode and its IP address. Leave that window open.

Useful options:

```powershell
.\run-server.bat --fps 15 --quality 75 --scale 0.8
.\run-server.bat --passcode 123456
.\run-server.bat --port 51334
```

## Run The Client

On the other Windows 11 machine:

```powershell
.\run-client.bat
```

Click **Discover**, select the server, click **Connect**, and enter the server passcode.

If discovery is blocked by Windows Firewall, connect directly:

```powershell
.\run-client.bat --host 192.168.1.25 --passcode 123456
```

## Firewall

Windows may ask whether Python can communicate on private networks. Allow it for private networks on both machines.

If discovery or connection is still blocked, create inbound firewall rules on the server machine:

- UDP port `51333` for discovery.
- TCP port `51334` for the remote desktop connection.

## Security Notes

- Use only on a trusted private LAN.
- Do not port-forward this service.
- The passcode prevents casual accidental connections, but traffic is not encrypted.
- Stop the server window when you are done.

## Known Limits

- The server streams the primary monitor only.
- Keyboard support covers normal text keys, modifiers, arrows, function keys, and common navigation keys.
- Performance depends heavily on Wi-Fi quality, screen resolution, and the `--scale`, `--fps`, and `--quality` settings.
