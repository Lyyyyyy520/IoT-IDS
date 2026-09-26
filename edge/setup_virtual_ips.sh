#!/bin/bash
# 给 wlan0 挂虚拟设备的多 IP（192.168.4.100 ~ 120，共 21 个）
# 用法：sudo bash setup_virtual_ips.sh

set -e

IFACE="wlan0"
START=100
END=120

echo "给 $IFACE 挂 $START~$END 的 IP ..."
for i in $(seq $START $END); do
  ip addr add 192.168.4.$i/24 dev "$IFACE" 2>/dev/null || echo "  192.168.4.$i 已存在，跳过"
done

echo ""
echo "当前 $IFACE 上的 IP："
ip addr show "$IFACE" | grep "192.168.4"
echo ""
echo "完成！现在可以运行 sudo python3 virtual_device_generator.py"
