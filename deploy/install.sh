#!/bin/bash
# APRS-X installer for Raspberry Pi OS (Debian 13 trixie, 64-bit), desktop or Lite.
#
# On a fresh SD card, as the normal user (not root):
#   curl -fsSL https://raw.githubusercontent.com/dougbram72/APRSX/main/deploy/install.sh | bash -s -- --callsign N0CALL-9
# or from a copy of the repository:
#   ./deploy/install.sh [options]
#
# Options:
#   --callsign CALL[-SSID]  set the station and let APRS-X manage Direwolf, with PTT
#                           on the Digirig when one is plugged in. Without it, the
#                           settings stay as they are (set them in the web UI).
#   --no-head               no touch-screen head unit (headless / web UI only)
#   --no-boot               leave /boot/firmware alone (UART GPS setup)
#   --dir DIR               where to install (default: ~/aprsx)
#   --branch NAME           git branch to fetch when not run from a copy (default: main)
#
# Safe to re-run: it updates the code and packages and keeps the settings and
# database (~/.local/share/aprsx). Boot files are backed up once as *.pre-aprsx.
set -euo pipefail

REPO=https://github.com/dougbram72/APRSX.git
APP_DIR=$HOME/aprsx
BRANCH=main
CALLSIGN=
HEAD=1
BOOT=1

while [ $# -gt 0 ]; do
    case $1 in
        --callsign) CALLSIGN=${2:?}; shift ;;
        --no-head) HEAD=0 ;;
        --no-boot) BOOT=0 ;;
        --dir) APP_DIR=${2:?}; shift ;;
        --branch) BRANCH=${2:?}; shift ;;
        -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
REBOOT=0

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as the normal user (e.g. pi), not root; it uses sudo where needed." >&2
    exit 1
fi
sudo true  # ask for the password once, up front

# --- packages ---------------------------------------------------------------
say "Installing packages"
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
    direwolf gpsd gpsd-clients python3-venv git rsync alsa-utils

# --- code -------------------------------------------------------------------
say "Installing APRS-X in $APP_DIR"
# Run from a copy of the repo, or piped from curl (then there's no script file).
SRC=
if [ -f "${BASH_SOURCE[0]:-}" ]; then
    SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fi
if [ -n "$SRC" ] && [ -f "$SRC/pyproject.toml" ] && [ -d "$SRC/aprsx" ]; then
    if [ "$SRC" != "$APP_DIR" ]; then
        mkdir -p "$APP_DIR"
        rsync -a --delete --exclude .venv --exclude .git --exclude '__pycache__' \
            "$SRC/" "$APP_DIR/"
    fi
elif [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch -q origin "$BRANCH"
    git -C "$APP_DIR" checkout -q "$BRANCH"
    git -C "$APP_DIR" merge -q --ff-only "origin/$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

if [ "$HEAD" = 1 ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q $(grep -v '^#' deploy/head-apt-packages.txt)
fi

# The core runs in its own venv; the head unit uses the system python3 (PySide6).
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
# The mesh extra is the MeshCore library; it only runs when MeshCore is turned on.
.venv/bin/pip install -q ".[mesh]"
# Same version number, new code: reinstall the package itself.
.venv/bin/pip install -q --force-reinstall --no-deps .

# --- gpsd -------------------------------------------------------------------
say "Configuring gpsd for the UART GPS"
if [ -f /etc/default/gpsd ] && [ ! -f /etc/default/gpsd.pre-aprsx ]; then
    sudo cp /etc/default/gpsd /etc/default/gpsd.pre-aprsx
fi
sudo install -m 644 deploy/gpsd.default /etc/default/gpsd
sudo systemctl enable -q gpsd.socket gpsd.service
sudo systemctl restart gpsd.service || true

# --- boot config: UART for the GPS -------------------------------------------
BOOTDIR=/boot/firmware
if [ "$BOOT" = 1 ] && [ -f $BOOTDIR/config.txt ]; then
    say "Boot config: UART on the GPIO pins for the GPS, Bluetooth off"
    for f in config.txt cmdline.txt; do
        [ -f $BOOTDIR/$f.pre-aprsx ] || sudo cp $BOOTDIR/$f $BOOTDIR/$f.pre-aprsx
    done
    # The Pi 3B+ only has one full UART; disable-bt gives it to the GPIO pins.
    if ! grep -qE '^\s*enable_uart=1' $BOOTDIR/config.txt \
        || ! grep -qE '^\s*dtoverlay=(disable-bt|miniuart-bt)' $BOOTDIR/config.txt; then
        {
            printf '\n# APRS-X: UART GPS on /dev/serial0 (see docs/INSTALL.md)\n[all]\n'
            grep -qE '^\s*enable_uart=1' $BOOTDIR/config.txt || echo enable_uart=1
            grep -qE '^\s*dtoverlay=(disable-bt|miniuart-bt)' $BOOTDIR/config.txt \
                || echo dtoverlay=disable-bt
        } | sudo tee -a $BOOTDIR/config.txt >/dev/null
        REBOOT=1
    fi
    # The serial console would talk over the GPS.
    if grep -qE 'console=(serial0|ttyAMA0|ttyS0),' $BOOTDIR/cmdline.txt; then
        sudo sed -i -E 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' $BOOTDIR/cmdline.txt
        REBOOT=1
    fi
    sudo systemctl disable -q --now serial-getty@ttyAMA0.service serial-getty@ttyS0.service \
        hciuart.service 2>/dev/null || true
fi

# --- audio: keep PipeWire off the Digirig ------------------------------------
if [ -d /usr/share/wireplumber ]; then
    mkdir -p ~/.config/wireplumber/wireplumber.conf.d
    install -m 644 deploy/wireplumber-disable-digirig.conf \
        ~/.config/wireplumber/wireplumber.conf.d/51-disable-digirig.conf
    systemctl --user restart wireplumber 2>/dev/null || true
fi

# --- audio: Digirig mixer ----------------------------------------------------
# Its C-Media chip starts with Auto Gain Control on, which overdrives Direwolf
# (receive level ~200, target ~50). Set a starting level once and save it, so it
# survives power cuts; later runs leave levels tuned by hand alone.
MIXER_DONE=~/.config/aprsx/digirig-mixer-set
DIGIRIG_CARD=$(grep -B1 'C-Media' /proc/asound/cards 2>/dev/null \
    | sed -n 's/^ *\([0-9]\{1,\}\) \[.*/\1/p' | head -n1 || true)
if [ -n "$DIGIRIG_CARD" ] && [ ! -f $MIXER_DONE ]; then
    say "Digirig audio: AGC off, starting capture level"
    amixer -q -c "$DIGIRIG_CARD" cset name='Auto Gain Control' off || true
    amixer -q -c "$DIGIRIG_CARD" cset name='Mic Capture Volume' 19 || true
    sudo alsactl store
    mkdir -p ~/.config/aprsx
    touch $MIXER_DONE
fi

# --- power button ------------------------------------------------------------
# The core (a lingering user service, no login session) shuts the Pi down for the
# head unit's power button; logind won't allow that without this rule.
say "Allowing APRS-X to shut down and restart the Pi"
RULE=$(mktemp)
echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot" > "$RULE"
sudo visudo -cqf "$RULE"
sudo install -m 440 -o root -g root "$RULE" /etc/sudoers.d/aprsx-power
rm -f "$RULE"

# --- services ---------------------------------------------------------------
say "Installing systemd user units"
UNITDIR=~/.config/systemd/user
mkdir -p $UNITDIR ~/.config/aprsx
for u in aprsx-direwolf aprsx-core aprsx-head; do
    sed "s|@APP_DIR@|$APP_DIR|g" deploy/$u.service > $UNITDIR/$u.service
done
if [ "$HEAD" = 1 ] && [ ! -f ~/.config/aprsx/head.env ]; then
    if [ "$(systemctl get-default)" = graphical.target ] && [ -e /usr/bin/labwc ]; then
        # Desktop image: a full-screen window in the autologin labwc session.
        printf '%s\n' QT_QPA_PLATFORM=wayland WAYLAND_DISPLAY=wayland-0 \
            > ~/.config/aprsx/head.env
    else
        # Lite image: straight onto the display through DRM/KMS.
        printf '%s\n' QT_QPA_PLATFORM=eglfs QT_QPA_EGLFS_ALWAYS_SET_MODE=1 \
            > ~/.config/aprsx/head.env
    fi
fi
# User services run at boot without anyone logging in.
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user stop aprsx-head.service aprsx-core.service 2>/dev/null || true

if [ -n "$CALLSIGN" ]; then
    say "Station settings"
    CALL=${CALLSIGN%%-*}
    SET=("callsign=$CALL" direwolf_managed=true)
    [ "$CALL" = "$CALLSIGN" ] || SET+=("ssid=${CALLSIGN#*-}")
    # The Digirig keys PTT with RTS on its CP2102 USB serial port.
    DIGIRIG=$(ls /dev/serial/by-id/*CP2102* 2>/dev/null | head -n1 || true)
    if [ -n "$DIGIRIG" ]; then
        SET+=("ptt=$DIGIRIG RTS")
    else
        echo "No Digirig found; set PTT in the web settings once it's plugged in."
    fi
    .venv/bin/aprsx-config "${SET[@]}" >/dev/null
fi

UNITS=(aprsx-direwolf.service aprsx-core.service)
if [ "$HEAD" = 1 ]; then
    UNITS+=(aprsx-head.service)
else
    systemctl --user disable -q aprsx-head.service 2>/dev/null || true
fi
systemctl --user enable -q "${UNITS[@]}"
systemctl --user start aprsx-core.service
[ "$HEAD" = 0 ] || systemctl --user restart aprsx-head.service

say "Done"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Web UI: http://${IP:-$(hostname)}:8080/  (settings: /settings)"
if [ "$REBOOT" = 1 ]; then
    echo "The boot config changed: reboot to finish (sudo reboot)."
fi
