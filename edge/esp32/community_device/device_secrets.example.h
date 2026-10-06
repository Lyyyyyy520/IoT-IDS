#pragma once

// Copy this file to device_secrets.h before compiling.  Never commit the copy.
#define IOT_WIFI_SSID "iot-community"
#define IOT_WIFI_PASSWORD "CHANGE_ME_WIFI_PASSWORD"

// The MQTT username must equal the device id so the broker ACL can constrain
// this firmware to community/<device_id>/status and /control.
#define IOT_DEVICE_ID "light-01"
#define IOT_MQTT_HOST "192.168.4.1"
#define IOT_MQTT_PORT 1883
#define IOT_MQTT_USERNAME IOT_DEVICE_ID
#define IOT_MQTT_PASSWORD "CHANGE_ME_UNIQUE_DEVICE_PASSWORD"

// Attack simulation is disabled by default.  Enable only inside the isolated
// lab after the gateway egress rule has been verified.
#define IOT_LAB_ATTACK_ENABLED false
#define IOT_LAB_ATTACK_TARGET "192.168.4.200"
#define IOT_LAB_ATTACK_PORT 9000
