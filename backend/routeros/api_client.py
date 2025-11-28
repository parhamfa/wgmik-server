from typing import List
from librouteros import connect
from .client_base import RouterOSClient, WGPeer, WGInterfaceConfig
from ipaddress import ip_network
import re


class RouterOSApiClient(RouterOSClient):
    def __init__(self, host: str, port: int, username: str, password: str, use_tls: bool = True, ssl_verify: bool = True):
        # librouteros uses ssl=True/False and port
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = use_tls
        self.ssl_verify = ssl_verify

    def _conn(self):
        return connect(
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            use_ssl=self.ssl,
            ssl_verify=self.ssl_verify,
            timeout=10,
        )

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

    def list_wireguard_interfaces(self) -> List[str]:
        api = self._conn()
        try:
            rows = api(cmd="/interface/wireguard/print")
            return [row.get("name") for row in rows if row.get("name")]
        finally:
            try:
                api.close()
            except Exception:
                pass

    def get_wireguard_interface(self, interface: str) -> WGInterfaceConfig:
        api = self._conn()
        try:
            rows = api(cmd="/interface/wireguard/print")
            for row in rows:
                if row.get("name") == interface:
                    return WGInterfaceConfig(
                        name=row.get("name", interface),
                        public_key=row.get("public-key", ""),
                        listen_port=int(row.get("listen-port", 0) or 0),
                    )
            raise KeyError(f"wireguard interface '{interface}' not found")
        finally:
            try:
                api.close()
            except Exception:
                pass

    def list_wireguard_peers(self, interface: str) -> List[WGPeer]:
        peers: List[WGPeer] = []
        api = self._conn()
        try:
            rows = api(cmd="/interface/wireguard/peers/print")
            for row in rows:
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
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_peer_disabled(self, interface: str, ros_id: str, disabled: bool) -> None:
        api = self._conn()
        try:
            api(cmd="/interface/wireguard/peers/set", **{".id": ros_id, "disabled": "yes" if disabled else "no"})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def get_primary_ipv4(self) -> str:
        """Return a best-effort primary IPv4 address from /ip/address."""
        api = self._conn()
        try:
            rows = api(cmd="/ip/address/print")
            public: str | None = None
            private: str | None = None
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
        finally:
            try:
                api.close()
            except Exception:
                pass


