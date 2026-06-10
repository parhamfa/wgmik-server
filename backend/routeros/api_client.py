from typing import List, Optional
from librouteros import connect
from .client_base import RouterOSClient, WGPeer, WGInterfaceConfig
from ipaddress import ip_network
import re


class RouterOSApiClient(RouterOSClient):
    def __init__(self, host: str, port: int, username: str, password: str, use_tls: bool = True, ssl_verify: bool = True, timeout: int = 10):
        # librouteros uses ssl=True/False and port
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = use_tls
        self.ssl_verify = ssl_verify
        self.timeout = timeout

    def _conn(self):
        return connect(
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            use_ssl=self.ssl,
            ssl_verify=self.ssl_verify,
            timeout=self.timeout,
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

    def _parse_bool(self, value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(int(value))
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "t", "yes", "y", "on", "enabled"):
                return True
            if v in ("0", "false", "f", "no", "n", "off", "disabled"):
                return False
        return bool(value)

    def get_system_version(self) -> str:
        api = self._conn()
        try:
            rows = api(cmd="/system/resource/print")
            if rows:
                return str(rows[0].get("version") or "").strip()
            return ""
        finally:
            try:
                api.close()
            except Exception:
                pass

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
                    addresses: List[str] = []
                    try:
                        address_rows = api(cmd="/ip/address/print")
                        for addr_row in address_rows:
                            if addr_row.get("interface") != interface:
                                continue
                            if self._parse_bool(addr_row.get("disabled", False)):
                                continue
                            addr = (addr_row.get("address") or "").strip()
                            if addr:
                                addresses.append(addr)
                    except Exception:
                        addresses = []
                    return WGInterfaceConfig(
                        name=row.get("name", interface),
                        public_key=row.get("public-key", ""),
                        listen_port=int(row.get("listen-port", 0) or 0),
                        addresses=addresses,
                    )
            raise KeyError(f"wireguard interface '{interface}' not found")
        finally:
            try:
                api.close()
            except Exception:
                pass

    def list_all_wireguard_peers(self) -> List[WGPeer]:
        peers: List[WGPeer] = []
        api = self._conn()
        try:
            rows = api(cmd="/interface/wireguard/peers/print")
            for row in rows:
                peers.append(
                    WGPeer(
                        ros_id=row.get(".id", ""),
                        interface=row.get("interface", ""),
                        name=row.get("name", ""),
                        public_key=row.get("public-key", ""),
                        allowed_address=row.get("allowed-address", ""),
                        disabled=self._parse_bool(row.get("disabled", False)),
                        rx_bytes=int(row.get("rx", 0)),
                        tx_bytes=int(row.get("tx", 0)),
                        last_handshake=self._parse_last_handshake(row.get("last-handshake")),
                        endpoint=row.get("current-endpoint-address", ""),
                        client_endpoint=(row.get("client-endpoint") or "").strip(),
                    )
                )
            return peers
        finally:
            try:
                api.close()
            except Exception:
                pass

    def list_wireguard_peers(self, interface: str) -> List[WGPeer]:
        return [peer for peer in self.list_all_wireguard_peers() if peer.interface == interface]

    def set_peer_disabled(self, interface: str, ros_id: str, disabled: bool) -> None:
        api = self._conn()
        try:
            api(cmd="/interface/wireguard/peers/set", **{".id": ros_id, "disabled": "yes" if disabled else "no"})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_peer_keys(self, interface: str, ros_id: str, public_key: str, private_key: str) -> None:
        api = self._conn()
        try:
            api(
                cmd="/interface/wireguard/peers/set",
                **{".id": ros_id, "public-key": public_key, "private-key": private_key},
            )
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_peer_name(self, interface: str, ros_id: str, name: str) -> None:
        api = self._conn()
        try:
            api(cmd="/interface/wireguard/peers/set", **{".id": ros_id, "name": name or ""})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_peer_client_endpoint(self, interface: str, ros_id: str, client_endpoint: Optional[str]) -> None:
        api = self._conn()
        try:
            api(
                cmd="/interface/wireguard/peers/set",
                **{".id": ros_id, "client-endpoint": (client_endpoint or "").strip()},
            )
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_peer_preshared_key(self, interface: str, ros_id: str, preshared_key: Optional[str]) -> None:
        api = self._conn()
        try:
            params = {".id": ros_id, "preshared-key": preshared_key or ""}
            api(cmd="/interface/wireguard/peers/set", **params)
        finally:
            try:
                api.close()
            except Exception:
                pass

    def get_wireguard_peer_preshared_key(self, interface: str, ros_id: str) -> Optional[str]:
        api = self._conn()
        try:
            try:
                rows = api(cmd="/interface/wireguard/peers/print", proplist=".id,interface,preshared-key")
            except Exception:
                rows = api(cmd="/interface/wireguard/peers/print")
            for row in rows or []:
                if (row.get(".id") or "") != ros_id:
                    continue
                if (row.get("interface") or "") != interface:
                    continue
                pk = (row.get("preshared-key") or "").strip()
                return pk or None
            return None
        finally:
            try:
                api.close()
            except Exception:
                pass

    def add_wireguard_peer(
        self,
        interface: str,
        public_key: str,
        allowed_address: str,
        name: str = "",
        disabled: bool = False,
        private_key: Optional[str] = None,
        preshared_key: Optional[str] = None,
        client_endpoint: Optional[str] = None,
    ) -> str:
        api = self._conn()
        try:
            params = {
                "interface": interface,
                "public-key": public_key,
                "allowed-address": allowed_address,
            }
            if private_key:
                params["private-key"] = private_key
            if preshared_key:
                params["preshared-key"] = preshared_key
            if client_endpoint:
                params["client-endpoint"] = client_endpoint
            if name:
                params["name"] = name
            if disabled:
                params["disabled"] = "yes"
            res = api(cmd="/interface/wireguard/peers/add", **params)
            # librouteros may return dict-like with 'ret' or list
            if isinstance(res, dict):
                rid = res.get("ret") or res.get(".id")
                if isinstance(rid, str) and rid:
                    return rid
            if isinstance(res, list) and res:
                first = res[0]
                if isinstance(first, dict):
                    rid = first.get("ret") or first.get(".id")
                    if isinstance(rid, str) and rid:
                        return rid
            # Fallback: locate by pubkey
            rows = api(cmd="/interface/wireguard/peers/print")
            for row in rows:
                if row.get("interface") == interface and row.get("public-key") == public_key:
                    rid = row.get(".id") or ""
                    if rid:
                        return rid
            raise RuntimeError("RouterOS did not return peer id")
        finally:
            try:
                api.close()
            except Exception:
                pass

    def remove_wireguard_peer(self, interface: str, ros_id: str) -> None:
        api = self._conn()
        try:
            api(cmd="/interface/wireguard/peers/remove", **{".id": ros_id})
        finally:
            try:
                api.close()
            except Exception:
                pass

    # ── Simple Queue management ──────────────────────────────────────────

    def add_simple_queue(self, name: str, target: str, max_limit_up: str, max_limit_down: str, comment: str = "") -> str:
        api = self._conn()
        try:
            params = {
                "name": name,
                "target": target,
                "max-limit": f"{max_limit_up}/{max_limit_down}",
            }
            if comment:
                params["comment"] = comment
            res = api(cmd="/queue/simple/add", **params)
            if isinstance(res, dict):
                rid = res.get("ret") or res.get(".id")
                if isinstance(rid, str) and rid:
                    return rid
            if isinstance(res, list) and res:
                first = res[0]
                if isinstance(first, dict):
                    rid = first.get("ret") or first.get(".id")
                    if isinstance(rid, str) and rid:
                        return rid
            rows = api(cmd="/queue/simple/print")
            for row in rows:
                if row.get("name") == name:
                    return row.get(".id", "")
            raise RuntimeError("RouterOS did not return queue id")
        finally:
            try:
                api.close()
            except Exception:
                pass

    def update_simple_queue(self, ros_id: str, max_limit_up: str, max_limit_down: str) -> None:
        api = self._conn()
        try:
            api(cmd="/queue/simple/set", **{".id": ros_id, "max-limit": f"{max_limit_up}/{max_limit_down}"})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def set_simple_queue_name(self, ros_id: str, name: str) -> None:
        api = self._conn()
        try:
            api(cmd="/queue/simple/set", **{".id": ros_id, "name": name})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def remove_simple_queue(self, ros_id: str) -> None:
        api = self._conn()
        try:
            api(cmd="/queue/simple/remove", **{".id": ros_id})
        finally:
            try:
                api.close()
            except Exception:
                pass

    def list_simple_queues(self, name_prefix: str = "") -> List[dict]:
        api = self._conn()
        try:
            rows = api(cmd="/queue/simple/print")
            result = []
            for row in rows:
                n = row.get("name", "")
                if name_prefix and not n.startswith(name_prefix):
                    continue
                result.append({
                    "ros_id": row.get(".id", ""),
                    "name": n,
                    "target": row.get("target", ""),
                    "max_limit": row.get("max-limit", ""),
                    "comment": row.get("comment", ""),
                })
            return result
        finally:
            try:
                api.close()
            except Exception:
                pass

    def get_wireguard_peer_private_key(self, interface: str, ros_id: str) -> Optional[str]:
        api = self._conn()
        try:
            # Best-effort: list rows and match by .id. Some ROS versions may not return private-key unless requested.
            try:
                rows = api(cmd="/interface/wireguard/peers/print", proplist=".id,interface,private-key")
            except Exception:
                rows = api(cmd="/interface/wireguard/peers/print")
            for row in rows or []:
                if (row.get(".id") or "") != ros_id:
                    continue
                if (row.get("interface") or "") != interface:
                    continue
                pk = (row.get("private-key") or "").strip()
                return pk or None
            return None
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
