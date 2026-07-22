# Wi-Fi provisioning fallback

Raises the pre-built `FitRaceStudio_Setup` access point at boot when the device
can't join any saved Wi-Fi — needed because the kiosk display offers no way to
pick a network on-device.

## Prerequisites

A NetworkManager AP-mode connection profile named `Hotspot` (SSID
`FitRaceStudio_Setup`, `ipv4.method shared`, WPA2 password). On .130 this
already exists. To recreate it:

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name Hotspot \
  autoconnect no ssid FitRaceStudio_Setup \
  802-11-wireless.mode ap ipv4.method shared \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk <hotspot-password>
```

## Install (once per device)

```bash
sudo install -m 0755 fitracestudio-wifi-fallback /usr/local/bin/fitracestudio-wifi-fallback
sudo install -m 0644 fitracestudio-wifi-fallback.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fitracestudio-wifi-fallback.service
```

## Behavior

On boot, waits `FITRACE_WIFI_GRACE_SEC` (default 60s) for NetworkManager to
autoconnect a saved network, then:

- wlan0 is a client on a real Wi-Fi -> do nothing.
- wlan0 is not connected (or only running the hotspot) -> `nmcli connection up
  Hotspot`.

It runs **once at boot only**, so a Wi-Fi drop during a live race never
triggers the hotspot. Connect a phone to `FitRaceStudio_Setup` and open
`http://10.42.0.1:8000` (hub) or `:8001` (edge) to pick the real network; once
connected, wlan0 leaves AP mode (single radio) and the hotspot drops.

Override the grace period, interface, or hotspot profile name with
`FITRACE_WIFI_GRACE_SEC` / `FITRACE_WIFI_IFACE` / `FITRACE_SETUP_HOTSPOT` in the
service unit.

## fitrace-set-wifi — change Wi-Fi over SSH

`fitrace-set-wifi` lets an operator switch the device's Wi-Fi from an SSH
session on-site. Install once:

```bash
sudo install -m 0755 fitrace-set-wifi /usr/local/bin/fitrace-set-wifi
```

Usage (self-elevates via sudo):

```bash
fitrace-set-wifi --status              # current Wi-Fi + IP
fitrace-set-wifi --list                # scan nearby + saved networks
fitrace-set-wifi "<SSID>" "<password>" # add/refresh the profile and switch
```

Switching Wi-Fi changes the device IP and **drops your SSH session** — this is
expected. The activation runs detached (systemd-run) so it finishes anyway.
Reconnect at `ssh <user>@raspberrypi.local`, or find the new IP on the AP's
client list (hostname `raspberrypi`). Connecting to the network you are already
on is a safe no-op.

