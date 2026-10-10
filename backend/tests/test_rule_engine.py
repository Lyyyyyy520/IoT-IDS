"""Tests for the Mirai rule signature engine and the second-layer annotation.

Contract (defense posture, see dev-logs/2026-10-08 consistency audit):
  - the GNN's level/probs are NEVER overridden by the rule layer;
  - a rule signature hit is attached as corroborating evidence only
    (rule_hits / rule_signature_hit fields);
  - normal (benign) community traffic never trips a signature.
"""
import pytest

from services.rule_engine import (
    C2_MIN_HITS,
    C2_PORT,
    C2_SERVER,
    NORMAL_UDP_PORTS,
    SCAN_PORTS,
    SYN_SCAN_MIN_HITS,
    UDP_FLOOD_MIN_PKTS,
    VICTIM_FLOOD_MIN_PKTS,
    VICTIM_IP,
    VICTIM_PORT,
    DeviceRuleCounter,
    RuleEngine,
)
from services.traffic_capture import TrafficCapture


class TestDeviceRuleCounter:
    def test_empty_counter_has_no_signature(self):
        assert DeviceRuleCounter().has_mirai_signature() is False

    def test_syn_scan_threshold_is_exact(self):
        counter = DeviceRuleCounter()
        counter.syn_scan_hits = SYN_SCAN_MIN_HITS - 1
        assert counter.has_mirai_signature() is False
        counter.syn_scan_hits = SYN_SCAN_MIN_HITS
        assert counter.has_mirai_signature() is True

    def test_udp_flood_threshold_is_exact(self):
        counter = DeviceRuleCounter()
        counter.udp_flood_pkts = UDP_FLOOD_MIN_PKTS - 1
        assert counter.has_mirai_signature() is False
        counter.udp_flood_pkts = UDP_FLOOD_MIN_PKTS
        assert counter.has_mirai_signature() is True

    def test_c2_threshold_is_exact(self):
        counter = DeviceRuleCounter()
        counter.c2_hits = C2_MIN_HITS - 1
        assert counter.has_mirai_signature() is False
        counter.c2_hits = C2_MIN_HITS
        assert counter.has_mirai_signature() is True


class TestRuleEngine:
    def test_internal_syn_scan_to_scan_ports_counted(self):
        engine = RuleEngine()
        for _ in range(SYN_SCAN_MIN_HITS):
            engine.on_packet("192.168.4.127", "192.168.4.101", 55555, 23, "TCP", "SYN")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].syn_scan_hits == SYN_SCAN_MIN_HITS
        assert snap["192.168.4.127"].has_mirai_signature()

    def test_syn_to_external_target_is_not_internal_scan(self):
        engine = RuleEngine()
        for _ in range(10):
            engine.on_packet("192.168.4.127", "8.8.8.8", 55555, 23, "TCP", "SYN")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].syn_scan_hits == 0

    def test_syn_to_non_scan_port_is_not_counted(self):
        engine = RuleEngine()
        engine.on_packet("192.168.4.127", "192.168.4.101", 55555, 8080, "TCP", "SYN")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].syn_scan_hits == 0

    def test_udp_flood_to_external_non_service_port_counted(self):
        engine = RuleEngine()
        for _ in range(UDP_FLOOD_MIN_PKTS):
            engine.on_packet("192.168.4.127", "45.33.32.156", 40000, 9999, "UDP", "")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].udp_flood_pkts == UDP_FLOOD_MIN_PKTS
        assert snap["192.168.4.127"].has_mirai_signature()

    def test_dns_and_ntp_udp_are_not_flood(self):
        engine = RuleEngine()
        for port in NORMAL_UDP_PORTS:
            engine.on_packet("192.168.4.100", "8.8.8.8", 40000, port, "UDP", "")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.100"].udp_flood_pkts == 0

    def test_internal_udp_is_not_flood(self):
        engine = RuleEngine()
        for _ in range(20):
            engine.on_packet("192.168.4.100", "192.168.4.101", 40000, 5683, "UDP", "")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.100"].udp_flood_pkts == 0

    def test_c2_counted_only_for_exact_ip_and_port(self):
        engine = RuleEngine()
        engine.on_packet("192.168.4.127", C2_SERVER, 55555, C2_PORT, "TCP", "PSH")
        engine.on_packet("192.168.4.127", C2_SERVER, 55555, 80, "TCP", "PSH")  # wrong port
        engine.on_packet("192.168.4.127", "10.99.1.200", 55555, C2_PORT, "TCP", "PSH")  # wrong ip
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].c2_hits == 1

    def test_counters_are_per_source_ip(self):
        engine = RuleEngine()
        engine.on_packet("192.168.4.127", "192.168.4.101", 55555, 23, "TCP", "SYN")
        engine.on_packet("192.168.4.100", "192.168.4.101", 55555, 23, "TCP", "SYN")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].syn_scan_hits == 1
        assert snap["192.168.4.100"].syn_scan_hits == 1

    def test_snapshot_and_reset_clears_state(self):
        engine = RuleEngine()
        engine.on_packet("192.168.4.127", "192.168.4.101", 55555, 23, "TCP", "SYN")
        first = engine.snapshot_and_reset()
        second = engine.snapshot_and_reset()
        assert "192.168.4.127" in first
        assert "192.168.4.127" not in second

    def test_victim_udp_flood_counted(self):
        engine = RuleEngine()
        for _ in range(VICTIM_FLOOD_MIN_PKTS):
            engine.on_packet("192.168.4.127", VICTIM_IP, 55555, VICTIM_PORT, "UDP", "")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].victim_flood_pkts == VICTIM_FLOOD_MIN_PKTS
        assert snap["192.168.4.127"].has_mirai_signature()

    def test_victim_flood_wrong_port_not_counted(self):
        engine = RuleEngine()
        engine.on_packet("192.168.4.127", VICTIM_IP, 55555, 9999, "UDP", "")
        snap = engine.snapshot_and_reset()
        assert snap["192.168.4.127"].victim_flood_pkts == 0


class TestRuleAnnotation:
    """规则层第二层佐证：只标注命中信息，不覆盖 GNN 判定（答辩口径）。"""

    @staticmethod
    def _annotate(result, snapshot):
        # _annotate_rule_hits is a pure function of (result, snapshot); skip
        # TrafficCapture.__init__ (which may load the GNN detector/model).
        capture = TrafficCapture.__new__(TrafficCapture)
        return capture._annotate_rule_hits(result, snapshot)

    def test_hit_annotated_without_overriding_verdict(self):
        counter = DeviceRuleCounter()
        counter.c2_hits = 1
        result = {
            "192.168.4.127": {
                "level": 1, "name": "疑似(黄)",
                "probs": [0.1, 0.5, 0.3, 0.1], "confidence": 0.5,
            }
        }
        annotated = self._annotate(result, {"192.168.4.127": counter})
        item = annotated["192.168.4.127"]
        # GNN 判定原样保留（不强制红、不降绿）
        assert item["level"] == 1
        assert item["name"] == "疑似(黄)"
        assert item["probs"] == [0.1, 0.5, 0.3, 0.1]
        assert item["confidence"] == 0.5
        # 规则佐证字段
        assert item["rule_signature_hit"] is True
        assert item["rule_hits"] == {"syn_scan": 0, "udp_flood": 0, "c2": 1, "victim_flood": 0}

    def test_no_hit_gets_false_flag(self):
        counter = DeviceRuleCounter()
        result = {"192.168.4.127": {"level": 0, "name": "正常(绿)",
                                    "probs": [0.9, 0.05, 0.03, 0.02]}}
        annotated = self._annotate(result, {"192.168.4.127": counter})
        assert annotated["192.168.4.127"]["rule_signature_hit"] is False
        assert annotated["192.168.4.127"]["level"] == 0

    def test_ip_without_counter_left_unchanged(self):
        result = {"192.168.4.101": {"level": 2, "name": "异常(橙)",
                                    "probs": [0.1, 0.2, 0.6, 0.1]}}
        annotated = self._annotate(result, {})
        assert annotated["192.168.4.101"] == result["192.168.4.101"]

    def test_non_dict_entries_are_skipped(self):
        result = {"garbage": "not-a-dict"}
        annotated = self._annotate(result, {})
        assert annotated == {}

    def test_signature_hit_never_escalates_level(self):
        # 即使规则命中，GNN 的绿也必须保持绿——红只能由 GNN 给出。
        counter = DeviceRuleCounter()
        counter.syn_scan_hits = SYN_SCAN_MIN_HITS
        result = {"192.168.4.127": {"level": 0, "name": "正常(绿)",
                                    "probs": [0.9, 0.05, 0.03, 0.02]}}
        annotated = self._annotate(result, {"192.168.4.127": counter})
        item = annotated["192.168.4.127"]
        assert item["level"] == 0
        assert item["probs"] == [0.9, 0.05, 0.03, 0.02]
        assert item["rule_signature_hit"] is True


class TestRuleFusion:
    """规则融合（IOT_IDS_RULE_FUSION=on，现场演示）：命中签名 → 强制红。"""

    @staticmethod
    def _fusion(result, snapshot):
        capture = TrafficCapture.__new__(TrafficCapture)
        return capture._apply_rule_fusion(result, snapshot)

    def test_signature_forces_red(self):
        counter = DeviceRuleCounter()
        counter.victim_flood_pkts = VICTIM_FLOOD_MIN_PKTS
        result = {"192.168.4.127": {"level": 0, "name": "正常(绿)",
                                    "probs": [0.9, 0.05, 0.03, 0.02]}}
        fused = self._fusion(result, {"192.168.4.127": counter})
        item = fused["192.168.4.127"]
        assert item["level"] == 3
        assert item["name"] == "僵尸网络(红)"
        assert item["rule_source"] == "mirai_signature"
        assert item["rule_hits"]["victim_flood"] == VICTIM_FLOOD_MIN_PKTS
        assert item["rule_signature_hit"] is True

    def test_no_signature_forces_green(self):
        # 现场演示：GNN 把正常稀疏流量误判成"黄/侦察"，规则层无签名 → 强制回绿。
        counter = DeviceRuleCounter()
        result = {"192.168.4.127": {"level": 1, "name": "侦察(黄)",
                                    "probs": [0.001, 0.998, 0.001, 0.0]}}
        fused = self._fusion(result, {"192.168.4.127": counter})
        item = fused["192.168.4.127"]
        assert item["level"] == 0
        assert item["name"] == "正常(绿)"
        assert item["rule_signature_hit"] is False
        assert item["rule_hits"]["victim_flood"] == 0
        assert item["rule_hits"]["syn_scan"] == 0

    def test_non_dict_entries_skipped(self):
        fused = self._fusion({"garbage": "x"}, {})
        assert fused == {}
