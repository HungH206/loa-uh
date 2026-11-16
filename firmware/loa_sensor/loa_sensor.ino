#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP_I2S.h>
#include <esp_system.h>
#include <math.h>
#include "secrets.h"

// XIAO ESP32-S3 Sense with its PDM microphone. ESP32 Arduino core 3.x.
// Relative digital level only: no A-weighting or SPL calibration.
constexpr int MIC_CLK = 42;
constexpr int MIC_DATA = 41;
constexpr int SAMPLE_RATE = 16000;
constexpr int WINDOW_SAMPLES = SAMPLE_RATE;
constexpr unsigned long RETRY_MS = 5000;
I2SClass microphone;
WiFiClient network;
PubSubClient mqtt(network);
char topic[96];
char bootId[17];
uint32_t sequenceNumber = 0;
unsigned long lastWiFiAttempt = 0, lastMQTTAttempt = 0;
double sampleSum = 0, sampleSquares = 0;
size_t samplesInWindow = 0;

void maintainNetwork() {
  const unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    if (now-lastWiFiAttempt >= RETRY_MS) {
      lastWiFiAttempt = now;
      WiFi.reconnect();
    }
    return;
  }
  if (mqtt.connected()) {mqtt.loop(); return;}
  if (now-lastMQTTAttempt >= RETRY_MS) {
    lastMQTTAttempt = now;
    // This connection attempt may briefly interrupt sampling; no replay buffer.
    const bool connected = mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_TOKEN);
    if (Serial) Serial.printf("MQTT %s (state %d)\n", connected ? "connected" : "reconnect failed", mqtt.state());
  }
}

void publishWindow() {
  const double mean = sampleSum / samplesInWindow;
  const double variance = fmax(0.0, sampleSquares / samplesInWindow - mean * mean);
  const float rms = sqrt(variance);
  const float dbfs = 20.0f * log10f(fmaxf(rms, 0.032768f) / 32768.0f);
  char payload[320];
  snprintf(payload, sizeof(payload),
    "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"sequence\":%lu,\"dbfs\":%.2f,\"rms\":%.2f,\"uptime_ms\":%lu}",
    DEVICE_ID, bootId, (unsigned long)sequenceNumber++, dbfs, rms, (unsigned long)millis());
  if (Serial) Serial.println(payload);
  // PubSubClient publishes at QoS 0. Its return value is not a broker ACK.
  if ((!mqtt.connected() || !mqtt.publish(topic, payload, false)) && Serial) Serial.println("Not transmitted; local sensing continues");
  sampleSum = sampleSquares = 0;
  samplesInWindow = 0;
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  // USB logging must never stall monitoring when no terminal is consuming it.
  Serial.setTxTimeoutMs(0);
#endif
  delay(1200);
  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  snprintf(topic, sizeof(topic), "loa/%s/noise", DEVICE_ID);
  microphone.setPinsPdmRx(MIC_CLK, MIC_DATA);
  if (!microphone.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("Microphone initialization failed");
    while (true) delay(1000);
  }
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(15);
  mqtt.setSocketTimeout(1);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastMQTTAttempt = millis()-RETRY_MS;
  Serial.printf("LOA %s boot %s; relative dBFS; backend handles personal alerts\n", DEVICE_ID, bootId);
}

void loop() {
  // Read whole 16-bit PCM samples. Stream::read() must not be used as a byte sampler.
  int16_t samples[256];
  const size_t remaining = WINDOW_SAMPLES-samplesInWindow;
  const size_t requested = remaining < 256 ? remaining : 256;
  const size_t bytesRead = microphone.readBytes((char*)samples, requested*sizeof(int16_t));
  const size_t count = bytesRead/sizeof(int16_t);
  for (size_t i=0; i<count; ++i) {
    const double sample = samples[i];
    sampleSum += sample;
    sampleSquares += sample*sample;
  }
  samplesInWindow += count;
  if (samplesInWindow >= WINDOW_SAMPLES) publishWindow();
  maintainNetwork();
}
