# Android Client

Android client for Home Remote Desktop.

## Build

Open `android-client/` in Android Studio and build the `app` module, or build from the command line with an Android SDK installed:

```powershell
cd android-client
.\gradlew.bat assembleDebug
```

If Java is not on PATH, install a JDK such as Microsoft OpenJDK 21 and set `JAVA_HOME`.

The debug APK will be at:

```text
android-client\app\build\outputs\apk\debug\app-debug.apk
```

## Use

1. Start the Windows server on the machine to control:

   ```powershell
   .\run-server.bat --passcode 123456
   ```

2. Install and open the Android APK on a device connected to the same LAN.
3. Tap **Discover**, select the server, enter the passcode, and tap **Connect**.

If discovery is blocked, enter the server IP and port manually.

Controls:

- Tap the remote screen to left-click.
- Drag on the remote screen to hold and move the left mouse button.
- Use the text field and **Type** button to send typed text to the server.
- Use **Enter** to press Enter on the server.
- Use **Fullscreen** to hide the connection controls. Two-finger tap the remote screen to toggle fullscreen back off.
