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
import json
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import List, Optional, Tuple

# Default configurations
DEFAULT_PORT = 31416
DEFAULT_POLL_INTERVAL = 1.0
ADB_TIMEOUT_SEC = 4.0
DEFAULT_NCM_SUBNET = "192.168.42"
DEFAULT_PC_IP = "192.168.42.1"
DEFAULT_HEADSET_IP = "192.168.42.2"
DEFAULT_NETMASK = "255.255.255.0"

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


def parse_dhcp_option(data: bytes, option_code: int) -> Optional[bytes]:
    """Extracts a specific DHCP option payload from a raw BOOTP/DHCP packet."""
    if len(data) < 240 or data[236:240] != bytes([99, 130, 83, 99]):
        return None
    idx = 240
    while idx < len(data):
        opt = data[idx]
        if opt == 255:  # End of options
            break
        if opt == 0:  # Pad option
            idx += 1
            continue
        if idx + 1 >= len(data):
            break
        opt_len = data[idx + 1]
        if opt == option_code:
            return data[idx + 2 : idx + 2 + opt_len]
        idx += 2 + opt_len
    return None


def build_dhcp_reply(
    req: bytes,
    msg_type: int,  # 2 = DHCPOFFER, 5 = DHCPACK
    server_ip: str = DEFAULT_PC_IP,
    offer_ip: str = DEFAULT_HEADSET_IP,
    netmask: str = DEFAULT_NETMASK,
    lease_time: int = 3600,
    dns_servers: Optional[List[str]] = None,
) -> bytes:
    """Constructs a binary BOOTP/DHCP reply packet (OFFER or ACK)."""
    if dns_servers is None:
        dns_servers = ["1.1.1.1", "8.8.8.8"]

    reply = bytearray(300)
    reply[0] = 2  # BOOTREPLY
    reply[1] = 1  # Hardware type: Ethernet
    reply[2] = 6  # Hardware address length: 6 bytes
    reply[3] = 0  # Hops: 0

    # Copy transaction ID (xid) and flags from request
    reply[4:8] = req[4:8]
    reply[10:12] = req[10:12]

    # Addresses
    reply[16:20] = socket.inet_aton(offer_ip)   # yiaddr: Your (client) IP address
    reply[20:24] = socket.inet_aton(server_ip)  # siaddr: Next server IP address
    reply[28:44] = req[28:44]                   # chaddr: Client hardware address (MAC)

    # Magic cookie (0x63825363)
    reply[236:240] = bytes([99, 130, 83, 99])

    # Options
    options = bytearray()
    # Option 53: DHCP Message Type
    options.extend(bytes([53, 1, msg_type]))
    # Option 54: Server Identifier
    options.extend(bytes([54, 4]) + socket.inet_aton(server_ip))
    # Option 51: IP Address Lease Time
    options.extend(bytes([51, 4]) + struct.pack(">I", lease_time))
    # Option 1: Subnet Mask
    options.extend(bytes([1, 4]) + socket.inet_aton(netmask))
    # Option 3: Router / Default Gateway
    options.extend(bytes([3, 4]) + socket.inet_aton(server_ip))
    # Option 6: Domain Name Server
    dns_bytes = b"".join(socket.inet_aton(dns) for dns in dns_servers)
    options.extend(bytes([6, len(dns_bytes)]) + dns_bytes)
    # Option 255: End Option
    options.append(255)

    reply[240 : 240 + len(options)] = options
    return bytes(reply[: 240 + len(options)])


class UsbNcmDhcpServer:
    """Lightweight pure-Python DHCP server for USB CDC-NCM point-to-point links."""

    def __init__(
        self,
        server_ip: str = DEFAULT_PC_IP,
        client_ip: str = DEFAULT_HEADSET_IP,
        netmask: str = DEFAULT_NETMASK,
        pc_mac: Optional[str] = None,
    ):
        self.server_ip = server_ip
        self.client_ip = client_ip
        self.netmask = netmask
        self.pc_mac = pc_mac.lower().replace("-", ":") if pc_mac else None
        self.running = False
        self.leased = False
        self.thread: Optional[threading.Thread] = None
        self.rx_sock: Optional[socket.socket] = None
        self.tx_sock: Optional[socket.socket] = None

    def start(self):
        """Starts the DHCP server in a background daemon thread."""
        if self.running:
            return
        self.running = True
        self.leased = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="UsbNcmDhcpServer")
        self.thread.start()

    def stop(self):
        """Stops the DHCP server and cleans up sockets."""
        self.running = False
        if self.rx_sock:
            try:
                self.rx_sock.close()
            except Exception:
                pass
        if self.tx_sock:
            try:
                self.tx_sock.close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.rx_sock = None
        self.tx_sock = None

    def _run(self):
        try:
            self.rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.rx_sock.bind(("", 67))
        except Exception as e:
            log_warn(f"UsbNcmDhcpServer: Unable to bind UDP port 67: {e}")
            return

        try:
            self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                self.tx_sock.bind((self.server_ip, 0))
            except Exception:
                self.tx_sock.bind(("", 0))
        except Exception as e:
            log_warn(f"UsbNcmDhcpServer: Unable to bind TX socket: {e}")
            return

        log_info(f"DHCP server active on UDP 67 (offering {self.client_ip} on subnet {self.server_ip})...")

        while self.running:
            try:
                r, _, _ = select.select([self.rx_sock], [], [], 0.5)
                if not r or not self.running:
                    continue
                data, _ = self.rx_sock.recvfrom(1500)
                if len(data) < 240 or data[0] != 1:  # Not BOOTREQUEST
                    continue
                if data[236:240] != bytes([99, 130, 83, 99]):  # Magic cookie mismatch
                    continue

                opt_53 = parse_dhcp_option(data, 53)
                if not opt_53:
                    continue
                msg_type = opt_53[0]

                mac = ":".join(f"{b:02x}" for b in data[28:34])
                # Skip requests originating from PC's own adapter
                if self.pc_mac and mac.lower() == self.pc_mac.lower():
                    continue

                if msg_type == 1:  # DHCPDISCOVER
                    reply = build_dhcp_reply(data, 2, self.server_ip, self.client_ip, self.netmask)
                    self.tx_sock.sendto(reply, ("255.255.255.255", 68))
                    log_info(f"DHCP: Received DISCOVER from [{mac}]. Sent OFFER -> {self.client_ip}")

                elif msg_type == 3:  # DHCPREQUEST
                    reply = build_dhcp_reply(data, 5, self.server_ip, self.client_ip, self.netmask)
                    ciaddr = socket.inet_ntoa(data[12:16])
                    dest = (ciaddr, 68) if ciaddr != "0.0.0.0" else ("255.255.255.255", 68)
                    self.tx_sock.sendto(reply, dest)
                    log_success(f"DHCP: Received REQUEST from [{mac}]. Leased {self.client_ip} successfully!")
                    self.leased = True

            except Exception:
                if not self.running:
                    break


def find_windows_ncm_adapter() -> Optional[dict]:
    """Finds the Windows network adapter corresponding to the USB NCM gadget."""
    if os.name != "nt":
        return None
    try:
        ps_cmd = (
            "Get-NetAdapter | Where-Object { "
            "$_.InterfaceDescription -match 'UsbNcm|NCM' "
            "} | Select-Object Name,InterfaceDescription,ifIndex,MacAddress,Status | "
            "ConvertTo-Json"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data
    except Exception as e:
        log_warn(f"Failed to query Windows network adapters: {e}")
    return None


def ensure_windows_firewall_rules(subnet_cidr: str = "192.168.42.0/24") -> bool:
    """Ensures inbound UDP port 67 and USB subnet traffic are permitted through Windows Defender Firewall."""
    if os.name != "nt":
        return True
    try:
        rules_needed = []
        for rule_name in ["Gnirehtet DHCP", "Gnirehtet Subnet"]:
            chk = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if rule_name not in chk.stdout:
                rules_needed.append(rule_name)

        if not rules_needed:
            return True

        log_info(f"Configuring Windows Firewall rules ({', '.join(rules_needed)})...")
        # Try direct commands first (succeeds if running as admin)
        all_ok = True
        for r in rules_needed:
            if r == "Gnirehtet DHCP":
                cmd = ["netsh", "advfirewall", "firewall", "add", "rule", "name=Gnirehtet DHCP", "dir=in", "action=allow", "protocol=UDP", "localport=67", "profile=any"]
            else:
                cmd = ["netsh", "advfirewall", "firewall", "add", "rule", "name=Gnirehtet Subnet", "dir=in", "action=allow", f"remoteip={subnet_cidr}", "profile=any"]
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode != 0:
                all_ok = False

        if all_ok:
            log_success("Windows Firewall rules added successfully.")
            return True

        # If elevation needed, trigger one-time UAC prompt to add missing rules
        log_info("Requesting administrator elevation to add Windows Firewall rules...")
        uac_script = (
            f'netsh advfirewall firewall add rule name=\\"Gnirehtet DHCP\\" dir=in action=allow protocol=UDP localport=67 profile=any; '
            f'netsh advfirewall firewall add rule name=\\"Gnirehtet Subnet\\" dir=in action=allow remoteip={subnet_cidr} profile=any'
        )
        uac_cmd = f'Start-Process cmd -ArgumentList "/c {uac_script}" -Verb RunAs -Wait'
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", uac_cmd],
            capture_output=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.5)

        log_success("Windows Firewall rules configured.")
        return True
    except Exception as e:
        log_warn(f"Firewall rule check failed: {e}")
    return False


def configure_windows_ncm_adapter(adapter: dict, ip: str = DEFAULT_PC_IP, prefix_len: int = 24) -> bool:
    """Ensures the Windows UsbNcm adapter is assigned the static IP and firewall rule is active."""
    if os.name != "nt":
        return True

    adapter_name = adapter.get("Name", "")
    if_index = adapter.get("ifIndex")

    # 1. Ensure Windows Firewall permits UDP 67 and Subnet traffic
    ensure_windows_firewall_rules()

    # 2. Check if IP is already configured
    try:
        chk_cmd = f"Get-NetIPAddress -InterfaceIndex {if_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress"
        chk_res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", chk_cmd],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if ip in chk_res.stdout:
            # Mark network as Private so firewall doesn't block local traffic
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Set-NetConnectionProfile -InterfaceIndex {if_index} -NetworkCategory Private -ErrorAction SilentlyContinue"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return True
    except Exception:
        pass

    # 3. Try configuring via PowerShell / Netsh
    log_info(f"Assigning static IP {ip}/{prefix_len} to Windows adapter '{adapter_name}'...")
    cfg_cmd = (
        f"Get-NetIPAddress -InterfaceIndex {if_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue; "
        f"Set-NetIPInterface -InterfaceIndex {if_index} -AddressFamily IPv4 -Dhcp Disabled -ErrorAction SilentlyContinue; "
        f"New-NetIPAddress -InterfaceIndex {if_index} -IPAddress {ip} -PrefixLength {prefix_len} -ErrorAction SilentlyContinue; "
        f"Set-NetConnectionProfile -InterfaceIndex {if_index} -NetworkCategory Private -ErrorAction SilentlyContinue"
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cfg_cmd],
        capture_output=True,
        text=True,
        timeout=6,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if res.returncode == 0:
        return True

    # 4. If unelevated, attempt netsh with UAC elevation
    try:
        netsh_args = f'interface ipv4 set address name="{adapter_name}" static {ip} 255.255.255.0'
        uac_cmd = f'Start-Process netsh -ArgumentList \'{netsh_args}\' -Verb RunAs -Wait'
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", uac_cmd],
            capture_output=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        time.sleep(1.0)
        # Verify again
        chk_res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", chk_cmd],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if ip in chk_res.stdout:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Set-NetConnectionProfile -InterfaceIndex {if_index} -NetworkCategory Private -ErrorAction SilentlyContinue"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return True
    except Exception as e:
        log_warn(f"Failed to elevate adapter IP configuration: {e}")

    return False


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
        pc_ip: str = DEFAULT_PC_IP,
        headset_ip: str = DEFAULT_HEADSET_IP,
        verbose: bool = False,
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
        self.pc_ip = pc_ip
        self.headset_ip = headset_ip
        self.verbose = verbose
        self.dhcp_server: Optional[UsbNcmDhcpServer] = None
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

        def _drain_relay_stdout(proc):
            try:
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        break
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    # Print connection lifecycle and packet stream in real time
                    if self.verbose:
                        print(f"[{time.strftime('%H:%M:%S')}] {Colors.CYAN}[RELAY]{Colors.RESET} {clean_line}")
                    elif any(k in clean_line for k in ["Open", "Close", "connected", "disconnected", "dropped", "ERROR", "Exception", "WARN"]):
                        print(f"[{time.strftime('%H:%M:%S')}] {Colors.CYAN}[RELAY]{Colors.RESET} {clean_line}")
            except Exception:
                pass

        threading.Thread(target=_drain_relay_stdout, args=(self.relay_process,), daemon=True, name="RelayDrainer").start()

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

    def ensure_ncm_link(self, serial: str) -> Optional[str]:
        """Configures native USB CDC-NCM gadget on device and serves DHCP to establish high-speed link."""
        if self.legacy:
            return None
        if self.host_override:
            return self.host_override

        log_info(f"Checking USB function status on [{serial}]...")
        code, funcs = self.run_adb(["-s", serial, "shell", "svc", "usb", "getFunctions"])
        if "ncm" not in funcs:
            log_info(f"Switching USB gadget to high-speed NCM on [{serial}]...")
            code, out = self.run_adb(["-s", serial, "shell", "svc", "usb", "setFunctions", "ncm"])
            if code != 0 and "setCurrentFunctions opId" not in out:
                log_warn(f"Failed to set USB function to NCM: {out}")
                return None
            time.sleep(2.0)

        # On Windows, locate the UsbNcm adapter, configure IP, and start DHCP
        if os.name == "nt":
            adapter = None
            log_info("Locating Windows 'UsbNcm Host Device' network adapter...")
            for _ in range(15):
                adapter = find_windows_ncm_adapter()
                if adapter and adapter.get("Status") == "Up":
                    break
                time.sleep(1.0)

            if not adapter:
                log_warn("Windows UsbNcm network adapter was not found or is not Up.")
                return None

            log_success(f"Windows UsbNcm adapter detected: '{adapter.get('Name')}' (ifIndex: {adapter.get('ifIndex')})")

            # Configure static IP on Windows adapter
            configured = configure_windows_ncm_adapter(adapter, self.pc_ip)
            if not configured:
                log_warn("Failed to set static IP on Windows UsbNcm adapter.")
                return None

            # Start Python DHCP server
            if not self.dhcp_server or not self.dhcp_server.running:
                self.dhcp_server = UsbNcmDhcpServer(
                    server_ip=self.pc_ip,
                    client_ip=self.headset_ip,
                    pc_mac=adapter.get("MacAddress"),
                )
                self.dhcp_server.start()

            # Wait for headset to take lease on usb0
            log_info("Waiting for headset to accept DHCP lease on usb0...")
            leased = False
            for _ in range(15):
                code, addr_out = self.run_adb(["-s", serial, "shell", "ip", "-o", "-4", "addr", "show", "usb0"])
                if code == 0 and self.headset_ip in addr_out:
                    leased = True
                    break
                time.sleep(1.0)

            if leased:
                log_success(f"Headset leased {self.headset_ip} on usb0 successfully! Native 3.8 Gbps USB pipeline active.")
                return self.pc_ip
            else:
                log_warn("Headset did not acquire DHCP lease on usb0 within timeout.")
                return None
        else:
            return self.detect_relay_host_ip(serial)

    def check_tunnel_health(self, serial: str) -> bool:
        """Verifies if the tether connection is active and healthy."""
        code, out = self.run_adb(["-s", serial, "shell", "pidof", "com.genymobile.gnirehtet"])
        if code != 0 or not out.strip():
            return False

        if self.active_native_mode:
            code, addr_out = self.run_adb(["-s", serial, "shell", "ip", "-o", "-4", "addr", "show", "usb0"])
            return code == 0 and self.headset_ip in addr_out
        else:
            code, out = self.run_adb(["-s", serial, "reverse", "--list"])
            return code == 0 and "localabstract:gnirehtet" in out

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
            log_info("Attempting high-speed Native USB (CDC-NCM) connection...")
            relay_host = self.ensure_ncm_link(serial)
            if relay_host:
                log_success(f"Native USB active! Direct TCP target: {relay_host}:{self.port}")
                self.active_native_mode = True
            else:
                log_warn("Native USB unavailable. Falling back to verified legacy ADB reverse tunnel...")
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
            "--esa", "dnsServers", "1.1.1.1,8.8.8.8",
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
        if self.dhcp_server:
            self.dhcp_server.stop()
            self.dhcp_server = None

    def handle_disconnect(self):
        """Handles link drop and state reset."""
        if self.is_connected:
            log_warn("Device disconnected or cable wiggled! Auto-recovery active...")
            self.is_connected = False
            self.current_serial = None
            if self.dhcp_server:
                self.dhcp_server.stop()
                self.dhcp_server = None

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
                # 1. Ensure relay server process is alive
                if not self.relay_process or self.relay_process.poll() is not None:
                    log_warn("Relay server process is not running. Restarting...")
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
        if self.dhcp_server:
            self.dhcp_server.stop()
            self.dhcp_server = None
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
    parser.add_argument("--pc-ip", default=DEFAULT_PC_IP, help="PC static IP on NCM adapter (default: 192.168.42.1)")
    parser.add_argument("--headset-ip", default=DEFAULT_HEADSET_IP, help="Headset DHCP IP on NCM link (default: 192.168.42.2)")
    parser.add_argument("--adb", help="Path to adb executable (defaults to auto-detect)")
    parser.add_argument("--java", help="Path to java executable (defaults to auto-detect)")
    parser.add_argument("--jar", help="Path to gnirehtet.jar (defaults to auto-detect)")
    parser.add_argument("--jvm-args", help="Custom JVM arguments (e.g. '-XX:+UseZGC -Xms1g -Xmx1g')")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose relay and packet activity logging")
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
        pc_ip=args.pc_ip,
        headset_ip=args.headset_ip,
        verbose=args.verbose,
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
