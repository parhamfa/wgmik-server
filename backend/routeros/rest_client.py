import httpx
from typing import List, Optional
import re
from ipaddress import ip_network
from urllib.parse import quote
from .client_base import RouterOSClient, WGPeer, WGInterfaceConfig


class RouterOSRestClient(RouterOSClient):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        tls_verify: bool = True,
        https: bool = True,
        allow_scheme_fallback: bool = True,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.https = https
        self.allow_scheme_fallback = allow_scheme_fallback
        self.auth = (username, password)
        self.verify = tls_verify
        self.timeout = timeout

    def _base(self, https: bool) -> str:
        return f"{'https' if https else 'http'}://{self.host}:{self.port}/rest"

    def _request(self, method: str, path: str, json: Optional[dict] = None):
        url = f"{self._base(self.https)}{path}"
        try:
            with httpx.Client(verify=self.verify, timeout=self.timeout, auth=self.auth) as c:
                r = c.request(method, url, json=json)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.text
        except httpx.HTTPStatusError:
            # Non-2xx from RouterOS is a real API response; do not "flip protocol".
            raise
        except httpx.TransportError:
            # Only fallback when connection/protocol fails (e.g. https vs http mismatch),
            # and only when the profile allows it.
            if not self.allow_scheme_fallback:
                raise
            alt_https = not self.https
            alt_url = f"{self._base(alt_https)}{path}"
            with httpx.Client(verify=self.verify, timeout=self.timeout, auth=self.auth) as c:
                r = c.request(method, alt_url, json=json)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.text

    def _get(self, path: str):
        return self._request("GET", path)

    def _put(self, path: str, json: dict):
        return self._request("PUT", path, json=json)

    def get_system_version(self) -> str:
        data = self._get("/system/resource")
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            return str(data.get("version") or "").strip()
        return ""

    def list_wireguard_interfaces(self) -> List[str]:
        data = self._get("/interface/wireguard")
        names = [row.get("name") for row in data if row.get("name")]
        return names

    def _interface_addresses(self, interface: str) -> List[str]:
        try:
            rows = self._get("/ip/address")
        except Exception:
            return []
        out: List[str] = []
        for row in rows:
            if row.get("interface") != interface:
                continue
            disabled = str(row.get("disabled", "")).strip().lower()
            if disabled in ("true", "yes", "1"):
                continue
            addr = (row.get("address") or "").strip()
            if addr:
                out.append(addr)
        return out

    def get_wireguard_interface(self, interface: str) -> WGInterfaceConfig:
        data = self._get("/interface/wireguard")
        for row in data:
            if row.get("name") == interface:
                return WGInterfaceConfig(
                    name=row.get("name", interface),
                    public_key=row.get("public-key", ""),
                    listen_port=int(row.get("listen-port", 0) or 0),
                    addresses=self._interface_addresses(interface),
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

    def list_all_wireguard_peers(self) -> List[WGPeer]:
        data = self._get("/interface/wireguard/peers")
        peers: List[WGPeer] = []
        for row in data:
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
                    client_endpoint=(
                        (row.get("client-endpoint") or row.get("clientEndpoint") or "") or ""
                    ).strip(),
                )
            )
        return peers

    def list_wireguard_peers(self, interface: str) -> List[WGPeer]:
        return [peer for peer in self.list_all_wireguard_peers() if peer.interface == interface]

    def set_peer_disabled(self, interface: str, ros_id: str, disabled: bool) -> None:
        # RouterOS REST has an item endpoint, but on some versions it returns 500 for PUT/PATCH.
        # The "set" action endpoint is reliable:
        #   POST /rest/interface/wireguard/peers/set  {"numbers":"*8","disabled":"yes"}
        self._request(
            "POST",
            "/interface/wireguard/peers/set",
            json={"numbers": ros_id, "disabled": "yes" if disabled else "no"},
        )

    def set_peer_keys(self, interface: str, ros_id: str, public_key: str, private_key: str) -> None:
        self._request(
            "POST",
            "/interface/wireguard/peers/set",
            json={
                "numbers": ros_id,
                "public-key": public_key,
                "private-key": private_key,
            },
        )

    def set_peer_name(self, interface: str, ros_id: str, name: str) -> None:
        self._request(
            "POST",
            "/interface/wireguard/peers/set",
            json={"numbers": ros_id, "name": name or ""},
        )

    def set_peer_client_endpoint(self, interface: str, ros_id: str, client_endpoint: Optional[str]) -> None:
        self._request(
            "POST",
            "/interface/wireguard/peers/set",
            json={"numbers": ros_id, "client-endpoint": (client_endpoint or "").strip()},
        )

    def set_peer_preshared_key(self, interface: str, ros_id: str, preshared_key: Optional[str]) -> None:
        payload: dict = {"numbers": ros_id}
        if preshared_key:
            payload["preshared-key"] = preshared_key
        else:
            payload["preshared-key"] = ""
        self._request("POST", "/interface/wireguard/peers/set", json=payload)

    def get_wireguard_peer_preshared_key(self, interface: str, ros_id: str) -> Optional[str]:
        try:
            row = self._get(f"/interface/wireguard/peers/{ros_id}")
            if isinstance(row, dict):
                pk = (row.get("preshared-key") or "").strip()
                if pk:
                    return pk
        except Exception:
            pass
        try:
            rows = self._get("/interface/wireguard/peers")
            if isinstance(rows, list):
                for r in rows:
                    if (r.get(".id") or "") == ros_id and (r.get("interface") or "") == interface:
                        pk = (r.get("preshared-key") or "").strip()
                        return pk or None
        except Exception:
            pass
        return None

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
        # RouterOS REST add expects hyphenated keys and returns {"ret":"*XX"}
        payload = {
            "interface": interface,
            "public-key": public_key,
            "allowed-address": allowed_address,
        }
        if private_key:
            payload["private-key"] = private_key
        if preshared_key:
            payload["preshared-key"] = preshared_key
        if client_endpoint:
            payload["client-endpoint"] = client_endpoint
        if name:
            payload["name"] = name
        if disabled:
            payload["disabled"] = "yes"
        res = self._request("POST", "/interface/wireguard/peers/add", json=payload)
        if isinstance(res, dict):
            rid = res.get("ret") or res.get(".id")
            if isinstance(rid, str) and rid:
                return rid
        # Fallback: re-list peers and locate by pubkey
        for p in self.list_wireguard_peers(interface):
            if p.public_key == public_key:
                return p.ros_id
        raise RuntimeError("RouterOS did not return peer id")

    def remove_wireguard_peer(self, interface: str, ros_id: str) -> None:
        # RouterOS REST maps remove to HTTP DELETE against the record URL.
        # RouterOS examples show star-prefixed ids directly in the URL; some versions
        # return 500 when the "*" is percent-encoded as "%2A".
        self._request("DELETE", f"/interface/wireguard/peers/{quote(ros_id, safe='*')}")

    # ── Simple Queue management ──────────────────────────────────────────

    def add_simple_queue(self, name: str, target: str, max_limit_up: str, max_limit_down: str, comment: str = "") -> str:
        payload = {
            "name": name,
            "target": target,
            "max-limit": f"{max_limit_up}/{max_limit_down}",
        }
        if comment:
            payload["comment"] = comment
        res = self._request("POST", "/queue/simple/add", json=payload)
        if isinstance(res, dict):
            rid = res.get("ret") or res.get(".id")
            if isinstance(rid, str) and rid:
                return rid
        for q in self.list_simple_queues():
            if q["name"] == name:
                return q["ros_id"]
        raise RuntimeError("RouterOS did not return queue id")

    def update_simple_queue(self, ros_id: str, max_limit_up: str, max_limit_down: str) -> None:
        self._request(
            "POST",
            "/queue/simple/set",
            json={"numbers": ros_id, "max-limit": f"{max_limit_up}/{max_limit_down}"},
        )

    def set_simple_queue_name(self, ros_id: str, name: str) -> None:
        self._request(
            "POST",
            "/queue/simple/set",
            json={"numbers": ros_id, "name": name},
        )

    def remove_simple_queue(self, ros_id: str) -> None:
        self._request("DELETE", f"/queue/simple/{quote(ros_id, safe='*')}")

    def list_simple_queues(self, name_prefix: str = "") -> List[dict]:
        data = self._get("/queue/simple")
        result = []
        for row in (data if isinstance(data, list) else []):
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

    def get_wireguard_peer_private_key(self, interface: str, ros_id: str) -> Optional[str]:
        # Try item endpoint first (fast path). RouterOS uses ids like "*3".
        try:
            row = self._get(f"/interface/wireguard/peers/{ros_id}")
            if isinstance(row, dict):
                pk = (row.get("private-key") or "").strip()
                if pk:
                    return pk
        except Exception:
            pass

        # Fallback: list and match by .id (some versions may not allow item endpoint).
        try:
            rows = self._get("/interface/wireguard/peers")
            if isinstance(rows, list):
                for r in rows:
                    if (r.get(".id") or "") == ros_id and (r.get("interface") or "") == interface:
                        pk = (r.get("private-key") or "").strip()
                        if pk:
                            return pk
        except Exception:
            pass
        return None
