#!/bin/sh
# Dipakai dari sesi tmux "pio" agar tahan terhadap timeout.
cd /data/data/com.termux/files/home/esp32-s3-test || exit 1
proot-distro login debian-ex -- bash -lc 'cd /data/data/com.termux/files/home/esp32-s3-test && /root/.platformio/penv/bin/pio run' > run.log 2>&1
code=$?
termux-notification --id pio-run --title "pio run exit $code" --content "$(tail -n 3 run.log)" 2>/dev/null
termux-vibrate -d 150 2>/dev/null
echo "PIO-RUN-DONE exit=$code"
