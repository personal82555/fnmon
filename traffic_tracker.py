#!/usr/bin/env python3
"""Traffic tracker: total / daily / monthly cumulative counters + per-container stats with speed."""

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import date

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'traffic.db')

# Module-level cache for per-container speed calculation
# { container_name: { 'rx': int, 'tx': int, 'ts': float }, ... }
_PREV_CONTAINER_STATS = {}


def _get_db():
    """Get SQLite connection with schema init."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS traffic_base (
        id TEXT PRIMARY KEY, rx_base INTEGER, tx_base INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS traffic_daily (
        date TEXT PRIMARY KEY, rx_base INTEGER, tx_base INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS traffic_monthly (
        month TEXT PRIMARY KEY, rx_base INTEGER, tx_base INTEGER
    )''')
    conn.commit()
    return conn


def _get_interfaces():
    """Read /proc/net/dev and return {iface: {rx, tx}} for all relevant interfaces."""
    interfaces = {}
    try:
        with open('/proc/net/dev') as f:
            for line in f.readlines()[2:]:  # skip headers
                parts = line.strip().split()
                if len(parts) >= 10:
                    iface = parts[0].rstrip(':')
                    # Skip loopback
                    if iface == 'lo':
                        continue
                    # Skip veth (Docker internal — counted per-container)
                    if iface.startswith('veth'):
                        continue
                    # Skip individual Docker bridges
                    if re.match(r'^br-[a-f0-9]{12}$', iface):
                        continue
                    interfaces[iface] = {
                        'rx': int(parts[1]),
                        'tx': int(parts[9]),
                    }
    except Exception:
        pass
    return interfaces


def _get_main_traffic(interfaces):
    """Aggregate main interfaces (eno, enx, eth)."""
    rx = tx = 0
    for iface, data in interfaces.items():
        if iface.startswith(('eno', 'enx', 'eth')):
            rx += data['rx']
            tx += data['tx']
    return rx, tx


def _parse_bytes(s):
    """Parse '1.23kB', '45.4MB', '960B' etc to bytes."""
    s = s.strip().replace(',', '')
    m = re.match(r'([\d.]+)\s*([a-zA-Z]+)', s)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).lower()
    multipliers = {'b': 1, 'kb': 1000, 'kib': 1024, 'mb': 1e6, 'mib': 1048576,
                   'gb': 1e9, 'gib': 1073741824, 'tb': 1e12, 'tib': 1099511627776}
    return int(val * multipliers.get(unit, 1))


def _get_docker_containers():
    """
    Get per-container network traffic + real-time speed.
    Returns list sorted by total traffic descending.
    Results cached for 30 seconds to avoid blocking SSE.
    """
    global _PREV_CONTAINER_STATS, _DOCKER_CACHE, _DOCKER_CACHE_TIME
    now = time.time()

    # Return cache if fresh (30s)
    if '_DOCKER_CACHE' in globals() and now - _DOCKER_CACHE_TIME < 30:
        return _DOCKER_CACHE

    containers = []
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format',
             '{{.Name}}\t{{.NetIO}}'],
            capture_output=True, text=True, timeout=30
        )
        lines = result.stdout.strip().split('\n')
        now = time.time()

        current_snapshot = {}
        for line in lines:
            parts = line.strip().split('\t')
            if len(parts) >= 2:
                name = parts[0]
                netio = parts[1].strip()
                rx_str, tx_str = '', ''
                if ' / ' in netio:
                    rx_str, tx_str = netio.split(' / ', 1)
                rx = _parse_bytes(rx_str.strip())
                tx = _parse_bytes(tx_str.strip())
                current_snapshot[name] = {'rx': rx, 'tx': tx, 'ts': now}

                # Compute speed from previous snapshot
                rx_speed = tx_speed = 0
                prev = _PREV_CONTAINER_STATS.get(name)
                if prev:
                    dt = now - prev['ts']
                    if dt > 0:
                        rx_speed = max(0, (rx - prev['rx']) / dt)
                        tx_speed = max(0, (tx - prev['tx']) / dt)

                containers.append({
                    'name': name,
                    'rx': rx,
                    'tx': tx,
                    'rx_speed': round(rx_speed),
                    'tx_speed': round(tx_speed),
                })

        _PREV_CONTAINER_STATS = current_snapshot
    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        pass  # Docker not available
    except Exception:
        pass

    # Sort by total traffic (rx + tx) descending
    containers.sort(key=lambda c: c['rx'] + c['tx'], reverse=True)
    _DOCKER_CACHE = containers
    _DOCKER_CACHE_TIME = time.time()
    return containers


def get_traffic_stats():
    """
    Return traffic stats:
      - total: cumulative since tracking started
      - daily: cumulative for today
      - monthly: cumulative for this month
      - containers: top Docker containers with cumulative + speed
    """
    conn = _get_db()
    c = conn.cursor()
    today = date.today().isoformat()
    this_month = today[:7]  # YYYY-MM

    interfaces = _get_interfaces()
    main_rx, main_tx = _get_main_traffic(interfaces)

    # ---- Total traffic baseline (set once) ----
    c.execute('SELECT rx_base, tx_base FROM traffic_base WHERE id=?', ('_main',))
    row = c.fetchone()
    if row:
        rx_base, tx_base = row
    else:
        rx_base, tx_base = main_rx, main_tx
        c.execute('INSERT OR REPLACE INTO traffic_base VALUES (?,?,?)',
                  ('_main', rx_base, tx_base))
        conn.commit()

    total_rx = max(0, main_rx - rx_base)
    total_tx = max(0, main_tx - tx_base)

    # ---- Daily cumulative (baseline at start of day) ----
    c.execute('SELECT rx_base, tx_base FROM traffic_daily WHERE date=?', (today,))
    row = c.fetchone()
    if row:
        day_rx_base, day_tx_base = row
    else:
        # First call today: set baseline = current total, so daily starts at 0
        day_rx_base, day_tx_base = total_rx, total_tx
        c.execute('INSERT OR REPLACE INTO traffic_daily VALUES (?,?,?)',
                  (today, day_rx_base, day_tx_base))
        conn.commit()

    daily_rx = max(0, total_rx - day_rx_base)
    daily_tx = max(0, total_tx - day_tx_base)

    # ---- Monthly cumulative (baseline at start of month) ----
    c.execute('SELECT rx_base, tx_base FROM traffic_monthly WHERE month=?',
              (this_month,))
    row = c.fetchone()
    if row:
        mon_rx_base, mon_tx_base = row
    else:
        mon_rx_base, mon_tx_base = total_rx, total_tx
        c.execute('INSERT OR REPLACE INTO traffic_monthly VALUES (?,?,?)',
                  (this_month, mon_rx_base, mon_tx_base))
        conn.commit()

    monthly_rx = max(0, total_rx - mon_rx_base)
    monthly_tx = max(0, total_tx - mon_tx_base)

    conn.close()

    # ---- Docker containers ----
    containers = _get_docker_containers()

    return {
        'total_rx': total_rx,
        'total_tx': total_tx,
        'daily_rx': daily_rx,
        'daily_tx': daily_tx,
        'monthly_rx': monthly_rx,
        'monthly_tx': monthly_tx,
        'containers': containers[:15],  # top 15
    }


if __name__ == '__main__':
    print(json.dumps(get_traffic_stats(), indent=2))
