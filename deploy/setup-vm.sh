#!/usr/bin/env bash
# RippleBot VM bootstrap (RHEL 9). Run ONCE on a freshly-provisioned VM:
#   curl -fsSL <raw-url>/deploy/setup-vm.sh | bash
# or copy this file to the VM and: bash setup-vm.sh
#
# Installs Docker + compose + nginx, creates /opt/ripplebot, and preps the reverse
# proxy. It does NOT place secrets — you add /opt/ripplebot/.env yourself afterward.
set -euo pipefail

echo "==> [1/6] Installing Docker CE + compose plugin"
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker

echo "==> [2/6] Adding $USER to the docker group (re-login to take effect)"
sudo usermod -aG docker "$USER" || true

echo "==> [3/6] Installing nginx"
sudo dnf -y install nginx
sudo systemctl enable --now nginx

echo "==> [4/6] SELinux: allow nginx to proxy to the container"
sudo setsebool -P httpd_can_network_connect 1 || true

echo "==> [5/6] Creating /opt/ripplebot"
sudo mkdir -p /opt/ripplebot
sudo chown "$USER" /opt/ripplebot

echo "==> [6/6] Done. Remaining manual steps:"
cat <<'EOF'

  1. Place the reverse-proxy config (edit server_name to your subdomain):
       sudo cp deploy/nginx-vm-ripplebot.conf /etc/nginx/conf.d/ripplebot.conf
       sudo nginx -t && sudo systemctl reload nginx

  2. Create /opt/ripplebot/.env from .env.vm.example and fill in real values.

  3. Let the VM pull the private image from GHCR (PAT with read:packages):
       echo <YOUR_GHCR_PAT> | docker login ghcr.io -u shobhitrh --password-stdin

  4. In GitHub → Settings → Secrets → Actions, set:
       VM_HOST, VM_USER, VM_SSH_KEY, GHCR_PAT

  5. Trigger the deploy: GitHub → Actions → "Build & Deploy (VM)" → Run workflow.

  Verify after deploy:
       curl -s http://localhost:8000/api/health
EOF
