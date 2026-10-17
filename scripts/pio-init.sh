#!/bin/sh
# Dipakai dari sesi tmux "pio" agar tahan terhadap timeout.
cd /data/data/com.termux/files/home/esp32-s3-test || exit 1
proot-distro login debian-ex -- bash -lc 'cd /data/data/com.termux/files/home/esp32-s3-test && /root/.platformio/penv/bin/pio project init --board esp32-s3-devkitc-1' > init.log 2>&1
code=$?
termux-notification --id pio-init --title "pio init exit $code" --content "$(tail -n 3 init.log)" 2>/dev/null
termux-vibrate -d 150 2>/dev/null
echo "PIO-INIT-DONE exit=$code"
