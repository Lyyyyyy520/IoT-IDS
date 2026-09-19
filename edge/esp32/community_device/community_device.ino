/*
 * 智慧社区 IoT 设备模拟固件 (ESP32-C3)
 *
 * 每块 ESP32 模拟一种社区 IoT 设备，通过 MQTT 遥测上报状态（正常行为）。
 * 支持"攻击模式"：收到 MQTT 指令后切换为 Mirai UDP 洪水（模拟被僵尸网络感染）。
 *
 * ┌─────────────── 编译前配置（见下方"配置区"）───────────────┐
 *   1. 选择设备类型  DEVICE_TYPE（DOOR/LIGHT/PLUG/SENSOR/SPEAKER）
 *   2. 配置 WiFi     WIFI_SSID / WIFI_PASS（树莓派热点）
 *   3. 配置 MQTT     MQTT_BROKER / DEVICE_ID
 *   4. 配置引脚      各设备对应引脚
 * └────────────────────────────────────────────────────────┘
 *
 * 依赖库（Arduino IDE 库管理器安装）:
 *   - PubSubClient (Nick O'Leary)
 *   - DHT sensor library (Adafruit)      —— 仅 SENSOR 需要
 *   - ESP32Servo (Kevin Harrington)       —— 仅 DOOR 需要
 *
 * 硬件:
 *   - 合宙/安信可 ESP32-C3 开发板（经典款，带 CH340）
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <WiFiUdp.h>
#include <esp_system.h>
#include <esp_timer.h>
#include "device_secrets.h"

// ==================== 配置区 ====================

// 设备类型（选一个）
#define DEVICE_DOOR     1   // 智能门禁（舵机锁）
#define DEVICE_LIGHT    2   // 智能路灯（光敏 + LED）
#define DEVICE_PLUG     3   // 智能插座（继电器）
#define DEVICE_SENSOR   4   // 温湿度传感器（DHT11）
#define DEVICE_SPEAKER  5   // 智能音箱（蜂鸣器发声）

#define DEVICE_TYPE DEVICE_LIGHT   // ← 改成你要烧录的设备

// WiFi、MQTT 凭据和设备 ID 来自不入库的 device_secrets.h。
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

// 引脚（按设备类型使用对应引脚）
#define PIN_SERVO   0   // 门禁舵机
#define PIN_LED     1   // 路灯 LED / 状态灯
#define PIN_RELAY   2   // 插座继电器
#define PIN_DHT     3   // 温湿度传感器
#define PIN_BUZZER  4   // 音箱蜂鸣器

// 遥测间隔（毫秒）
const unsigned long TELEMETRY_MS = 5000;

// ==================== 设备相关库（按需引入） ====================

#if DEVICE_TYPE == DEVICE_SENSOR
  #include <DHT.h>
  DHT dht(PIN_DHT, DHT11);
#endif

#if DEVICE_TYPE == DEVICE_DOOR
  #include <ESP32Servo.h>
  Servo doorServo;
#endif

// ==================== 全局对象 ====================

WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);
WiFiUDP       udp;

bool attack_mode = false;         // 是否处于攻击模式（被感染）
unsigned long lastTelemetry = 0;  // 上次遥测时间
unsigned long lastAttack   = 0;   // 上次攻击发包时间
unsigned long attackStarted = 0;  // 攻击实验开始时间
char bootId[33] = {0};
uint64_t telemetrySequence = 0;

// 攻击实验只能指向明确允许的隔离本地靶机。
const bool  LAB_ATTACK_ENABLED = IOT_LAB_ATTACK_ENABLED;
const char* ATTACK_TARGET = IOT_LAB_ATTACK_TARGET;
const int   ATTACK_PORT   = IOT_LAB_ATTACK_PORT;
const unsigned long ATTACK_INTERVAL_MS = 100;
const unsigned long ATTACK_MAX_MS = 30000;

// ==================== 遥测（按设备类型） ====================

void generateBootId() {
  snprintf(bootId, sizeof(bootId), "%08lx%08lx%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
}

String buildDeviceTelemetry() {
  char buf[160];
#if DEVICE_TYPE == DEVICE_DOOR
  // 门禁：上报门状态（locked/unlocked）
  snprintf(buf, sizeof(buf), "{\"device_type\":\"door\",\"state\":\"locked\"}");
#elif DEVICE_TYPE == DEVICE_LIGHT
  int light = analogRead(PIN_LED) / 16;  // 简化为光照占位
  snprintf(buf, sizeof(buf), "{\"device_type\":\"light\",\"level\":%d}", light);
#elif DEVICE_TYPE == DEVICE_PLUG
  snprintf(buf, sizeof(buf), "{\"device_type\":\"plug\",\"power\":220,\"on\":true}");
#elif DEVICE_TYPE == DEVICE_SENSOR
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t)) t = 25.0;  // 读取失败用占位
  if (isnan(h)) h = 50.0;
  snprintf(buf, sizeof(buf), "{\"device_type\":\"sensor\",\"temp\":%.1f,\"humidity\":%.1f}", t, h);
#elif DEVICE_TYPE == DEVICE_SPEAKER
  snprintf(buf, sizeof(buf), "{\"device_type\":\"speaker\",\"state\":\"idle\"}");
#endif
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

String topicStatus()  { return String("community/") + DEVICE_ID + "/status"; }
String topicControl() { return String("community/") + DEVICE_ID + "/control"; }

bool isAllowedAttackTarget() {
  IPAddress target;
  if (!target.fromString(ATTACK_TARGET)) return false;
  return target[0] == 192 && target[1] == 168 && target[2] == 4
         && target[3] >= 2 && target[3] <= 254;
}

// ==================== 执行器动作（按设备类型） ====================

void actuatorNormal() {
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(90);        // 门锁关闭
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, HIGH); // 灯亮
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, LOW); // 继电器吸合（通电）
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, LOW); // 静音
#endif
}

void actuatorAttack() {
  // 被入侵后的"专属反应"（震撼演示）
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(0);         // 门锁"自己打开"
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, !digitalRead(PIN_LED)); // 灯疯狂闪烁
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, !digitalRead(PIN_RELAY)); // 继电器反复通断
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, HIGH); // 发出怪声
#endif
}

void actuatorBlocked() {
  // 被隔离后的反应（物理阻断）
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(90);        // 锁死
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, LOW); // 熄灭
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, HIGH); // 断电
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, LOW); // 静音
#endif
}

// ==================== MQTT 回调 ====================

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
      Serial.println("[控制] 进入攻击模式（模拟被感染）");
    } else if (msg == "normal") {
      attack_mode = false;
      actuatorNormal();
      Serial.println("[控制] 恢复正常模式");
    } else if (msg == "block") {
      attack_mode = false;
      actuatorBlocked();
      Serial.println("[控制] 已隔离（物理阻断）");
    }
  }
}

// ==================== 攻击模式（Mirai UDP 洪水） ====================

void doAttack() {
  // udpplain：向目标高频发送 UDP 包
  unsigned long now = millis();
  if (now - attackStarted >= ATTACK_MAX_MS) {
    attack_mode = false;
    actuatorNormal();
    Serial.println("[安全] 攻击实验达到最长时限，已自动停止");
    return;
  }
  if (now - lastAttack < ATTACK_INTERVAL_MS) return;  // 硬限速
  lastAttack = now;

  udp.beginPacket(ATTACK_TARGET, ATTACK_PORT);
  udp.write((const uint8_t*)"\x00\x00\x00\x00", 4);
  udp.endPacket();
  actuatorAttack();  // 触发设备专属"被入侵反应"
}

// ==================== WiFi / MQTT 连接 ====================

void connectWiFi() {
  Serial.printf("连接 WiFi: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(200);

  // 关键修复：ESP32-C3 克隆板默认发射功率过高会导致天线失真、能扫描但连不上，
  // 必须降低发射功率。逐档尝试，直到连上为止。
  wifi_power_t powers[] = {WIFI_POWER_8_5dBm, WIFI_POWER_15dBm, WIFI_POWER_11dBm, WIFI_POWER_5dBm};
  const char* names[]   = {"8.5dBm", "15dBm", "11dBm", "5dBm"};
  int n = sizeof(powers) / sizeof(powers[0]);

  for (int i = 0; i < n; i++) {
    WiFi.setTxPower(powers[i]);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries < 20) {  // 每档等 10 秒
      delay(500);
      Serial.print(".");
      tries++;
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("\n已连接 (功率 %s), IP: %s\n", names[i], WiFi.localIP().toString().c_str());
      return;
    }
    Serial.printf("\n功率 %s 失败，换下一档...\n", names[i]);
    WiFi.disconnect();
    delay(300);
  }
  Serial.println("\nWiFi 连接失败（所有功率档都试过）");
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
  generateBootId();
  mqtt.setBufferSize(MQTT_BUFFER_BYTES);

  // 初始化引脚
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.attach(PIN_SERVO);
  doorServo.write(90);
#elif DEVICE_TYPE == DEVICE_LIGHT
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, HIGH);
#elif DEVICE_TYPE == DEVICE_PLUG
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, LOW);
#elif DEVICE_TYPE == DEVICE_SENSOR
  dht.begin();
#elif DEVICE_TYPE == DEVICE_SPEAKER
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
#endif

  connectWiFi();
  connectMQTT();
  Serial.printf("设备启动: %s (类型 %d)\n", DEVICE_ID, DEVICE_TYPE);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqtt.connected()) {
    connectMQTT();
  }
  mqtt.loop();

  unsigned long now = millis();

  // 攻击模式：持续发 UDP 洪水
  if (attack_mode) {
    doAttack();
  }
  // 正常模式：周期遥测
  else if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;
    String telemetry = buildTelemetry();
    bool published = mqtt.publish(topicStatus().c_str(), telemetry.c_str());
    Serial.printf("[遥测] boot=%s sequence=%llu result=%s\n", bootId,
                  (unsigned long long)telemetrySequence,
                  published ? "sent" : "failed");
  }
}
