# Installing APRS-X on a Raspberry Pi

For Raspberry Pi OS (Debian 13 trixie), 64-bit, desktop or Lite. Tested on a Pi 3B+
with a 5" DSI touch screen, a Digirig and a u-blox GPS on the GPIO UART.

## One command

Flash Raspberry Pi OS with Raspberry Pi Imager (set the user, Wi-Fi and SSH there),
boot it, then as that user:

```bash
curl -fsSL https://raw.githubusercontent.com/dougbram72/APRSX/main/deploy/install.sh | bash -s -- --callsign N0CALL-9
sudo reboot
```

Or from a copy of the repository: `./deploy/install.sh --callsign N0CALL-9`.

| Option | |
|---|---|
| `--callsign CALL[-SSID]` | Set the station, and let APRS-X write and run Direwolf's config, with PTT on the Digirig (RTS on its CP2102) when it's plugged in. Leave it out to keep the current settings. |
| `--no-head` | No touch-screen head unit (web UI only). |
| `--no-boot` | Don't touch `/boot/firmware` (see below). |
| `--dir DIR` | Install somewhere other than `~/aprsx`. |
| `--branch NAME` | Git branch to install when piped from curl (default `main`). |

Then open `http://<pi>:8080/settings` and check the audio device, PTT, path,
position and beacon settings. **A fresh install beacons by default** (SmartBeaconing
with a GPS fix, otherwise every 30 minutes from the fixed position) once a callsign
is set; turn that off in the settings first if you don't want it.

Re-running the script updates the code and packages and keeps the settings and
database (`~/.local/share/aprsx/aprsx.db`).

## What it sets up

- **Packages:** `direwolf`, `gpsd`, `gpsd-clients`, `python3-venv`, `git`, `rsync`, `alsa-utils`, and
  for the head unit Debian's PySide6/Qt 6 packages (`deploy/head-apt-packages.txt`).
- **Core:** a venv at `~/aprsx/.venv` with the `aprsx` package installed, with the
  `mesh` extra (the `meshcore` library, for a MeshCore companion radio on USB).
- **gpsd:** `/etc/default/gpsd` from `deploy/gpsd.default` (GPS on `/dev/serial0`,
  USB auto-probing off so it leaves the Digirig and SCU-66 alone). The original is
  kept as `/etc/default/gpsd.pre-aprsx`.
- **Digirig audio** (first run, when the Digirig is plugged in): turns off the sound
  chip's Auto Gain Control and sets Mic Capture Volume to 19, then saves the mixer with
  `alsactl store` so it survives power cuts. With AGC on, Direwolf sees a receive level
  of about 200 even with the radio turned down. After that, adjust the level yourself
  (see *Receive audio level*); re-running the installer leaves it alone.
- **WirePlumber** (desktop image): a rule so PipeWire leaves the Digirig's sound card
  to Direwolf.
- **Power button:** `/etc/sudoers.d/aprsx-power` lets the core run `systemctl poweroff`
  and `systemctl reboot` (and nothing else) without a password, for the head unit's ⏻
  button (`POST /api/system/power`). systemd stops the services cleanly before the Pi
  turns off; wait for the green activity light to stop before pulling power.
- **systemd user units** in `~/.config/systemd/user/`, with lingering on so they start
  at boot without a login:

| Unit | Runs | Notes |
|---|---|---|
| `aprsx-direwolf.service` | Direwolf with `~/.config/aprsx/direwolf.conf` | Only starts once that file exists. The core writes it when *Manage Direwolf* is on and restarts the unit when radio settings change. |
| `aprsx-core.service` | `aprsx-core` (web UI and API on :8080) | Reconnects to Direwolf and gpsd on its own, so their start order doesn't matter. |
| `aprsx-head.service` | `python3 -m aprsx.head --fullscreen` (system Python) | Wants and starts after the core. Settings in `~/.config/aprsx/head.env`. |

gpsd is the normal system service (`gpsd.socket`/`gpsd.service`).

Useful commands:

```bash
systemctl --user status aprsx-core aprsx-direwolf aprsx-head
systemctl --user restart aprsx-head
sudo journalctl _SYSTEMD_USER_UNIT=aprsx-core.service -f   # when the user journal isn't persistent
~/aprsx/.venv/bin/aprsx-config                              # print the settings
```

`aprsx-config KEY=VALUE ...` changes settings from the shell (e.g. `ssid=7`,
`aprsis.enabled=true`). Stop `aprsx-core` first, or the running core will overwrite
the change on its next save.

## Receive audio level

Direwolf prints `audio level = N` for each packet it decodes; aim for about 50 on
strong local stations. Set the radio's volume first (about a third to half on a UV-5R
works well with the installer's level), then fine-tune the Digirig's capture gain:

```bash
journalctl -f -o cat _SYSTEMD_USER_UNIT=aprsx-direwolf.service | grep "audio level"
amixer -c Device cset name='Mic Capture Volume' 19   # 0-35, 1 dB per step
sudo alsactl store                                   # keep it after a power cut
```

## MeshCore radio (optional)

A LoRa board such as a Heltec V4 flashed with MeshCore's **USB-serial companion**
firmware (the BLE companion build doesn't answer on USB). Plug it into the Pi, then on
the Settings page turn on *MeshCore* and pick its `/dev/serial/by-id/...` port. The
status strip shows a Mesh pill once it's connected; messages, nodes heard and
war-driving are on the Mesh page (and behind the MESH pill on the head unit). For
war-driving, add a `#wardriving` channel on the device first, or only discovery
requests go out. The `pi` user needs to be in the `dialout` group to open the port.

## Boot configuration

### UART GPS

The Pi 3B+ has one full UART, normally used by Bluetooth. The installer (unless
`--no-boot`) backs up `config.txt` and `cmdline.txt` as `*.pre-aprsx`, then:

- adds `enable_uart=1` and `dtoverlay=disable-bt` to `config.txt` (skipped if
  `miniuart-bt` or `disable-bt` is already there; `miniuart-bt` keeps Bluetooth on the
  weaker mini UART, if you need it);
- removes any `console=serial0,…` from `cmdline.txt`, so no login console runs on the
  GPS pins;
- disables `hciuart` and the serial getty.

A reboot is needed after the first run; the script says when. Check the GPS with
`gpspipe -w`.

### DSI screen

The official Raspberry Pi 5" and 7" DSI displays are detected by the default
`display_auto_detect=1` in `config.txt`. Other DSI panels need their own
`dtoverlay=` line from the maker. Rotation is set on the display (desktop: *Screen
Configuration*; Lite: `video=DSI-1:…,rotate=…` in `cmdline.txt`).

### Head unit: desktop or eglfs

The installer writes `~/.config/aprsx/head.env` on the first run:

- **Desktop image** (graphical target with labwc): `QT_QPA_PLATFORM=wayland` and
  `WAYLAND_DISPLAY=wayland-0`. The head unit waits for the autologin session's
  compositor, then covers the screen. Desktop autologin must stay on (the default).
- **Lite image:** `QT_QPA_PLATFORM=eglfs`. Qt draws straight to the display through
  DRM/KMS and reads touch through libinput, so no desktop is needed; it boots faster and
  uses less RAM. The user needs to be in the `video`, `render` and `input` groups
  (the default Pi user is).

Only one of them can own the display: don't use eglfs while a desktop session is
running. To switch, edit `head.env` and run `systemctl --user restart aprsx-head`.
With both HDMI and DSI connected, eglfs uses the first connected output; to pin it to
the DSI screen, add `QT_QPA_EGLFS_KMS_CONFIG=/home/pi/.config/aprsx/eglfs.json` with:

```json
{ "device": "/dev/dri/card0", "outputs": [ { "name": "HDMI1", "mode": "off" } ] }
```

Extra head-unit options (e.g. `--no-keyboard`) go in `HEAD_ARGS=` in `head.env`.
