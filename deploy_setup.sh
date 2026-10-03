#!/bin/bash
# 🚀 1-Click Cloud Deployment Script for Crypto Arbitrage Bot
echo "=========================================================="
echo " Starting Crypto Arbitrage Bot Setup on Cloud (Ubuntu/Debian)"
echo "=========================================================="

# Update and install python3
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip git screen ufw

# Allow port 5000 in firewall
sudo ufw allow 5000/tcp

# Run in background via systemd service
SERVICE_FILE="/etc/systemd/system/arbitrage-bot.service"
WORKDIR=$(pwd)

echo "[Unit]
Description=Crypto Arbitrage Auto Bot Service
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${WORKDIR}
ExecStart=/usr/bin/python3 ${WORKDIR}/server.py
Restart=always
RestartSec=5
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=arbitrage-bot

[Install]
WantedBy=multi-user.target" | sudo tee ${SERVICE_FILE}

# Reload and start service
sudo systemctl daemon-reload
sudo systemctl enable arbitrage-bot
sudo systemctl restart arbitrage-bot

echo "=========================================================="
echo "✅ Bot is now running 24/7 as a background systemd service!"
echo "Status check: sudo systemctl status arbitrage-bot"
echo "Live logs: sudo journalctl -u arbitrage-bot -f"
echo "Open your browser at: http://YOUR_SERVER_IP:5000/autotrade.html"
echo "=========================================================="
