# in working state

import asyncio
import ipaddress
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Endpoint:
    ip: str
    hostname: str          = ''
    endpoint_type: str     = 'unknown'   # 'desktop' | 'robot' | 'unknown'
    last_seen: float       = 0.0
    reachable: bool        = False
    axes: List              = field(default_factory=list)
    streams: List           = field(default_factory=list)
    capabilities_received: bool = False


# ---------------------------------------------------------------------------
# Local-IP helpers (intranet-safe, no 8.8.8.8)
# ---------------------------------------------------------------------------

def _iface_addresses() -> List[str]:
    """
    Return all IPv4 addresses assigned to local interfaces by parsing
    `ip -4 addr show` output.  No external connection required.
    """
    ips = []
    try:
        out = subprocess.check_output(
            ['ip', '-4', 'addr', 'show'],
            stderr=subprocess.DEVNULL,
        ).decode()
        for match in re.finditer(r'inet (\d+\.\d+\.\d+\.\d+)/(\d+)', out):
            ip = match.group(1)
            if not ip.startswith('127.'):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _subnet_for_ip(ip: str, prefix: int = 24) -> Optional[ipaddress.IPv4Network]:
    try:
        return ipaddress.IPv4Network(f'{ip}/{prefix}', strict=False)
    except ValueError:
        return None


def _local_subnets() -> List[ipaddress.IPv4Network]:
    """
    Return one /24 subnet per non-loopback interface address.
    Falls back to an empty list if nothing is found.
    """
    subnets = []
    try:
        out = subprocess.check_output(
            ['ip', '-4', 'addr', 'show'],
            stderr=subprocess.DEVNULL,
        ).decode()
        for match in re.finditer(r'inet (\d+\.\d+\.\d+\.\d+)/(\d+)', out):
            ip     = match.group(1)
            prefix = int(match.group(2))
            if ip.startswith('127.'):
                continue
            net = _subnet_for_ip(ip, min(prefix, 24))
            if net and net not in subnets:
                subnets.append(net)
    except Exception:
        pass
    return subnets


# ---------------------------------------------------------------------------
# ARP cache + ping sweep
# ---------------------------------------------------------------------------

def _read_arp_cache() -> List[str]:
    ips = []
    try:
        with open('/proc/net/arp') as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[2] != '0x0':
                    ips.append(parts[0])
    except OSError:
        pass
    return ips


async def _ping_host(ip: str, timeout: float = 0.4) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            'ping', '-c', '1', '-W', '1', str(ip),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout + 1.5)
        return proc.returncode == 0
    except Exception:
        return False


async def ping_sweep(subnet: ipaddress.IPv4Network,
                     concurrency: int = 64) -> List[str]:
    sem = asyncio.Semaphore(concurrency)

    async def _check(ip):
        async with sem:
            if await _ping_host(str(ip)):
                return str(ip)
        return None

    results = await asyncio.gather(*(_check(ip) for ip in subnet.hosts()))
    return [r for r in results if r]


async def arp_scan_sudo(password: str,
                        interface: Optional[str] = None) -> List[str]:
    """
    Run `arp-scan` with a sudo password supplied via the UI.
    *interface* may be specified for adhoc (e.g. 'wlan0').
    """
    cmd = ['sudo', '-S', 'arp-scan', '-l']
    if interface:
        cmd += ['-I', interface]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(input=(password + '\n').encode()), timeout=15)
        ips = []
        for line in stdout.decode().splitlines():
            parts = line.split()
            if parts and parts[0].count('.') == 3:
                try:
                    ipaddress.IPv4Address(parts[0])
                    ips.append(parts[0])
                except ValueError:
                    pass
        return ips
    except Exception as e:
        print(f'[scanner] arp-scan failed: {e}')
        return []


# ---------------------------------------------------------------------------
# Main scanner class
# ---------------------------------------------------------------------------

class NetworkScanner:
    """
    Discovers robonet endpoints on the local network.

    For adhoc networks call set_subnet() before run() since there is no
    DHCP address from which to infer the subnet automatically::

        scanner.set_subnet('192.168.2.0/24')
        asyncio.create_task(scanner.run())
    """

    def __init__(self,
                 on_found: Optional[Callable[[Endpoint], None]] = None,
                 on_lost:  Optional[Callable[[Endpoint], None]] = None,
                 probe_port: int = 9999,
                 scan_interval: float = 10.0):
        self.on_found      = on_found or (lambda e: None)
        self.on_lost       = on_lost  or (lambda e: None)
        self.probe_port    = probe_port
        self.scan_interval = scan_interval
        self.by_ip:       Dict[str, Endpoint] = {}
        self.by_hostname: Dict[str, Endpoint] = {}
        self._sudo_password: Optional[str]   = None
        self._forced_subnet: Optional[ipaddress.IPv4Network] = None
        self._adhoc_iface:   Optional[str]   = None

    def set_subnet(self, cidr: str, iface: Optional[str] = None):
        """
        Override automatic subnet detection.  Use for adhoc networks
        where the address is statically configured.
        """
        self._forced_subnet = ipaddress.IPv4Network(cidr, strict=False)
        self._adhoc_iface   = iface

    def supply_sudo_password(self, pw: str):
        self._sudo_password = pw

    def _register(self, ep: Endpoint):
        self.by_ip[ep.ip] = ep
        if ep.hostname:
            self.by_hostname[ep.hostname] = ep

    def _unregister(self, ep: Endpoint):
        self.by_ip.pop(ep.ip, None)
        if ep.hostname:
            self.by_hostname.pop(ep.hostname, None)

    def _update_hostname(self, ep: Endpoint, hostname: str):
        """Attach a newly resolved hostname to an existing endpoint."""
        if ep.hostname:
            self.by_hostname.pop(ep.hostname, None)
        ep.hostname = hostname
        if hostname:
            self.by_hostname[hostname] = ep

    async def _resolve_hostname(self, ip: str) -> str:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, socket.gethostbyaddr, ip)
            return result[0]
        except Exception:
            return ''

    async def _probe_port(self, ip: str) -> bool:
        """TCP knock on probe_port -- cheap reachability check."""
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection(ip, self.probe_port), timeout=0.5)
            w.close()
            return True
        except Exception:
            return False

    async def _scan_once(self):
        candidates: List[str] = list(_read_arp_cache())

        subnets = ([self._forced_subnet]
                   if self._forced_subnet else _local_subnets())

        for subnet in subnets:
            swept = await ping_sweep(subnet)
            for ip in swept:
                if ip not in candidates:
                    candidates.append(ip)

        if self._sudo_password:
            extra = await arp_scan_sudo(self._sudo_password,
                                        interface=self._adhoc_iface)
            for ip in extra:
                if ip not in candidates:
                    candidates.append(ip)

        now = time.time()
        seen: set = set()

        for ip in candidates:
            reachable = await self._probe_port(ip)
            seen.add(ip)
            if ip not in self.by_ip:
                hostname = await self._resolve_hostname(ip)
                ep = Endpoint(ip=ip, hostname=hostname, last_seen=now, reachable=reachable)
                self._register(ep)
                self.on_found(ep)
            else:
                ep = self.by_ip[ip]
                ep.last_seen = now
                ep.reachable = reachable
                if not ep.hostname:
                    hostname = await self._resolve_hostname(ip)
                    if hostname:
                        self._update_hostname(ep, hostname)

        stale = [ip for ip, ep in self.by_ip.items()
                 if now - ep.last_seen > self.scan_interval * 2
                 and ip not in seen]
        for ip in stale:
            ep = self.by_ip[ip]
            self._unregister(ep)
            self.on_lost(ep)

    async def run(self):
        while True:
            try:
                await self._scan_once()
            except Exception as e:
                print(f'[scanner] error: {e}')
            await asyncio.sleep(self.scan_interval)