# voxnova-esp32

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform: ESP32-S3](https://img.shields.io/badge/Platform-ESP32--S3-red.svg)](https://www.espressif.com/en/products/socs/esp32-s3)
[![Framework: PlatformIO](https://img.shields.io/badge/Framework-PlatformIO-orange.svg)](https://platformio.org/)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers%20%26%20DO-F38020?logo=cloudflare)](https://workers.cloudflare.com/)

> A real-time, low-latency AI voice assistant running on the **ESP32-S3 microcontroller**, communicating bidirectionally with **Google Gemini Live API** via **Cloudflare Workers (Durable Objects)** and local server bridges. Adapted and enhanced from the original [Nova AI](https://github.com/parth/nova-ai) by Parth.

---

## ⚡ Overview

**voxnova-esp32** turns an ESP32-S3 development board into an autonomous voice assistant. By streaming raw PCM audio over WebSockets to a Cloudflare Worker edge bridge, it provides natural voice conversations with Google Gemini Live API without requiring a tethered smartphone or local host computer.

---

## ✨ Key Features & Enhancements

- **Hardware Client (ESP32-S3 N16R8)**:
  - **I2S Audio Pipeline**: 16kHz 16-bit mono input via INMP441 MEMS microphone & 24kHz output through MAX98357A I2S DAC amplifier.
  - **Push-to-Talk (PTT)**: Buffered recording with 640KB PSRAM allocation and safe internal SRAM fallback.
  - **Over-the-Air Updates (OTA)**: Seamless wireless firmware flashing via ArduinoOTA.
  - **Fault-Tolerant Engine**: Non-blocking audio I2S loops, 20s Wi-Fi connect timeouts, and automatic 15s visual reset guards on Gemini quota depletion (HTTP 429).
  - **Visual Feedback**: Real-time status rendered on an SSD1306 128x64 OLED display alongside a WS2812 RGB LED indicator (GPIO48).
- **Edge Bridge (`cloudflare/`)**:
  - Hosted directly on Cloudflare Workers using **Durable Objects**.
  - Paced audio downstream (~1.5x real-time chunks) preserving microcontroller ring buffers.
  - Manual VAD coordination (`activityStart` / `activityEnd`) triggered via PTT state transitions.
- **Local Server (`server/`)**:
  - Python FastAPI WebSocket bridge for rapid local benchmarking and testing.

---

## ⚠️ Attention & Critical Notes

> [!WARNING]
> Please review these mandatory operational constraints prior to setup:

1. **Active Internet Connection Required (No Offline Mode)**:
   - This device **CANNOT operate offline**. It requires a continuous 2.4 GHz Wi-Fi connection with active outbound internet access to stream audio to the AI backend.
2. **First Flash MUST Use USB Cable**:
   - ArduinoOTA wireless flashing is **only accessible after the base firmware is already flashed and connected to your local Wi-Fi**.
   - Initial flashing on a bare ESP32-S3 board **must be performed over a wired USB cable** using PlatformIO or serial flashing tools (`esptool.py` / `nrflash`).
3. **Flash Memory Offset Warning (Prevent Board Bricking)**:
   - When writing a merged factory binary (`firmware.factory.bin`), flash at offset **`0x0`**.
   - When writing application-only firmware (`firmware.bin`), flash at offset **`0x10000`**. Flashing at the wrong offset will cause immediate bootloops.
4. **Hardware Prerequisite**:
   - The firmware is built specifically for **ESP32-S3 boards with Octal PSRAM (N16R8: 16MB Flash + 8MB PSRAM)**. Standard ESP32 or ESP32-S3 variants without external PSRAM will fail to allocate the required audio capture buffers.

---

## 📋 Requirements

### 1. Hardware
- **ESP32-S3 Dev Board** with Octal PSRAM (N16R8 recommended).
- **INMP441** Omnidirectional MEMS I2S Microphone module.
- **MAX98357A** I2S Mono Audio Amplifier module + 4Ω/8Ω speaker.
- **SSD1306** 0.96" I2C OLED Display (128x64).
- Tactile Push Button (PTT, GPIO18 to GND).
- USB Type-C data cable (for initial flash and debugging).

### 2. Software & Tooling
- [PlatformIO Core / CLI](https://platformio.org/) for building and deploying firmware.
- [Node.js](https://nodejs.org/) (v18+) and [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/) (`npm install -g wrangler`) for deploying the Cloudflare Worker.
- Python 3.10+ (optional, only needed for the local FastAPI server bridge).

### 3. Accounts & API Keys
- Active **Cloudflare Account** (Free plan includes Workers and Durable Objects).
- **Google Gemini API Key** with active access to Gemini Multimodal Live API (`v1alpha`).

---

## 🔌 Hardware Pinout (ESP32-S3 N16R8)

| Module | ESP32-S3 Pin | Function |
| :--- | :--- | :--- |
| **INMP441 Microphone** | `GPIO15` (WS), `GPIO16` (SCK), `GPIO17` (SD) | I2S Audio Input |
| **MAX98357A Amplifier**| `GPIO5` (BCLK), `GPIO6` (LRC), `GPIO7` (DIN) | I2S Audio Output |
| **SSD1306 Display**    | `GPIO8` (SDA), `GPIO9` (SCL)                 | I2C Visual Interface |
| **Push-to-Talk Button**| `GPIO18` (Active LOW to GND)                 | Input Pullup Control |
| **Status LED**         | `GPIO48`                                     | Onboard WS2812 RGB LED |

---

## 🚀 Quick Start

### 1. Deploy Cloudflare Worker Edge Bridge
```bash
cd cloudflare
npm install
npx wrangler secret put GEMINI_API_KEY
# Enter your Google Gemini API Key when prompted

npx wrangler deploy
# Note your deployed Worker URL (e.g. your-subdomain.workers.dev)
```

### 2. Configure & Flash ESP32-S3 Firmware
```bash
cd firmware
cp src/secrets.h.example src/secrets.h
# Edit src/secrets.h with your Wi-Fi SSID and Password

# Edit src/nova.ino to set websocket_server to your Cloudflare Worker hostname
# Compile and flash via USB cable:
pio run -t upload
```

### 3. Subsequent Flashing via Wireless OTA
Once connected to your local network, you can flash updates over-the-air:
```bash
pio run -t upload --upload-port <ESP32_IP_ADDRESS>
```

---

## 📂 Project Structure

```
voxnova-esp32/
├── firmware/         # PlatformIO ESP32-S3 C++ firmware
│   ├── platformio.ini
│   └── src/
│       ├── nova.ino
│       ├── secrets.h.example
│       └── secrets.h (ignored)
├── cloudflare/       # Edge bridge using Cloudflare Workers & Durable Objects
│   ├── src/
│   │   ├── index.ts
│   │   └── nova-do.ts
│   ├── package.json
│   └── wrangler.toml
├── server/           # Optional local Python backend (FastAPI WebSocket)
│   ├── app.py
│   ├── requirements.txt
│   └── .env.example
├── legacy/           # Original upstream & initial experimental code
├── scripts/          # Flashing utilities and chip diagnostics
├── docs/             # Technical schematics and asset files
└── LICENSE           # MIT License
```

---

## 📄 License & Attribution

This project is licensed under the [MIT License](LICENSE).

It is based on and evolved from the original [Nova AI](https://github.com/parth/nova-ai) project created by **Parth**. The original copyright notice and MIT permissions are fully retained in accordance with the license terms.
