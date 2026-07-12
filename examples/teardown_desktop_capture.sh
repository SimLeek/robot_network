#!/bin/bash

# Reverses setup_desktop_capture.sh: unloads v4l2loopback/snd-aloop now
# and removes the persistent-across-reboot config for both. Desktop
# capture no longer needs either -- it captures directly via ximagesrc/
# pulsesrc straight into GstSender instead. Needs sudo. Stop
# desktop_endpoint.py first if it's currently running (modprobe -r
# fails while a module is in use).

set -e

echo "Unloading v4l2loopback and snd-aloop (harmless if already unloaded)..."
sudo modprobe -r v4l2loopback 2> /dev/null || echo "  v4l2loopback: not loaded, or still in use -- stop desktop_endpoint.py and re-run if needed"
sudo modprobe -r snd-aloop 2> /dev/null || echo "  snd-aloop: not loaded, or still in use -- stop desktop_endpoint.py and re-run if needed"

echo "Removing persistent-load config..."
sudo rm -f /etc/modprobe.d/v4l2loopback-robonet.conf
sudo rm -f /etc/modules-load.d/v4l2loopback-robonet.conf
sudo rm -f /etc/modules-load.d/snd-aloop-robonet.conf

echo "------------------------------------------------"
echo "Done. v4l2loopback and snd-aloop are unloaded and won't load on next boot."
echo "If you also want to remove the v4l2loopback-dkms package itself:"
echo "  Arch:   sudo pacman -R v4l2loopback-dkms"
echo "  Debian: sudo apt-get remove v4l2loopback-dkms"
echo "------------------------------------------------"
