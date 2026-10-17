#include <Adafruit_NeoPixel.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <driver/i2s.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <esp_heap_caps.h>
#include <ArduinoOTA.h>
#include <WiFiClientSecure.h>

// NETWORK
#if __has_include("secrets.h")
#include "secrets.h"
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASS;
#else
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
#endif

const char* websocket_server = "nova-esp32.chikip.workers.dev";  // Cloudflare Worker (tanpa HP)
const uint16_t websocket_port = 443;

WebSocketsClient webSocket;

// MIC (INMP441) - hardware user: SCK=16 WS=15 SD=17
#define MIC_WS   15
#define MIC_SCK  16
#define MIC_SD   17

// SPEAKER (MAX98357A) - hardware user: BCLK=5 LRC=6 DIN=7
#define SPK_LRC  6
#define SPK_BCLK 5
#define SPK_DIN  7
#define BUFFER_SAMPLES 128

// LED onboard ESP32-S3 (NeoPixel tunggal, GPIO48)
#define LED_PIN 48
#define NUM_LEDS 1

Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);

int16_t audio_buffer[BUFFER_SAMPLES];

// OLED (SSD1306 128x64) - hardware user: SDA=8 SCL=9
#define OLED_SDA 8
#define OLED_SCL 9
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oled_ok = false;

// BUTTON push-to-talk - GPIO18, ditekan = LOW (pullup internal)
#define BTN_PIN 18

// REKAMAN PTT: 16kHz mono 16-bit = 32000 byte/detik, maks 20 detik (di PSRAM)
#define PTT_MAX_BYTES (32000UL * 20)
#define PTT_MIN_BYTES (32000UL / 2)  // 0.5 detik minimum; lebih pendek = kepencet tak sengaja
uint8_t* ptt_buf = NULL;
size_t ptt_len = 0;
bool ptt_recording = false;
bool ptt_streaming = false;
size_t ptt_hold = 0;
#define PTT_START_BYTES (32000UL / 3)

// STATE
volatile bool nova_is_talking = false;
unsigned long last_speaker_time = 0;
unsigned long last_thinking_time = 0;

// JITTER BUFFER
#define RING_BUFFER_SIZE (96 * 1024)
uint8_t ring_buffer[RING_BUFFER_SIZE];
volatile size_t ring_head = 0;
volatile size_t ring_tail = 0;
volatile size_t ring_count = 0;
SemaphoreHandle_t ring_mutex;

#define PREBUFFER_BYTES (12000)
#define SPEAKER_VOL_PCT 45   // atenuasi software (gain hardware MAX98357A tetap 9dB)
volatile bool speaker_primed = false;

enum NovaState {
  BOOTING,
  LISTENING,
  RECORDING,
  THINKING,
  SPEAKING,
  SLEEPING
};

NovaState novaState = BOOTING;
volatile bool is_sleeping = false;

// forward declarations (prototipe otomatis Arduino gagal untuk argumen default)
void oled_show(const char* l1, const char* l2 = "");
void setState(NovaState s, const char* l1, const char* l2 = "");

void ring_buffer_write(const uint8_t* data, size_t len) {
  if (xSemaphoreTake(ring_mutex, portMAX_DELAY) == pdTRUE) {
    for (size_t i = 0; i < len; i++) {
      if (ring_count >= RING_BUFFER_SIZE) {
        ring_tail = (ring_tail + 1) % RING_BUFFER_SIZE;
        ring_count--;
      }
      ring_buffer[ring_head] = data[i];
      ring_head = (ring_head + 1) % RING_BUFFER_SIZE;
      ring_count++;
    }
    xSemaphoreGive(ring_mutex);
  }
}

size_t ring_buffer_read(uint8_t* out, size_t max_len) {
  size_t read_len = 0;
  if (xSemaphoreTake(ring_mutex, portMAX_DELAY) == pdTRUE) {
    read_len = (max_len < ring_count) ? max_len : ring_count;
    for (size_t i = 0; i < read_len; i++) {
      out[i] = ring_buffer[ring_tail];
      ring_tail = (ring_tail + 1) % RING_BUFFER_SIZE;
    }
    ring_count -= read_len;
    xSemaphoreGive(ring_mutex);
  }
  return read_len;
}

size_t ring_buffer_available() {
  size_t avail = 0;
  if (xSemaphoreTake(ring_mutex, portMAX_DELAY) == pdTRUE) {
    avail = ring_count;
    xSemaphoreGive(ring_mutex);
  }
  return avail;
}

void ring_buffer_clear() {
  if (xSemaphoreTake(ring_mutex, portMAX_DELAY) == pdTRUE) {
    ring_head = 0;
    ring_tail = 0;
    ring_count = 0;
    xSemaphoreGive(ring_mutex);
  }
  speaker_primed = false;
}

// SPEAKER TASK
void speakerTask(void* param) {
  uint8_t play_chunk[1024];
  size_t bytes_written;

  for (;;) {
    if (!speaker_primed) {
      if (ring_buffer_available() >= PREBUFFER_BYTES) {
        speaker_primed = true;
      } else {
        vTaskDelay(pdMS_TO_TICKS(5));
        continue;
      }
    }

    // baca hanya kelipatan 2 byte agar frame I2S 16-bit tetap sejajar
    size_t want = sizeof(play_chunk);
    size_t avail = ring_buffer_available();
    if (want > avail) want = avail;
    want &= ~((size_t)1);
    size_t got = (want > 0) ? ring_buffer_read(play_chunk, want) : 0;

    if (got > 0) {
      // atenuasi volume (PCM int16) agar tidak terlalu keras
      int16_t* ps = (int16_t*)play_chunk;
      for (size_t i = 0; i < got / 2; i++) {
        ps[i] = (int16_t)(((int32_t)ps[i] * SPEAKER_VOL_PCT) / 100);
      }
      nova_is_talking = true;
      last_speaker_time = millis();
      i2s_write(I2S_NUM_1, play_chunk, got, &bytes_written, portMAX_DELAY);
    } else {
      // underrun sementara: senyap pendek, TETAP primed (tanpa gap 0.25s)
      static const uint8_t sil[256] = {0};
      i2s_write(I2S_NUM_1, sil, sizeof(sil), &bytes_written, portMAX_DELAY);
    }
  }
}

// I2S SETUP
void setup_i2s() {
  // ----- MIC -----
  i2s_config_t mic_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 128,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t mic_pins = {
    .mck_io_num = I2S_PIN_NO_CHANGE,
    .bck_io_num = MIC_SCK,
    .ws_io_num = MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = MIC_SD
  };

  i2s_driver_install(I2S_NUM_0, &mic_config, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &mic_pins);

  // ----- SPEAKER -----
  i2s_config_t spk_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 24000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 16,
    .dma_buf_len = 1024,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };

  i2s_pin_config_t spk_pins = {
    .mck_io_num = I2S_PIN_NO_CHANGE,
    .bck_io_num = SPK_BCLK,
    .ws_io_num = SPK_LRC,
    .data_out_num = SPK_DIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };

  i2s_driver_install(I2S_NUM_1, &spk_config, 0, NULL);
  i2s_set_pin(I2S_NUM_1, &spk_pins);
}

// WEBSOCKET EVENTS
void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch(type) {
    case WStype_CONNECTED:
      Serial.println("[+] Successfully Linked to Python Brain!");
      novaState = LISTENING;
      nova_is_talking = false;
      ring_buffer_clear();
      break;

    case WStype_DISCONNECTED:
      Serial.println("[-] Disconnected from Brain.");
      nova_is_talking = false;
      novaState = BOOTING;
      ring_buffer_clear();
      break;

    case WStype_TEXT: {

      String msg = String((char*)payload).substring(0, length);

      Serial.print("[TEXT CMD] ");
      Serial.println(msg);

      if (msg == "CLEAR_AUDIO") {

          Serial.println("[i] Barge-in: clearing speaker buffer.");
          ring_buffer_clear();

      }

      else if (msg == "IDLE") {

          Serial.println("[i] Server: tidak ada suara ditranskrip, batal berpikir.");
          if (!ptt_recording) setState(LISTENING, "NOVA", "siap");

      }

      else if (msg == "SLEEP") {

          Serial.println("[i] AI commanded sleep.");
          novaState = SLEEPING;
          oled_show("TIDUR", "zzz...");
          is_sleeping = true;
          webSocket.disconnect();

      }

      else if (msg == "ERROR_QUOTA") {
          Serial.println("[i] Server: Kuota API habis.");
          setState(LISTENING, "KUOTA HABIS", "tunggu kuota");
      }

      else if (msg == "ERROR") {
          Serial.println("[i] Server error.");
          setState(LISTENING, "ERROR", "coba lagi");
      }

      // RGB LIGHT CONTROLS

      else if (msg == "RGB_ON") {

          Serial.println("RGB ON");

      }

      else if (msg == "RGB_OFF") {

          Serial.println("RGB OFF");

      }

      else if (msg == "RGB_RED") {

          Serial.println("RGB RED");

      }

      else if (msg == "RGB_GREEN") {

          Serial.println("RGB GREEN");

      }

      else if (msg == "RGB_BLUE") {

          Serial.println("RGB BLUE");

      }

      break;
    }

    case WStype_BIN:
      if (length > 0) {
        setState(SPEAKING, "NOVA", "bicara...");
        ring_buffer_write(payload, length);
      }
      break;
  }
}

// OLED HELPERS
void oled_show(const char* l1, const char* l2) {
  if (!oled_ok) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 4);
  oled.println(l1);
  if (l2[0]) {
    oled.setTextSize(1);
    oled.setCursor(0, 42);
    oled.println(l2);
  }
  oled.display();
}

NovaState oled_state = BOOTING;
void setState(NovaState s, const char* l1, const char* l2) {
  novaState = s;
  if (s == THINKING) {
    last_thinking_time = millis();
  }
  if (s != oled_state) {
    oled_state = s;
    oled_show(l1, l2);
  }
}

// LED UPDATE LOGIC
void updateLEDs() {
  static int brightness = 20;
  static int direction = 5;

  switch(novaState) {
    case LISTENING:
      // Solid Green
      for(int i = 0; i < NUM_LEDS; i++) {
        strip.setPixelColor(i, strip.Color(0, 255, 0));
      }
      break;

    case THINKING:
      // Solid Yellow
      for(int i = 0; i < NUM_LEDS; i++) {
        strip.setPixelColor(i, strip.Color(255, 255, 0));
      }
      break;

    case SPEAKING:
      brightness += direction;
      if(brightness >= 200) {
          brightness = 200;
          direction = -5;
      }
      if(brightness <= 20) {
          brightness = 20;
          direction = 5;
      }
      for(int i = 0; i < NUM_LEDS; i++) {
        strip.setPixelColor(i, strip.Color(brightness / 2, 0, brightness));
      }
      break;

    case RECORDING:
      // Solid Red
      for(int i = 0; i < NUM_LEDS; i++) {
        strip.setPixelColor(i, strip.Color(255, 0, 0));
      }
      break;

    case SLEEPING:
      strip.clear();
      break;

    default:
      for(int i = 0; i < NUM_LEDS; i++) {
        strip.setPixelColor(i, strip.Color(0, 0, 255));
      }
      break;
  }

  strip.show();
}

// SETUP
void setup() {
  strip.begin();
  strip.setBrightness(40);
  
  for(int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, strip.Color(0, 0, 255));
  }
  strip.show();

  Serial.begin(115200);
  Serial.println("\n>>> Booting Nova Core...");

  pinMode(BTN_PIN, INPUT_PULLUP);

  Wire.begin(OLED_SDA, OLED_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    oled_ok = oled.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  } else {
    oled_ok = true;
  }
  Serial.printf("[i] OLED: %s\n", oled_ok ? "OK" : "TIDAK KETEMU");
  if (oled_ok) oled_show("NOVA", "booting...");

  Serial.printf("[i] PSRAM: %u KB\n", ESP.getPsramSize() / 1024);
  ptt_buf = (uint8_t*) heap_caps_malloc(PTT_MAX_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!ptt_buf) {
    Serial.println("[!] PSRAM gagal alokasi 640KB. Fallback ke RAM internal 96KB.");
    ptt_buf = (uint8_t*) malloc(32000UL * 3);
  }
  Serial.printf("[i] PTT buffer: %s\n", ptt_buf ? "OK" : "GAGAL");
  if (!ptt_buf && oled_ok) oled_show("BUF GAGAL", "cek PSRAM");

  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");

  unsigned long wifi_start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - wifi_start < 20000)) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[-] WiFi Gagal terhubung (timeout 20s). Memulai ulang...");
    if (oled_ok) oled_show("WIFI GAGAL", "restart...");
    delay(3000);
    ESP.restart();
  }

  Serial.println("\n[+] Wi-Fi Connected!");
  setState(LISTENING, "NOVA", "siap");
  setup_i2s();
  Serial.println("[+] Audio Hardware Initialized");

  ring_mutex = xSemaphoreCreateMutex();

  xTaskCreatePinnedToCore(
    speakerTask,
    "SpeakerTask",
    4096,
    NULL,
    2,
    NULL,
    1
  );

  webSocket.beginSSL(websocket_server, websocket_port, "/esp32");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);

  ArduinoOTA.setHostname("nova");
  ArduinoOTA.begin();
  Serial.printf("[+] OTA siap: nova.local (IP %s)\n", WiFi.localIP().toString().c_str());
}

// LOOP WITH SMART NOISE GATE
void loop() {
  ArduinoOTA.handle();
  updateLEDs();
  if (is_sleeping) {
    delay(100);
    return; 
  }
  webSocket.loop();

  if (nova_is_talking && (millis() - last_speaker_time > 500)) {
      nova_is_talking = false;
      if (!ptt_recording) setState(LISTENING, "NOVA", "siap");
  }

  // Timeout jika server macet/tidak merespons saat thinking
  if (novaState == THINKING && (millis() - last_thinking_time > 15000)) {
      Serial.println("[i] Thinking timeout: server tidak merespons.");
      setState(LISTENING, "TIMEOUT", "siap");
  }

  bool pressed = (digitalRead(BTN_PIN) == LOW);

  // --- mulai rekam (debounce sederhana) ---
  if (pressed && !ptt_recording && webSocket.isConnected() && ptt_buf) {
    delay(20);
    if (digitalRead(BTN_PIN) == LOW) {
      ptt_recording = true;
      ptt_len = 0;
      ptt_hold = 0;
      ptt_streaming = false;
      if (nova_is_talking) ring_buffer_clear(); // barge-in: stop suara Nova
      setState(RECORDING, "MEREKAM", "lepas u/ kirim");
      Serial.println("[i] PTT: mulai merekam...");
    }
  }

  // --- sedang rekam: tahan awal, lalu kirim mic real-time (kurangi jeda) ---
  if (ptt_recording) {
    size_t bytes_read = 0;
    i2s_read(
      I2S_NUM_0,
      audio_buffer,
      sizeof(audio_buffer),
      &bytes_read,
      portMAX_DELAY
    );
    if (bytes_read > 0) {
      ptt_len += bytes_read;
      if (!ptt_streaming) {
        if (ptt_hold + bytes_read <= PTT_MAX_BYTES) {
          memcpy(ptt_buf + ptt_hold, audio_buffer, bytes_read);
          ptt_hold += bytes_read;
        }
        if (ptt_hold >= PTT_START_BYTES) {
          for (size_t off = 0; off < ptt_hold; off += 2048) {
            size_t n = (ptt_hold - off > 2048) ? 2048 : (ptt_hold - off);
            webSocket.sendBIN(ptt_buf + off, n);
          }
          ptt_streaming = true;
          Serial.printf("[i] PTT: streaming mulai (%u byte awal).\n", ptt_hold);
        }
      } else {
        webSocket.sendBIN((uint8_t*)audio_buffer, bytes_read);
      }
    }
    bool released = (digitalRead(BTN_PIN) == HIGH);
    bool full = (ptt_len >= PTT_MAX_BYTES);
    if (released || full) {
      ptt_recording = false;
      if (ptt_streaming) {
        webSocket.sendTXT("PTT_END");  // manual VAD: tanda akhir giliran bicara
        Serial.printf("[i] PTT: selesai, %u byte.\n", ptt_len);
        setState(THINKING, "BERPIKIR", "...");
      } else {
        Serial.printf("[i] PTT: terlalu pendek (%u byte), dibuang.\n", ptt_len);
        setState(LISTENING, "NOVA", "siap");
      }
      ptt_streaming = false;
      ptt_len = 0;
      ptt_hold = 0;
    }
    return;
  }

  // idle: kuras DMA mic agar tidak penuh (non-blocking agar tidak menahan loop/OTA/WS)
  size_t bytes_read = 0;
  i2s_read(
    I2S_NUM_0,
    audio_buffer,
    sizeof(audio_buffer),
    &bytes_read,
    0
  );
}