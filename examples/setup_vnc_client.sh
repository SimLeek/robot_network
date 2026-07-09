#!/bin/bash
#WARNING: untested boiler plate code. Probably doesn't work.

# Exit immediately if a command exits with a non-zero status
set -e

echo "Starting robotar installation..."

# 1. Install the current project
# If this fails (e.g. permission denied on system python), the script stops here.
echo "Installing robotar package and dependencies..."
pip install -e .

# 2. Capture environment metadata
# Using sys.executable ensures we use the current venv if active
PYTHON_PATH=$(python3 -c "import sys; print(sys.executable)")
WORKING_DIR=$(pwd)
SERVICE_NAME="robotar_vnc_client"

# 3. Verify the entry point is available
echo "Verifying installation..."
$PYTHON_PATH -c "import robopi_client" || { echo "Error: robopi_client module not found."; exit 1; }

# 4. Setup systemd user directory
SERVICE_DIR="$HOME/.local/share/systemd/user"
mkdir -p "$SERVICE_DIR"

# 5. Create the service unit file
echo "Generating systemd service file..."
cat <<EOF > "$SERVICE_DIR/$SERVICE_NAME.service"
[Unit]
Description=Robotar RoboPi Client Service
After=network.target

[Service]
Type=simple
WorkingDirectory=$WORKING_DIR
# Using -m allows python to run the installed entry point module
ExecStart=$PYTHON_PATH -m robotar.vnc_client
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# 6. Reload and trigger the service
echo "Registering and starting service..."
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service"
systemctl --user restart "$SERVICE_NAME.service"

echo "------------------------------------------------"
echo "[OK] Installation Successful!"
echo "Service is running as a user unit."
echo "View logs: journalctl --user -u $SERVICE_NAME -f"
echo "------------------------------------------------"
