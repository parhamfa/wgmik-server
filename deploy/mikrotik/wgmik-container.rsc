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
# The script is idempotent: safe to re-run after a partial failure.
# It detects x86_64 vs arm64 and downloads the matching release tarball.
#
# RouterOS older than 7.18 cannot follow GitHub's download redirects.
# On those versions: download the tarball on a PC, upload it to the router
# as "wgmik-server.tar.gz", then re-import this script (the download step
# is skipped when the file already exists).
#
# Storage: defaults to internal storage with relative paths
# (containers/wgmik + wgmik-server.tar.gz). If internal free space is below
# ~500MiB and a mounted disk (usb1, pcie1, ...) is present, that disk is used
# automatically instead.

:local arch [/system/resource/get architecture-name]
:local assetArch ""
:if ($arch = "x86_64") do={ :set assetArch "amd64" }
:if ($arch = "arm64") do={ :set assetArch "arm64" }
:if ($assetArch = "") do={
  :error ("Unsupported architecture: " . $arch . " (supported: x86_64, arm64)")
}

# Pick storage: internal if it has enough free space, otherwise first mounted disk
:local prefix ""
:if ([/system/resource/get free-hdd-space] < 524288000) do={
  :foreach d in=[/disk/find] do={
    :if ($prefix = "") do={
      :do {
        :local mp [/disk/get $d mount-point]
        :if ([:len $mp] > 0) do={ :set prefix ($mp . "/") }
      } on-error={}
    }
  }
  :if ($prefix = "") do={
    :error "Not enough free internal storage (need ~500MiB) and no mounted disk found. Attach/mount a disk (USB, etc) and re-run: /import wgmik-container.rsc"
  }
  :put ("Internal storage low, using disk: " . $prefix)
}
:local imageFile ($prefix . "wgmik-server.tar.gz")
:local rootDir ($prefix . "containers/wgmik")
:local imageUrl ("https://github.com/parhamfa/wgmik-server/releases/download/mikrotik-container-images-2026-06-11/wgmik-server-linux-" . $assetArch . ".tar.gz")

:if ([:len [/container/find comment="wgmik"]] > 0) do={
  :put "wgmik container already exists, skipping image download"
} else={
:if ([:len [/file/find name=$imageFile]] > 0) do={
  :put ("Image tarball " . $imageFile . " already present, skipping download")
} else={
  :put ("Downloading wgmik-server image for " . $arch . " from " . $imageUrl)
  :local fetched false
  :do {
    :local fetchCmd [:parse ("/tool fetch url=\"" . $imageUrl . "\" dst-path=\"" . $imageFile . "\" http-max-redirect-count=5")]
    $fetchCmd
    :set fetched true
  } on-error={}
  :if (!$fetched) do={
    :do {
      /tool fetch url=$imageUrl dst-path=$imageFile
      :set fetched true
    } on-error={}
  }
  :if (!$fetched) do={
    :do { /file/remove [find name=$imageFile] } on-error={}
    :error ("Download failed. Either the connection dropped, storage is full, or this RouterOS version (pre-7.18) cannot follow GitHub redirects. Fix: download " . $imageUrl . " on a PC, upload it to the router as " . $imageFile . ", then re-run: /import wgmik-container.rsc")
  }
}
}

:if ([:len [/interface/veth/find name="veth-wgmik"]] = 0) do={
  /interface/veth/add name=veth-wgmik address=10.99.0.2/24 gateway=10.99.0.1
}
:if ([:len [/interface/bridge/find name="wgmik-net"]] = 0) do={
  /interface/bridge/add name=wgmik-net
}
:if ([:len [/ip/address/find address="10.99.0.1/24" interface="wgmik-net"]] = 0) do={
  /ip/address/add address=10.99.0.1/24 interface=wgmik-net
}
:if ([:len [/interface/bridge/port/find bridge="wgmik-net" interface="veth-wgmik"]] = 0) do={
  /interface/bridge/port/add bridge=wgmik-net interface=veth-wgmik
}
:if ([:len [/ip/firewall/nat/find comment="wgmik" chain=srcnat]] = 0) do={
  /ip/firewall/nat/add chain=srcnat action=masquerade src-address=10.99.0.0/24 comment=wgmik
}
:if ([:len [/ip/firewall/nat/find comment="wgmik" chain=dstnat]] = 0) do={
  /ip/firewall/nat/add chain=dstnat action=dst-nat dst-port=6574 protocol=tcp to-addresses=10.99.0.2 to-ports=6574 comment=wgmik
}

:if ([:len [/container/find comment="wgmik"]] = 0) do={
  /container/add comment=wgmik file=$imageFile interface=veth-wgmik root-dir=$rootDir
}
/container/set [find comment="wgmik"] cmd="uvicorn backend.main:app --host 0.0.0.0 --port 6574" start-on-boot=yes logging=yes

:put "Waiting for image extraction, then starting container (up to ~60s)..."
:local started false
:for i from=1 to=12 do={
  :if (!$started) do={
    :do {
      /container/start [find comment="wgmik"]
      :set started true
    } on-error={
      :delay 5s
    }
  }
}
:if ($started) do={
  :put "wgmik container started. Open http://<router-ip>:6574"
} else={
  :put "Container did not start within 60s. Check: /container/print detail and /log/print where topics~\"container\""
}
