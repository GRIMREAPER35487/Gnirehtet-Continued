"""
Gnirehtet Continued by Synthos - Auto-Recovery Watchdog & Supervisor
=====================================================================
A resilient supervisor for Gnirehtet reverse tethering (Android over USB).

Copyright (C) 2026 Synthos
Based on Gnirehtet by Genymobile, Copyright (C) 2017 Genymobile

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import argparse
import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import List, Optional, Tuple

# Default configurations
DEFAULT_PORT = 31416
DEFAULT_POLL_INTERVAL = 1.0
ADB_TIMEOUT_SEC = 4.0

# Process priority flag for Windows (High priority class)
HIGH_PRIORITY_CLASS = 0x00000080 if os.name == "nt" else 0


class Colors:
    CYAN = "\033[96m" if sys.stdout.isatty() else ""
    GREEN = "\033[92m" if sys.stdout.isatty() else ""
    YELLOW = "\033[93m" if sys.stdout.isatty() else ""
    RED = "\033[91m" if sys.stdout.isatty() else ""
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""


def log_info(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {Colors.CYAN}[INFO]{Colors.RESET} {msg}")


def log_success(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {Colors.GREEN}{Colors.BOLD}[OK]{Colors.RESET} {msg}")


def log_warn(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {Colors.YELLOW}[WARN]{Colors.RESET} {msg}")


def log_error(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {Colors.RED}{Colors.BOLD}[ERROR]{Colors.RESET} {msg}")


def find_java(explicit_path: Optional[str] = None) -> str:
    """Dynamically locates a Java runtime without hardcoded user paths."""
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path

    env_java = os.getenv("JAVA")
    if env_java and os.path.isfile(env_java):
        return env_java

    java_home = os.getenv("JAVA_HOME")
    if java_home:
        candidate = os.path.join(java_home, "bin", "java.exe" if os.name == "nt" else "java")
        if os.path.isfile(candidate):
            return candidate

    which_java = shutil.which("java")
    if which_java:
        return which_java

    if os.name == "nt":
        # Search common Windows JDK installation directories dynamically
        search_patterns = [
            r"C:\Program Files\Eclipse Adoptium\jdk-*\bin\java.exe",
            r"C:\Program Files\Java\jdk-*\bin\java.exe",
            r"C:\Program Files\Microsoft\jdk-*\bin\java.exe",
            r"C:\Program Files\BellSoft\*\bin\java.exe",
        ]
        candidates = []
        for pat in search_patterns:
            candidates.extend(glob.glob(pat))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0]

    return "java"


def find_adb(explicit_path: Optional[str] = None) -> str:
    """Dynamically locates the ADB executable without hardcoded user paths."""
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path

    # Check local script directory or working directory first
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_name = "adb.exe" if os.name == "nt" else "adb"
    for base in [script_dir, os.getcwd()]:
        candidate = os.path.join(base, local_name)
        if os.path.isfile(candidate):
            return candidate

    env_adb = os.getenv("ADB")
    if env_adb and os.path.isfile(env_adb):
        return env_adb

    # Check ANDROID_HOME or ANDROID_SDK_ROOT
    for sdk_env in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        sdk_root = os.getenv(sdk_env)
        if sdk_root:
            candidate = os.path.join(sdk_root, "platform-tools", local_name)
            if os.path.isfile(candidate):
                return candidate

    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    if os.name == "nt":
        # Check standard user local app data SDK path dynamically
        local_app_data = os.getenv("LOCALAPPDATA", "")
        if local_app_data:
            candidate = os.path.join(local_app_data, r"Android\Sdk\platform-tools\adb.exe")
            if os.path.isfile(candidate):
                return candidate

    return "adb"


def find_jar(explicit_path: Optional[str] = None) -> str:
    """Dynamically locates gnirehtet.jar."""
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "gnirehtet.jar"),
        os.path.join(os.getcwd(), "gnirehtet.jar"),
        os.path.join(script_dir, "relay-java", "build", "libs", "gnirehtet.jar"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return "gnirehtet.jar"


def get_java_major_version(java_bin: str) -> Optional[int]:
    """Detects Java major version to determine feature compatibility."""
    try:
        res = subprocess.run([java_bin, "-version"], capture_output=True, text=True, timeout=3)
        output = res.stderr or res.stdout
        match = re.search(r'version "(?:1\.)?(\d+)', output)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def get_default_jvm_args(java_bin: str) -> List[str]:
    """Selects optimal JVM flags based on Java version."""
    custom_args = os.getenv("GNIREHTET_JVM_ARGS")
    if custom_args:
        return custom_args.split()

    major = get_java_major_version(java_bin)
    # ZGC is standard production in Java 15+
    if major is not None and major >= 15:
        return ["-XX:+UseZGC", "-Xms512m", "-Xmx1g"]
    return ["-Xms512m", "-Xmx1g"]


class GnirehtetSupervisor:
    def __init__(
        self,
        port: int = DEFAULT_PORT,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        java_path: Optional[str] = None,
        jar_path: Optional[str] = None,
        jvm_args: Optional[List[str]] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        legacy: bool = False,
        host: Optional[str] = None,
    ):
        self.port = port
        self.target_serial = serial or os.getenv("ANDROID_SERIAL")
        self.adb = find_adb(adb_path)
        self.java = find_java(java_path)
        self.jar = find_jar(jar_path)
        self.jvm_args = jvm_args if jvm_args is not None else get_default_jvm_args(self.java)
        self.poll_interval = poll_interval
        self.legacy = legacy
        self.host_override = host
        self.active_native_mode = False

        self.relay_process: Optional[subprocess.Popen] = None
        self.current_serial: Optional[str] = None
        self.is_connected = False
        self.adb_fail_count = 0
        self.running = True

    def run_adb(self, args: List[str], timeout: float = ADB_TIMEOUT_SEC) -> Tuple[int, str]:
        """Runs an adb command with timeout protection against USB hangs."""
        cmd = [self.adb] + args
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.adb_fail_count = 0
            return res.returncode, (res.stdout or res.stderr).strip()
        except subprocess.TimeoutExpired:
            self.adb_fail_count += 1
            log_warn(f"ADB command timed out ({timeout}s): {' '.join(cmd)}")
            if self.adb_fail_count >= 2:
                self.repair_adb()
            return -1, "TIMEOUT"
        except Exception as e:
            return -1, str(e)

    def repair_adb(self):
        """Restarts the ADB host server if USB transport locks up."""
        log_warn("ADB daemon appears unresponsive. Restarting ADB host server...")
        try:
            subprocess.run([self.adb, "kill-server"], timeout=3)
            time.sleep(0.5)
            subprocess.run([self.adb, "start-server"], timeout=5)
            self.adb_fail_count = 0
            log_info("ADB host server restarted successfully.")
        except Exception as e:
            log_error(f"Failed to restart ADB server: {e}")

    def is_relay_listening(self) -> bool:
        """Checks if relay port is actively listening."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def start_relay_server(self):
        """Starts the Java relay server with high priority and configured JVM flags."""
        if self.is_relay_listening():
            log_info(f"Relay server is already listening on port {self.port}.")
            return

        jvm_flags_str = " ".join(self.jvm_args) if self.jvm_args else "default"
        log_info(f"Starting Java relay server on port {self.port} [{jvm_flags_str}]...")

        cmd = [self.java] + self.jvm_args + ["-jar", self.jar, "relay", "-p", str(self.port)]
        
        self.relay_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=HIGH_PRIORITY_CLASS
        )
        time.sleep(0.8)
        if self.is_relay_listening():
            log_success("Relay server successfully started and listening.")
        else:
            log_warn("Relay server launched, waiting for port to bind...")

    def stop_relay_server(self):
        """Stops the managed Java relay server."""
        if self.relay_process and self.relay_process.poll() is None:
            log_info("Stopping relay server process...")
            self.relay_process.terminate()
            try:
                self.relay_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.relay_process.kill()
        self.relay_process = None

    def get_connected_devices(self) -> List[Tuple[str, str]]:
        """Returns list of (serial, state) for connected devices."""
        code, out = self.run_adb(["devices"])
        if code != 0 or not out:
            return []
        devices = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        return devices

    def detect_relay_host_ip(self, serial: str) -> Optional[str]:
        """Detects the PC host IP address on the native USB network interface."""
        if self.host_override:
            return self.host_override

        # 1. Query device routing table
        code, out = self.run_adb(["-s", serial, "shell", "ip", "route"])
        if code == 0 and out:
            # Check for routes with a gateway on a usb/ncm/rndis/eth interface
            # e.g., "default via 192.168.137.1 dev usb0" or "192.168.137.0/24 via 192.168.137.1 dev ncm0"
            for line in out.splitlines():
                line = line.strip()
                m_gw = re.search(r"via\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\s+dev\s+(usb\w*|ncm\w*|rndis\w*|eth\w*)", line)
                if m_gw:
                    return m_gw.group(1)

        # 2. Query ARP table / neighbor cache for usb/ncm/rndis/eth interfaces
        code, out = self.run_adb(["-s", serial, "shell", "cat", "/proc/net/arp"])
        if code == 0 and out:
            # Format: IP address HW type Flags HW address Mask Device
            for line in out.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 6:
                    ip_addr, dev = parts[0], parts[5]
                    if re.match(r"^(usb\w*|ncm\w*|rndis\w*|eth\w*)$", dev):
                        if re.match(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$", ip_addr) and ip_addr != "0.0.0.0":
                            return ip_addr

        # 3. Query ip neigh show
        code, out = self.run_adb(["-s", serial, "shell", "ip", "neigh", "show"])
        if code == 0 and out:
            for line in out.splitlines():
                m_neigh = re.search(r"^([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\s+dev\s+(usb\w*|ncm\w*|rndis\w*|eth\w*)", line.strip())
                if m_neigh:
                    return m_neigh.group(1)

        # 4. Check interface IPv4 addresses directly and infer host IP on point-to-point subnet
        code, out = self.run_adb(["-s", serial, "shell", "ip", "-4", "addr", "show"])
        if code == 0 and out:
            current_dev = None
            for line in out.splitlines():
                line = line.strip()
                dev_match = re.match(r"^\d+:\s+([a-zA-Z0-9_\-]+):", line)
                if dev_match:
                    current_dev = dev_match.group(1)
                elif current_dev and re.match(r"^(usb\w*|ncm\w*|rndis\w*|eth\w*)$", current_dev):
                    inet_match = re.search(r"inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)/(\d+)", line)
                    if inet_match:
                        device_ip = inet_match.group(1)
                        octets = device_ip.split(".")
                        last_octet = int(octets[3])
                        # In typical point-to-point links (e.g. 192.168.137.x), host is .1 if device is not .1, else .2
                        host_last = 1 if last_octet != 1 else 2
                        inferred_host = f"{octets[0]}.{octets[1]}.{octets[2]}.{host_last}"
                        return inferred_host

        return None

    def check_tunnel_health(self, serial: str) -> bool:
        """Verifies if the tether connection is active and healthy."""
        if self.active_native_mode:
            code, out = self.run_adb(["-s", serial, "shell", "pidof", "com.genymobile.gnirehtet"])
            return code == 0 and bool(out.strip())
        else:
            code, out = self.run_adb(["-s", serial, "reverse", "--list"])
            if code != 0 or "localabstract:gnirehtet" not in out:
                return False
            code, out = self.run_adb(["-s", serial, "shell", "pidof", "com.genymobile.gnirehtet"])
            return code == 0 and bool(out.strip())

    def setup_headset_tether(self, serial: str) -> bool:
        """Sets up high-speed Native USB transport or fallback legacy reverse tunnel."""
        log_info(f"Device connected: [{serial}]. Configuring tether...")

        # 1. Verify APK is installed on the device; auto-install if missing
        code, out = self.run_adb(["-s", serial, "shell", "pm", "path", "com.genymobile.gnirehtet"])
        if "package:" not in out:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            apk_path = os.path.join(script_dir, "gnirehtet.apk")
            if not os.path.isfile(apk_path):
                candidate = os.path.join(script_dir, "app", "build", "outputs", "apk", "release", "gnirehtet-release.apk")
                if os.path.isfile(candidate):
                    apk_path = candidate
            if os.path.isfile(apk_path):
                log_info(f"Gnirehtet APK missing on [{serial}]. Auto-installing {os.path.basename(apk_path)}...")
                inst_code, inst_out = self.run_adb(["-s", serial, "install", "-r", apk_path], timeout=15.0)
                if inst_code == 0:
                    log_success(f"Installed {os.path.basename(apk_path)} onto [{serial}] successfully.")
                else:
                    log_warn(f"Auto-install warning: {inst_out}")

        # 2. Stop any stale instance
        self.run_adb(["-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"])

        # 3. Determine transport mode: Native USB (default) vs Legacy ADB reverse
        relay_host: Optional[str] = None
        if self.legacy:
            log_info("Legacy mode specified (--legacy). Using ADB reverse tunnel...")
            self.active_native_mode = False
        else:
            relay_host = self.detect_relay_host_ip(serial)
            if relay_host:
                log_success(f"Native USB network detected (PC host IP: {relay_host}). Using direct CDC-NCM transport!")
                self.active_native_mode = True
            else:
                log_warn("Native USB network interface not found on device (is 'USB connection for apps' enabled in Quest Settings > Link?). Falling back to legacy ADB reverse tunnel...")
                self.active_native_mode = False

        if not self.active_native_mode:
            # Legacy mode: Reset reverse tunnels and establish new one
            self.run_adb(["-s", serial, "reverse", "--remove-all"])
            code, out = self.run_adb(["-s", serial, "reverse", "localabstract:gnirehtet", f"tcp:{self.port}"])
            if code != 0:
                log_error(f"Failed to set adb reverse: {out}")
                return False
        else:
            # Native USB mode: Clean up any old adb reverse tunnels
            self.run_adb(["-s", serial, "reverse", "--remove-all"])

        # 4. Start the VPN intent on Android
        intent_cmd = [
            "-s", serial,
            "shell", "am", "start",
            "-a", "com.genymobile.gnirehtet.START",
            "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
        ]
        if self.active_native_mode and relay_host:
            intent_cmd.extend(["-e", "relayHost", relay_host, "--ei", "relayPort", str(self.port)])

        code, out = self.run_adb(intent_cmd)
        if code != 0:
            log_error(f"Failed to start Android VPN service: {out}")
            return False

        time.sleep(0.5)
        mode_desc = f"Native USB [Direct IP {relay_host}:{self.port}]" if self.active_native_mode else "Legacy ADB Reverse"
        log_success(f"Tether active and running on [{serial}] via {mode_desc}!")
        return True

    def teardown_headset_tether(self, serial: str):
        """Stops the VPN service on the device and clears tunnels."""
        log_info(f"Tearing down tether on [{serial}]...")
        self.run_adb(["-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"])
        self.run_adb(["-s", serial, "reverse", "--remove-all"])

    def handle_disconnect(self):
        """Handles link drop and state reset."""
        if self.is_connected:
            log_warn("Device disconnected or cable wiggled! Auto-recovery active...")
            self.is_connected = False
            self.current_serial = None

    def supervisor_loop(self):
        """Main resilient supervision loop."""
        if os.name == "nt":
            os.system("title Gnirehtet Continued by Synthos")
        print(f"{Colors.BOLD}{Colors.CYAN}====================================================={Colors.RESET}")
        print(f"{Colors.BOLD}           Gnirehtet Continued by Synthos            {Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}====================================================={Colors.RESET}")
        log_info(f"ADB:   {self.adb}")
        log_info(f"Java:  {self.java}")
        log_info(f"JAR:   {self.jar}")
        log_info(f"Port:  {self.port}")
        mode_str = "Legacy ADB Reverse" if self.legacy else "Native USB (CDC-NCM / Direct IP) [Default]"
        log_info(f"Mode:  {mode_str}")
        if self.host_override:
            log_info(f"Host:  {self.host_override}")
        if self.target_serial:
            log_info(f"Target Serial: {self.target_serial}")

        # Ensure relay server is running
        self.start_relay_server()

        log_info("Waiting for Android / Quest device to connect...")

        while self.running:
            try:
                # 1. Ensure relay server is alive
                if not self.is_relay_listening():
                    log_warn("Relay server is not listening. Restarting...")
                    self.start_relay_server()

                # 2. Check connected devices
                devices = self.get_connected_devices()
                ready_devices = [s for s, state in devices if state == "device"]
                unauth_devices = [s for s, state in devices if state == "unauthorized"]

                if unauth_devices:
                    log_warn("Device unauthorized! Check headset/phone to allow USB debugging.")

                # Filter target serial if specified
                if self.target_serial:
                    target_available = self.target_serial in ready_devices
                    chosen_serial = self.target_serial if target_available else None
                else:
                    chosen_serial = ready_devices[0] if ready_devices else None

                if not chosen_serial:
                    self.handle_disconnect()
                    time.sleep(self.poll_interval)
                    continue

                # 3. Connection state management
                if not self.is_connected or self.current_serial != chosen_serial:
                    self.current_serial = chosen_serial
                    success = self.setup_headset_tether(chosen_serial)
                    if success:
                        self.is_connected = True
                    else:
                        time.sleep(2.0)
                        continue

                # 4. Active link health check
                if self.is_connected:
                    if not self.check_tunnel_health(chosen_serial):
                        log_warn("Tunnel vanished from adb reverse list. Re-establishing...")
                        self.handle_disconnect()
                        time.sleep(1.0)
                        continue

                time.sleep(self.poll_interval)

            except Exception as e:
                log_error(f"Supervisor error: {e}")
                time.sleep(2.0)

    def shutdown(self):
        """Clean shutdown handler."""
        self.running = False
        log_info("Shutting down supervisor...")
        if self.current_serial:
            self.teardown_headset_tether(self.current_serial)
        self.stop_relay_server()
        log_success("Supervisor exited cleanly.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Gnirehtet Auto-Recovery Watchdog & Supervisor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-s", "--serial", help="Specific device serial (defaults to auto-detecting first device)")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="Relay server port")
    parser.add_argument("--legacy", action="store_true", help="Force legacy ADB reverse tunnel instead of native USB CDC-NCM")
    parser.add_argument("--host", help="Custom PC host IP for Native USB mode (auto-detected if omitted)")
    parser.add_argument("--adb", help="Path to adb executable (defaults to auto-detect)")
    parser.add_argument("--java", help="Path to java executable (defaults to auto-detect)")
    parser.add_argument("--jar", help="Path to gnirehtet.jar (defaults to auto-detect)")
    parser.add_argument("--jvm-args", help="Custom JVM arguments (e.g. '-XX:+UseZGC -Xms1g -Xmx1g')")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL, help="Device polling interval (seconds)")
    return parser.parse_args()


def main():
    args = parse_args()
    jvm_args = args.jvm_args.split() if args.jvm_args else None

    supervisor = GnirehtetSupervisor(
        port=args.port,
        serial=args.serial,
        adb_path=args.adb,
        java_path=args.java,
        jar_path=args.jar,
        jvm_args=jvm_args,
        poll_interval=args.interval,
        legacy=args.legacy,
        host=args.host,
    )

    def sig_handler(sig, frame):
        supervisor.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        supervisor.supervisor_loop()
    except KeyboardInterrupt:
        supervisor.shutdown()


if __name__ == "__main__":
    main()
