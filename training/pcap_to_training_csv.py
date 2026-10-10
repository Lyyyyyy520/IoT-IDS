"""把现场抓包（pcap）转成训练用的标注流量 CSV —— 真实流量校准（docs/11 阶段5）。

用法:
    python training/pcap_to_training_csv.py --benign 良性.pcap --attack 攻击.pcap \
        --attacker 192.168.4.11 --out training/data/real/real_training_flows.csv

流程：pcap 流式读取 -> 五元组聚合成流 -> 按源 IP 打标签
（良性 pcap 全部 Benign_Final；攻击 pcap 中 attacker 的流标 Mirai-udpplain，
其余 Benign_Final），输出列与 flows_sample.csv / env_training_flows.csv 一致。
"""
import argparse
import sys

from scapy.all import PcapReader, IP, TCP, UDP

_PROTO = {6: 6, 17: 17, 1: 1}


def _tcp_flags(pkt) -> int:
    f = pkt[TCP].flags.value if TCP in pkt else 0
    # scapy 的 flags 是 FlagValue；训练数据用位掩码（F=1,S=2,R=4,P=8,A=16,U=32,E=64,C=128）
    # 这里直接取数值（与训练 FLAG_BITS 对应关系近似），用于后续聚合
    return int(f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--benign", required=True)
    p.add_argument("--attack", required=True)
    p.add_argument("--attacker", default="192.168.4.11")
    p.add_argument("--out", default="training/data/real/real_training_flows.csv")
    p.add_argument("--subnet", default="192.168.4.")
    args = p.parse_args()

    flows = {}  # (src,dst,sport,dport,proto) -> flow dict

    def add_packet(src, dst, sport, dport, proto, length, flags, ts, label):
        key = (src, dst, sport, dport, proto)
        if key not in flows:
            flows[key] = dict(
                src_ip=src, dst_ip=dst, src_port=sport, dst_port=dport,
                protocol=proto, packets=0, bytes=0, tcp_flags=0,
                ts_start=ts, ts_end=ts, label_class=label,
            )
        f = flows[key]
        f["packets"] += 1
        f["bytes"] += length
        f["tcp_flags"] |= flags
        f["ts_start"] = min(f["ts_start"], ts)
        f["ts_end"] = max(f["ts_end"], ts)

    def process_pcap(path, label_fn):
        count = 0
        with PcapReader(path) as reader:
            for pkt in reader:
                if IP not in pkt:
                    continue
                src = pkt[IP].src
                dst = pkt[IP].dst
                proto = pkt[IP].proto
                if proto not in _PROTO:
                    continue
                sport = dport = 0
                if TCP in pkt:
                    sport, dport = pkt[TCP].sport, pkt[TCP].dport
                    flags = _tcp_flags(pkt)
                elif UDP in pkt:
                    sport, dport = pkt[UDP].sport, pkt[UDP].dport
                    flags = 0
                else:
                    continue
                ts = float(pkt.time)
                label = label_fn(src, dst)
                add_packet(src, dst, sport, dport, proto, len(pkt), flags, ts, label)
                count += 1
                if count % 100000 == 0:
                    print(f"  已处理 {count} 包", file=sys.stderr)
        print(f"{path}: 处理 {count} 包", file=sys.stderr)

    print("处理良性 pcap...", file=sys.stderr)
    process_pcap(args.benign, lambda s, d: "Benign_Final")
    print("处理攻击 pcap...", file=sys.stderr)
    process_pcap(args.attack, lambda s, d: "Mirai-udpplain" if s == args.attacker else "Benign_Final")

    # 只保留社区网段内的流量（设备/网关/受害机），排除笔记本等无关客户端
    rows = [f for f in flows.values()
            if (f["src_ip"].startswith(args.subnet) or f["dst_ip"].startswith(args.subnet))]

    import os
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as out:
        out.write("src_ip,dst_ip,src_port,dst_port,protocol,packets,bytes,"
                  "tcp_flags,ts_start,ts_end,label_class\n")
        for f in rows:
            out.write(f"{f['src_ip']},{f['dst_ip']},{f['src_port']},{f['dst_port']},"
                      f"{f['protocol']},{f['packets']},{f['bytes']},{f['tcp_flags']},"
                      f"{f['ts_start']},{f['ts_end']},{f['label_class']}\n")
    print(f"输出 {len(rows)} 条流 -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
