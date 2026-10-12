#!/usr/bin/env bash
set -euo pipefail

install -d -o debian -g debian -m 0700 /home/debian/.ssh
install -o debian -g debian -m 0600 /tmp/image-factory-vagrant.pub /home/debian/.ssh/authorized_keys
rm -f /root/.ssh/authorized_keys
apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/*
cloud-init clean --logs --machine-id --seed
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed
rm -f /home/debian/.bash_history /root/.bash_history
python3 /tmp/image-factory-zero-free-space.py
find /tmp /var/tmp -mindepth 1 -delete
find /var/log -type f -exec truncate -s 0 {} +
sync
shutdown -P now
