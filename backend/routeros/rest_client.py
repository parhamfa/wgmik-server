import httpx
from typing import List, Optional
import re
from ipaddress import ip_network
from .client_base import RouterOSClient, WGPeer, WGInterfaceConfig


class RouterOSRestClient(RouterOSClient):
    def __init__(self, host: str, port: int, username: str, password: str, tls_verify: bool = True, https: bool = True):
        self.host = host
        self.port = port
        self.https = https
        self.auth = (username, password)
        self.verify = tls_verify

    def _base(self, https: bool) -> str:
        return f"{'https' if https else 'http'}://{self.host}:{self.port}/rest"

    def _request(self, method: str, path: str, json: Optional[dict] = None):
        url = f"{self._base(self.https)}{path}"
        try:
            with httpx.Client(verify=self.verify, timeout=10.0, auth=self.auth) as c:
                r = c.request(method, url, json=json)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.text
        except Exception:
            # Fallback to opposite scheme if initial scheme fails
            alt_https = not self.https
            alt_url = f"{self._base(alt_https)}{path}"
            with httpx.Client(verify=self.verify, timeout=10.0, auth=self.auth) as c:
                r = c.request(method, alt_url, json=json)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.text

    def _get(self, path: str):
        return self._request("GET", path)

    def _put(self, path: str, json: dict):
        return self._request("PUT", path, json=json)

    def list_wireguard_interfaces(self) -> List[str]:
        data = self._get("/interface/wireguard")
        names = [row.get("name") for row in data if row.get("name")]
        return names

    def get_wireguard_interface(self, interface: str) -> WGInterfaceConfig:
        data = self._get("/interface/wireguard")
        for row in data:
            if row.get("name") == interface:
                return WGInterfaceConfig(
                    name=row.get("name", interface),
                    public_key=row.get("public-key", ""),
                    listen_port=int(row.get("listen-port", 0) or 0),
                )
        raise KeyError(f"wireguard interface '{interface}' not found")

    def get_primary_ipv4(self) -> str:
        """Return a best-effort primary IPv4 address from /ip/address."""
        try:
            rows = self._get("/ip/address")
        except Exception:
            return ""
        public: Optional[str] = None
        private: Optional[str] = None
        for row in rows:
            addr = row.get("address") or ""
            if not addr or "/" not in addr:
                continue
            ip_str = addr.split("/")[0]
            try:
                net = ip_network(ip_str + "/32", strict=False)
            except ValueError:
                continue
            if net.version != 4:
                continue
            if not net.is_private:
                if not public:
                    public = ip_str
            else:
                if not private:
                    private = ip_str
        return public or private or ""

    def _parse_last_handshake(self, value):
        if value in (None, "", 0):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if value.isdigit():
                return int(value)
            total = 0
            for amount, unit in re.findall(r"(\d+)([wdhms])", value):
                amt = int(amount)
                if unit == "w":
                    total += amt * 604800
                elif unit == "d":
                    total += amt * 86400
                elif unit == "h":
                    total += amt * 3600
                elif unit == "m":
                    total += amt * 60
                elif unit == "s":
                    total += amt
            return total or None
        return None

    def list_wireguard_peers(self, interface: str) -> List[WGPeer]:
        # Filter peers by interface
        data = self._get("/interface/wireguard/peers")
        peers = []
        for row in data:
            if row.get("interface") != interface:
                continue
            peers.append(
                WGPeer(
                    ros_id=row.get(".id", ""),
                    interface=row.get("interface", ""),
                    name=row.get("name", ""),
                    public_key=row.get("public-key", ""),
                    allowed_address=row.get("allowed-address", ""),
                    disabled=row.get("disabled", False),
                    rx_bytes=int(row.get("rx", 0)),
                    tx_bytes=int(row.get("tx", 0)),
                    last_handshake=self._parse_last_handshake(row.get("last-handshake")),
                    endpoint=row.get("current-endpoint-address", ""),
                )
            )
        return peers

    def set_peer_disabled(self, interface: str, ros_id: str, disabled: bool) -> None:
        # PUT to the peer resource: path format /interface/wireguard/peers/<id>
        self._put(f"/interface/wireguard/peers/{ros_id}", {"disabled": disabled})


