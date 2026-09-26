/*
 * ESP32-C3 WiFi 发射测试（诊断用）
 *
 * 目的：确认板子 WiFi 是"能收不能发"（硬件发射缺陷）还是完全正常。
 *
 * 测试步骤：
 *   1. 烧录本固件（开发板选 ESP32C3 Dev Module，Flash Mode DIO，同之前设置）
 *   2. 打开串口监视器(115200)，看能不能扫描到网络 → 测试"接收"
 *   3. 用手机搜索 WiFi，看能不能找到 "esp32-test" → 测试"发射"
 *
 * 判断：
 *   - 扫描到网络 但 手机看不到 esp32-test → 发射坏了（硬件缺陷，换板）
 *   - 扫描到网络 且 手机能看到 esp32-test → 发射正常（问题在别处）
 */

#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(500);

  // 打印本机 MAC（识别用）
  Serial.printf("本机 MAC: %s\n", WiFi.macAddress().c_str());

  // ===== 测试 1：接收（扫描） =====
  Serial.println("\n[测试1] 扫描 WiFi（测接收）...");
  int n = WiFi.scanNetworks();
  Serial.printf("扫描到 %d 个网络:\n", n);
  for (int i = 0; i < n && i < 20; i++) {
    String ssid = WiFi.SSID(i);
    wifi_auth_mode_t enc = WiFi.encryptionType(i);
    Serial.printf("  %-20s %d dBm  加密=%d\n", ssid.c_str(), WiFi.RSSI(i), (int)enc);
  }
  Serial.println("加密类型对照: 0=开放 1=WEP 2=WPA 4=WPA2 5=WPA/WPA2混合 7=WPA3");

  // ===== 测试 2：发射（softAP） =====
  Serial.println("\n[测试2] 建立热点 esp32-test（测发射）...");
  WiFi.mode(WIFI_AP);
  bool ok = WiFi.softAP("esp32-test", "12345678");
  if (ok) {
    Serial.printf("softAP 已启动，IP: %s\n", WiFi.softAPIP().toString().c_str());
    Serial.println(">>> 现在用手机搜索 WiFi，看列表里有没有 'esp32-test'");
  } else {
    Serial.println("softAP 启动失败!");
  }
}

void loop() {
  delay(1000);
}
