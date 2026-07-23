# 树莓派透明网桥部署方案

## 原理

```
Internet → 路由器 → 树莓派（网桥） → 交换机/WiFi → 摄像头/门禁/传感器
                          ↑
                    所有流量经过这里
                    ONNX 模型检测每个包
                    告警推送到 Windows 后台
```

## 需要的硬件

| 设备 | 价格 | 作用 |
|------|------|------|
| USB 转以太网卡 | ~30 元 | 给树莓派增加第二个网口 |
| 树莓派 4B | 已有 | 检测引擎 |
| 网线 x2 | 已有 | 连接 |

## 第一步：硬件连接

```
路由器 LAN口 → 网线 → 树莓派自带网口(eth0)
                          ↓
                  USB网卡(eth1) → 网线 → 交换机/其他设备
```

USB 网卡插到树莓派的 USB 3.0 口（蓝色那个）。

## 第二步：在树莓派上安装桥接工具

```bash
# 树莓派终端（PuTTY）
sudo apt update
sudo apt install bridge-utils -y
```

## 第三步：配置透明网桥

```bash
# 创建网桥
sudo brctl addbr br0

# 把两个网口加入网桥
sudo brctl addif br0 eth0
sudo brctl addif br0 eth1

# 启用网桥
sudo ip link set br0 up
```

> 注意：执行后会断网！因为 eth0 被加入网桥。用网线直连树莓派继续操作。

## 第四步：配置永久生效

```bash
# 编辑网络配置
sudo nano /etc/network/interfaces.d/br0
```

写入以下内容：
```
auto br0
iface br0 inet dhcp
    bridge_ports eth0 eth1
    bridge_stp off
    bridge_fd 0
```

保存（Ctrl+O → 回车 → Ctrl+X），重启：
```bash
sudo reboot
```

## 第五步：启动检测

```bash
cd ~/backend
source ~/iot-env/bin/activate
sudo python3 edge_detect.py --live
```

树莓派现在会检测所有经过网桥的流量，包括社区里每台 IoT 设备的所有通信。

## 验证

用一个设备（比如摄像头）正常上网或通信，树莓派终端会打印：
```
  [1] Normal   | 192.168.1.10:443 -> 192.168.1.1:80 | 85%
  [2] Mirai    | 10.99.1.100:60000 -> 192.168.1.10:23 | 97%
```

## 供电注意事项

树莓派 + USB 网卡功耗约 5-8W，确保电源适配器至少 5V/2.5A。如果 USB 网卡不识别，换一个更大功率的电源。
