# FitRaceStudio Hub kiosk mode

This setup turns the Central Hub display into a FitRaceStudio kiosk after boot.
It is intended for Raspberry Pi OS Bookworm with LightDM auto-login and the
default `labwc` desktop session.

## Behavior

- Auto-opens Chromium to the local race dashboard.
- Hides the normal panel and desktop by replacing the system `labwc` autostart
  and the user `labwc` autostart.
- Disables common desktop/window shortcuts in the user `labwc` config.
- Restarts Chromium if it exits.

The default URL is:

```text
http://127.0.0.1:8000/static/index.html
```

## Install on the Hub

From a checked-out release directory on the Hub:

```bash
sudo deploy_update/kiosk/install_hub_kiosk.sh
sudo reboot
```

To install for a different desktop user or dashboard URL:

```bash
sudo FITRACE_KIOSK_USER=tony \
  FITRACE_KIOSK_URL=http://127.0.0.1:8000/static/index.html \
  deploy_update/kiosk/install_hub_kiosk.sh
```

## Rollback

The installer backs up existing files with a timestamp suffix:

```text
~/.config/labwc/autostart.fitrace-backup-YYYYmmddHHMMSS
~/.config/labwc/rc.xml.fitrace-backup-YYYYmmddHHMMSS
/etc/xdg/labwc/autostart.fitrace-backup-YYYYmmddHHMMSS
```

To temporarily leave kiosk mode over SSH:

```bash
pkill -f fitracestudio-kiosk || true
pkill -f chromium-browser || true
```

To remove kiosk mode permanently, restore the backup files or delete:

```text
~/.config/labwc/autostart
~/.config/labwc/rc.xml
~/.config/fitracestudio/kiosk.env
/usr/local/bin/fitracestudio-kiosk
```

Then restore `/etc/xdg/labwc/autostart` from its backup to bring back the
standard Raspberry Pi desktop panel and file-manager desktop.
