# 树莓派选购 + 部署完整指南（小白专用）

> 从零开始，不需要任何硬件经验，每一步都有截图级描述。

---

## 一、买什么

### 核心设备

| 物品 | 型号 | 大约价格 | 在哪买 |
|------|------|----------|--------|
| **树莓派主板** | Raspberry Pi 4B（4GB 内存版） | ¥300-400 | 淘宝搜「树莓派4B 4G」 |
| **Micro SD 卡** | 32GB 以上，Class10 速度 | ¥30-50 | 淘宝买「闪迪/三星 32G TF卡」 |
| **读卡器** | USB 读卡器 | ¥10-15 | 任何读卡器都行，插电脑 USB 口 |
| **电源** | 5V 3A Type-C 接口 | ¥25-35 | 搜「树莓派4B 电源」，买带开关的 |
| **网线** | 普通超五类网线，1-2 米 | ¥5-10 | 连接树莓派到路由器 |

### 可选（建议买）

| 物品 | 作用 | 价格 |
|------|------|------|
| 散热片 3 件套 | 贴在芯片上散热 | ¥5 |
| 外壳 | 保护主板，防尘 | ¥15-25 |
| 微型风扇 | 长期运行时散热 | ¥8 |

### 总预算：约 ¥400-500

如果你已经有读卡器、USB-C 充电头、SD 卡，只需要树莓派主板约 ¥300。

---

## 二、你需要准备的东西

除了上面的硬件，还需要：

1. **一台 Windows 电脑**（你现在在用的这台就行）
2. **家里的路由器**，有空的网线接口
3. **电脑上装两个免费软件**（下面会一步步教）

---

## 三、部署步骤

一共 5 步，每步 10-20 分钟。

```
第1步：烧录系统 → 第2步：首次开机 → 第3步：传文件 → 第4步：装依赖 → 第5步：运行
```

### 第 1 步：给 SD 卡装系统

**你需要**：电脑 + 读卡器 + SD 卡

1. 用读卡器把 SD 卡插到电脑 USB 口
2. 下载 **Raspberry Pi Imager**（官方烧录工具）
   - 网址：https://www.raspberrypi.com/software/
   - 点那个大按钮 Download for Windows，下载后安装
3. 打开 Raspberry Pi Imager
4. 点 **CHOOSE DEVICE** → 选 **Raspberry Pi 4**
5. 点 **CHOOSE OS** → 选 **Raspberry Pi OS (other)** → 选 **Raspberry Pi OS Lite (64-bit)**
   - 注意：选 Lite 版（无桌面），因为我们要节省资源跑检测
6. 点 **CHOOSE STORAGE** → 选你的 SD 卡（⚠ 注意别选错，会清空卡上所有数据）
7. 点右下角齿轮图标 ⚙️，设置：
   - 勾选 **Enable SSH**（用密码登录）
   - 勾选 **Set username and password**：用户名填 `pi`，密码自己设一个，记住它
   - 勾选 **Configure wireless LAN**：填你的 WiFi 名和密码（SSID = WiFi 名，Password = WiFi 密码）
   - 选 WiFi 国家为 **CN**
8. 点 **SAVE** 保存设置
9. 点 **WRITE** 开始烧录，等进度条走完（约 5-10 分钟）
10. 完成后拔掉读卡器，取出 SD 卡

> 现在 SD 卡里已经有系统了。

---

### 第 2 步：首次开机

**你需要**：树莓派主板 + SD 卡 + 网线 + 电源

1. 把 SD 卡插入树莓派底部的卡槽（轻轻推进去，听到咔嗒声）
2. 网线一头插树莓派网口，另一头插路由器 LAN 口
3. 电源线插树莓派 Type-C 口（标着 POWER 的那个），插上电源
4. **红色灯亮 → 绿色灯闪烁** → 说明启动正常，等 1 分钟

#### 找到树莓派的 IP 地址

树莓派没有屏幕，你需要从你的 Windows 电脑连接它。先找到它的 IP：

1. 打开浏览器，输入你路由器管理地址（通常是 `192.168.1.1` 或 `192.168.0.1`）
2. 登录路由器后台（账号密码一般贴路由器底部）
3. 找到 **DHCP 客户端列表** 或 **已连接设备**
4. 找设备名含 `raspberrypi` 的，记下 IP 地址，比如 `192.168.1.105`

> 这就是你树莓派的地址，后面都用这个 IP。

---

### 第 3 步：把项目文件传到树莓派

**你需要**：电脑上装 WinSCP

1. 下载 WinSCP：https://winscp.net/ → 下载安装
2. 打开 WinSCP，点 **新建会话**：
   - 文件协议：选 **SFTP**
   - 主机名：填树莓派 IP（比如 `192.168.1.105`）
   - 用户名：`pi`
   - 密码：你在第 1 步设的密码
   - 点 **登录**
3. 登录后左边是你电脑的文件，右边是树莓派的文件
4. 在右边（树莓派端）右键 → **新建 → 目录**，输入 `iot-ids`，回车
5. 在左边找到你电脑上的项目目录 `d:\Project\iot-ids\`
6. 把以下文件/文件夹**从左边拖到右边**的 `iot-ids` 目录里：
   - `backend/models/inference.py`
   - `backend/services/feature_extract.py`
   - `backend/data/best_model.onnx`
   - `backend/data/best_model.onnx.data`
   - `backend/data/scaler.pkl`
   - `edge/edge_detect.py`
   - `edge/probe_client.py`

> 只需要拖上面列出的 7 个文件，不需要拖整个项目。

---

### 第 4 步：在树莓派上装环境

**你需要**：电脑上装 PuTTY 或者直接用 Windows 的命令行

方式一：用 Windows 自带的命令行：

1. 按 `Win + R`，输入 `cmd`，回车
2. 输入 `ssh pi@192.168.1.105`（把 IP 换成你的），回车
3. 输入密码（输的时候不显示，这是正常的安全特性），回车
4. 看到 `pi@raspberrypi:~ $` 就说明连上了

方式二（推荐）：下载 PuTTY → 主机名填 IP → 端口 22 → Open → 登录

连上后，逐条执行以下命令（一行一行复制粘贴）：

```bash
# 1. 更新系统
sudo apt update && sudo apt upgrade -y

# 2. 安装 Python 工具
sudo apt install python3-pip python3-dev -y

# 3. 安装 ONNX Runtime（模型推理引擎）
pip3 install onnxruntime numpy

# 4. 安装网络相关工具
pip3 install scapy pandas scikit-learn

# 5. 装完后检查
python3 -c "import onnxruntime; print('ONNX OK')"
```

最后一条如果输出 `ONNX OK`，说明环境装好了。

> 装环境大概需要 20-30 分钟，取决于网速。中间可能会卡住一阵，别关掉，等就行。

---

### 第 5 步：运行检测程序

在 SSH 终端里继续：

```bash
# 进入项目目录
cd ~/iot-ids

# 先测试一下能不能跑（demo 模式）
python3 edge_detect.py
```

正常的话会看到：

```
IoT IDS Edge Detection — Demo Mode
  Model: best_model.onnx
  Model loaded: True
  [ 1] 🔴 Mirai     | Conf: 97% | Risk: critical
  [ 2] 🟢 Normal    | Conf: 86% | Risk: low
  ...
  1000 inferences in 1.23s
  Avg latency: 1.23 ms/sample
```

说明成功！

---

## 四、设置开机自动运行

想让树莓派一通电就自动跑检测程序：

```bash
# 创建自启动服务
sudo nano /etc/systemd/system/iot-ids.service
```

把下面内容粘贴进去（Ctrl+Shift+V）：

```
[Unit]
Description=IoT IDS Edge Detection
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/iot-ids
ExecStart=/usr/bin/python3 /home/pi/iot-ids/edge_detect.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

按 `Ctrl+X` → 按 `Y` → 回车保存。

然后启用并启动：

```bash
sudo systemctl enable iot-ids
sudo systemctl start iot-ids
sudo systemctl status iot-ids   # 看看运行状态
```

看到 `active (running)` 就成功了。以后树莓派一插电，检测程序自动启动。

---

## 五、常见问题

| 问题 | 解决 |
|------|------|
| 绿色灯不闪 | SD 卡没插好或系统没烧录成功，重新烧录 |
| SSH 连不上 | 检查 IP 对不对，WiFi 密码对不对，路由器有没有限制 |
| ONNX 报错 | 检查 `best_model.onnx` 和 `best_model.onnx.data` **两个文件**是不是都拷到树莓派了 |
| 推理很慢 | 正常，树莓派 CPU 跑 1000 次要 1-2 秒 |
| 忘记 IP | 重新进路由器后台查看 |

---

## 六、需要帮助？

把报错截图或文字发到项目群里，@李云锦 或 @韦思杨。
