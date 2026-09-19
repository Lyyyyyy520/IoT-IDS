/*
 * 智能摄像头模拟固件 (ESP32-CAM)
 *
 * 模拟智慧社区的智能摄像头：正常时云台缓慢扫描 + MQTT 心跳；
 * 被入侵后云台疯狂乱转 + 发 UDP 洪水（模拟被僵尸网络感染）。
 *
 * 编译前配置（见下方"配置区"）:
 *   1. WiFi（树莓派热点）
 *   2. MQTT Broker（树莓派 IP）+ DEVICE_ID
 *   3. 云台舵机引脚（ESP32-CAM 空闲 GPIO，默认 GPIO2）
 *
 * 依赖库（Arduino IDE 库管理器安装）:
 *   - PubSubClient (Nick O'Leary)
 *   - ESP32Servo (Kevin Harrington)
 *
 * 硬件:
 *   - AI-Thinker ESP32-CAM（带 OV2640）
 *   - SG90 舵机（云台，接 GPIO2）
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <WiFiUdp.h>
#include <ESP32Servo.h>
#include <esp_system.h>
#include <esp_timer.h>
#include "device_secrets.h"

// ==================== 配置区 ====================

const char* WIFI_SSID     = IOT_WIFI_SSID;
const char* WIFI_PASS     = IOT_WIFI_PASSWORD;
const char* MQTT_BROKER   = IOT_MQTT_HOST;
const int   MQTT_PORT     = IOT_MQTT_PORT;
const char* MQTT_USER     = IOT_MQTT_USERNAME;
const char* MQTT_PASSWORD = IOT_MQTT_PASSWORD;
const char* DEVICE_ID     = IOT_DEVICE_ID;
const char* FIRMWARE_VERSION = "0.3.0";
const int MQTT_HEARTBEAT_SCHEMA_VERSION = 2;
const uint16_t MQTT_BUFFER_BYTES = 768;

#define PIN_PAN      2      // 云台舵机（ESP32-CAM 空闲 GPIO2）
#define TELEMETRY_MS 5000   // 心跳间隔

// ==================== 全局对象 ====================

WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);
WiFiUDP       udp;
Servo         panServo;

bool attack_mode = false;
unsigned long lastTelemetry = 0;
unsigned long lastAttack   = 0;
unsigned long attackStarted = 0;
int  pan_angle = 90;          // 云台当前角度
char bootId[33] = {0};
uint64_t telemetrySequence = 0;

const bool  LAB_ATTACK_ENABLED = IOT_LAB_ATTACK_ENABLED;
const char* ATTACK_TARGET = IOT_LAB_ATTACK_TARGET;
const int   ATTACK_PORT   = IOT_LAB_ATTACK_PORT;
const unsigned long ATTACK_INTERVAL_MS = 100;
const unsigned long ATTACK_MAX_MS = 30000;

// ==================== MQTT ====================

String topicStatus()  { return String("community/") + DEVICE_ID + "/status"; }
String topicControl() { return String("community/") + DEVICE_ID + "/control"; }

bool isAllowedAttackTarget() {
  IPAddress target;
  if (!target.fromString(ATTACK_TARGET)) return false;
  return target[0] == 192 && target[1] == 168 && target[2] == 4
         && target[3] >= 2 && target[3] <= 254;
}

void generateBootId() {
  snprintf(bootId, sizeof(bootId), "%08lx%08lx%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
}

String buildDeviceTelemetry() {
  char buf[128];
  snprintf(buf, sizeof(buf),
           "{\"device_type\":\"camera\",\"state\":\"recording\",\"angle\":%d}",
           pan_angle);
  return String(buf);
}

String buildTelemetry() {
  telemetrySequence++;
  uint64_t uptimeMs = (uint64_t)(esp_timer_get_time() / 1000ULL);
  String ip = WiFi.localIP().toString();
  String mac = WiFi.macAddress();
  String deviceTelemetry = buildDeviceTelemetry();
  char envelope[640];
  snprintf(envelope, sizeof(envelope),
           "{\"schema_version\":%d,\"device_id\":\"%s\",\"boot_id\":\"%s\","
           "\"sequence\":%llu,\"firmware_version\":\"%s\",\"uptime_ms\":%llu,"
           "\"ip\":\"%s\",\"mac\":\"%s\",\"telemetry\":%s}",
           MQTT_HEARTBEAT_SCHEMA_VERSION, DEVICE_ID, bootId,
           (unsigned long long)telemetrySequence, FIRMWARE_VERSION,
           (unsigned long long)uptimeMs, ip.c_str(), mac.c_str(),
           deviceTelemetry.c_str());
  return String(envelope);
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

  if (String(topic) == topicControl()) {
    if (msg == "attack") {
      if (!LAB_ATTACK_ENABLED || !isAllowedAttackTarget()) {
        Serial.println("[安全] 攻击实验未启用或目标不在隔离网段，已拒绝指令");
        return;
      }
      attack_mode = true;
      attackStarted = millis();
      Serial.println("[控制] 进入攻击模式（云台乱转）");
    } else if (msg == "normal") {
      attack_mode = false;
      panServo.write(90);
      Serial.println("[控制] 恢复正常（云台回正）");
    } else if (msg == "block") {
      attack_mode = false;
      panServo.write(90);   // 云台停止
      Serial.println("[控制] 已隔离（云台停止）");
    }
  }
}

// ==================== 行为 ====================

void doNormal() {
  // 正常：云台缓慢扫描（往返）
  static unsigned long lastPan = 0;
  static int dir = 1;
  if (millis() - lastPan < 3000) return;
  lastPan = millis();
  pan_angle += 15 * dir;
  if (pan_angle >= 150) { pan_angle = 150; dir = -1; }
  if (pan_angle <= 30)  { pan_angle = 30;  dir = 1; }
  panServo.write(pan_angle);
}

void doAttack() {
  // 被入侵：云台疯狂乱转 + UDP 洪水
  unsigned long now = millis();
  if (now - attackStarted >= ATTACK_MAX_MS) {
    attack_mode = false;
    panServo.write(90);
    Serial.println("[安全] 攻击实验达到最长时限，已自动停止");
    return;
  }
  static unsigned long lastPan = 0;
  if (millis() - lastPan > 200) {
    lastPan = millis();
    pan_angle = random(0, 180);
    panServo.write(pan_angle);
  }
  if (now - lastAttack < ATTACK_INTERVAL_MS) return;
  lastAttack = now;
  udp.beginPacket(ATTACK_TARGET, ATTACK_PORT);
  udp.write((const uint8_t*)"\x00\x00\x00\x00", 4);
  udp.endPacket();
}

// ==================== WiFi / MQTT 连接 ====================

void connectWiFi() {
  Serial.printf("连接 WiFi: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500); Serial.print("."); tries++;
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("\n已连接, IP: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("\nWiFi 连接失败");
}

void connectMQTT() {
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  while (!mqtt.connected()) {
    if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD)) {
      mqtt.subscribe(topicControl().c_str());
      Serial.printf("MQTT 已连接, 订阅 %s\n", topicControl().c_str());
    } else {
      Serial.print("MQTT 连接失败, 重试...\n");
      delay(2000);
    }
  }
}

// ==================== setup / loop ====================

void setup() {
  Serial.begin(115200);
  delay(500);
  randomSeed(analogRead(0));
  generateBootId();
  mqtt.setBufferSize(MQTT_BUFFER_BYTES);

  panServo.attach(PIN_PAN);
  panServo.write(90);

  connectWiFi();
  connectMQTT();
  Serial.printf("摄像头启动: %s\n", DEVICE_ID);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  unsigned long now = millis();

  if (attack_mode) {
    doAttack();
  } else {
    doNormal();
    if (now - lastTelemetry >= TELEMETRY_MS) {
      lastTelemetry = now;
      String telemetry = buildTelemetry();
      bool published = mqtt.publish(topicStatus().c_str(), telemetry.c_str());
      Serial.printf("[心跳] boot=%s sequence=%llu result=%s\n", bootId,
                    (unsigned long long)telemetrySequence,
                    published ? "sent" : "failed");
    }
  }
}
