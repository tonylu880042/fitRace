# FitRace Studio boot splash

Installs a custom Plymouth theme on the Raspberry Pi Hub.

```bash
sudo deploy_update/plymouth/install_fitrace_splash.sh
```

The installer backs up `/etc/plymouth/plymouthd.conf`, installs the theme,
selects `Theme=fitrace`, and rebuilds every initramfs.

To restore the Raspberry Pi splash:

```bash
sudo deploy_update/plymouth/install_fitrace_splash.sh --restore-pix
```
