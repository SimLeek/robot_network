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

# Function to install and configure UFW firewall
setup_firewall() {
    echo "Installing UFW (Uncomplicated Firewall)..."
    if [[ "$(pacman -Qi ufw)" -eq 1 ]]; then
      sudo pacman -Syu ufw --noconfirm
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

    # Step 1: Enable IPv6 if disabled
    enable_ipv6

    # Step 2: Set up the firewall and allow port 9999
    setup_firewall

    # Step 3: Ensure multicast is enabled on all network interfaces
    enable_multicast

    echo "Network configuration is complete."
}

# Run the main function
main
