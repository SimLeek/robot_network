#!/bin/bash

# Script to ensure that IPv6, multicast, and port 9999 are configured on Arch Linux

# Exit on any error
set -e

# Function to check and enable IPv6
enable_ipv6() {
    echo "Checking if IPv6 is enabled..."
    
    # Check if IPv6 is disabled in sysctl
    ipv6_status=$(sysctl net.ipv6.conf.all.disable_ipv6 | awk '{print $3}')
    if [[ "$ipv6_status" -eq 1 ]]; then
        echo "Enabling IPv6..."
        sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0
        sudo sysctl -w net.ipv6.conf.default.disable_ipv6=0
        sudo sysctl -p
    else
        echo "IPv6 is already enabled."
    fi
}

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

install_package_arch() {
    package_name=$1
    if [[ "$(pacman -Qi "$package_name" > /dev/null 2>&1; echo $?)" -ne 0 ]]; then
        echo "Installing $package_name on Arch Linux..."
        sudo pacman -S "$package_name" --noconfirm
    else
        echo "$package_name is already installed."
    fi
}

install_package_debian() {
    package_name=$1
    if [[ "$(dpkg -l | grep "$package_name" > /dev/null 2>&1; echo $?)" -ne 0 ]]; then
        echo "Installing $package_name on Raspberry Pi (Debian-based)..."
        sudo apt-get install "$package_name" -y
    else
        echo "$package_name is already installed."
    fi
}

setup_gst() {
  if [[ "$OS" == "Arch" ]]; then
    install_package_arch gstreamer
    install_package_arch gst-plugins-base
    install_package_arch gst-plugins-good
    install_package_arch gst-plugins-bad
    install_package_arch gst-plugins-ugly
    install_package_arch gst-plugin-rsrtp
    install_package_arch gst-python
    install_package_arch python-gobject
    install_package_arch libv4l
  elif [[ "$OS" == "Debian" ]]; then
    install_package_debian libgstreamer1.0-dev
    install_package_debian libgstreamer-plugins-base1.0-dev
    install_package_debian gstreamer1.0-plugins-base
    install_package_debian gstreamer1.0-plugins-good
    install_package_debian gstreamer1.0-plugins-bad
    install_package_debian gstreamer1.0-plugins-ugly
    install_package_debian gstreamer1.0-plugins-bad-apps
    install_package_debian gstreamer1.0-python3-plugin-loader
    install_package_debian gstreamer1.0-libav
    install_package_debian gstreamer1.0-tools
    install_package_debian python3-gst-1.0 python3-gi
    install_package_debian libv4l-dev
  fi
}

# Function to install and configure UFW firewall
setup_firewall() {
    echo "Installing UFW (Uncomplicated Firewall)..."
    if [[ "$OS" == "Arch" ]]; then
    install_package_arch ufw
    install_package_arch networkmanager
    elif [[ "$OS" == "Debian" ]]; then
    install_package_debian ufw
    install_package_debian network-manager
    install_package_debian libv4l-dev  # needed for python v4l camera library
    fi

    echo "Enabling UFW..."
    sudo systemctl enable ufw
    sudo systemctl start ufw

    # Allow UDP and TCP traffic on port 9999
    echo "Allowing traffic on port 9999 (UDP and TCP)..."
    sudo ufw allow 9999/udp
    sudo ufw allow 9999/tcp
    sudo ufw allow 9998/udp
    sudo ufw allow 9998/tcp

    echo "Allowing traffic on port 5600-5603 (GStreamer RTP and RTCP)..."
    sudo ufw allow 5600/udp
    sudo ufw allow 5600/tcp
    sudo ufw allow 5601/udp
    sudo ufw allow 5601/tcp
    # RTCP runs on port+1 by convention (RFC 3550 §11)
    sudo ufw allow 5602/udp
    sudo ufw allow 5602/tcp
    sudo ufw allow 5603/udp
    sudo ufw allow 5603/tcp
    # Enable the firewall if it's not already enabled
    echo "Enabling the firewall..."
    sudo ufw enable
    sudo ufw status
}

# Function to configure multicast support
enable_multicast() {
    echo "Checking multicast support..."

    # Check multicast support for each interface
    interfaces=$(ip -o link show | awk -F': ' '{print $2}')

    for interface in $interfaces; do
        echo "Checking multicast addresses on interface: $interface"
        
        # Get multicast addresses for this interface
        mcast_addresses=$(ip maddr show dev $interface)
        
        # Check if multicast IPv6 (ff02::1) is present
        if echo "$mcast_addresses" | grep -q 'ff02::1'; then
            echo "IPv6 multicast (ff02::1) is enabled on $interface."
        else
            echo "IPv6 multicast (ff02::1) is NOT enabled on $interface. Enabling multicast..."
            sudo ip link set dev $interface multicast on
        fi

        # Display all multicast addresses for verification
        echo "Multicast addresses on $interface:"
        echo "$mcast_addresses"
    done

    echo "Multicast configuration done."
   }

# Main function
main() {
    echo "Starting network configuration script..."

    enable_ipv6
    setup_gst
    setup_firewall
    enable_multicast

    echo "Network configuration is complete."
}

# Run the main function
main
