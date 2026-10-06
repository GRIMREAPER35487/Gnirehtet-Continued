# Gnirehtet Continued by Synthos

Reverse tethering over ADB for Android devices and Meta Quest headsets. Allows your device to use your computer's internet connection over USB without root access.

> [!TIP]
> ### Primarily Focused on VR Headsets & Continuous Wired Play
> This edition is specifically engineered for standalone and PCVR headsets (such as Meta Quest 2, Quest 3, Quest Pro, and Pico devices).
>
> Because of this, it is tuned for continuous high-bandwidth traffic.

---

## Changes in This Fork

* **Automatic Link Recovery:** Standard batch scripts stop or hang when an ADB connection breaks. A Python supervisor (`gnirehtet_watchdog.py`) monitors device state, cleans up stale tunnels when the device sleeps or disconnects, and restores the reverse tether the moment it reconnects.
* **Java Relay Only:** The legacy Rust relay was removed in favor of the Java NIO implementation, which handles sudden socket drops without leaking port bindings or locking threads.
* **Automatic APK Installation:** Checks for the `com.genymobile.gnirehtet` package on connect and installs `gnirehtet.apk` automatically if it is not present.
* **Modern Java & ZGC:** Updated to build on modern JDKs (up to Java 25). Automatically applies `-XX:+UseZGC` when available to keep GC pauses minimal under heavy traffic.
* **Automatic Tool Discovery:** Finds `adb` and `java` in standard system paths (Android SDK, JDK installs, PATH) without requiring manual configuration.
* **Simple Launchers:** Includes `run_watchdog.cmd` for Windows and `run_watchdog.sh` for Linux/macOS.

---

## Why Java Over Rust?

Upstream Gnirehtet offered both Java and Rust versions of the desktop relay. For continuous tethering, especially with VR headsets, the Java relay proved more reliable:

1. **Clean Socket Teardown:**  
   Reverse ADB pipes themselves can be an issue and are inherently error-prone—broken pipes, transport stalls, and reset events don't just happen when a cable moves or a headset sleeps, but can occur spontaneously within ADB itself. In the Rust relay, abrupt disconnects could leave port 31416 stuck in a bound zombie state. The Java NIO relay handles client disconnections cleanly without affecting the listening server socket.

2. **Garbage Collection (ZGC):**  
   Modern runtimes (Java 17+) provide the Z Garbage Collector (`-XX:+UseZGC`), which keeps pause times below a millisecond even under sustained high network throughput.

3. **Portability:**  
   A single compiled JAR runs across Windows, Linux, and macOS without native binary dependencies or compiler toolchain differences.

---

## Quick Start

### Requirements
* **Computer:** Windows, Linux, or macOS with **Python 3** and **Java 8+** (Java 17+ recommended).
* **ADB:** [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) installed and in your `PATH` or standard SDK directory.
* **Device:** Android 5.0+ or Meta Quest with **USB debugging enabled**.

### Running

* **Windows:** Run `run_watchdog.cmd` (or double-click it).
* **Linux / macOS:** Run `./run_watchdog.sh`.

The supervisor will:
1. Start the Java relay server (`gnirehtet.jar`).
2. Wait for a device over ADB.
3. Install `gnirehtet.apk` if missing.
4. Set up the ADB reverse tunnel and start the VPN service.
5. Monitor link health and recover automatically if the connection drops.

---

## Command-Line Options

Arguments can be passed to `run_watchdog.cmd` / `run_watchdog.sh` or directly to `gnirehtet_watchdog.py`:

```text
usage: gnirehtet_watchdog.py [-h] [-s SERIAL] [-p PORT] [--adb ADB]
                             [--java JAVA] [--jar JAR] [--jvm-args JVM_ARGS]
                             [--interval INTERVAL]

options:
  -h, --help           Show this help message and exit
  -s, --serial SERIAL  Target specific device serial (default: auto-detects first device)
  -p, --port PORT      Relay server port (default: 31416)
  --adb ADB            Custom path to adb executable
  --java JAVA          Custom path to java executable
  --jar JAR            Custom path to gnirehtet.jar
  --jvm-args JVM_ARGS  Custom JVM arguments (e.g. '-XX:+UseZGC -Xms1g -Xmx1g')
  --interval INTERVAL  Device polling interval in seconds (default: 1.0)
```

Example:
```powershell
.\run_watchdog.cmd --port 31416
```

---

## Building from Source

### Java Relay (`relay-java`)
Requires JDK 17, 21, or 25:

```powershell
cd relay-java
.\gradlew.bat assembleRelease
```
*(On Linux/macOS: `./gradlew assembleRelease`)*

The output JAR is generated at `relay-java/build/libs/gnirehtet.jar`.

### Android Client (`app`)
Can be built using Android Studio or the root Gradle wrapper with the Android SDK and NDK installed:

```powershell
.\gradlew.bat assembleRelease
```

---

## License & Attribution

This project is licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

* **Original Author:** [Genymobile](https://www.genymobile.com/) ([Original Repository](https://github.com/Genymobile/gnirehtet)), Copyright (C) 2017 Genymobile.
* **Continuation & Modifications:** Copyright (C) 2026 Synthos.
