#!/bin/bash
# Script to set up Morpheus API systemd service on EC2
set -e  # Exit on error

echo "======================================================"
echo "Setting up Morpheus API Systemd Service"
echo "======================================================"

# Check if running as root/sudo
if [ "$EUID" -ne 0 ]; then 
    echo "Please run with sudo"
    exit 1
fi

# Create the systemd service file
echo "Creating systemd service file..."
cat > /etc/systemd/system/morpheus-api.service << 'EOL'
[Unit]
Description=Morpheus API Gateway
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=ec2-user
Group=ec2-user
WorkingDirectory=/home/ec2-user/morpheus-API
EnvironmentFile=/home/ec2-user/morpheus-API/.env
Environment="PATH=/home/ec2-user/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONPATH=/home/ec2-user/morpheus-API"

ExecStart=/home/ec2-user/venv/bin/python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8003

Restart=always
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30

# Hardening security
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=read-only

# Logging
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOL

# Set proper permissions
echo "Setting permissions..."
chmod 644 /etc/systemd/system/morpheus-api.service

# Verify paths and permissions
echo "Verifying paths and permissions..."
if [ ! -d "/home/ec2-user/morpheus-API" ]; then
    echo "Error: /home/ec2-user/morpheus-API directory not found"
    exit 1
fi

if [ ! -d "/home/ec2-user/venv" ]; then
    echo "Error: /home/ec2-user/venv directory not found"
    exit 1
fi

# Ensure proper ownership
chown -R ec2-user:ec2-user /home/ec2-user/morpheus-API
chown -R ec2-user:ec2-user /home/ec2-user/venv

# Reload systemd
echo "Reloading systemd..."
systemctl daemon-reload

# Enable and start service
echo "Enabling and starting service..."
systemctl enable morpheus-api
systemctl start morpheus-api

# Check status
echo "Checking service status..."
systemctl status morpheus-api

echo "======================================================"
echo "Setup complete! Checking logs..."
echo "To monitor logs in real-time, run: journalctl -u morpheus-api -f"
echo "======================================================"

# Show initial logs
journalctl -u morpheus-api -n 50 --no-pager 