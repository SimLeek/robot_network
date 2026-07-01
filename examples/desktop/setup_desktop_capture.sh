#!/bin/bash

# One-time setup for desktop-mode capture: loads the v4l2loopback (virtual
# camera) and snd-aloop (ALSA loopback) kernel modules, and makes both
# persistent across reboots. Needs sudo. Run this once before using
# examples/desktop/desktop_endpoint.py.
#
# Run ../../network_setup.sh first (or make sure gstreamer + gst-plugins-good
# are already installed) -- this script only handles the two extra kernel
# modules desktop capture needs, not the base gstreamer stack.

set -e

DESKTOP_CAM_LABEL="RobonetDesktopCam"
DESKTOP_CAM_VIDEO_NR=42

echo "Detecting OS..."
if [[ -f /etc/arch-release ]]; then
    OS="Arch"
elif [[ -f /etc/debian_version ]]; then
    OS="Debian"
else
    echo "Unsupported OS. Exiting..."
    exit 1
fi
echo "OS detected: $OS"

install_v4l2loopback_arch() {
    if pacman -Qi v4l2loopback-dkms > /dev/null 2>&1; then
        echo "v4l2loopback-dkms is already installed."
        return 0
    fi
    echo "v4l2loopback-dkms is not in the official repos; it needs an AUR helper."
    if command -v paru > /dev/null 2>&1; then
        paru -S --noconfirm v4l2loopback-dkms
    elif command -v yay > /dev/null 2>&1; then
        yay -S --noconfirm v4l2loopback-dkms
    else
        echo "No AUR helper (paru or yay) found on this machine."
        echo "Install v4l2loopback-dkms manually, then re-run this script, e.g.:"
        echo "  paru -S v4l2loopback-dkms"
        exit 1
    fi
}

install_v4l2loopback_debian() {
    if dpkg -l | grep -q v4l2loopback-dkms; then
        echo "v4l2loopback-dkms is already installed."
        return 0
    fi
    echo "Installing v4l2loopback-dkms..."
    sudo apt-get update
    sudo apt-get install -y v4l2loopback-dkms
}

if [[ "$OS" == "Arch" ]]; then
    install_v4l2loopback_arch
elif [[ "$OS" == "Debian" ]]; then
    install_v4l2loopback_debian
fi

echo "Configuring v4l2loopback (device nr $DESKTOP_CAM_VIDEO_NR, label '$DESKTOP_CAM_LABEL')..."
echo "options v4l2loopback video_nr=$DESKTOP_CAM_VIDEO_NR card_label=\"$DESKTOP_CAM_LABEL\" exclusive_caps=1" \
    | sudo tee /etc/modprobe.d/v4l2loopback-robonet.conf > /dev/null
echo "v4l2loopback" | sudo tee /etc/modules-load.d/v4l2loopback-robonet.conf > /dev/null

# snd-aloop ships in the mainline kernel on both Arch and Debian -- nothing
# to install, just load and persist it.
echo "Configuring snd-aloop (ALSA loopback, for desktop audio capture)..."
echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop-robonet.conf > /dev/null

echo "Loading modules now (this also happens automatically on next boot)..."
# Unload first in case it's already loaded with different options from an
# earlier run of this script (e.g. a different video_nr). Fails harmlessly
# if the device is currently in use -- stop desktop_endpoint.py and re-run
# this script if you need to change video_nr or the label.
sudo modprobe -r v4l2loopback 2> /dev/null || true
sudo modprobe v4l2loopback video_nr=$DESKTOP_CAM_VIDEO_NR card_label="$DESKTOP_CAM_LABEL" exclusive_caps=1
sudo modprobe snd-aloop

echo "Checking pulseaudio/pipewire-pulse is reachable (for the desktop audio feeder)..."
if command -v pactl > /dev/null 2>&1 && pactl info > /dev/null 2>&1; then
    echo "pactl OK -- desktop audio capture will work."
else
    echo "pactl not found or not reachable. Desktop VIDEO capture will still work,"
    echo "but desktop AUDIO capture will be skipped until a PulseAudio-compatible"
    echo "sound server (PulseAudio, or PipeWire with pipewire-pulse) is running."
fi

echo "------------------------------------------------"
echo "Done. v4l2loopback and snd-aloop are loaded and will persist across reboots."
echo "You can now run: python examples/desktop/desktop_endpoint.py"
echo "------------------------------------------------"
