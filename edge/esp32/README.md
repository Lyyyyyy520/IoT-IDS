# ESP32 设备固件使用说明

`community_device/community_device.ino` 是智慧社区 IoT 设备模拟固件，每块 ESP32 模拟一种社区设备，通过 MQTT 遥测上报状态，支持"攻击模式"模拟被僵尸网络感染。

## 1. 设备类型与硬件对应

| DEVICE_TYPE | 设备 | 引脚 | 执行器 | 被入侵后的反应 |
|-------------|------|------|--------|---------------|
| `DEVICE_DOOR` | 智能门禁 | GPIO0 | SG90 舵机 | 舵机"开门" |
| `DEVICE_LIGHT` | 智能路灯 | GPIO1 | LED | 灯疯狂闪烁 |
| `DEVICE_PLUG` | 智能插座 | GPIO2 | 继电器 | 继电器反复通断 |
| `DEVICE_SENSOR` | 温湿度传感器 | GPIO3 | DHT11 | 上报假数据 |
| `DEVICE_SPEAKER` | 智能音箱 | GPIO4 | 蜂鸣器 | 发出怪声 |

> 摄像头（ESP32-CAM）是单独设备，用另一套固件（见第 6 节）。

## 2. 编译前配置（`community_device.ino` 顶部"配置区"）

每台设备烧录前改 4 处：

```cpp
#define DEVICE_TYPE DEVICE_SENSOR   // ① 改成对应设备类型
const char* WIFI_SSID = "iot-community";   // ② 树莓派热点 SSID
const char* WIFI_PASS = "12345678";        // ③ 热点密码
const char* MQTT_BROKER = "192.168.4.1";   // ④ 树莓派 IP
const char* DEVICE_ID = "sensor-01";       // ⑤ 唯一设备 ID（每台不同）
```

**6 台设备建议配置：**

| 设备 | DEVICE_TYPE | DEVICE_ID | 静态 IP（树莓派 DHCP 绑定） |
|------|-------------|-----------|---------------------------|
| 摄像头 | ESP32-CAM（另套固件） | camera-01 | 192.168.4.10 |
| 门禁 | `DEVICE_DOOR` | door-01 | 192.168.4.11 |
| 路灯 | `DEVICE_LIGHT` | light-01 | 192.168.4.12 |
| 插座 | `DEVICE_PLUG` | plug-01 | 192.168.4.13 |
| 传感器 | `DEVICE_SENSOR` | sensor-01 | 192.168.4.14 |
| 音箱 | `DEVICE_SPEAKER` | speaker-01 | 192.168.4.15 |

## 3. 依赖库（Arduino IDE 库管理器搜索安装）

| 库 | 用途 | 需要的设备 |
|----|------|-----------|
| PubSubClient (Nick O'Leary) | MQTT | 全部 |
| DHT sensor library (Adafruit) | 温湿度 | SENSOR |
| ESP32Servo (Kevin Harrington) | 舵机 | DOOR |

## 4. 烧录步骤（Arduino IDE）

1. 开发板管理器安装 **ESP32** 支持包（Arduino-ESP32 core）
2. 开发板选择：**ESP32C3 Dev Module**
3. 插 USB，选择对应串口
4. 改好配置 → 上传

## 5. MQTT 主题与攻击模式

| 主题 | 方向 | 说明 |
|------|------|------|
| `community/{DEVICE_ID}/status` | 设备→Pi | 遥测上报（每 5s） |
| `community/{DEVICE_ID}/control` | Pi→设备 | 控制指令 |

**控制指令**（发到 control 主题）：

| 指令 | 效果 |
|------|------|
| `attack` | 进入攻击模式（发 Mirai UDP 洪水 + 设备"被入侵反应"） |
| `normal` | 恢复正常模式 |
| `block` | 隔离（物理阻断：锁死/断电/静音） |

**演示时**，在树莓派上用 mosquitto 发指令即可触发攻击/隔离：

```bash
# 让门禁"被感染"
mosquitto_pub -h 192.168.4.1 -t "community/door-01/control" -m "attack"
# 隔离门禁
mosquitto_pub -h 192.168.4.1 -t "community/door-01/control" -m "block"
```

## 6. 摄像头（ESP32-CAM）说明

摄像头用独立固件 `camera_device/camera_device.ino`（已写好）：
- 正常：云台缓慢扫描 + MQTT 心跳
- 被入侵：云台疯狂乱转 + UDP 洪水
- 隔离：云台停止

硬件：ESP32-CAM + SG90 舵机（云台，接 GPIO2）。依赖库：PubSubClient + ESP32Servo。

摄像头不做复杂视频流，只需 MQTT 心跳 + 云台动作（Pi 检测的是"这台设备在通信"，不是画面内容）。

## 7. 域偏移提醒

固件生成的流量（MQTT 遥测 + UDP 洪水）与训练数据 CICIoT2023 的流量模式**可能有差异**（域偏移），导致模型现场误判。现场联调时需**校准遥测频率/包长**，使设备流量接近训练分布。见 `dev-logs/2026-08-28.md` 的域偏移记录。
