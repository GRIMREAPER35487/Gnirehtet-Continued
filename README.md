# Gnirehtet Continued by Synthos

A high-performance, resilient **reverse tethering** supervisor over ADB for Android and Meta Quest VR headsets.

It allows Android devices and Meta Quest headsets to share the internet connection of the computer they are connected to over USB. It requires **no root access** on the device or computer.

---

## What's New in this Continued Edition

This edition enhances the original project with a dedicated auto-recovery supervisor and optimizations tailored for Meta Quest headsets and continuous wired VR:

* **Resilient Auto-Recovery:** Eliminates the classic batch file freezes when cables wiggle or headsets sleep. The supervisor automatically detects disconnects and re-establishes the reverse tether the moment the device reconnects.
* **Auto-Provisioning Client APK:** Automatically detects whether the Gnirehtet VPN app is installed on the connected device and seamlessly installs `gnirehtet.apk` on first run.
* **Modern Java & Low-Latency ZGC:** Built and verified on modern Java runtimes (Java 17, 21, and 25). Automatically enables low-latency ZGC garbage collection (`-XX:+UseZGC`) on modern JVMs to eliminate latency spikes.
* **Dynamic Environment Discovery:** Automatically locates your ADB tools and Java installations across standard locations without any hardcoded paths or environment setup required.
* **In-Headset Status Notifications:** 
  * Audio cues (distinct tones on connect and disconnect).
  * Optional VRChat OSC chatbox notifications (`[Gnirehtet] Reverse tether connected!`).
* **Cross-Platform Launchers:** Simple one-click launchers for both Windows (`run_watchdog.cmd`) and Linux/macOS (`run_watchdog.sh`).

---

## Why Java Over Rust for ADB Reverse Tethering?

The original Genymobile project historically offered both Java and native Rust implementations. While Rust produces small native binaries, in practice for **continuous ADB reverse tethering (especially wired VR / Meta Quest use), the Java relay proved significantly more resilient**:

1. **Superior Socket Recovery & Zero Zombie States:**  
   ADB reverse tunnels (`localabstract:gnirehtet -> tcp:31416`) are prone to sudden socket resets, broken pipes, and transport hangs when USB cables wiggle or headsets sleep. In native Rust (`mio`), abrupt disconnects frequently caused thread lockups or zombie socket states where port 31416 remained bound but unresponsive. The Java NIO implementation handles per-client socket disconnects cleanly, resetting client state without taking down or corrupting the listening socket.

2. **Sub-Millisecond Latency with Modern ZGC:**  
   Historical concerns with Java centered around garbage collection pauses. On modern runtimes (Java 17, 21, and 25), the **Z Garbage Collector (`-XX:+UseZGC`)** keeps GC pauses sub-millisecond, eliminating micro-stutters and delivering rock-solid network throughput for high-bandwidth VR streaming.

3. **Bulletproof Cross-Platform Stability:**  
   A single compiled JAR executes identically across Windows 10/11, Linux, and macOS without the compiler variances, C-runtime dependencies, or MinGW cross-compilation quirks of native binaries.

---

## Quick Start

### 1. Requirements
* **Computer:** Windows, Linux, or macOS with **Python 3** and **Java 8 or higher** (JDK 17, 21, or 25 recommended).
* **ADB:** [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) (`adb.exe` on PATH or in standard SDK directory).
* **Device:** Android 5.0+ or Meta Quest with **USB debugging enabled**.

### 2. Launching

* **Windows:** Double-click `run_watchdog.cmd` (or run `.\run_watchdog.cmd` in PowerShell/CMD).
* **Linux / macOS:** Run `./run_watchdog.sh`.

The supervisor will automatically start the Java relay server, wait for your headset or phone to connect, install the client APK if needed, and activate the reverse tether.

---

## Command-Line Options

You can pass arguments directly to the supervisor script or through the launcher:

```text
usage: gnirehtet_watchdog.py [-h] [-s SERIAL] [-p PORT] [--adb ADB]
                             [--java JAVA] [--jar JAR] [--jvm-args JVM_ARGS]
                             [--interval INTERVAL] [--no-sound] [--no-osc]
                             [--osc-ip OSC_IP] [--osc-port OSC_PORT]

options:
  -h, --help           Show this help message and exit
  -s, --serial SERIAL  Target specific device serial (default: auto-detects first device)
  -p, --port PORT      Relay server port (default: 31416)
  --adb ADB            Custom path to adb executable
  --java JAVA          Custom path to java executable
  --jar JAR            Custom path to gnirehtet.jar
  --jvm-args JVM_ARGS  Custom JVM arguments (e.g. '-XX:+UseZGC -Xms1g -Xmx1g')
  --interval INTERVAL  Device polling interval in seconds (default: 1.0)
  --no-sound           Disable audio tones on connect/disconnect
  --no-osc             Disable VRChat OSC notifications
  --osc-ip OSC_IP      VRChat OSC destination IP (default: 127.0.0.1)
  --osc-port OSC_PORT  VRChat OSC destination port (default: 9000)
```

Example:
```powershell
.\run_watchdog.cmd --no-osc --port 31416
```

---

## Building from Source

### Java Relay (`relay-java`)
The desktop relay server is powered by **Gradle 9.1** and supports compiling under modern JDKs (JDK 21 or 25 recommended):

```powershell
cd relay-java
.\gradlew.bat assembleRelease
```
*(On Linux/macOS: `./gradlew assembleRelease`)*

This produces the updated relay binary in `relay-java/build/libs/gnirehtet.jar`.

### Android Client (`app`)
The Android APK can be compiled using Android Studio or via the Gradle wrapper with the Android SDK installed:

```powershell
.\gradlew.bat assembleRelease
```

---

## License & Attribution

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) and [NOTICE](NOTICE) files for details.

* **Original Author:** [Genymobile](https://www.genymobile.com/) ([Original Repository](https://github.com/Genymobile/gnirehtet)), Copyright (C) 2017 Genymobile.
* **Continuation & Modifications:** Copyright (C) 2026 Synthos.
