#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <HTTPClient.h>
#include <sys/time.h>
#include <time.h>
#include <ESP_I2S.h>
#include <esp_system.h>
#include <math.h>
#include "secrets.h"

// XIAO ESP32-S3 Sense with its PDM microphone. ESP32 Arduino core 3.x.
// Relative digital level only: no A-weighting or SPL calibration.
constexpr int MIC_CLK = 42;
constexpr int MIC_DATA = 41;
constexpr int SAMPLE_RATE = 16000;

// Networking experiment settings. Override with build flags, for example
// arduino-cli compile --build-property "build.extra_flags=-DLOA_PUBLISH_HZ=10 -DLOA_TRANSPORT_HTTP=1" ...
#ifndef LOA_PUBLISH_HZ
#define LOA_PUBLISH_HZ 1          // Readings per second: 1, 5 or 10 for Experiment 2.
#endif
#ifndef LOA_TRANSPORT_HTTP
#define LOA_TRANSPORT_HTTP 0      // 0 = MQTT publish, 1 = HTTP POST /api/readings (Experiment 1).
#endif
#ifndef LOA_HTTP_PORT
#define LOA_HTTP_PORT 8000        // FastAPI port on the same host as MQTT_HOST.
#endif
#ifndef LOA_NTP_SERVER
#define LOA_NTP_SERVER "pool.ntp.org"  // Use the Pi's address if it serves NTP on the LAN.
#endif
static_assert(LOA_PUBLISH_HZ >= 1 && LOA_PUBLISH_HZ <= 20 && SAMPLE_RATE % LOA_PUBLISH_HZ == 0,
              "LOA_PUBLISH_HZ must divide 16000 and be between 1 and 20");
constexpr int WINDOW_SAMPLES = SAMPLE_RATE / LOA_PUBLISH_HZ;
constexpr unsigned long RETRY_MS = 5000;
I2SClass microphone;
WiFiClient network;
PubSubClient mqtt(network);
#if LOA_TRANSPORT_HTTP
WiFiClient httpNetwork;
HTTPClient http;
char authorization[160];
#endif
char topic[96];
char bootId[17];
uint32_t sequenceNumber = 0;
unsigned long lastWiFiAttempt = 0, lastMQTTAttempt = 0;
// Link timing for Experiment 3, printed as "LINK ..." serial lines.
unsigned long wifiDownSince = 0, mqttDownSince = 0;
bool wifiWasUp = false, mqttWasUp = false;
double sampleSum = 0, sampleSquares = 0;
size_t samplesInWindow = 0;

bool clockSynced(struct timeval& tv) {
  gettimeofday(&tv, nullptr);
  return tv.tv_sec > 1700000000;  // NTP has set the clock (after Nov 2023).
}

void maintainNetwork() {
  const unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiWasUp) {
      wifiWasUp = false;
      wifiDownSince = now;
      if (Serial) Serial.printf("LINK wifi_down uptime_ms=%lu\n", now);
    }
    if (mqttWasUp) {
      mqttWasUp = false;
      mqttDownSince = now;
    }
    if (now-lastWiFiAttempt >= RETRY_MS) {
      lastWiFiAttempt = now;
      WiFi.reconnect();
    }
    return;
  }
  if (!wifiWasUp) {
    wifiWasUp = true;
    if (Serial) Serial.printf("LINK wifi_up uptime_ms=%lu outage_ms=%lu rssi=%d\n", now, wifiDownSince ? now-wifiDownSince : 0, WiFi.RSSI());
  }
#if LOA_TRANSPORT_HTTP
  return;  // HTTP mode opens connections per request; no MQTT session to keep.
#endif
  if (mqtt.connected()) {mqtt.loop(); return;}
  if (mqttWasUp) {
    mqttWasUp = false;
    mqttDownSince = now;
    if (Serial) Serial.printf("LINK mqtt_down uptime_ms=%lu state=%d\n", now, mqtt.state());
  }
  if (now-lastMQTTAttempt >= RETRY_MS) {
    lastMQTTAttempt = now;
    // This connection attempt may briefly interrupt sampling; no replay buffer.
    const bool connected = mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_TOKEN);
    if (Serial) Serial.printf("MQTT %s (state %d)\n", connected ? "connected" : "reconnect failed", mqtt.state());
    if (connected) {
      mqttWasUp = true;
      if (Serial) Serial.printf("LINK mqtt_up uptime_ms=%lu outage_ms=%lu\n", millis(), mqttDownSince ? millis()-mqttDownSince : 0);
    }
  }
}

bool transmit(const char* payload) {
#if LOA_TRANSPORT_HTTP
  if (WiFi.status() != WL_CONNECTED) return false;
  // setReuse keeps the TCP connection open between requests (HTTP/1.1 keep-alive).
  http.begin(httpNetwork, MQTT_HOST, LOA_HTTP_PORT, "/api/readings");
  http.setReuse(true);
  http.setTimeout(1000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", authorization);
  const int status = http.POST((uint8_t*)payload, strlen(payload));
  http.end();
  if (status != 201 && Serial) Serial.printf("HTTP status %d\n", status);
  return status == 201;  // 201 is an application-level acknowledgment.
#else
  // PubSubClient publishes at QoS 0. Its return value is not a broker ACK.
  return mqtt.connected() && mqtt.publish(topic, payload, false);
#endif
}

void publishWindow() {
  const double mean = sampleSum / samplesInWindow;
  const double variance = fmax(0.0, sampleSquares / samplesInWindow - mean * mean);
  const float rms = sqrt(variance);
  const float dbfs = 20.0f * log10f(fmaxf(rms, 0.032768f) / 32768.0f);
  char payload[320];
  int length = snprintf(payload, sizeof(payload),
    "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"sequence\":%lu,\"dbfs\":%.2f,\"rms\":%.2f,\"uptime_ms\":%lu",
    DEVICE_ID, bootId, (unsigned long)sequenceNumber++, dbfs, rms, (unsigned long)millis());
  // Send time from NTP lets the backend compute one-way latency (received_at - timestamp).
  struct timeval tv;
  if (clockSynced(tv)) length += snprintf(payload+length, sizeof(payload)-length, ",\"timestamp\":%lu.%03lu", (unsigned long)tv.tv_sec, (unsigned long)(tv.tv_usec/1000));
  snprintf(payload+length, sizeof(payload)-length, "}");
  // At 10 Hz, printing every payload slows the loop; print only at 1 Hz.
  if (Serial && LOA_PUBLISH_HZ == 1) Serial.println(payload);
  if (!transmit(payload) && Serial) Serial.println("Not transmitted; local sensing continues");
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
  configTime(0, 0, LOA_NTP_SERVER);
#if LOA_TRANSPORT_HTTP
  snprintf(authorization, sizeof(authorization), "Bearer %s", DEVICE_TOKEN);
#endif
  lastMQTTAttempt = millis()-RETRY_MS;
  Serial.printf("LOA %s boot %s; %d Hz over %s; relative dBFS; backend handles personal alerts\n",
    DEVICE_ID, bootId, LOA_PUBLISH_HZ, LOA_TRANSPORT_HTTP ? "HTTP" : "MQTT");
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
