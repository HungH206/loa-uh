#pragma once
// Copy to secrets.h (ignored by git). Use 2.4 GHz Wi-Fi.
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "YOUR_LAPTOP_LAN_IP";
constexpr unsigned short MQTT_PORT = 1883;
const char* DEVICE_ID = "loa-001";
// MQTT username is DEVICE_ID. Configure the broker with this device token.
const char* DEVICE_TOKEN = "FROM_DEVICE_PROVISIONING";
