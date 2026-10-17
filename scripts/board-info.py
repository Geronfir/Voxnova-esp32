#!/usr/bin/env python3
"""board-info.py - baca info lengkap board ESP32 via transport nrflash.

Memakai bootstrap USB + RomLoader nrflash yg sama (no-root Termux),
lalu membaca register eFuse gaya esptool (MAC, revisi, paket, dsb).

Pakai:  python3 ~/IoT/board-info.py     (ketuk Allow saat diminta)
"""

import os
import struct
import sys

import espbridge as usb_device
from nrflash import cli, rom_loader

# --- peta register ESP32-S3 (dari esptool/targets/esp32s3.py) ---
EFUSE_BASE = 0x60007000
BLOCK1 = EFUSE_BASE + 0x44
BLOCK2 = EFUSE_BASE + 0x5C
MAC_REG = EFUSE_BASE + 0x044


def s3_decode(r):
    """r = fungsi baca register -> dict info."""
    mac0 = r(MAC_REG)
    mac1 = r(MAC_REG + 4)
    mac = struct.pack(">II", mac1, mac0)[2:]
    w3 = r(BLOCK1 + 12)   # word3 block1
    w5 = r(BLOCK1 + 20)   # word5 block1
    w4 = r(BLOCK1 + 16)   # word4 block1
    pkg = (w3 >> 21) & 0x07
    minor = (((w5 >> 23) & 0x01) << 3) | ((w3 >> 18) & 0x07)
    major = (w5 >> 24) & 0x03
    flash_cap = (w3 >> 27) & 0x07
    psram_cap = ((((w5 >> 19) & 0x01) << 2) | ((w4 >> 3) & 0x03))
    return {
        "mac": ":".join(f"{b:02x}" for b in mac),
        "paket": {0: "ESP32-S3 (QFN56)", 1: "ESP32-S3-PICO-1 (LGA56)"}.get(
            pkg, f"tak dikenal ({pkg})"),
        "revisi": f"v{major}.{minor}",
        "flash_embedded": {0: "tidak ada (pakai flash eksternal board)",
                           1: "embedded 8MB", 2: "embedded 4MB"}.get(
            flash_cap, f"tak dikenal ({flash_cap})"),
        "psram_embedded": {0: "tidak ada (pakai PSRAM eksternal board)",
                           1: "embedded 8MB", 2: "embedded 2MB",
                           3: "embedded 16MB", 4: "embedded 4MB"}.get(
            psram_cap, f"tak dikenal ({psram_cap})"),
        "secure_boot": bool(r(EFUSE_BASE + 0x38) & (1 << 20)),
        "flash_crypt_cnt": (r(EFUSE_BASE + 0x34) >> 18) & 0x07,
    }


def child_main():
    device, ep_in, ep_out, reset = cli.acquire_device()
    read_fn, write_fn = cli.make_read_write(device, ep_in, ep_out)
    loader = rom_loader.RomLoader(read_fn, write_fn, "esp32")
    cli._log("[*] Resetting into ROM bootloader...")
    if not cli.enter_bootloader_and_sync(device, loader, reset):
        cli._log("[-] Bootloader tidak menjawab.")
        reset.reset_to_app(device)
        sys.exit(1)
    cli._log("[+] Bootloader is alive and synced.")

    magic = loader.read_reg(rom_loader.CHIP_MAGIC_REG_ADDR)
    chip = rom_loader.KNOWN_MAGIC.get(magic, "tak dikenal")
    loader.chip = chip if chip in ("esp32s3", "esp32c3", "esp32s2",
                                   "esp32", "esp8266") else "esp32s3"
    cli._log(f"[+] Chip magic : 0x{magic:08X} -> {chip}")
    cli._log(f"[*] USB        : {usb_device.describe_device(device)}")

    if chip == "esp32s3":
        info = s3_decode(loader.read_reg)
        cli._log(f"[+] Paket      : {info['paket']}")
        cli._log(f"[+] Revisi     : {info['revisi']}")
        cli._log(f"[+] MAC        : {info['mac']}")
        cli._log(f"[+] Flash emb. : {info['flash_embedded']}")
        cli._log(f"[+] PSRAM emb. : {info['psram_embedded']}")
        cli._log(f"[+] SecureBoot : {'AKTIF' if info['secure_boot'] else 'mati'}")
        cli._log(f"[+] CryptCnt   : {info['flash_crypt_cnt']} "
                 "(0 = flash tidak terenkripsi)")
        cli._log("[+] Kristal    : 40MHz (tetap di S3)")
        cli._log("[+] Fitur      : Wi-Fi, BT 5 (LE), Dual Core, 240MHz")
    else:
        cli._log("[-] Decode register detail baru didukung esp32s3.")

    reset.reset_to_app(device)
    cli._log("[*] Reset back to application.")


def parent_main():
    try:
        device_path = usb_device.auto_detect_device()
    except RuntimeError as e:
        print(f"[-] {e}")
        sys.exit(1)
    print(f"[+] Found device: {device_path}")
    print("[*] Requesting USB permission (tap Allow on your phone)...")
    if not usb_device.request_permission(device_path):
        print("[-] Permission denied or timed out.")
        sys.exit(1)
    cmd = f"{sys.executable} {os.path.abspath(__file__)} --child"
    child_pid, child_done, tail_thread = usb_device.launch_with_fd(
        cmd=cmd, device_path=device_path, log_file=cli.LOG_FILE,
        tail_fn=lambda line: print(line, end="", flush=True),
    )
    try:
        _, status = os.waitpid(child_pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
    except KeyboardInterrupt:
        import signal
        os.kill(child_pid, signal.SIGTERM)
        _, status = os.waitpid(child_pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
    child_done.set()
    tail_thread.join(timeout=2)
    sys.exit(exit_code)


if __name__ == "__main__":
    if "TERMUX_USB_FD" in os.environ or "--child" in sys.argv:
        child_main()
    else:
        parent_main()
