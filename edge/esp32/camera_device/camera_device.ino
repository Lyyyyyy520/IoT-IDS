/*
 * 智能摄像头固件 (ESP32-CAM + OV2640)
 *
 * 真实 MJPEG 视频流 + MQTT 心跳 + 设备间联动（门禁→录像）+ 攻击模拟。
 * v0.4.0 起：去掉云台舵机，改用 OV2640 真实画面推流（HTTP /stream）。
 *
 * 正常：OV2640 持续推流 + MQTT 心跳
 * 被入侵：继续推流 + 向隔离网段受害机 .200:9000 发 UDP 洪水（模拟被僵尸网络感染）
 * 隔离：停止攻击（画面不受影响）
 *
 * 编译前配置（见下方"配置区"）:
 *   1. WiFi（树莓派热点）
 *   2. MQTT Broker（树莓派 IP）+ DEVICE_ID
 *   3. device_secrets.h（攻击目标等）
 *
 * 依赖库（Arduino 库管理器安装）:
 *   - PubSubClient (Nick O'Leary)
 *   - esp32cam (Junxiao Shi，自带 OV2640 驱动)
 *
 * 硬件:
 *   - AI-Thinker ESP32-CAM（带 OV2640，配 MB 底板供电/烧录）
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp32cam.h>
#include "device_secrets.h"

// ==================== 配置区 ====================

const char* WIFI_SSID     = IOT_WIFI_SSID;
const char* WIFI_PASS     = IOT_WIFI_PASSWORD;
const char* MQTT_BROKER   = IOT_MQTT_HOST;
const int   MQTT_PORT     = IOT_MQTT_PORT;
const char* MQTT_USER     = IOT_MQTT_USERNAME;
const char* MQTT_PASSWORD = IOT_MQTT_PASSWORD;
const char* DEVICE_ID     = IOT_DEVICE_ID;
const char* FIRMWARE_VERSION = "0.4.0";
const int MQTT_HEARTBEAT_SCHEMA_VERSION = 2;
const uint16_t MQTT_BUFFER_BYTES = 768;

#define STREAM_PORT   80      // 视频流 HTTP 端口（前端直连 http://<ip>/stream）
#define TELEMETRY_MS  5000    // 心跳间隔

// ==================== 纯固件联动仿真（LINK_SIM_MODE） ====================
// 0（默认）= 现网行为：裸 'evt' 触发录像（非阻塞状态机）
// 1 = 现场演示：解析 evt:door_open:* + 发送方白名单（只信 door-01 .11）+
//     周期出向流量（视频上行边的流量证据，UDP 到私有云地址:80 零阻塞）
#define LINK_SIM_MODE   1   // ← 现场演示烧 1

#if LINK_SIM_MODE
  const char* COAP_ALLOW_FROM = "192.168.4.11";   // 只信 door-01（开门录像）
#endif

// ==================== 全局对象 ====================

WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);
WiFiUDP       udp;
WiFiUDP       coapUdp;   // 设备间联动 CoAP（门禁→摄像头）
WebServer     server(STREAM_PORT);

bool attack_mode = false;
unsigned long lastTelemetry = 0;
unsigned long lastAttack   = 0;
unsigned long attackStarted = 0;
char bootId[33] = {0};
uint64_t telemetrySequence = 0;
unsigned long recordingUntil = 0;   // 录像状态机（非阻塞）

#if LINK_SIM_MODE
  bool linkage_enabled = true;
  unsigned long nextOutbound = 0;    // 周期出向流量计时
#endif

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
  return String("{\"device_type\":\"camera\",\"state\":\"streaming\"}");
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
      Serial.println("[控制] 进入攻击模式（UDP 洪水）");
    } else if (msg == "normal") {
      attack_mode = false;
      Serial.println("[控制] 恢复正常");
    } else if (msg == "block") {
      attack_mode = false;
      Serial.println("[控制] 已隔离（停止攻击）");
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

// ==================== 行为 ====================

void doAttack() {
  // 被入侵：向隔离网段受害机 .200:9000 发 UDP 洪水（画面推流不受影响）
  unsigned long now = millis();
  if (now - attackStarted >= ATTACK_MAX_MS) {
    attack_mode = false;
    Serial.println("[安全] 攻击实验达到最长时限，已自动停止");
    return;
  }
  if (now - lastAttack < ATTACK_INTERVAL_MS) return;
  lastAttack = now;
  // 只打隔离网段内的受害机（.200:9000），攻击不得出实验网段
  udp.beginPacket(ATTACK_TARGET, ATTACK_PORT);
  udp.write((const uint8_t*)"\x00\x00\x00\x00", 4);
  udp.endPacket();
}

// ==================== 视频流（HTTP /stream、/capture） ====================

void handleRoot() {
  String html = String("<!doctype html><html><head><meta charset='utf-8'>")
              + "<title>摄像头 " + DEVICE_ID + "</title></head>"
              + "<body style='margin:0;background:#000'>"
              + "<img src='/stream' style='width:100%'>"
              + "</body></html>";
  server.send(200, "text/html", html);
}

void handleCapture() {
  auto frame = esp32cam::capture();
  if (!frame) { server.send(500, "text/plain", "capture error\n"); return; }
  WiFiClient client = server.client();
  server.setContentLength(frame->size());
  server.send(200, "image/jpeg");
  frame->writeTo(client);
}

// 关键：合作式 MJPEG——每帧之间跑一次 housekeeping，保证推流期间 MQTT 心跳不断，
// 否则后端会把摄像头判成离线。
void handleStream() {
  WiFiClient client = server.client();
  client.setNoDelay(true);
  server.sendContent("HTTP/1.1 200 OK\r\n"
                     "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                     "\r\n");

  Serial.println("[画面] MJPEG 客户端接入，开始推流");
  while (client.connected()) {
    auto frame = esp32cam::capture();
    if (!frame) { Serial.println("[画面] 抓帧失败，流结束"); break; }
    String head = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                  + String(frame->size()) + "\r\n\r\n";
    server.sendContent(head);
    frame->writeTo(client);
    server.sendContent("\r\n");

    serviceHousekeeping();   // MQTT 保活 + 遥测 + 联动 + 攻击
    delay(50);               // ~20fps 上限，给其它任务留时间
  }
  Serial.println("[画面] 客户端断开，流结束");
}

// ==================== WiFi / MQTT 连接 ====================

// 固定 IP：CoAP 直连需要设备知道彼此的 IP（门禁→摄像头联动目标就是 .10）
IPAddress GATEWAY(192, 168, 4, 1);
IPAddress SUBNET(255, 255, 255, 0);
IPAddress STATIC_IP(192, 168, 4, 10);

void connectWiFi() {
  Serial.printf("连接 WiFi: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.config(STATIC_IP, GATEWAY, SUBNET);  // 静态 IP
  WiFi.setTxPower(WIFI_POWER_8_5dBm);       // 降低发射功率（社区密集部署）
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

// ==================== 设备间联动（CoAP 监听） ====================

void checkCoapEvent() {
  // 接收门禁联动（开门录像）：2 秒"录像"用 millis 状态机记录（无舵机，只做联动日志/状态）
  int sz = coapUdp.parsePacket();
  if (sz <= 0) return;
#if LINK_SIM_MODE
  // 事件类载荷 + 发送方白名单校验（只信 door-01）
  char buf[40];
  coapUdp.read(buf, sizeof(buf));
  buf[sizeof(buf) - 1] = '\0';
  IPAddress sender = coapUdp.remoteIP();
  String allow = String(COAP_ALLOW_FROM);
  int start = 0;
  bool ok = false;
  while (start < (int)allow.length()) {
    int comma = allow.indexOf(',', start);
    if (comma < 0) comma = allow.length();
    IPAddress a;
    if (a.fromString(allow.substring(start, comma).c_str()) && a == sender) { ok = true; break; }
    start = comma + 1;
  }
  if (!ok) { Serial.println("[安全] 联动来源不在白名单，忽略"); return; }
  char cls[12], role[16];
  if (sscanf(buf, "evt:%11[^:]:%15s", cls, role) != 2 || strcmp(cls, "door_open") != 0) {
    Serial.println("[联动] 非 door_open 事件，忽略");
    return;
  }
  Serial.printf("[联动] 收到 %s (角色 %s)，开始录像\n", cls, role);
  recordingUntil = millis() + 2000;
#else
  char buf[8];
  coapUdp.read(buf, sizeof(buf));
  Serial.println("[联动] 收到门禁联动事件，开始录像");
  recordingUntil = millis() + 2000;
#endif
}

// ==================== 主循环保活（推流期间也会被调用） ====================

void serviceHousekeeping() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  checkCoapEvent();   // 检查门禁联动（开门录像）

  unsigned long now = millis();

  // 录像状态机：到点结束（非阻塞）
  if (recordingUntil && now >= recordingUntil) {
    recordingUntil = 0;
    Serial.println("[联动] 录像结束");
  }

#if LINK_SIM_MODE
  // 周期出向流量：视频上行边的流量证据（UDP 到私有云地址 10.0.0.1:80，零阻塞；
  // 无路由时包在网关被丢弃，但 wlan0 抓包可见——与虚拟生成器口径一致）
  if (linkage_enabled && now >= nextOutbound) {
    nextOutbound = now + 60000UL;
    for (int i = 0; i < 3; i++) {
      udp.beginPacket("10.0.0.1", 80);
      udp.write((const uint8_t*)"GET", 3);
      udp.endPacket();
    }
    Serial.println("[出向] 周期上行流量（视频边证据）");
  }
#endif

  if (attack_mode) {
    doAttack();
  } else if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;
    String telemetry = buildTelemetry();
    bool published = mqtt.publish(topicStatus().c_str(), telemetry.c_str());
    Serial.printf("[心跳] boot=%s sequence=%llu result=%s\n", bootId,
                  (unsigned long long)telemetrySequence,
                  published ? "sent" : "failed");
  }
}

// ==================== setup / loop ====================

void setup() {
  Serial.begin(115200);
  delay(500);
  randomSeed(analogRead(0));
  generateBootId();
  mqtt.setBufferSize(MQTT_BUFFER_BYTES);

  connectWiFi();

  // 摄像头初始化（OV2640）
  {
    using namespace esp32cam;
    Config cfg;
    cfg.setPins(pins::AiThinker);
    cfg.setResolution(Resolution::find(800, 600));  // SVGA
    cfg.setJpeg(80);
    cfg.setBufferCount(1);
    if (Camera.begin(cfg)) {
      Serial.println("[摄像头] 初始化成功，开始推流");
    } else {
      Serial.println("[摄像头] 初始化失败（无画面，MQTT 心跳继续）");
    }
  }

  // HTTP 视频流服务
  server.on("/", handleRoot);
  server.on("/stream", handleStream);
  server.on("/capture", handleCapture);
  server.begin();
  Serial.printf("[HTTP] 视频流 http://%s/stream\n", WiFi.localIP().toString().c_str());

  coapUdp.begin(5683);   // 监听设备间联动（门禁→摄像头）
  // 必须在 WiFi 初始化之后：UDP socket 走 lwIP tcpip 线程，
  // 线程未启动时调用会触发 tcpip_send_msg_wait_sem (Invalid mbox) 崩溃

  connectMQTT();
  Serial.printf("摄像头启动: %s\n", DEVICE_ID);
}

void loop() {
  serviceHousekeeping();
  server.handleClient();
}
