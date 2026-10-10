#!/usr/bin/env bash
# 烧录 ESP32-CAM 摄像头固件（真实视频流版 v0.4.0）
#
# 用法:
#   bash flash_camera.sh            # 只编译 + 列出可用串口
#   bash flash_camera.sh COM5       # 编译 + 烧录到 COM5
#
# 说明: esp32cam 库需要 C++17（esp32 core 2.0.17 默认 C++11），
#       编译用 compiler.cpp.extra_flags 追加 -std=gnu++17。
#       上传用 esptool 直写（arduino-cli upload 不接受 --build-property）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OPS="$HERE/../../.ops"
SKETCH="$HERE/camera_device"
FQBN="esp32:esp32:esp32cam:FlashMode=dio"
BUILD="$OPS/build-camera"
PORT="${1:-}"

# 定位 arduino 数据目录 + esptool + boot_app0
DATA_DIR="$("$OPS/arduino-cli.exe" config dump 2>/dev/null | sed -n 's/^ *data: *//p' | tr -d '\r')"
DATA_U="$(cygpath -u "$DATA_DIR" 2>/dev/null || echo "$DATA_DIR")"
ESPTOOL="$(ls "$DATA_U"/packages/esp32/tools/esptool_py/*/esptool.exe 2>/dev/null | sort | tail -1)"
BOOTAPP0="$(ls "$DATA_U"/packages/esp32/hardware/esp32/*/tools/partitions/boot_app0.bin 2>/dev/null | sort | tail -1)"

echo "==> 编译固件 (FQBN=$FQBN, C++17)"
"$OPS/arduino-cli.exe" compile \
  --fqbn "$FQBN" \
  --build-property "compiler.cpp.extra_flags=-std=gnu++17" \
  --build-path "$BUILD" \
  "$SKETCH"

if [ -z "$PORT" ]; then
  echo ""
  echo "==> 未指定端口，当前串口："
  "$OPS/arduino-cli.exe" board list
  echo ""
  echo "用法: bash $0 COM端口   （例如 COM5）"
  exit 0
fi

echo ""
echo "==> esptool 烧录到 $PORT"
"$ESPTOOL" --chip esp32 --port "$PORT" --baud 921600 \
  --before default_reset --after hard_reset write_flash \
  --flash_mode dio --flash_freq 80m --flash_size 4MB \
  0x1000 "$BUILD/camera_device.ino.bootloader.bin" \
  0x8000 "$BUILD/camera_device.ino.partitions.bin" \
  0xe000 "$BOOTAPP0" \
  0x10000 "$BUILD/camera_device.ino.bin"

echo ""
echo "==> 完成。视频流地址: http://192.168.4.10/stream"
echo "   浏览器直接打开该地址应能看到实时画面；前端自动嵌入此流。"
