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

# Install and configure PostgreSQL if not present
echo "Checking PostgreSQL installation..."
if ! command -v psql &> /dev/null; then
    echo "Installing PostgreSQL..."
    sudo yum install postgresql postgresql-server postgresql-devel postgresql-contrib -y
    sudo postgresql-setup initdb
    sudo systemctl enable postgresql
    sudo systemctl start postgresql
    echo "PostgreSQL installed and started"
fi

# Install and configure Redis if not present
echo "Checking Redis installation..."
if ! command -v redis-cli &> /dev/null; then
    echo "Installing Redis..."
    sudo yum install redis -y
    sudo systemctl enable redis
    sudo systemctl start redis
    echo "Redis installed and started"
fi

# Get actual service names - typical for Amazon Linux 2023
POSTGRES_SERVICE="postgresql.service"
REDIS_SERVICE="redis.service"

echo "Using PostgreSQL service: $POSTGRES_SERVICE"
echo "Using Redis service: $REDIS_SERVICE"

# Create the systemd service file
echo "Creating systemd service file..."
cat > /etc/systemd/system/morpheus-api.service << EOL
[Unit]
Description=Morpheus API Gateway
After=network.target ${POSTGRES_SERVICE} ${REDIS_SERVICE}
Requires=${POSTGRES_SERVICE} ${REDIS_SERVICE}
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

# Verify .env file exists
if [ ! -f "/home/ec2-user/morpheus-API/.env" ]; then
    echo "Warning: .env file not found in /home/ec2-user/morpheus-API/"
    echo "Please ensure you have created the .env file with proper configuration"
    read -p "Press Enter to continue anyway, or Ctrl+C to cancel..."
fi

# Ensure proper ownership
chown -R ec2-user:ec2-user /home/ec2-user/morpheus-API
chown -R ec2-user:ec2-user /home/ec2-user/venv

# Verify services are running
echo "Verifying required services..."
for service in "$POSTGRES_SERVICE" "$REDIS_SERVICE"; do
    if ! systemctl is-active --quiet "$service"; then
        echo "Starting $service..."
        systemctl start "$service"
    fi
    echo "$service is running"
done

# Setting up PostgreSQL user and database if not already done
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='morpheus_user'" | grep -q 1; then
    echo "PostgreSQL user 'morpheus_user' already exists"
else
    echo "Creating PostgreSQL user and database..."
    sudo -u postgres psql -c "CREATE USER morpheus_user WITH PASSWORD 'your_password';"
    sudo -u postgres psql -c "CREATE DATABASE morpheus_db;"
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE morpheus_db TO morpheus_user;"
    echo "PostgreSQL user and database created"
fi

# Configure Redis password if needed
if ! grep -q "requirepass" /etc/redis/redis.conf 2>/dev/null; then
    echo "Configuring Redis password..."
    echo "requirepass your_redis_password" | sudo tee -a /etc/redis/redis.conf
    sudo systemctl restart redis
    echo "Redis password configured"
fi

# Reload systemd
echo "Reloading systemd..."
systemctl daemon-reload

# Stop existing service if running
if systemctl is-active --quiet morpheus-api; then
    echo "Stopping existing morpheus-api service..."
    systemctl stop morpheus-api
fi

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

# Final status check
echo "======================================================"
echo "Service Status Summary:"
echo "--------------------"
echo "PostgreSQL: $(systemctl is-active $POSTGRES_SERVICE)"
echo "Redis: $(systemctl is-active $REDIS_SERVICE)"
echo "Morpheus API: $(systemctl is-active morpheus-api)"
echo "======================================================" 