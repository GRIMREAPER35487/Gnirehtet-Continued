# Gnirehtet Continued - Developer Guide

This document explains the architecture and developer workflow for Gnirehtet Continued.

---

## Getting Started

### Requirements
- **Java Relay:** JDK 17, 21, or 25.
- **Android Client:** Android Studio / Android SDK (Platform 28) and NDK (for native C++ packet routing).
- **Supervisor:** Python 3.8+.

---

## Build Commands

### 1. Building the Desktop Relay (`relay-java`)
The relay server runs on Gradle 9.1:

```powershell
cd relay-java
.\gradlew.bat assembleRelease
```
*(On Linux/macOS: `./gradlew assembleRelease`)*

This compiles the relay server, runs unit tests, runs Checkstyle linting, and produces `gnirehtet.jar` under `relay-java/build/libs/`.

### 2. Building the Android Client (`app`)
The Android APK can be built through Android Studio or with the root Gradle wrapper:

```powershell
.\gradlew.bat assembleRelease
```
*(Outputs APK to `app/build/outputs/apk/release/gnirehtet-release.apk`)*

---

## Architecture Overview

Gnirehtet consists of three primary components:

1. **Android Client (`app/`):**
   - Implements an Android `VpnService` that captures all IPv4 traffic on the device.
   - Encapsulates raw IP packets into a local abstract socket stream (`localabstract:gnirehtet`).
   - Forwards packets across ADB reverse tunnel to the host computer.

2. **Java Relay Server (`relay-java/`):**
   - High-performance, non-blocking asynchronous I/O server using `Java NIO`.
   - Listens on `127.0.0.1:31416` for connections established over `adb reverse`.
   - Strips packet headers, opens standard TCP/UDP sockets to the target internet destinations, and proxies bidirectional traffic with minimal latency.
   - Uses zero-copy packet slicing where `IPv4Header`, `TCPHeader`, and `UDPHeader` share slices of the raw packet buffer.

3. **Auto-Recovery Supervisor (`gnirehtet_watchdog.py`):**
   - Monitors USB device connectivity via ADB.
   - Restarts the tunnel and restarts the VPN service on sleep/disconnect/reconnect events.
   - Provisions `gnirehtet.apk` automatically on first connect.
   - Sends VRChat OSC and audio cue notifications.

---

## Relay Server Internals

### Selector & Asynchronous I/O
The relay server uses a single Java NIO `Selector` managing:
- The server socket listening on port 31416.
- A socket for each connected client.
- Outgoing TCP and UDP connections to the destination endpoints.

Because the relay is event-driven and monothreaded per selector loop, packet processing avoids lock contention and thread synchronization overhead.

### Flow Control & Dropping Strategy
- **UDP:** Packets are forwarded as-is. Idle UDP connections expire after 2 minutes of inactivity.
- **TCP:** Packet loss from device-to-network is handled by the client's TCP retransmission. However, data retrieved from the target server must never be lost. When internal buffers approach capacity, `interestOps` disables read readiness on the remote socket until the client drains the buffer.

---

## License & Attribution

This project is licensed under the [Apache License 2.0](LICENSE).
Original work Copyright (C) 2017 Genymobile.
Continuation and modifications Copyright (C) 2026 Synthos.
