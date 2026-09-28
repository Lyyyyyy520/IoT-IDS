"""
Rule Engine — YAML-based signature matching for IDS detection

Loads detection rules from rules/*.yaml and evaluates network flow data
against them. Works alongside the CNN+LSTM ONNX model as the dual-engine
detection system.
"""
import os
import yaml
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

RULES_DIR = os.path.join(os.path.dirname(__file__), '..', 'rules')


class FlowRecord:
    """Represents a single network flow or packet for rule matching."""
    def __init__(self, **kwargs):
        self.src_ip = kwargs.get('src_ip', '')
        self.dst_ip = kwargs.get('dst_ip', '')
        self.src_port = kwargs.get('src_port', 0)
        self.dst_port = kwargs.get('dst_port', 0)
        self.protocol = kwargs.get('protocol', '')
        self.flags = kwargs.get('flags', '')
        self.length = kwargs.get('length', 0)
        self.payload = kwargs.get('payload', '')
        self.timestamp = kwargs.get('timestamp', datetime.now())


class Rule:
    """A single detection rule."""
    def __init__(self, rule_dict: dict):
        self.id = rule_dict.get('id', '')
        self.name = rule_dict.get('name', '')
        self.category = rule_dict.get('category', 'unknown')
        self.severity = rule_dict.get('severity', 'medium')
        self.description = rule_dict.get('description', '')
        self.pattern = rule_dict.get('pattern', {})
        self.action = rule_dict.get('action', 'alert')
        self.enabled = True

    def match(self, flow: FlowRecord, window_stats: dict) -> bool:
        """Check if a flow matches this rule."""
        p = self.pattern
        if not p:
            return False

        # Protocol filter
        if 'protocol' in p:
            if flow.protocol.upper() != p['protocol'].upper():
                return False

        # Port filter — single value or list
        if 'dst_port' in p:
            ports = p['dst_port']
            if isinstance(ports, int):
                ports = [ports]
            if flow.dst_port not in ports:
                return False

        # Flags filter (for TCP)
        if 'flags' in p and flow.flags:
            if p['flags'].upper() not in flow.flags.upper():
                return False

        # Payload content match
        if 'payload_contains' in p:
            payload_lower = (flow.payload or '').lower()
            if not any(kw.lower() in payload_lower for kw in p['payload_contains']):
                return False

        # Window-based conditions (use accumulated stats)
        if 'condition' in p:
            condition = p['condition']

            # distinct_dst_ports > N
            m = re.search(r'distinct_dst_ports\s*>\s*(\d+)', condition)
            if m:
                threshold = int(m.group(1))
                if window_stats.get('distinct_dst_ports', 0) <= threshold:
                    return False

            # packet_count > N
            m = re.search(r'packet_count\s*>\s*(\d+)', condition)
            if m:
                threshold = int(m.group(1))
                if window_stats.get('packet_count', 0) <= threshold:
                    return False

            # total_bytes > N
            m = re.search(r'total_bytes\s*>\s*(\d+)', condition)
            if m:
                threshold = int(m.group(1))
                if window_stats.get('total_bytes', 0) <= threshold:
                    return False

            # packet_length > N
            m = re.search(r'packet_length\s*>\s*(\d+)', condition)
            if m:
                threshold = int(m.group(1))
                if flow.length <= threshold:
                    return False

            # dst_port_not_in [ports]
            m = re.search(r'dst_port_not_in\s*\[([^\]]+)\]', condition)
            if m:
                excluded = [int(x.strip()) for x in m.group(1).split(',')]
                if flow.dst_port in excluded:
                    return False

            # dst_ip_not_in [network ranges]
            m = re.search(r'dst_ip_not_in\s*\[([^\]]+)\]', condition)
            if m:
                ranges_str = m.group(1)
                ranges = [x.strip().strip("'").strip('"') for x in ranges_str.split(',')]
                if _ip_in_ranges(flow.dst_ip, ranges):
                    return False

        return True


class RuleEngine:
    """Loads rules and evaluates flows against them."""

    def __init__(self):
        self.rules: List[Rule] = []
        self._window_data: Dict[str, dict] = defaultdict(lambda: {
            'flows': [],
            'packet_count': 0,
            'distinct_dst_ports': set(),
            'total_bytes': 0,
        })
        self.load_rules()

    def load_rules(self):
        """Load all YAML rule files from the rules directory."""
        self.rules = []
        if not os.path.exists(RULES_DIR):
            print(f'[RuleEngine] Rules directory not found: {RULES_DIR}')
            return

        for filename in sorted(os.listdir(RULES_DIR)):
            if not filename.endswith(('.yaml', '.yml')):
                continue
            filepath = os.path.join(RULES_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                if data and 'rules' in data:
                    for rule_dict in data['rules']:
                        self.rules.append(Rule(rule_dict))
            except Exception as e:
                print(f'[RuleEngine] Error loading {filename}: {e}')

        print(f'[RuleEngine] Loaded {len(self.rules)} rules')

    def evaluate(self, flow: FlowRecord) -> List[dict]:
        """
        Evaluate a single flow against all rules.
        Returns list of matched rules with alert info.
        """
        # Update window stats for the source IP
        key = f"{flow.src_ip}:{flow.dst_ip}"
        window_sec = 30
        now = flow.timestamp

        # Clean old entries from window
        window = self._window_data[key]
        window['flows'] = [f for f in window['flows']
                           if (now - f['ts']).total_seconds() < window_sec]

        # Add current flow to window
        window['flows'].append({'ts': now, 'port': flow.dst_port, 'len': flow.length})
        window['packet_count'] = len(window['flows'])
        window['distinct_dst_ports'] = len(set(f['port'] for f in window['flows']))
        window['total_bytes'] = sum(f['len'] for f in window['flows'])

        stats = {
            'packet_count': window['packet_count'],
            'distinct_dst_ports': window['distinct_dst_ports'],
            'total_bytes': window['total_bytes'],
        }

        # Check all rules
        matches = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.match(flow, stats):
                matches.append({
                    'rule_id': rule.id,
                    'rule_name': rule.name,
                    'category': rule.category,
                    'severity': rule.severity,
                    'description': rule.description,
                    'action': rule.action,
                })

        return matches


def _ip_in_ranges(ip: str, ranges: List[str]) -> bool:
    """Check if an IP address is within any of the given network ranges."""
    import ipaddress
    try:
        ip_obj = ipaddress.ip_address(ip)
        for r in ranges:
            try:
                if '/' in r:
                    if ip_obj in ipaddress.ip_network(r, strict=False):
                        return True
                else:
                    if ip == r:
                        return True
            except ValueError:
                continue
    except ValueError:
        pass
    return False


# ---- Singleton ----
_engine: Optional[RuleEngine] = None


def get_rule_engine() -> RuleEngine:
    global _engine
    if _engine is None:
        _engine = RuleEngine()
    return _engine
