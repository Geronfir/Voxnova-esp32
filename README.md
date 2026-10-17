# nova-esp32-bridge

> Standalone ESP32-S3 Voice Assistant client with Cloudflare Worker & local Python server bridges, adapted and enhanced from [Nova AI](https://github.com/parth/nova-ai) by Parth.

## Ringkasan Proyek

Proyek ini adalah implementasi asisten suara real-time berbasis mikrokontroler **ESP32-S3 (N16R8)**. Perangkat dapat berinteraksi dua arah secara real-time melalui Gemini Live API, baik menggunakan **Cloudflare Worker (Durable Object)** tanpa server lokal, maupun melalui **Local Python Server (FastAPI)** di jaringan lokal.

## Arsitektur & Fitur Utama

- **Firmware ESP32-S3 (PlatformIO)**:
  - Audio I2S: Input mic INMP441 (16kHz 16-bit mono) & output speaker MAX98357A (24kHz).
  - Push-to-Talk (PTT) pintar dengan buffer PSRAM 640KB dan fallback RAM internal.
  - Streaming real-time WebSocket terenkripsi SSL/WSS.
  - OLED visual feedback (SSD1306) & single-pixel status LED (NeoPixel GPIO48).
  - Support Over-the-Air (ArduinoOTA) flashing.
  - Non-blocking audio I2S loop dengan auto-timeout proteksi state "BERPIKIR" jika API kuota habis.
- **Cloudflare Worker Bridge (`cloudflare/`)**:
  - Berjalan di edge Cloudflare (Durable Object) tanpa perlu menjalankan komputer/HP lokal.
  - Pacer audio ~1.5x real-time (2048B chunks) agar ring buffer hardware stabil.
  - Manual VAD handshake (activityStart/activityEnd) via PTT_END.
- **Local Python Server (`server/`)**:
  - FastAPI + WebSocket bridge untuk pengujian lokal di Termux Android / PC.

## Struktur Direktori

```
nova-esp32-bridge/
├── firmware/         # Firmware ESP32-S3 PlatformIO
│   ├── platformio.ini
│   └── src/
│       ├── nova.ino
│       └── secrets.h.example
├── cloudflare/       # Cloudflare Worker + Durable Object bridge
│   ├── src/
│   │   ├── index.ts
│   │   └── nova-do.ts
│   ├── package.json
│   └── wrangler.toml
├── server/           # Backend server lokal Python (FastAPI)
│   ├── app.py
│   ├── requirements.txt
│   └── .env.example
├── legacy/           # Arsip kode awal & upstream
├── scripts/          # Skrip utilitas flashing dan probe
├── docs/             # Dokumentasi teknis & aset
└── LICENSE           # Lisensi MIT (Copyright (c) 2026 Parth & kontributor)
```

## Hardware Pinout (ESP32-S3 N16R8)

| Komponen | Pin ESP32-S3 | Deskripsi |
| :--- | :--- | :--- |
| **INMP441 (Mic)** | WS: GPIO15, SCK: GPIO16, SD: GPIO17 | I2S Microphone |
| **MAX98357A (Amp)** | BCLK: GPIO5, LRC: GPIO6, DIN: GPIO7 | I2S Audio Amp |
| **SSD1306 (OLED)** | SDA: GPIO8, SCL: GPIO9 | Display 128x64 I2C |
| **Push-to-Talk** | GPIO18 | Active LOW (Internal Pullup) |
| **Status LED** | GPIO48 | Onboard WS2812 RGB |

## Lisensi & Atribusi

Proyek ini dilisensikan di bawah **MIT License**.
Diadaptasi dan dikembangkan dari kode awal proyek **Nova AI** ciptaan Parth. Teks lisensi dan hak cipta asli disertakan lengkap pada berkas `LICENSE`.
