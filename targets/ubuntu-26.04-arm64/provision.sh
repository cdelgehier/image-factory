#!/usr/bin/env bash
set -euo pipefail

cloud-init status --wait
systemctl enable ssh qemu-guest-agent
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-image-factory.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
cat >/etc/cloud/cloud.cfg.d/99-image-factory.cfg <<'EOF'
system_info:
  default_user:
    name: ubuntu
    lock_passwd: true
    gecos: Ubuntu
    groups: [adm, sudo]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    shell: /bin/bash
EOF
sshd -t
sudo -u ubuntu sudo -n true
python3 --version
rsync --version
