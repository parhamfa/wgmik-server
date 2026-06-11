# wgmik-server — raw /container install for MikroTik RouterOS 7.15+
#
# Before importing:
#   Enable container device-mode:
#     /system/device-mode update container=yes
#   Then reboot (CHR) or physically confirm within 5 minutes (RouterBOARD).
#
# Install:
#   /tool fetch url="https://raw.githubusercontent.com/parhamfa/wgmik-server/main/deploy/mikrotik/wgmik-container.rsc" dst-path=wgmik-container.rsc
#   /import wgmik-container.rsc
#
# Open http://<router-ip>:6574
#
# This uses relative RouterOS file paths:
#   containers/wgmik - container root-dir
#   wgmik-server.tar.gz - downloaded image tarball
#
# If your router has too little internal storage, edit those paths to a mounted
# disk first (for example usb1/containers/wgmik and usb1/wgmik-server.tar.gz).
# The script detects x86_64 vs arm64 and downloads the matching release tarball.

:local arch [/system/resource/get architecture-name]
:local assetArch ""

:if ($arch = "x86_64") do={
  :set assetArch "amd64"
}
:if ($arch = "arm64") do={
  :set assetArch "arm64"
}
:if ($assetArch = "") do={
  :error ("Unsupported architecture: " . $arch . " (supported: x86_64, arm64)")
}

:local imageUrl ("https://github.com/parhamfa/wgmik-server/releases/download/mikrotik-container-images-2026-06-11/wgmik-server-linux-" . $assetArch . ".tar.gz")
:put ("Downloading wgmik-server image for " . $arch . " from " . $imageUrl)
/tool fetch url=$imageUrl dst-path=wgmik-server.tar.gz http-max-redirect-count=5
/interface/veth/add name=veth-wgmik address=10.99.0.2/24 gateway=10.99.0.1
/interface/bridge/add name=wgmik-net
/ip/address/add address=10.99.0.1/24 interface=wgmik-net
/interface/bridge/port/add bridge=wgmik-net interface=veth-wgmik
/ip/firewall/nat/add chain=srcnat action=masquerade src-address=10.99.0.0/24 comment=wgmik
/ip/firewall/nat/add chain=dstnat action=dst-nat dst-port=6574 protocol=tcp to-addresses=10.99.0.2 to-ports=6574 comment=wgmik
/container/add comment=wgmik file=wgmik-server.tar.gz interface=veth-wgmik root-dir="containers/wgmik"
:while ([/container/get [find comment=wgmik] status] = "extracting") do={
  :delay 5s
}
/container/set [find comment=wgmik] cmd="uvicorn backend.main:app --host 0.0.0.0 --port 6574" start-on-boot=yes logging=yes
/container/start [find comment=wgmik]
