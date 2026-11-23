#pragma once
// Copy to secrets.h (ignored by git). The ESP32-S3 only uses 2.4 GHz Wi-Fi.
constexpr unsigned short MQTT_PORT = 1883;
const char* DEVICE_ID = "loa-001";
// MQTT username is DEVICE_ID. Configure the broker with this device token.
const char* DEVICE_TOKEN = "FROM_DEVICE_PROVISIONING";

// ---- Option A: one WPA2-Personal network (home router) ----
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "YOUR_PI_LAN_IP";  // Or the Pi's mDNS name, e.g. "raspberrypi.local"

// ---- Option B: several networks, tried in order ----
// Delete Option A above and uncomment this block. The sensor moves to the next network after
// 20 s without Wi-Fi, or 60 s with Wi-Fi but no way to reach the Pi. See docs/HARDWARE.md.
// Each mqtt_host is the Pi's address as seen from that network. A ".local" name (the Pi's
// hostname + ".local") keeps working when the Pi's IP changes, e.g. on a phone hotspot.
//
// #define LOA_WIFI_NETWORKS
// const LoaNetwork NETWORKS[] = {
//   // Home router (WPA2-Personal)
//   {"HOME_WIFI", "HOME_PASSWORD", "192.168.1.50"},
//   // Phone hotspot (WPA2-Personal; on iPhone turn on "Maximize Compatibility" for 2.4 GHz)
//   {"MY_PHONE_HOTSPOT", "HOTSPOT_PASSWORD", "raspberrypi.local"},
//   // School WPA2-Enterprise (PEAP + MSCHAPv2 is the most common; some schools use WPA2_AUTH_TTLS)
//   {"SCHOOL_WIFI", "MY_SCHOOL_PASSWORD", "raspberrypi.local", WPA2_AUTH_PEAP,
//    "anonymous@school.edu",   // Outer identity: what your school's setup guide says, or nullptr
//    "student@school.edu",     // Your login
//    SCHOOL_CA_PEM},           // The school's Wi-Fi CA certificate, below (or nullptr: NOT recommended)
// };
//
// Paste the certificate your school publishes for Wi-Fi setup (often on the IT help page):
// const char SCHOOL_CA_PEM[] = R"PEM(
// -----BEGIN CERTIFICATE-----
// ...
// -----END CERTIFICATE-----
// )PEM";
// (Define SCHOOL_CA_PEM above the NETWORKS list.)
