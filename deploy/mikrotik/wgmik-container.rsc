# wgmik-server — raw /container install for MikroTik RouterOS 7.15+
#
# Before importing:
#   1. Enable container device-mode: /system/device-mode update container=yes (reboot to confirm)
#   2. Format your storage disk if needed: /disk format <slot> file-system=ext4
#   3. Replace every "disk1" below with your disk slot (usb1, pcie1, nvme1, etc.)
#
# Install:
#   /tool fetch url="https://raw.githubusercontent.com/parhamfa/wgmik-server/main/deploy/mikrotik/wgmik-container.rsc" dst-path=wgmik-container.rsc
#   /import wgmik-container.rsc
#
# Open http://<router-ip>:6574
#
# Note: registry-url is global — if you pull other containers from Docker Hub, change it back afterwards.

/container/config/set registry-url=https://ghcr.io tmpdir=disk1/tmp
/interface/veth/add name=veth-wgmik address=10.99.0.2/24 gateway=10.99.0.1
/interface/bridge/add name=wgmik-net
/ip/address/add address=10.99.0.1/24 interface=wgmik-net
/interface/bridge/port/add bridge=wgmik-net interface=veth-wgmik
/ip/firewall/nat/add chain=srcnat action=masquerade src-address=10.99.0.0/24 comment=wgmik
/ip/firewall/nat/add chain=dstnat action=dst-nat dst-port=6574 protocol=tcp to-addresses=10.99.0.2 to-ports=6574 comment=wgmik
/container/add remote-image=parhamfa/wgmik-server:latest interface=veth-wgmik root-dir=disk1/wgmik name=wgmik start-on-boot=yes logging=yes
/container/start [find name=wgmik]
