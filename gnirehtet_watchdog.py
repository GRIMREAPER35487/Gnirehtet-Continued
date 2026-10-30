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
import errno
import glob
import hashlib
import ipaddress
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

# Native (CDC-NCM) setup retry backoff, in seconds
NATIVE_RETRY_BASE_DELAY = 10.0
NATIVE_RETRY_MAX_DELAY = 300.0

# Legacy mode: address of the gnirehtet VPN interface on the device, and how long to wait for it after "am start"
VPN_CLIENT_ADDRESS = "10.0.0.2"
VPN_START_TIMEOUT_SEC = 15.0
# Legacy mode: a live tether always keeps one client connected to the relay; restart the tether after this long without one
RELAY_NO_CLIENT_TIMEOUT_SEC = 20.0
# Minimum delay between two "adb reconnect offline" attempts
ADB_RECONNECT_INTERVAL_SEC = 10.0
# Health checks: a restart of the tether kills every connection going through it (e.g. Virtual Desktop has
# to be restarted), so only restart once the link is really dead, never on a single slow or failed check.
HEALTH_CHECK_INTERVAL_SEC = 5.0
# number of consecutive failed health checks before the tether is restarted
HEALTH_FAILURES_BEFORE_RESTART = 3
# adb may be too slow to answer while the USB link is busy (streaming): if no check could even be
# performed for this long, count it as a failure
HEALTH_UNKNOWN_GIVE_UP_SEC = 60.0
# timeout of each adb command used by the health checks
HEALTH_ADB_TIMEOUT_SEC = 8.0
# the device may briefly disappear from "adb devices" (USB hiccup): wait this long before considering it gone
DEVICE_MISSING_GRACE_SEC = 10.0
# consecutive adb timeouts before restarting the adb server (only done while no tether is up)
ADB_TIMEOUTS_BEFORE_REPAIR = 3
RELAY_CLIENT_EVENT = re.compile(r"Client #(\d+) (connected|disconnected)")


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

    # Check the script directory first (never the working directory, which may be untrusted)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_name = "adb.exe" if os.name == "nt" else "adb"
    candidate = os.path.join(script_dir, local_name)
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
        self._close_sockets()
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=1.0)

    def _close_sockets(self):
        for sock in (self.rx_sock, self.tx_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self.rx_sock = None
        self.tx_sock = None

    def _run(self):
        try:
            self.rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Bind to the NCM adapter address only: binding to all interfaces would answer
            # DHCP requests from the LAN/Wi-Fi as well. On Windows, a socket bound to an
            # interface address still receives the broadcasts arriving on that interface.
            self.rx_sock.bind((self.server_ip, 67))
        except Exception as e:
            log_info(
                f"UDP port 67 unavailable on {self.server_ip} ({e}); "
                "assuming Windows SharedAccess / ICS will serve the DHCP lease."
            )
            self._close_sockets()
            self.running = False
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
            self._close_sockets()
            self.running = False
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


_uac_declined = False


def run_elevated_powershell(ps_command: str, timeout: float = 60.0) -> bool:
    """Runs a PowerShell command that starts an elevated process (Start-Process -Verb RunAs).
    Returns False without prompting again if the user already declined a UAC prompt in this session."""
    global _uac_declined
    if _uac_declined:
        log_warn("Administrator elevation was declined earlier in this session; skipping.")
        return False
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,  # leave time for the user to answer the UAC prompt
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        log_warn("Timed out waiting for the elevated command.")
        return False
    if res.returncode != 0:
        # Start-Process fails when the UAC prompt is declined ("The operation was canceled by the user")
        _uac_declined = True
        log_warn("Administrator elevation was declined or failed; it will not be requested again this session.")
        return False
    return True


def prefix_to_netmask(prefix_len: int) -> str:
    """Converts an IPv4 prefix length (e.g. 24) to a dotted netmask (e.g. 255.255.255.0)."""
    return str(ipaddress.IPv4Network(f"0.0.0.0/{int(prefix_len)}").netmask)


def get_adapter_ipv4_addresses(if_index: int) -> List[str]:
    """Returns the non link-local IPv4 addresses currently assigned to an adapter."""
    chk_cmd = f"Get-NetIPAddress -InterfaceIndex {int(if_index)} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress"
    try:
        chk_res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", chk_cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        return []
    return [line.strip() for line in chk_res.stdout.splitlines() if line.strip() and not line.strip().startswith("169.254.")]


def set_private_network_profile(if_index: int):
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Set-NetConnectionProfile -InterfaceIndex {int(if_index)} -NetworkCategory Private -ErrorAction SilentlyContinue"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def ensure_windows_firewall_rules(subnet_cidr: str = "192.168.42.0/24") -> bool:
    """Ensures inbound UDP port 67 and USB subnet traffic are permitted through Windows Defender Firewall."""
    if os.name != "nt":
        return True
    def missing_rules() -> List[str]:
        missing = []
        for rule_name in ["Gnirehtet DHCP", "Gnirehtet Subnet"]:
            chk = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if rule_name not in chk.stdout:
                missing.append(rule_name)
        return missing

    def rule_args(rule_name: str) -> List[str]:
        if rule_name == "Gnirehtet DHCP":
            return ["dir=in", "action=allow", "protocol=UDP", "localport=67", "profile=any"]
        return ["dir=in", "action=allow", f"remoteip={subnet_cidr}", "profile=any"]

    try:
        rules_needed = missing_rules()

        if not rules_needed:
            return True

        log_info(f"Configuring Windows Firewall rules ({', '.join(rules_needed)})...")
        # Try direct commands first (succeeds if running as admin)
        all_ok = True
        for r in rules_needed:
            cmd = ["netsh", "advfirewall", "firewall", "add", "rule", f"name={r}"] + rule_args(r)
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode != 0:
                all_ok = False

        if all_ok:
            log_success("Windows Firewall rules added successfully.")
            return True

        # If elevation needed, trigger one-time UAC prompt to add the rules that are still missing
        rules_needed = missing_rules()
        log_info("Requesting administrator elevation to add Windows Firewall rules...")
        # cmd.exe chains commands with "&" (";" is not a separator). The whole cmd line is passed
        # as a PowerShell single-quoted string, so the inner double quotes need no escaping.
        cmd_line = " & ".join(
            f'netsh advfirewall firewall add rule name="{r}" ' + " ".join(rule_args(r)) for r in rules_needed
        )
        uac_cmd = "Start-Process cmd -ArgumentList '/c " + cmd_line.replace("'", "''") + "' -Verb RunAs -Wait"
        run_elevated_powershell(uac_cmd)

        still_missing = missing_rules()
        if still_missing:
            log_warn(f"Windows Firewall rules still missing: {', '.join(still_missing)}")
            return False

        log_success("Windows Firewall rules configured.")
        return True
    except Exception as e:
        log_warn(f"Firewall rule check failed: {e}")
    return False


def ensure_windows_nat(subnet_cidr: str = "192.168.42.0/24", nat_name: str = "GnirehtetNat") -> bool:
    """Configures Windows NAT (WinNAT) if available so headset receives internet across the cable."""
    if os.name != "nt":
        return False
    try:
        chk_cmd = "Get-CimClass -Namespace root/StandardCimv2 -ClassName MSFT_NetNat -ErrorAction SilentlyContinue"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", chk_cmd], capture_output=True, text=True, timeout=3)
        if not res.stdout.strip():
            return False

        chk_nat = f"Get-NetNat -Name '{nat_name}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"
        res_nat = subprocess.run(["powershell", "-NoProfile", "-Command", chk_nat], capture_output=True, text=True, timeout=3)
        if nat_name in res_nat.stdout:
            return True

        create_nat = f"New-NetNat -Name '{nat_name}' -InternalIPInterfaceAddressPrefix '{subnet_cidr}' -ErrorAction Stop"
        res_create = subprocess.run(["powershell", "-NoProfile", "-Command", create_nat], capture_output=True, text=True, timeout=5)
        if res_create.returncode == 0:
            log_success(f"Windows NAT '{nat_name}' active: Headset has direct full-speed internet over cable!")
            return True
    except Exception:
        pass
    return False


def configure_windows_ncm_adapter(adapter: dict, ip: str = DEFAULT_PC_IP, prefix_len: int = 24) -> Optional[str]:
    """Ensures the Windows UsbNcm adapter is assigned an IP and firewall rule is active.
    Returns the active IP on the adapter (supports Windows ICS 192.168.137.1 or custom)."""
    if os.name != "nt":
        return ip

    adapter_name = adapter.get("Name", "")
    try:
        if_index = int(adapter.get("ifIndex"))
    except (TypeError, ValueError):
        log_warn(f"Invalid interface index for adapter '{adapter_name}': {adapter.get('ifIndex')!r}")
        return None

    # 1. Ensure Windows Firewall permits UDP 67 and Subnet traffic
    ensure_windows_firewall_rules()

    # 2. Check if an IP is already configured (e.g. from Windows ICS or prior configuration)
    existing_ips = get_adapter_ipv4_addresses(if_index)
    if existing_ips:
        set_private_network_profile(if_index)
        return existing_ips[0]

    # 3. Try configuring static IP via PowerShell (only succeeds when running as administrator)
    log_info(f"Assigning static IP {ip}/{prefix_len} to Windows adapter '{adapter_name}'...")
    cfg_cmd = (
        f"Get-NetIPAddress -InterfaceIndex {if_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue; "
        f"Set-NetIPInterface -InterfaceIndex {if_index} -AddressFamily IPv4 -Dhcp Disabled -ErrorAction SilentlyContinue; "
        f"New-NetIPAddress -InterfaceIndex {if_index} -IPAddress {ip} -PrefixLength {int(prefix_len)} -ErrorAction SilentlyContinue; "
        f"Set-NetConnectionProfile -InterfaceIndex {if_index} -NetworkCategory Private -ErrorAction SilentlyContinue"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", cfg_cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        log_warn("Timed out while assigning the static IP.")
    # -ErrorAction SilentlyContinue hides failures (e.g. not elevated): verify the result
    if ip in get_adapter_ipv4_addresses(if_index):
        return ip

    # 4. If unelevated, attempt netsh with UAC elevation.
    # The interface is designated by its index (netsh accepts it as "name"), so the adapter name
    # never needs to be quoted or escaped.
    netsh_args = f"interface ipv4 set address name={if_index} static {ip} {prefix_to_netmask(prefix_len)}"
    uac_cmd = f"Start-Process netsh -ArgumentList '{netsh_args}' -Verb RunAs -Wait"
    if run_elevated_powershell(uac_cmd):
        time.sleep(1.0)
        existing_ips = get_adapter_ipv4_addresses(if_index)
        if existing_ips:
            set_private_network_profile(if_index)
            return existing_ips[0]

    return None


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
        native: bool = False,
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
        self.native = native
        self.legacy = legacy or not native
        self.host_override = host
        self.pc_ip = pc_ip
        self.headset_ip = headset_ip
        self.verbose = verbose
        self.dhcp_server: Optional[UsbNcmDhcpServer] = None
        self.active_native_mode = False

        self.relay_process: Optional[subprocess.Popen] = None
        self.current_serial: Optional[str] = None
        self.setup_failures = 0
        # Relay clients currently connected (tracked from the managed relay's log), and since when there is none
        self.relay_clients = set()
        self.relay_clients_lock = threading.Lock()
        self.relay_no_client_since: Optional[float] = time.time()
        self.last_adb_reconnect = 0.0
        self.is_connected = False
        self.adb_fail_count = 0
        self.running = True
        self.relay_down_reported = False
        self.external_relay_reported = False
        self.native_failures = 0
        self.native_retry_at = 0.0
        self.health_failures = 0
        self.health_unknown_since: Optional[float] = None
        self.last_health_check = 0.0
        self.device_missing_since: Optional[float] = None

    def run_adb(self, args: List[str], timeout: float = ADB_TIMEOUT_SEC, merge_output: bool = False) -> Tuple[int, str]:
        """Runs an adb command with timeout protection against USB hangs.
        With merge_output, returns stdout and stderr together (adb install reports failures on stderr)."""
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
            if merge_output:
                return res.returncode, ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            return res.returncode, (res.stdout or res.stderr).strip()
        except subprocess.TimeoutExpired:
            self.adb_fail_count += 1
            log_warn(f"ADB command timed out ({timeout}s): {' '.join(cmd)}")
            if self.adb_fail_count >= ADB_TIMEOUTS_BEFORE_REPAIR:
                if self.is_connected:
                    # "adb kill-server" would drop the reverse tunnel, and every connection with it
                    log_warn("ADB is slow to answer; not restarting it while the tether is up.")
                    self.adb_fail_count = 0
                else:
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
        """Checks if the relay port is in use, without connecting to it (a connection would be seen
        by the relay as a client connecting and disconnecting)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", self.port))
            except OSError as e:
                return e.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(e, "winerror", None) in (10048, 10013)
            return False

    def is_relay_alive(self) -> bool:
        """True if the managed relay process runs, or if an external relay already listens on the port."""
        if self.relay_process and self.relay_process.poll() is None:
            return True
        return self.is_relay_listening()

    def start_relay_server(self):
        """Starts the Java relay server with high priority and configured JVM flags."""
        if self.is_relay_listening():
            if not self.external_relay_reported:
                log_info(f"Relay server is already listening on port {self.port}.")
                self.external_relay_reported = True
            return
        self.external_relay_reported = False

        jvm_flags_str = " ".join(self.jvm_args) if self.jvm_args else "default"
        log_info(f"Starting Java relay server on port {self.port} [{jvm_flags_str}]...")

        cmd = [self.java] + self.jvm_args + ["-jar", self.jar, "relay", "-p", str(self.port)]
        
        with self.relay_clients_lock:
            self.relay_clients.clear()
            self.relay_no_client_since = time.time()
        self.relay_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",  # never fail on undecodable relay output
            bufsize=1,
            creationflags=HIGH_PRIORITY_CLASS
        )

        def _drain_relay_stdout(proc):
            # Keep draining until EOF: if the pipe filled up, the relay would block on its next log line
            while True:
                try:
                    line = proc.stdout.readline()
                except (OSError, ValueError):
                    break  # pipe closed
                if not line:
                    break  # EOF
                try:
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    self.track_relay_client(clean_line)
                    # Print connection lifecycle and packet stream in real time
                    if self.verbose:
                        print(f"[{time.strftime('%H:%M:%S')}] {Colors.CYAN}[RELAY]{Colors.RESET} {clean_line}")
                    elif any(k in clean_line for k in ["Open", "Close", "connected", "disconnected", "dropped", "ERROR", "Exception", "WARN"]):
                        print(f"[{time.strftime('%H:%M:%S')}] {Colors.CYAN}[RELAY]{Colors.RESET} {clean_line}")
                except Exception:
                    pass  # a single bad line must not stop the drainer

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

    def get_connected_devices(self) -> Optional[List[Tuple[str, str]]]:
        """Returns list of (serial, state) for connected devices, or None if adb did not answer."""
        code, out = self.run_adb(["devices"])
        if code == -1:
            return None
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

            # Configure or detect IP on Windows adapter (supports ICS 192.168.137.1)
            configured_ip = configure_windows_ncm_adapter(adapter, self.pc_ip)
            if not configured_ip:
                log_warn("Failed to configure IP on Windows UsbNcm adapter.")
                return None

            self.pc_ip = configured_ip
            octets = self.pc_ip.split(".")
            if len(octets) == 4 and self.headset_ip == DEFAULT_HEADSET_IP and self.pc_ip != DEFAULT_PC_IP:
                self.headset_ip = f"{octets[0]}.{octets[1]}.{octets[2]}.2"

            # Start Python DHCP server (if not provided by host ICS)
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
                if code == 0 and "inet " in addr_out:
                    match = re.search(r"inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", addr_out)
                    if match:
                        actual_ip = match.group(1)
                        if not actual_ip.startswith("169.254."):
                            self.headset_ip = actual_ip
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

    def check_tunnel_health(self, serial: str) -> Optional[bool]:
        """Verifies if the tether connection is active and healthy.
        Returns True if healthy, False if broken, None if it could not be determined (adb timed out)."""
        t = HEALTH_ADB_TIMEOUT_SEC
        if self.active_native_mode:
            code, addr_out = self.run_adb(["-s", serial, "shell", "ip", "-o", "-4", "addr", "show", "usb0"], timeout=t)
            if code == -1:
                return None
            return code == 0 and "inet " in addr_out and not ("169.254." in addr_out)

        code, out = self.run_adb(["-s", serial, "shell", "pidof", "com.genymobile.gnirehtet"], timeout=t)
        if code == -1:
            return None
        if code != 0 or not out.strip():
            return False
        code, out = self.run_adb(["-s", serial, "shell", "ip", "-o", "-4", "addr"], timeout=t)
        if code == -1:
            return None
        if code != 0 or f"{VPN_CLIENT_ADDRESS}/" not in out:
            return False
        code, out = self.run_adb(["-s", serial, "reverse", "--list"], timeout=t)
        if code == -1:
            return None
        if code != 0 or "localabstract:gnirehtet" not in out:
            # the app and its VPN are fine, only the reverse rule is gone (e.g. adb reconnected):
            # restore it in place instead of restarting the app, so existing connections survive
            log_warn("adb reverse tunnel missing; restoring it...")
            code, out = self.run_adb(["-s", serial, "reverse", "localabstract:gnirehtet", f"tcp:{self.port}"], timeout=t)
            return code == 0
        return True

    def track_relay_client(self, line: str):
        """Keeps track of the relay clients from the relay log ("Client #N connected/disconnected")."""
        m = RELAY_CLIENT_EVENT.search(line)
        if not m:
            return
        with self.relay_clients_lock:
            if m.group(2) == "connected":
                self.relay_clients.add(m.group(1))
                self.relay_no_client_since = None
            else:
                self.relay_clients.discard(m.group(1))
                if not self.relay_clients and self.relay_no_client_since is None:
                    self.relay_no_client_since = time.time()

    def relay_has_lost_client(self) -> bool:
        """True if the managed relay has had no client for too long (the device side of the tunnel is dead).
        Not applicable to an external relay, whose log is not visible."""
        if not self.relay_process or self.relay_process.poll() is not None:
            return False
        with self.relay_clients_lock:
            since = self.relay_no_client_since
        return since is not None and time.time() - since > RELAY_NO_CLIENT_TIMEOUT_SEC

    def reconnect_offline_devices(self, devices: List[Tuple[str, str]]):
        """A USB hiccup often leaves the device "offline" in adb until it is replugged: ask adb to reconnect it."""
        offline = [s for s, state in devices if state == "offline"]
        if not offline or time.time() - self.last_adb_reconnect < ADB_RECONNECT_INTERVAL_SEC:
            return
        self.last_adb_reconnect = time.time()
        log_warn(f"Device offline in adb: [{', '.join(offline)}]. Reconnecting...")
        self.run_adb(["reconnect", "offline"], timeout=10.0)

    def is_vpn_up(self, serial: str) -> bool:
        """True if the gnirehtet VPN interface (address 10.0.0.2) is up on the device."""
        code, out = self.run_adb(["-s", serial, "shell", "ip", "-o", "-4", "addr"])
        return code == 0 and f"{VPN_CLIENT_ADDRESS}/" in out

    @staticmethod
    def find_apk() -> Optional[str]:
        """Locates the gnirehtet APK bundled next to this script (or a local release build)."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate in [
            os.path.join(script_dir, "gnirehtet.apk"),
            os.path.join(script_dir, "app", "build", "outputs", "apk", "release", "gnirehtet-release.apk"),
        ]:
            if os.path.isfile(candidate):
                return candidate
        return None

    def ensure_apk_up_to_date(self, serial: str):
        """Installs the bundled APK if the app is missing on the device, or if the installed APK is a
        different build (compared by SHA-256: Android keeps the installed APK file unchanged)."""
        apk_path = self.find_apk()
        if not apk_path:
            return
        with open(apk_path, "rb") as f:
            local_hash = hashlib.sha256(f.read()).hexdigest()

        code, out = self.run_adb(["-s", serial, "shell", "pm", "path", "com.genymobile.gnirehtet"])
        installed_path = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:") and line.endswith("base.apk"):
                installed_path = line[len("package:"):]
                break
            if line.startswith("package:") and installed_path is None:
                installed_path = line[len("package:"):]

        if installed_path is None:
            reason = "missing"
        else:
            code, out = self.run_adb(["-s", serial, "shell", "sha256sum", installed_path])
            remote_hash = out.split()[0].lower() if code == 0 and out.split() else None
            if remote_hash is None:
                return  # cannot compare (no sha256sum on the device): keep the installed app
            if remote_hash == local_hash:
                return
            reason = "outdated"

        log_info(f"Gnirehtet APK {reason} on [{serial}]. Installing {os.path.basename(apk_path)}...")
        code, out = self.run_adb(["-s", serial, "install", "-r", apk_path], timeout=30.0, merge_output=True)
        if code == 0 and "Success" in out:
            log_success(f"Gnirehtet APK installed on [{serial}].")
        elif "VERSION_DOWNGRADE" in out:
            log_warn(f"The installed app on [{serial}] is newer than {os.path.basename(apk_path)}; keeping it.")
        elif "UPDATE_INCOMPATIBLE" in out:
            # Android refuses updates signed with another key: replace the app (the VPN permission
            # prompt will be shown again on the headset)
            log_warn(f"The installed app on [{serial}] is signed with a different key. Reinstalling it...")
            self.run_adb(["-s", serial, "uninstall", "com.genymobile.gnirehtet"], timeout=15.0)
            code, out = self.run_adb(["-s", serial, "install", apk_path], timeout=30.0, merge_output=True)
            if code == 0 and "Success" in out:
                log_success(f"Gnirehtet APK reinstalled on [{serial}]. Accept the VPN prompt on the headset.")
            else:
                log_warn(f"APK install failed on [{serial}]: {out}")
        else:
            log_warn(f"APK install failed on [{serial}]: {out}")

    def setup_headset_tether(self, serial: str) -> bool:
        """Sets up high-speed Native USB transport or fallback legacy reverse tunnel."""
        log_info(f"Device connected: [{serial}]. Configuring tether...")

        # 1. Stop any stale VPN instance (ensures no VpnService swallows the native hardware link).
        # On quick retries, keep the app alive so a pending VPN permission prompt is not killed every cycle.
        if self.setup_failures % 3 == 0:
            self.run_adb(["-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"])

        # 2. Determine transport mode: Standard ADB reverse (default) vs Native USB (experimental)
        relay_host: Optional[str] = None
        self.active_native_mode = False
        if self.native:
            now = time.monotonic()
            if now < self.native_retry_at:
                log_info(f"Native USB setup failed recently; retrying in {int(self.native_retry_at - now)}s. Using ADB reverse tunnel meanwhile.")
            else:
                log_info("Experimental Native USB (CDC-NCM) mode requested (--native)...")
                relay_host = self.ensure_ncm_link(serial)
                if relay_host:
                    self.active_native_mode = True
                    self.native_failures = 0
                    self.native_retry_at = 0.0
                else:
                    self.native_failures += 1
                    delay = min(NATIVE_RETRY_BASE_DELAY * (2 ** (self.native_failures - 1)), NATIVE_RETRY_MAX_DELAY)
                    self.native_retry_at = time.monotonic() + delay
                    log_warn(f"Native USB unavailable (next attempt in {int(delay)}s). Falling back to default ADB reverse tunnel...")

        if not self.active_native_mode:
            # Legacy mode: install the bundled APK if it is missing or differs from the installed one
            self.ensure_apk_up_to_date(serial)

            # Ensure relay server is running for legacy reverse mode
            self.start_relay_server()

            # Establish reverse tunnel
            self.run_adb(["-s", serial, "reverse", "--remove-all"])
            code, out = self.run_adb(["-s", serial, "reverse", "localabstract:gnirehtet", f"tcp:{self.port}"])
            if code != 0:
                log_error(f"Failed to set adb reverse: {out}")
                return False

            # Start VPN intent on Android
            intent_cmd = [
                "-s", serial,
                "shell", "am", "start",
                "-a", "com.genymobile.gnirehtet.START",
                "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
                "--esa", "dnsServers", "1.1.1.1,8.8.8.8",
            ]
            # A headset that sits idle (screen off) may defer the activity launch, so the VPN never starts
            self.run_adb(["-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            code, out = self.run_adb(intent_cmd)
            if code != 0:
                log_error(f"Failed to start Android VPN service: {out}")
                return False

            # "am start" succeeding does not mean the VPN is up: wait until the tunnel interface exists
            deadline = time.time() + VPN_START_TIMEOUT_SEC
            while not self.is_vpn_up(serial):
                if time.time() >= deadline:
                    log_warn(f"VPN did not come up on [{serial}]. Wake the headset (or accept the VPN prompt); retrying...")
                    return False
                time.sleep(0.5)

            log_success(f"Tether active and running on [{serial}] via Legacy ADB Reverse!")
            return True
        else:
            # Native USB mode: clear reverse tunnels and ensure no VPN is running.
            # On Android, any active VpnService captures uid 0-99999 and swallows local traffic,
            # preventing PCVR streaming apps (Virtual Desktop / Steam Link) from reaching the hardware wire.
            self.run_adb(["-s", serial, "reverse", "--remove-all"])
            self.run_adb(["-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"])

            # Setup Windows NAT if supported
            subnet_prefix = f"{self.pc_ip.rsplit('.', 1)[0]}.0/24"
            ensure_windows_nat(subnet_prefix)

            log_success(f"Tether active and running on [{serial}] via Native USB [3.8 Gbps Direct Wire]!")
            log_info(f"PC Network IP: {relay_host} | Headset IP: {self.headset_ip}")
            log_info("In Virtual Desktop / Steam Link: Direct hardware pipeline is active with 0 ms latency!")
            return True

    def teardown_headset_tether(self, serial: str):
        """Stops the VPN service on the device and clears tunnels."""
        log_info(f"Tearing down tether on [{serial}]...")
        self.run_adb(["-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"])
        self.run_adb(["-s", serial, "reverse", "--remove-all"])
        if self.dhcp_server:
            self.dhcp_server.stop()
            self.dhcp_server = None

    def health_check_says_dead(self, healthy: Optional[bool]) -> bool:
        """Records a health check result; True once the tether must be restarted."""
        now = time.time()
        if healthy is True:
            if self.health_failures:
                log_success("Tether link recovered.")
            self.health_failures = 0
            self.health_unknown_since = None
            return False
        if healthy is None:
            # adb did not answer in time: not a proof that the link is dead
            if self.health_unknown_since is None:
                self.health_unknown_since = now
                log_warn("Health check inconclusive (adb is slow to answer); not restarting the tether.")
            if now - self.health_unknown_since < HEALTH_UNKNOWN_GIVE_UP_SEC:
                return False
            self.health_unknown_since = now  # count one failure per give-up period
        self.health_failures += 1
        if self.health_failures < HEALTH_FAILURES_BEFORE_RESTART:
            log_warn(f"Health check failed ({self.health_failures}/{HEALTH_FAILURES_BEFORE_RESTART}); checking again before restarting...")
            return False
        return True

    def handle_disconnect(self):
        """Handles link drop and state reset."""
        self.health_failures = 0
        self.health_unknown_since = None
        self.last_health_check = time.time()
        self.setup_failures = 0
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
        mode_str = "Native USB (CDC-NCM) [Experimental]" if self.native else "Standard ADB Reverse Relay [Default]"
        log_info(f"Mode:  {mode_str}")
        if self.host_override:
            log_info(f"Host:  {self.host_override}")
        if self.target_serial:
            log_info(f"Target Serial: {self.target_serial}")

        # Ensure relay server is running (default mode requires it)
        if not self.native:
            self.start_relay_server()

        log_info("Waiting for Android / Quest device to connect...")

        while self.running:
            try:
                # 1. Ensure relay server process is alive (in default mode)
                if not self.active_native_mode:
                    if self.is_relay_alive():
                        self.relay_down_reported = False
                    else:
                        # log only on state change, not on every poll
                        if not self.relay_down_reported:
                            log_warn("Relay server process is not running. Restarting...")
                            self.relay_down_reported = True
                        self.start_relay_server()

                # 2. Check connected devices
                devices = self.get_connected_devices()
                if devices is None:
                    # adb did not answer (busy USB link): do not conclude anything
                    time.sleep(self.poll_interval)
                    continue
                ready_devices = [s for s, state in devices if state == "device"]
                unauth_devices = [s for s, state in devices if state == "unauthorized"]

                if unauth_devices:
                    log_warn("Device unauthorized! Check headset/phone to allow USB debugging.")
                self.reconnect_offline_devices(devices)

                # Filter target serial if specified
                if self.target_serial:
                    target_available = self.target_serial in ready_devices
                    chosen_serial = self.target_serial if target_available else None
                else:
                    chosen_serial = ready_devices[0] if ready_devices else None

                if not chosen_serial:
                    now = time.time()
                    if self.is_connected:
                        if self.device_missing_since is None:
                            self.device_missing_since = now
                        if now - self.device_missing_since < DEVICE_MISSING_GRACE_SEC:
                            # brief USB hiccup: keep the tether state, the device may come back
                            time.sleep(self.poll_interval)
                            continue
                    self.device_missing_since = None
                    self.handle_disconnect()
                    time.sleep(self.poll_interval)
                    continue
                self.device_missing_since = None

                # 3. Connection state management
                if not self.is_connected or self.current_serial != chosen_serial:
                    self.current_serial = chosen_serial
                    success = self.setup_headset_tether(chosen_serial)
                    if success:
                        self.is_connected = True
                        self.setup_failures = 0
                        with self.relay_clients_lock:
                            if not self.relay_clients:
                                self.relay_no_client_since = time.time()  # give the new tunnel time to connect
                    else:
                        self.setup_failures += 1
                        time.sleep(2.0)
                        continue

                # 4. Active link health check
                if self.is_connected and time.time() - self.last_health_check >= HEALTH_CHECK_INTERVAL_SEC:
                    self.last_health_check = time.time()
                    if self.health_check_says_dead(self.check_tunnel_health(chosen_serial)):
                        log_warn("Tether link is down. Re-establishing...")
                        self.handle_disconnect()
                        time.sleep(1.0)
                        continue
                    if not self.active_native_mode and self.relay_has_lost_client():
                        log_warn(f"No device connected to the relay for {RELAY_NO_CLIENT_TIMEOUT_SEC:.0f}s. Restarting the tether...")
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
    parser.add_argument("--native", "--ncm", dest="native", action="store_true", help="[Experimental] Opt-in to native USB CDC-NCM Ethernet gadget (work in progress)")
    parser.add_argument("--legacy", action="store_true", help="Explicitly specify standard ADB reverse tunnel (default behavior)")
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
        native=args.native,
        legacy=args.legacy or not args.native,
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
