> 历史参考：本文记录旧版规划或部署方案，不代表当前产品需求或可用 API。当前范围见 [README](../README.md)、[API 目录](05-api-spec.md) 与 [v3 清理清单](rebuild/legacy-cleanup-inventory.md)。

# VMware IoT 流量实时接入 IoT-IDS

目标：不经过 PCAP/CSV 中转。Ubuntu 虚拟机上的真实模拟流量直接进入 Windows 上运行的 IoT-IDS。

## 拓扑

```text
IoT-device 192.168.41.136
  multi_devices.py / mqtt_flood_test.py
              |
              | MQTT / TCP 1883
              v
IoT-server 192.168.41.135
  Mosquitto + vm_probe_client.py (ens33)
              |
              | HTTP /api/probe/push
              v
Windows 主机 IoT-IDS backend :5000
  rule/model pipeline -> SQLite -> Web frontend :3000
```

## 1. Windows：查 VMnet8 地址

运行 `ipconfig`，找到 `VMware Network Adapter VMnet8` 的 IPv4。通常和两台 VM 位于同一网段，例如 `192.168.41.1`，但必须以实际输出为准。

## 2. Windows：开放后端端口

管理员 PowerShell：

```powershell
netsh advfirewall firewall add rule name="IoT IDS 5000" dir=in action=allow protocol=TCP localport=5000
```

启动项目，确认 Flask 显示监听 `0.0.0.0:5000`。

## 3. IOT-server：准备 VM 探针

把 `edge/vm_probe_client.py` 复制到 `192.168.41.135`，然后：

```bash
python3 -m venv ~/iot-probe-venv
source ~/iot-probe-venv/bin/activate
pip install requests scapy
```

先验证能访问 Windows 后端（将 IP 换成第 1 步实际 VMnet8 IP）：

```bash
curl http://192.168.41.1:5000/api/health
```

## 4. IOT-server：运行实时探针

```bash
sudo ~/iot-probe-venv/bin/python ~/vm_probe_client.py \
  --server http://192.168.41.1:5000 \
  --name Pi-001 \
  --interface ens33 \
  --token '<与后端 IOT_IDS_PROBE_TOKEN 一致的凭据>' \
  --bpf "host 192.168.41.136 and not tcp port 5000"
```

`Pi-001` 用于兼容当前网页中的启动/停止控制名称。

## 5. Web：启动真实采集

打开 `http://localhost:3000`，进入“流量分析” → “实时网卡检测”：

1. 检测模式切到“真实”。
2. 点击开始。
3. VM 探针终端应显示 `capture STARTED`。

## 6. IOT-device：产生正常流量

```bash
cd ~/iot-simulator
source venv/bin/activate
python multi_devices.py
```

网页“实时流量日志”应直接出现 `192.168.41.136 -> 192.168.41.135:1883`，且 `source=real`。

## 7. IOT-device：产生受限攻击测试流量

停止正常模拟器后，在自己的隔离 VMware 网络内运行已有的 `mqtt_flood_test.py`。网页流量日志会实时增加；规则 `RULE-013 MQTT 异常订阅` 在超过阈值后会生成一次去重告警。

## 8. 后续增加独立攻击机

如果新增 `192.168.41.137` 攻击 VM，把探针过滤器改成：

```bash
--bpf "(host 192.168.41.136 or host 192.168.41.137) and not tcp port 5000"
```

这样正常设备与攻击源都会进入同一个 IDS 管线。

## 重要说明

- `vm_probe_client.py` 是传输/采集桥，不生成模拟数据；模拟数据仍由 `multi_devices.py` 和实验攻击脚本产生。
- 探针推送的数据会先进入 `TrafficCapture.process_external_packet()`，再经过规则、模型、日志和告警管线。
- 当前 21 维 ONNX 的“实时单包特征”仍是简化实现；验证攻击时应以规则告警为确定性基线，模型结果暂时不能直接作为正式实验结论。
