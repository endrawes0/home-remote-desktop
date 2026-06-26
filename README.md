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

You can also install it with winget:

```powershell
winget install Python.Python.3.12
```

Download the repository:

```powershell
git clone https://github.com/endrawes0/home-remote-desktop.git
cd home-remote-desktop
```

On both Windows machines, open PowerShell in this folder and run:

```powershell
.\install.bat
```

If `install.bat` reports that Python is missing, close and reopen PowerShell after installing Python, then run `.\install.bat` again.

Inside a Codex desktop workspace, the helper scripts `run-client-codex-python.bat` and `run-server-codex-python.bat` can use Codex's bundled Python runtime.

Optional performance backends:

```powershell
.\install.bat --performance
```

This installs Python performance packages and attempts to install the Visual C++ libjpeg-turbo package with winget: `libjpeg-turbo.libjpeg-turbo.VC`.
The installer requests the libjpeg-turbo architecture that matches the Python executable, which matters on Windows on Arm when Python may be x64.

TurboJPEG also requires the native libjpeg-turbo DLL. If it is not discoverable automatically, pass it explicitly:

```powershell
.\run-server.bat --jpeg-backend turbojpeg --turbojpeg-lib-path C:\path\to\turbojpeg.dll
```

The server automatically checks `TURBOJPEG_LIB_PATH`, `TURBOJPEG`, PATH entries, `C:\Program Files\libjpeg-turbo64\bin\turbojpeg.dll`, and `C:\libjpeg-turbo64\bin\turbojpeg.dll`.

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
.\run-server.bat --capture-backend auto --jpeg-backend auto --delta-mode tiles --tile-size 384 --no-jpeg-optimize
```

Performance flags:

- `--capture-backend mss|dxcam|auto` uses the original MSS capture path, optional DXCam, or DXCam with MSS fallback.
- `--jpeg-backend pillow|turbojpeg|auto` uses Pillow, optional TurboJPEG, or TurboJPEG with Pillow fallback.
- `--turbojpeg-lib-path` points PyTurboJPEG at the native `turbojpeg.dll` when automatic discovery fails.
- `--jpeg-optimize` / `--no-jpeg-optimize` controls Pillow's JPEG optimize pass. The default is `--no-jpeg-optimize` for lower latency.
- `--delta-mode off|tiles` sends full frames or changed JPEG tiles after the first full frame.
- `--tile-size` controls delta tile dimensions.
- `--full-frame-interval` sends a full refresh every N frames in tile mode.

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

## Profiling

The client can run a headless receive/decode profile, and the server can write capture/encode/send metrics when the client disconnects.

In one terminal:

```powershell
.\run-server.bat --passcode 123456 --profile-output server-profile.json
```

In another terminal:

```powershell
.\run-client.bat --host 127.0.0.1 --passcode 123456 --profile-seconds 10 --profile-output client-profile.json
```

The JSON reports include FPS, bandwidth, payload size, capture time, JPEG encode time, send time, client frame wait/receive time, decode time, and end-to-end timing for same-machine tests.

To test several configurations on the current machine and write a recommendation:

```powershell
.\run-client.bat --profile-config-sweep --profile-config-seconds 6 --profile-config-output profile-recommendation.json
```

The recommendation includes suggested server and client commands. Run it on each machine you care about, because the best settings depend on display resolution, CPU/GPU, and installed optional backends.

To test an actual client/server pair over an existing server process, start the server normally:

```powershell
.\run-server.bat --passcode 123456 --capture-backend auto
```

Then run the pair sweep from the client machine:

```powershell
.\run-client.bat --host 192.168.1.25 --passcode 123456 --pair-profile-sweep --pair-profile-seconds 6 --pair-profile-output pair-profile-recommendation.json
```

This keeps one connection open, asks the server to switch stream settings for each test, measures client decode/network behavior, uses server-reported capture/resize/encode timings, and writes a best configuration for that specific pair of machines.

## Android Client

An Android client project is available in `android-client/`. Build it with Android Studio or:

```powershell
cd android-client
.\gradlew.bat assembleDebug
```

The app supports LAN discovery, passcode auth, JPEG full/delta frame display, and basic touch-to-mouse input.
