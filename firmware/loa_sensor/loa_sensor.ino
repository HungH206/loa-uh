#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <HTTPClient.h>
#include <sys/time.h>
#include <time.h>
#include <ESP_I2S.h>
#include <esp_system.h>
#include <math.h>

// One Wi-Fi network the sensor may use. secrets.h either defines WIFI_SSID / WIFI_PASSWORD /
// MQTT_HOST (one WPA2-Personal network), or a NETWORKS[] list of these, tried in order.
struct LoaNetwork {
  const char* ssid;
  const char* password;                // Wi-Fi password (Personal/hotspot) or your account password (Enterprise)
  const char* mqtt_host;               // The Pi's address on this network: IP, or a name such as "raspberrypi.local"
  int eap_method = -1;                 // -1 = WPA2-Personal. WPA2_AUTH_PEAP or WPA2_AUTH_TTLS = WPA2-Enterprise
  const char* eap_identity = nullptr;  // Enterprise outer identity, e.g. "anonymous@school.edu" (defaults to eap_username)
  const char* eap_username = nullptr;  // Enterprise login, e.g. "student@school.edu"
  const char* ca_pem = nullptr;        // Enterprise: the school's RADIUS CA certificate (PEM). Strongly recommended.
};

#include "secrets.h"  // DEVICE_ID, DEVICE_TOKEN, MQTT_PORT, and the Wi-Fi network(s)

#ifndef LOA_WIFI_NETWORKS
const LoaNetwork NETWORKS[] = {{WIFI_SSID, WIFI_PASSWORD, MQTT_HOST}};
#endif
constexpr size_t NETWORK_COUNT = sizeof(NETWORKS) / sizeof(NETWORKS[0]);

// ======================================
// LOA — Wearable Sound Monitoring Firmware
// XIAO ESP32-S3 Sense with its PDM microphone. ESP32 Arduino core 3.x.
// Relative digital level only (dBFS): no A-weighting or SPL calibration.
// ======================================

// --- Audio configuration ---
constexpr int MIC_CLK = 42;
constexpr int MIC_DATA = 41;
constexpr int SAMPLE_RATE = 16000;

// --- Networking experiment settings ---
// Override with build flags, for example
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
// Each reading is the RMS of every sample in its window: 16,000 samples (1 s) at 1 Hz.
constexpr int WINDOW_SAMPLES = SAMPLE_RATE / LOA_PUBLISH_HZ;
constexpr unsigned long RETRY_INTERVAL_MS = 5000;       // MQTT reconnect attempts.
// Joining a phone hotspot (handshake + DHCP) can take several seconds; restarting the attempt
// sooner would cut it off every time.
constexpr unsigned long WIFI_RETRY_MS = 10000;
// With more than one network: move to the next one after this long without Wi-Fi...
constexpr unsigned long NETWORK_SWITCH_MS = 20000;
// ...or after this long with Wi-Fi but no way to reach the Pi (e.g. campus Wi-Fi that isolates devices).
constexpr unsigned long UNREACHABLE_SWITCH_MS = 60000;

// --- On-device alert (works with no network) ---
// The dashboard applies each user's own tolerance, duration and cooldown on the server.
// This fixed local rule is a fallback that also works offline: it lights the onboard LED
// and is reported as "alert" in each reading. Relative dBFS, NOT dBA.
#ifndef LOA_LOCAL_ALERT_DBFS
#define LOA_LOCAL_ALERT_DBFS -40.0f
#endif
#ifndef LOA_LOCAL_ALERT_SECONDS
#define LOA_LOCAL_ALERT_SECONDS 3  // Loud for this long (consecutive readings) before alerting.
#endif
#ifndef LOA_LED_ACTIVE_LOW
#define LOA_LED_ACTIVE_LOW 1       // XIAO ESP32-S3 user LED: LOW = on. Set 0 if the LED looks inverted.
#endif
constexpr int ALERT_CONFIRM_COUNT = LOA_LOCAL_ALERT_SECONDS * LOA_PUBLISH_HZ;

// --- MQTT topic ---
// Must be loa/<DEVICE_ID>/noise: the broker ACL lets each device publish only there,
// and the backend rejects readings whose topic and device_id differ.
char topic[96];

// --- Hardware and network objects ---
I2SClass microphone;
WiFiClient network;
PubSubClient mqtt(network);
#if LOA_TRANSPORT_HTTP
WiFiClient httpNetwork;
HTTPClient http;
char authorization[160];
#endif

// --- System state ---
char bootId[17];                 // Random per boot: lets the backend separate restarts and drop duplicates.
uint32_t sequenceNumber = 0;     // Keeps counting while offline, so gaps show lost readings.
unsigned long lastWiFiAttempt = 0, lastMQTTAttempt = 0;
size_t activeNetwork = 0;
unsigned long networkStartedAt = 0;  // When the current network was chosen or last dropped.
unsigned long lastReachedPi = 0;     // Last MQTT connection/publish or HTTP 201.
volatile uint8_t lastWifiReason = 0; // Why the last Wi-Fi attempt failed (set by the Wi-Fi event below).
// Link timing for Experiment 3, printed as "LINK ..." serial lines.
unsigned long wifiDownSince = 0, mqttDownSince = 0;
bool wifiWasUp = false, mqttWasUp = false;
// Running sums for the current window.
double sampleSum = 0, sampleSquares = 0;
size_t samplesInWindow = 0;
float currentRMS = 0.0f, currentDBFS = -120.0f;
int loudCount = 0;
bool noiseAlert = false;

// Payload and status lines are printed only at 1 Hz; at 5–10 Hz printing slows the loop.
constexpr bool VERBOSE = LOA_PUBLISH_HZ == 1;

// ======================================
// Onboard LED (local alert indicator)
// ======================================
void setAlertLed(bool on) {
#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, (on != (LOA_LED_ACTIVE_LOW != 0)) ? HIGH : LOW);
#endif
}

// ======================================
// Wi-Fi and MQTT connection
// ======================================
void onWiFiDisconnected(WiFiEvent_t event, WiFiEventInfo_t info) {
  lastWifiReason = info.wifi_sta_disconnected.reason;
}

const char* wifiReason() {
  // Common values: NO_AP_FOUND (name not in range / wrong band), AUTH_FAIL or
  // 4WAY_HANDSHAKE_TIMEOUT (usually a wrong password), ASSOC_LEAVE (we disconnected).
  return lastWifiReason ? WiFi.disconnectReasonName((wifi_err_reason_t)lastWifiReason) : "none yet";
}

void beginNetwork(size_t index) {
  const LoaNetwork& net = NETWORKS[index];
  activeNetwork = index;
  networkStartedAt = lastWiFiAttempt = lastReachedPi = millis();
  lastWifiReason = 0;
  mqtt.disconnect();
  mqtt.setServer(net.mqtt_host, MQTT_PORT);  // A ".local" name is resolved with mDNS.
  if (net.eap_method >= 0) {
#if CONFIG_ESP_WIFI_ENTERPRISE_SUPPORT
    const char* identity = net.eap_identity ? net.eap_identity : net.eap_username;
    WiFi.begin(net.ssid, (wpa2_auth_method_t)net.eap_method, identity, net.eap_username, net.password, net.ca_pem);
#else
    Serial.println("ERROR: this ESP32 build has no WPA2-Enterprise support");
#endif
  } else {
    WiFi.begin(net.ssid, net.password);
  }
  Serial.printf("LOA: Connecting to Wi-Fi \"%s\" (%s, %u of %u), Pi at %s...\n", net.ssid,
    net.eap_method >= 0 ? "WPA2-Enterprise" : "WPA2-Personal", (unsigned)index + 1, (unsigned)NETWORK_COUNT, net.mqtt_host);
  if (net.eap_method >= 0 && !net.ca_pem) Serial.println("LOA: Warning: no CA certificate, so the school's Wi-Fi server is not verified");
}

void switchNetwork(const char* reason) {
  const size_t next = (activeNetwork + 1) % NETWORK_COUNT;
  Serial.printf("LINK wifi_switch reason=%s wifi_error=%s from=\"%s\" to=\"%s\"\n", reason, wifiReason(), NETWORKS[activeNetwork].ssid, NETWORKS[next].ssid);
  WiFi.disconnect();
  beginNetwork(next);
}

void startWiFi() {
  WiFi.onEvent(onWiFiDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(DEVICE_ID);  // Shows as e.g. "loa-001" in router / hotspot device lists.
  WiFi.setAutoReconnect(true);
  // List the networks in range: shows at once whether a configured network is visible,
  // on which channel and how strong (a missing antenna shows up as no or very weak networks).
  const int found = WiFi.scanNetworks();
  Serial.printf("LOA: Wi-Fi scan found %d network(s)\n", found < 0 ? 0 : found);
  for (int i = 0; i < found && i < 12; ++i) {
    bool configured = false;
    for (size_t n = 0; n < NETWORK_COUNT; ++n) configured |= WiFi.SSID(i) == NETWORKS[n].ssid;
    Serial.printf("  %s \"%s\" ch %d rssi %d dBm\n", configured ? "*" : " ", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
  }
  WiFi.scanDelete();
  // Network time for one-way latency; set once Wi-Fi has internet access.
  configTime(0, 0, LOA_NTP_SERVER);
  beginNetwork(0);
}

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
      networkStartedAt = now;  // Give the current network NETWORK_SWITCH_MS to come back.
      Serial.printf("LINK wifi_down uptime_ms=%lu\n", now);
    }
    if (mqttWasUp) {
      mqttWasUp = false;
      mqttDownSince = now;
    }
    if (NETWORK_COUNT > 1 && now - networkStartedAt >= NETWORK_SWITCH_MS) {
      switchNetwork("no_wifi");
    } else if (now - lastWiFiAttempt >= WIFI_RETRY_MS) {
      lastWiFiAttempt = now;
      Serial.printf("LOA: Retrying Wi-Fi \"%s\" (last error: %s)...\n", NETWORKS[activeNetwork].ssid, wifiReason());
      WiFi.reconnect();
    }
    return;
  }
  if (!wifiWasUp) {
    wifiWasUp = true;
    lastReachedPi = now;  // Start the "can we reach the Pi" timer from Wi-Fi up.
    Serial.printf("LINK wifi_up uptime_ms=%lu outage_ms=%lu rssi=%d ssid=\"%s\" ip=%s\n", now, wifiDownSince ? now - wifiDownSince : 0,
      WiFi.RSSI(), NETWORKS[activeNetwork].ssid, WiFi.localIP().toString().c_str());
  }
  if (NETWORK_COUNT > 1 && now - lastReachedPi >= UNREACHABLE_SWITCH_MS) {
    switchNetwork("pi_unreachable");
    return;
  }
#if LOA_TRANSPORT_HTTP
  return;  // HTTP mode opens connections per request; no MQTT session to keep.
#endif
  if (mqtt.connected()) {
    mqtt.loop();
    lastReachedPi = now;
    return;
  }
  if (mqttWasUp) {
    mqttWasUp = false;
    mqttDownSince = now;
    Serial.printf("LINK mqtt_down uptime_ms=%lu state=%d\n", now, mqtt.state());
  }
  if (now - lastMQTTAttempt >= RETRY_INTERVAL_MS) {
    lastMQTTAttempt = now;
    Serial.println("LOA: Connecting to MQTT...");
    // MQTT username is the device ID and the password is its device token.
    // This connection attempt may briefly interrupt sampling; there is no replay buffer.
    if (mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_TOKEN)) {
      mqttWasUp = true;
      lastReachedPi = millis();
      Serial.println("LOA: MQTT connected!");
      Serial.printf("LINK mqtt_up uptime_ms=%lu outage_ms=%lu\n", millis(), mqttDownSince ? millis() - mqttDownSince : 0);
    } else {
      Serial.printf("LOA: MQTT failed, state=%d\n", mqtt.state());
    }
  }
}

// ======================================
// Microphone initialization
// ======================================
void setupMicrophone() {
  microphone.setPinsPdmRx(MIC_CLK, MIC_DATA);
  if (!microphone.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("ERROR: Microphone failed!");
    while (true) delay(1000);
  }
  Serial.println("LOA: Microphone ready!");
}

// ======================================
// Read microphone samples into the current window.
// Returns true when the window is complete and currentRMS/currentDBFS are updated.
// ======================================
bool measureSound() {
  // Read whole 16-bit PCM samples. Stream::read() returns single bytes and must not be used here.
  int16_t samples[256];
  const size_t remaining = WINDOW_SAMPLES - samplesInWindow;
  const size_t requested = remaining < 256 ? remaining : 256;
  const size_t bytesRead = microphone.readBytes((char*)samples, requested * sizeof(int16_t));
  const size_t count = bytesRead / sizeof(int16_t);
  for (size_t i = 0; i < count; ++i) {
    const double sample = samples[i];
    sampleSum += sample;
    sampleSquares += sample * sample;
  }
  samplesInWindow += count;
  if (samplesInWindow < WINDOW_SAMPLES) return false;

  // Remove the average DC offset, then convert RMS to a relative digital level.
  const double mean = sampleSum / samplesInWindow;
  const double variance = fmax(0.0, sampleSquares / samplesInWindow - mean * mean);
  currentRMS = sqrt(variance);
  currentDBFS = 20.0f * log10f(fmaxf(currentRMS, 0.032768f) / 32768.0f);  // Floor: -120 dBFS.
  sampleSum = sampleSquares = 0;
  samplesInWindow = 0;
  return true;
}

// ======================================
// On-device noise alert
// ======================================
void updateNoiseAlert() {
  const bool wasAlert = noiseAlert;
  if (currentDBFS >= LOA_LOCAL_ALERT_DBFS) {
    if (loudCount < ALERT_CONFIRM_COUNT) loudCount++;
    if (loudCount >= ALERT_CONFIRM_COUNT) noiseAlert = true;
  } else {
    loudCount = 0;
    noiseAlert = false;
  }
  if (noiseAlert != wasAlert) {
    setAlertLed(noiseAlert);
    Serial.println(noiseAlert ? "LOA ALERT: Noise threshold exceeded!" : "LOA: Noise back below threshold");
  }
}

// ======================================
// Send one reading over MQTT or HTTP
// ======================================
bool transmit(const char* payload) {
#if LOA_TRANSPORT_HTTP
  if (WiFi.status() != WL_CONNECTED) return false;
  // setReuse keeps the TCP connection open between requests (HTTP/1.1 keep-alive).
  http.begin(httpNetwork, NETWORKS[activeNetwork].mqtt_host, LOA_HTTP_PORT, "/api/readings");
  http.setReuse(true);
  http.setTimeout(1000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", authorization);
  const int status = http.POST((uint8_t*)payload, strlen(payload));
  http.end();
  if (status != 201) Serial.printf("HTTP: status %d\n", status);
  else lastReachedPi = millis();
  return status == 201;  // 201 is an application-level acknowledgment.
#else
  // PubSubClient publishes at QoS 0. Its return value is not a broker ACK.
  return mqtt.connected() && mqtt.publish(topic, payload, false);
#endif
}

void publishReading() {
  char payload[320];
  int length = snprintf(payload, sizeof(payload),
    "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"sequence\":%lu,\"dbfs\":%.2f,\"rms\":%.2f,\"alert\":%s,\"uptime_ms\":%lu",
    DEVICE_ID, bootId, (unsigned long)sequenceNumber++, currentDBFS, currentRMS,
    noiseAlert ? "true" : "false", (unsigned long)millis());
  // Send time from NTP lets the backend compute one-way latency (received_at - timestamp).
  struct timeval tv;
  if (clockSynced(tv)) length += snprintf(payload + length, sizeof(payload) - length, ",\"timestamp\":%lu.%03lu", (unsigned long)tv.tv_sec, (unsigned long)(tv.tv_usec / 1000));
  snprintf(payload + length, sizeof(payload) - length, "}");

  if (VERBOSE) {
    Serial.print("LOA Reading: ");
    Serial.println(payload);
  }
  const bool sent = transmit(payload);
  if (VERBOSE) {
    if (sent) Serial.println(LOA_TRANSPORT_HTTP ? "HTTP: Delivered" : "MQTT: Published");
    else Serial.println("Offline - local sensing active");
  }
}

// ======================================
// Arduino setup
// ======================================
void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  // USB logging must never stall monitoring when no terminal is reading it.
  Serial.setTxTimeoutMs(0);
#endif
  delay(1200);
  Serial.println();
  Serial.println("======================");
  Serial.println("LOA Wearable Starting");
  Serial.println("======================");

#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
  setAlertLed(true);   // Short blink at boot shows the LED polarity is right.
  delay(300);
  setAlertLed(false);
#endif

  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  snprintf(topic, sizeof(topic), "loa/%s/noise", DEVICE_ID);
  setupMicrophone();

  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(15);
  mqtt.setSocketTimeout(1);
#if LOA_TRANSPORT_HTTP
  snprintf(authorization, sizeof(authorization), "Bearer %s", DEVICE_TOKEN);
#endif
  startWiFi();  // Also points MQTT at the first network's Pi address.
  lastMQTTAttempt = millis() - RETRY_INTERVAL_MS;  // First MQTT attempt as soon as Wi-Fi is up.

  Serial.printf("LOA %s boot %s; %d Hz over %s; topic %s; local alert %.0f dBFS for %d s\n",
    DEVICE_ID, bootId, LOA_PUBLISH_HZ, LOA_TRANSPORT_HTTP ? "HTTP" : "MQTT", topic,
    (double)LOA_LOCAL_ALERT_DBFS, LOA_LOCAL_ALERT_SECONDS);
  Serial.println("LOA: Setup complete");
}

// ======================================
// Main loop
// ======================================
void loop() {
  // Sound is measured continuously, independent of the network.
  if (measureSound()) {
    updateNoiseAlert();
    publishReading();
  }
  maintainNetwork();
}
