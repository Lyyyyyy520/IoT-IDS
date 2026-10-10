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

#define DEVICE_TYPE   DEVICE_LIGHT   // ← 改成你要烧录的设备

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

// ==================== 纯固件联动仿真（LINK_SIM_MODE） ====================
// 0（默认）= 现网行为：30s 无条件发裸 'evt'，监听端不校验来源（与旧版逐字一致）
// 1 = 现场演示：事件类载荷 evt:<class>:<role> + 发送方白名单校验 + 角色托管
//     （6 板模拟 docs/12 全部联动关系类别）+ 事件驱动触发 + 演示兜底补发。
//     改动清单见 dev-logs/2026-10-08「固件 LINK_SIM_MODE」章节。
#define LINK_SIM_MODE   1   // ← 现场演示烧 1
// 演示兜底：真实事件（DHT 阈值/开门按键）久未触发时，周期补发模拟事件保证演示必现
#define LINK_DEMO_FORCE 1

#if LINK_SIM_MODE
// 发送方白名单（监听端校验，终结"任意 UDP 包万能触发"）：
// 只接受托管对应角色的板子发来的联动事件。
#if DEVICE_TYPE == DEVICE_DOOR
  const char* COAP_ALLOW_FROM = "192.168.4.14";          // sensor-01（托管烟雾）
#elif DEVICE_TYPE == DEVICE_LIGHT
  const char* COAP_ALLOW_FROM = "192.168.4.14,192.168.4.15"; // sensor(烟雾) + speaker(门磁)
#elif DEVICE_TYPE == DEVICE_PLUG
  const char* COAP_ALLOW_FROM = "192.168.4.14";          // sensor-01（温湿度）
#elif DEVICE_TYPE == DEVICE_SPEAKER
  const char* COAP_ALLOW_FROM = "192.168.4.11";          // door-01（开门）
#elif DEVICE_TYPE == DEVICE_SENSOR
  const char* COAP_ALLOW_FROM = "";                      // 传感器不监听联动
#endif

// 角色托管目标（每板按 DEVICE_TYPE 只编译自己的表）
#if DEVICE_TYPE == DEVICE_DOOR
  const char* DOOR_OPEN_TARGETS[] = {"192.168.4.15", "192.168.4.10"};  // 音箱+摄像头
#elif DEVICE_TYPE == DEVICE_SENSOR
  const char* SMOKE_TARGETS[]   = {"192.168.4.11", "192.168.4.12"};    // 门禁+路灯
  const char* TEMP_TARGETS[]    = {"192.168.4.13"};                    // 插座
#elif DEVICE_TYPE == DEVICE_SPEAKER
  const char* MAGNET_TARGETS[]  = {"192.168.4.12"};                    // 楼道灯
#endif
#endif  // LINK_SIM_MODE

// ==================== 设备间联动配置（CoAP 直连） ====================
// 固定 IP：CoAP 直连需要设备知道彼此的 IP，所以用静态 IP
IPAddress GATEWAY(192, 168, 4, 1);
IPAddress SUBNET(255, 255, 255, 0);

#if DEVICE_TYPE == DEVICE_DOOR
  IPAddress STATIC_IP(192, 168, 4, 11);
  const char* COAP_TARGET = "192.168.4.15";  // 联动音箱 speaker-01
  #define COAP_SEND   1   // 门禁主动发联动
  #define COAP_LISTEN 1   // 门禁也接收烟雾的联动
  #if LINK_SIM_MODE
    #define PIN_BUTTON 5  // 开门按键（INPUT_PULLUP，按下=开门；不接则靠演示兜底）
  #endif
#elif DEVICE_TYPE == DEVICE_LIGHT
  IPAddress STATIC_IP(192, 168, 4, 12);
  const char* COAP_TARGET = "";
  #define COAP_SEND   0
  #define COAP_LISTEN 1   // 路灯接收烟雾/门磁的联动（应急亮灯）
#elif DEVICE_TYPE == DEVICE_PLUG
  IPAddress STATIC_IP(192, 168, 4, 13);
  const char* COAP_TARGET = "";
  #define COAP_SEND   0
  #define COAP_LISTEN 1   // 插座接收传感器的联动
#elif DEVICE_TYPE == DEVICE_SENSOR
  IPAddress STATIC_IP(192, 168, 4, 14);
  const char* COAP_TARGET = "192.168.4.13";  // 联动插座 plug-01
  #define COAP_SEND   1   // 传感器主动发联动
  #define COAP_LISTEN 0
#elif DEVICE_TYPE == DEVICE_SPEAKER
  IPAddress STATIC_IP(192, 168, 4, 15);
  const char* COAP_TARGET = "";
  #define COAP_SEND   0
  #define COAP_LISTEN 1   // 音箱接收门禁的联动
#endif

// ==================== 设备相关库（按需引入） ====================

#if DEVICE_TYPE == DEVICE_SENSOR
  #include <DHTesp.h>
  DHTesp dht;
#endif

#if DEVICE_TYPE == DEVICE_DOOR
  #include <ESP32Servo.h>
  Servo doorServo;
#endif

// ==================== 全局对象 ====================

WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);
WiFiUDP       udp;
WiFiUDP       coapUdp;    // 设备间联动 CoAP（独立于攻击 UDP）

bool attack_mode = false;         // 是否处于攻击模式（被感染）
unsigned long lastTelemetry = 0;  // 上次遥测时间
unsigned long lastAttack   = 0;   // 上次攻击发包时间
unsigned long attackStarted = 0;  // 攻击实验开始时间
char bootId[33] = {0};
uint64_t telemetrySequence = 0;

// 执行器状态（遥测如实上报，处置闭环在 UI 可见；旧版恒报 locked/on:true）
#if DEVICE_TYPE == DEVICE_DOOR
  bool doorUnlocked = false;
#elif DEVICE_TYPE == DEVICE_PLUG
  bool plugOn = true;
#elif DEVICE_TYPE == DEVICE_SENSOR
  bool sensorFakeData = false;    // 被入侵后上报假数据（README 声明的行为）
#endif

#if LINK_SIM_MODE
  bool linkage_enabled = true;    // MQTT linkage_off/on 运行时开关（切场景免重烧）
  unsigned long nextRoleEvent = 0;   // 托管角色 1 的下次发送时间
  unsigned long nextRole2Event = 0;  // 托管角色 2 的下次发送时间
  unsigned long lastRealEvent = 0;   // 最近一次真实事件（演示兜底判据）
  unsigned long doorUnlockedUntil = 0; // 门禁联动解锁保持（millis 状态机）
  uint8_t roleIndex = 0;             // 角色轮换游标
#endif

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
  // 门禁：上报真实门状态（联动解锁/开门按键 -> unlocked，回锁 -> locked）
  snprintf(buf, sizeof(buf), "{\"device_type\":\"door\",\"state\":\"%s\"}",
           doorUnlocked ? "unlocked" : "locked");
#elif DEVICE_TYPE == DEVICE_LIGHT
  int light = analogRead(PIN_LED) / 16;  // 简化为光照占位
  snprintf(buf, sizeof(buf), "{\"device_type\":\"light\",\"level\":%d}", light);
#elif DEVICE_TYPE == DEVICE_PLUG
  snprintf(buf, sizeof(buf), "{\"device_type\":\"plug\",\"power\":220,\"on\":%s}",
           plugOn ? "true" : "false");
#elif DEVICE_TYPE == DEVICE_SENSOR
  if (sensorFakeData) {
    // 被入侵：上报假数据（前端可见 99.9℃）
    snprintf(buf, sizeof(buf), "{\"device_type\":\"sensor\",\"temp\":99.9,\"humidity\":99.9}");
  } else {
    TempAndHumidity th = dht.getTempAndHumidity();
    float t = th.temperature;
    float h = th.humidity;
    Serial.printf("[DHT] status=%d temp=%.1f hum=%.1f\n", dht.getStatus(), t, h);
    if (isnan(t)) t = 25.0;  // 读取失败用占位
    if (isnan(h)) h = 50.0;
    snprintf(buf, sizeof(buf), "{\"device_type\":\"sensor\",\"temp\":%.1f,\"humidity\":%.1f}", t, h);
  }
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
  doorUnlocked = false;
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, HIGH); // 灯亮
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, LOW); // 继电器吸合（通电）
  plugOn = true;
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, LOW); // 静音
#elif DEVICE_TYPE == DEVICE_SENSOR
  sensorFakeData = false;
#endif
}

void actuatorAttack() {
  // 被入侵后的"专属反应"（震撼演示）
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(0);         // 门锁"自己打开"
  doorUnlocked = true;
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, !digitalRead(PIN_LED)); // 灯疯狂闪烁
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, !digitalRead(PIN_RELAY)); // 继电器反复通断
  plugOn = !plugOn;
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, HIGH); // 发出怪声
#elif DEVICE_TYPE == DEVICE_SENSOR
  sensorFakeData = true;      // 上报假数据（遥测出现 99.9℃/99.9%）
#endif
}

void actuatorBlocked() {
  // 被隔离后的反应（物理阻断）
#if DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(90);        // 锁死
  doorUnlocked = false;
#elif DEVICE_TYPE == DEVICE_LIGHT
  digitalWrite(PIN_LED, LOW); // 熄灭
#elif DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, HIGH); // 断电
  plugOn = false;
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, LOW); // 静音
#elif DEVICE_TYPE == DEVICE_SENSOR
  sensorFakeData = false;
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
#if LINK_SIM_MODE
    else if (msg == "linkage_off") {
      linkage_enabled = false;
      Serial.println("[控制] 联动仿真已关闭（切虚拟版场景免重烧）");
    } else if (msg == "linkage_on") {
      linkage_enabled = true;
      Serial.println("[控制] 联动仿真已开启");
    }
#endif
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

  // 只打隔离网段内的受害机（.200:9000），攻击不得出实验网段
  udp.beginPacket(ATTACK_TARGET, ATTACK_PORT);
  udp.write((const uint8_t*)"\x00\x00\x00\x00", 4);
  udp.endPacket();
  actuatorAttack();  // 触发设备专属"被入侵反应"
}

// ==================== WiFi / MQTT 连接 ====================

void connectWiFi() {
  Serial.printf("连接 WiFi: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.config(STATIC_IP, GATEWAY, SUBNET);  // 静态 IP：CoAP 直连需要设备有固定 IP
  WiFi.setTxPower(WIFI_POWER_8_5dBm);       // 降低发射功率（社区密集部署）
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {  // 最多等 10 秒
    delay(500);
    Serial.print(".");
    tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n已连接, IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\nWiFi 连接失败（当前热点可能未开启，稍后会自动重连）");
  }
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

// ==================== 设备间联动（CoAP 直连） ====================

#if COAP_SEND
void sendCoapEvent() {
  // 周期发送联动事件给目标设备（UDP 到 5683 端口）
  coapUdp.beginPacket(COAP_TARGET, 5683);
  coapUdp.write((const uint8_t*)"evt", 3);
  coapUdp.endPacket();
  Serial.printf("[联动] 发送联动事件给 %s\n", COAP_TARGET);
#if DEVICE_TYPE == DEVICE_DOOR
  // 门禁额外联动摄像头（开门录像，安防联动）
  coapUdp.beginPacket("192.168.4.10", 5683);
  coapUdp.write((const uint8_t*)"evt", 3);
  coapUdp.endPacket();
  Serial.println("[联动] 发送联动事件给 192.168.4.10 (摄像头)");
#endif
}
#endif

#if LINK_SIM_MODE
// ==================== 事件类联动（LINK_SIM_MODE=1） ====================

void sendCoapEventTo(const char* cls, const char* role, const char* targetIp) {
  // 载荷：evt:<事件类>:<角色>（如 evt:door_open:door-01）
  char payload[40];
  snprintf(payload, sizeof(payload), "evt:%s:%s", cls, role);
  coapUdp.beginPacket(targetIp, 5683);
  coapUdp.write((const uint8_t*)payload, strlen(payload));
  coapUdp.endPacket();
  Serial.printf("[联动] 发送 %s -> %s\n", payload, targetIp);
}

bool senderAllowed(IPAddress sender) {
  // COAP_ALLOW_FROM：逗号分隔的发送方白名单（终结"任意 UDP 包万能触发"）
  String allow = String(COAP_ALLOW_FROM);
  int start = 0;
  while (start < (int)allow.length()) {
    int comma = allow.indexOf(',', start);
    if (comma < 0) comma = allow.length();
    IPAddress a;
    if (a.fromString(allow.substring(start, comma).c_str()) && a == sender) return true;
    start = comma + 1;
  }
  return false;
}

void handleLinkageEvent(const char* cls, const char* role) {
  // 按事件类分派的执行器反应（本板只编译自己的 DEVICE_TYPE 分支）
  Serial.printf("[联动] 收到 %s (角色 %s)\n", cls, role);
#if DEVICE_TYPE == DEVICE_DOOR
  if (strcmp(cls, "smoke") == 0) {
    // 火灾逃生：解锁 500ms 回锁（millis 状态机，不阻塞主循环）
    doorServo.write(0);
    doorUnlocked = true;
    doorUnlockedUntil = millis() + 500;
    Serial.println("[联动] 门禁联动解锁（烟雾）");
  }
#elif DEVICE_TYPE == DEVICE_LIGHT
  // 应急亮灯：门磁/烟雾触发闪烁 3 次（常亮状态下的可见反应）
  for (int i = 0; i < 3; i++) {
    digitalWrite(PIN_LED, LOW);
    delay(150);
    digitalWrite(PIN_LED, HIGH);
    delay(150);
  }
  Serial.println("[联动] 路灯应急闪烁");
#elif DEVICE_TYPE == DEVICE_PLUG
  if (strcmp(cls, "temp_high") == 0) {
    digitalWrite(PIN_RELAY, LOW);   // 继电器吸合（通电开空调）
    plugOn = true;
    Serial.println("[联动] 插座通电（高温）");
  } else if (strcmp(cls, "temp_normal") == 0) {
    digitalWrite(PIN_RELAY, HIGH);  // 断电
    plugOn = false;
    Serial.println("[联动] 插座断电（降温）");
  }
#elif DEVICE_TYPE == DEVICE_SPEAKER
  if (strcmp(cls, "door_open") == 0) {
    digitalWrite(PIN_BUZZER, HIGH);
    delay(200);
    digitalWrite(PIN_BUZZER, LOW);
    Serial.println("[联动] 音箱播报（开门）");
  }
#endif
}
#endif  // LINK_SIM_MODE

#if COAP_LISTEN
void checkCoapEvent() {
  // 检查是否有设备间联动事件到达
  int sz = coapUdp.parsePacket();
  if (sz <= 0) return;
#if LINK_SIM_MODE
  // 事件类载荷 + 发送方白名单校验
  char buf[40];
  coapUdp.read(buf, sizeof(buf));
  buf[sizeof(buf) - 1] = '\0';
  if (!senderAllowed(coapUdp.remoteIP())) {
    Serial.println("[安全] 联动来源不在白名单，忽略");
    return;
  }
  char cls[12], role[16];
  if (sscanf(buf, "evt:%11[^:]:%15s", cls, role) != 2) {
    Serial.println("[联动] 载荷格式不符，忽略");
    return;
  }
  handleLinkageEvent(cls, role);
#else
  char buf[8];
  coapUdp.read(buf, sizeof(buf));
  Serial.println("[联动] 收到设备间联动事件");
  actuatorTriggered();
#endif
}

void actuatorTriggered() {
  // 收到联动事件后的设备反应（LINK_SIM_MODE=0 的现网行为）
#if DEVICE_TYPE == DEVICE_PLUG
  digitalWrite(PIN_RELAY, !digitalRead(PIN_RELAY));  // 插座切换通断
  plugOn = !plugOn;
  Serial.println("[联动] 插座切换通断");
#elif DEVICE_TYPE == DEVICE_SPEAKER
  digitalWrite(PIN_BUZZER, HIGH);  // 音箱响一声
  delay(200);
  digitalWrite(PIN_BUZZER, LOW);
  Serial.println("[联动] 音箱响一声");
#elif DEVICE_TYPE == DEVICE_DOOR
  doorServo.write(0);   // 门禁联动解锁（烟雾触发）
  doorUnlocked = true;
  delay(500);
  doorServo.write(90);  // 回位
  doorUnlocked = false;
  Serial.println("[联动] 门禁联动解锁");
#elif DEVICE_TYPE == DEVICE_LIGHT
  // 路灯联动：闪烁 3 次（烟雾/门磁触发，应急亮灯）
  for (int i = 0; i < 3; i++) {
    digitalWrite(PIN_LED, HIGH);
    delay(200);
    digitalWrite(PIN_LED, LOW);
    delay(200);
  }
  digitalWrite(PIN_LED, HIGH);  // 恢复常亮
  Serial.println("[联动] 路灯闪烁");
#endif
}
#endif

#if LINK_SIM_MODE
// ==================== 角色托管调度（LINK_SIM_MODE=1） ====================
// 每板按 DEVICE_TYPE 只编译自己的角色表：事件驱动优先（真实事件），
// 周期 + 随机抖动 + 错峰兜底（esp_random 抖动，避免 6 板假同步）。

void handleLinkageSim(unsigned long now) {
  if (!linkage_enabled) return;

#if DEVICE_TYPE == DEVICE_DOOR
  // 真实事件：GPIO5 开门按键（可选硬件；按下即发 door_open + 遥测 3s unlocked）
  static bool btnWasDown = false;
  bool btnDown = digitalRead(PIN_BUTTON) == LOW;   // INPUT_PULLUP，按下为 LOW
  if (btnDown && !btnWasDown) {
    sendCoapEventTo("door_open", "door-01", DOOR_OPEN_TARGETS[0]);
    sendCoapEventTo("door_open", "door-01", DOOR_OPEN_TARGETS[1]);
    doorUnlocked = true;
    doorUnlockedUntil = now + 3000;   // 真实开门：3s 后回锁
    lastRealEvent = now;
    Serial.println("[联动] 开门按键触发 door_open");
  }
  btnWasDown = btnDown;
  // 托管门禁角色 door-01/02/03：周期 45~90s（含真实角色，演示兜底）
  if (nextRoleEvent == 0) nextRoleEvent = now + 30000;
  if (now >= nextRoleEvent) {
    const char* roles[] = {"door-01", "door-02", "door-03"};
    const char* role = roles[roleIndex % 3];
    roleIndex++;
    sendCoapEventTo("door_open", role, DOOR_OPEN_TARGETS[0]);
    sendCoapEventTo("door_open", role, DOOR_OPEN_TARGETS[1]);
    nextRoleEvent = now + 45000UL + (esp_random() % 45000UL);
  }

#elif DEVICE_TYPE == DEVICE_SENSOR
  // 真实事件：DHT11 高温阈值（>30℃ 发 temp_high，<28℃ 回差发 temp_normal）
  // DHT 读取 5s 节流：DHT11 单次采样 ~20ms，每循环读会卡主循环
  static unsigned long lastDhtRead = 0;
  float t = NAN;
  if (now - lastDhtRead >= 5000) {
    lastDhtRead = now;
    t = dht.getTempAndHumidity().temperature;
  }
  static bool tempHigh = false;
  if (!isnan(t)) {
    if (t >= 30.0 && !tempHigh) {
      tempHigh = true;
      sendCoapEventTo("temp_high", "sensor-01", TEMP_TARGETS[0]);
      lastRealEvent = now;
    } else if (t <= 28.0 && tempHigh) {
      tempHigh = false;
      sendCoapEventTo("temp_normal", "sensor-01", TEMP_TARGETS[0]);
      lastRealEvent = now;
    }
  }
#if LINK_DEMO_FORCE
  // 演示兜底：真实温度 10 分钟未触发阈值时，周期补发 temp_high -> temp_normal
  // （20s 后恢复正常，插座 ON->OFF 双向演示；每 4~6 分钟一轮）
  static bool forceHigh = false;
  if (now - lastRealEvent > 600000UL) {
    if (nextRoleEvent == 0) nextRoleEvent = now + 1000;
    if (now >= nextRoleEvent) {
      if (!forceHigh) {
        sendCoapEventTo("temp_high", "ts-02", TEMP_TARGETS[0]);
        forceHigh = true;
        nextRoleEvent = now + 20000UL;
      } else {
        sendCoapEventTo("temp_normal", "ts-03", TEMP_TARGETS[0]);
        forceHigh = false;
        nextRoleEvent = now + 240000UL + (esp_random() % 120000UL);
      }
    }
  }
#endif
  // 托管烟雾角色 sm-01~03：周期 120~300s 发 smoke -> 门禁解锁 + 路灯应急
  if (nextRole2Event == 0) nextRole2Event = now + 60000;
  if (now >= nextRole2Event) {
    const char* roles[] = {"sm-01", "sm-02", "sm-03"};
    const char* role = roles[roleIndex % 3];
    roleIndex++;
    for (uint8_t i = 0; i < sizeof(SMOKE_TARGETS) / sizeof(SMOKE_TARGETS[0]); i++) {
      sendCoapEventTo("smoke", role, SMOKE_TARGETS[i]);
    }
    nextRole2Event = now + 120000UL + (esp_random() % 180000UL);
  }

#elif DEVICE_TYPE == DEVICE_SPEAKER
  // 托管门磁角色 ds-01~06：周期 40~90s 发 magnet -> 楼道灯
  if (nextRoleEvent == 0) nextRoleEvent = now + 20000;
  if (now >= nextRoleEvent) {
    const char* roles[] = {"ds-01", "ds-02", "ds-03", "ds-04", "ds-05", "ds-06"};
    const char* role = roles[roleIndex % 6];
    roleIndex++;
    sendCoapEventTo("magnet", role, MAGNET_TARGETS[0]);
    nextRoleEvent = now + 40000UL + (esp_random() % 50000UL);
  }
#endif
}
#endif  // LINK_SIM_MODE

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
  dht.setup(PIN_DHT, DHTesp::DHT11);
#elif DEVICE_TYPE == DEVICE_SPEAKER
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
#endif

  connectWiFi();

  // 设备间联动：绑定 CoAP 端口（接收方监听 5683）
  // 必须放在 WiFi 初始化之后：UDP socket 走 lwIP tcpip 线程，
  // 线程未启动时调用会触发 tcpip_send_msg_wait_sem (Invalid mbox) 崩溃
#if COAP_LISTEN
  coapUdp.begin(5683);
#endif

#if LINK_SIM_MODE && DEVICE_TYPE == DEVICE_DOOR
  pinMode(PIN_BUTTON, INPUT_PULLUP);   // 开门按键（可选硬件）
#endif

  connectMQTT();
  Serial.printf("设备启动: %s (类型 %d) 联动仿真:%d\n",
                DEVICE_ID, DEVICE_TYPE, LINK_SIM_MODE);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  if (!mqtt.connected()) {
    connectMQTT();
  }
  mqtt.loop();

  // 设备间联动：接收联动事件
#if COAP_LISTEN
  checkCoapEvent();
#endif

  unsigned long now = millis();

#if LINK_SIM_MODE
  // 门禁联动解锁的 millis 状态机（回锁不阻塞主循环）
#if DEVICE_TYPE == DEVICE_DOOR
  if (doorUnlockedUntil && now >= doorUnlockedUntil) {
    doorServo.write(90);
    doorUnlocked = false;
    doorUnlockedUntil = 0;
  }
#endif
  // 事件驱动 + 角色托管调度
  handleLinkageSim(now);
#else
  // 设备间联动：周期发送联动事件（每 30 秒）—— 现网行为
#if COAP_SEND
  static unsigned long lastCoapSend = 0;
  if (now - lastCoapSend >= 30000) {
    lastCoapSend = now;
    sendCoapEvent();
  }
#endif
#endif

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
