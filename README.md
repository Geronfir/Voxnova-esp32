# Nova AI - Voice Assistant ESP32-S3

Proyek asisten suara berbasis **ESP32-S3 (N16R8)** dengan mikrofon I2S (INMP441) dan amplifier speaker I2S (MAX98357A), terhubung via WebSocket ke server AI.

## Struktur Direktori

```
nova/
├── firmware/         # Firmware ESP32-S3 berbasis PlatformIO
│   ├── platformio.ini
│   └── src/
│       └── nova.ino
├── server/           # Backend server Python (FastAPI + WebSocket)
│   ├── app.py
│   ├── requirements.txt
│   └── .env.example
├── legacy/           # Versi terdahulu (upstream & adaptasi awal)
│   ├── NovaAI-upstream.ino
│   └── NovaAI-adapted-initial.ino
├── scripts/          # Skrip utilitas (OTA, USB probe, build runner)
│   ├── board-info.py
│   ├── pio-run.sh
│   └── pio-init.sh
└── docs/             # Dokumentasi dan aset visual
```

## Hardware Pinout (ESP32-S3)

- **Mikrofon (INMP441)**: WS=GPIO15, SCK=GPIO16, SD=GPIO17
- **Speaker (MAX98357A)**: BCLK=GPIO5, LRC=GPIO6, DIN=GPIO7
- **OLED (SSD1306 128x64)**: SDA=GPIO8, SCL=GPIO9
- **Button (Push-to-Talk)**: GPIO18 (Active LOW)
- **Status LED**: GPIO48 (RGB NeoPixel)

## Alur Kerja

1. **Flash Firmware**:
   - Via OTA: `pio run -t upload --upload-port 192.168.1.3`
   - Via USB: `nrflash write --chip esp32s3 --offset 0x10000 firmware.bin --erase --verify`
2. **Jalankan Server**:
   - `python server/app.py`
