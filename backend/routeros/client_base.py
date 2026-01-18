from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WGPeer:
    ros_id: str
    interface: str
    name: str
    public_key: str
    allowed_address: str
    disabled: bool
    rx_bytes: int
    tx_bytes: int
    last_handshake: Optional[int]  # epoch seconds or None
    endpoint: str


@dataclass
class WGInterfaceConfig:
    name: str
    public_key: str
    listen_port: int


class RouterOSClient:
    def list_wireguard_interfaces(self) -> List[str]:
        raise NotImplementedError

    def list_wireguard_peers(self, interface: str) -> List[WGPeer]:
        raise NotImplementedError

    def set_peer_disabled(self, interface: str, ros_id: str, disabled: bool) -> None:
        raise NotImplementedError

    def add_wireguard_peer(
        self,
        interface: str,
        public_key: str,
        allowed_address: str,
        name: str = "",
        comment: str = "",
        disabled: bool = False,
    ) -> str:
        """Create a WireGuard peer and return its RouterOS internal .id."""
        raise NotImplementedError

    def remove_wireguard_peer(self, interface: str, ros_id: str) -> None:
        """Remove a WireGuard peer by RouterOS internal .id."""
        raise NotImplementedError

    def get_wireguard_interface(self, interface: str) -> WGInterfaceConfig:
        """Return config for a single WireGuard interface (public key, listen port, etc.)."""
        raise NotImplementedError

    def get_primary_ipv4(self) -> str:
        """Return primary IPv4 address of the router (public if available, otherwise first private)."""
        raise NotImplementedError

