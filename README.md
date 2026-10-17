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

## Requirements

1. **Hardware**:
   - Board **ESP32-S3 dengan Octal PSRAM** (rekomendasi varian N16R8: Flash 16MB + PSRAM 8MB). Board tanpa PSRAM tidak dapat menampung buffer audio PTT 640KB secara penuh.
   - Modul mic I2S INMP441 + DAC Amp I2S MAX98357A + Speaker 4Ω/8Ω.
   - Display OLED I2C SSD1306 (128x64).
   - Push Button untuk Push-to-Talk (GPIO18 ke GND).
   - Kabel data USB Type-C untuk flashing awal.
2. **Software & Toolchain**:
   - PlatformIO Core / CLI (untuk kompilasi firmware).
   - Node.js (v18+) & Wrangler CLI (`npm install -g wrangler`) untuk deploy Cloudflare Worker.
   - Python 3.10+ (opsional, jika memakai server lokal FastAPI).
3. **Akun & API Key**:
   - Akun **Cloudflare** aktif (Free Tier mendukung Workers & Durable Objects).
   - **Google Gemini API Key** dengan kuota aktif untuk Gemini Live API.

## Attention & Catatan Penting

> [!WARNING]
> Harap perhatikan hal-hal wajib berikut sebelum perakitan dan penggunaan:

1. **Wajib Terhubung ke Internet (Tidak Bisa Offline)**:
   - Asisten ini **sepenuhnya bergantung pada cloud AI** dan **tidak bisa berjalan offline**.
   - ESP32 wajib terhubung ke jaringan Wi-Fi 2.4 GHz dengan akses internet aktif sepanjang waktu interaksi.
2. **Flash Pertama Wajib Menggunakan Kabel USB**:
   - Fitur update nirkabel (ArduinoOTA) **hanya aktif setelah ESP32 berhasil ter-flash dan terhubung ke Wi-Fi lokal**.
   - Untuk pertama kali (fresh chip), **wajib flash melalui kabel USB** menggunakan PlatformIO atau flasher serial (`esptool.py` / `nrflash`).
3. **Offset Flash Firmware (Cegah Board Brick)**:
   - Jika flashing biner pabrik gabungan (`firmware.factory.bin`), tulis di offset `0x0`.
   - Jika flashing biner aplikasi saja (`firmware.bin`), tulis di offset `0x10000`.
4. **Konfigurasi Cloudflare Worker**:
   - Deploy worker dari folder `cloudflare/` via `npx wrangler deploy`.
   - Masukkan secret API key: `npx wrangler secret put GEMINI_API_KEY`.
   - Sesuaikan hostname `websocket_server` di `firmware/src/nova.ino` dengan domain worker Anda sebelum kompilasi.

## Lisensi & Atribusi

Proyek ini dilisensikan di bawah **MIT License**.
Diadaptasi dan dikembangkan dari kode awal proyek **Nova AI** ciptaan Parth. Teks lisensi dan hak cipta asli disertakan lengkap pada berkas `LICENSE`.
