"""受害机 UDP 洪水计数器（攻击演示的可视化道具）。

现场演示时，拿第二台设备（笔记本/手机）静态配成 192.168.4.200，跑本脚本：
攻击开始后，door-01 的 UDP 洪水会打到这台设备，计数器持续爬升，是"洪水真实发生"
的独立证据（与 IDS 系统无任何耦合）。

用法（在受害机上）:
    python victim_udp_counter.py
可选参数:
    --port 9000    监听的 UDP 端口（默认 9000，与固件 IOT_LAB_ATTACK_PORT 一致）
"""
import argparse
import socket


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--ip", default="0.0.0.0", help="监听地址（默认全部接口）")
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    print(f"[受害机] 监听 UDP {args.ip}:{args.port}，等待被洪水命中...")
    print("[受害机] 提示：本机需静态配置为 192.168.4.200，并连接 iot-community 热点")

    count = 0
    while True:
        sock.recvfrom(2048)
        count += 1
        if count % 10 == 0:
            print(f"[受害机] 已收到攻击包 {count} 个")


if __name__ == "__main__":
    main()
