#!/usr/bin/env python3
"""
App Usage Tracker — 进程 + Docker 容器资源用量排行
按日/周/月/年/总计 聚合 CPU、内存、网络流量
"""

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_usage.db')

_LAST_SNAPSHOT_TS = 0
_SNAPSHOT_INTERVAL = 60  # collect snapshots every 60s


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        ts INTEGER, app TEXT, cpu_pct REAL, mem_mb REAL,
        net_rx INTEGER, net_tx INTEGER
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_snap_app ON snapshots(app)')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_rollup (
        day TEXT, app TEXT, cpu_avg REAL, mem_avg REAL,
        net_rx INTEGER, net_tx INTEGER,
        PRIMARY KEY (day, app)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS weekly_rollup (
        week TEXT, app TEXT, cpu_avg REAL, mem_avg REAL,
        net_rx INTEGER, net_tx INTEGER,
        PRIMARY KEY (week, app)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS monthly_rollup (
        month TEXT, app TEXT, cpu_avg REAL, mem_avg REAL,
        net_rx INTEGER, net_tx INTEGER,
        PRIMARY KEY (month, app)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS yearly_rollup (
        year TEXT, app TEXT, cpu_avg REAL, mem_avg REAL,
        net_rx INTEGER, net_tx INTEGER,
        PRIMARY KEY (year, app)
    )''')
    conn.commit()
    return conn


def _get_process_list():
    """Get current processes: {comm: {cpu_pct, mem_mb}}"""
    procs = {}
    try:
        result = os.popen("ps -eo comm,pcpu,rss --no-headers 2>/dev/null").read()
        for line in result.strip().split('\n'):
            parts = line.strip().split(None, 2)
            if len(parts) >= 3:
                name = parts[0]
                # Normalize names: strip docker prefixes, version suffixes
                if name.startswith('docker-'):
                    name = name[7:]  # strip 'docker-'
                name = re.sub(r'[-_]\d+$', '', name)  # strip trailing numbers
                name = re.sub(r'[-_](v?\d[\d.]*|arm.*|amd64)$', '', name)

                try:
                    cpu = float(parts[1])
                except ValueError:
                    cpu = 0
                try:
                    mem_kb = int(parts[2])
                except ValueError:
                    mem_kb = 0

                if name not in procs:
                    procs[name] = {'cpu': 0, 'mem': 0}
                procs[name]['cpu'] += cpu
                procs[name]['mem'] += mem_kb
    except Exception:
        pass
    # Convert mem from KB to MB
    for v in procs.values():
        v['mem'] = round(v['mem'] / 1024, 1)
    return procs


def _get_docker_traffic():
    """Get per-container network: {name: {rx, tx}}"""
    traffic = {}
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', '{{.Name}}\t{{.NetIO}}'],
            capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.strip().split('\n'):
            parts = line.strip().split('\t')
            if len(parts) < 2:
                continue
            name = parts[0]
            netio = parts[1].strip()
            rx_str, tx_str = '', ''
            if ' / ' in netio:
                rx_str, tx_str = netio.split(' / ', 1)

            def parse(s):
                s = s.strip().replace(',', '')
                m = re.match(r'([\d.]+)\s*([a-zA-Z]+)', s)
                if not m:
                    return 0
                v = float(m.group(1))
                u = m.group(2).lower()
                mul = {'b': 1, 'kb': 1000, 'kib': 1024, 'mb': 1e6,
                       'mib': 1048576, 'gb': 1e9, 'gib': 1073741824}
                return int(v * mul.get(u, 1))

            traffic[name] = {'rx': parse(rx_str), 'tx': parse(tx_str)}
    except Exception:
        pass
    return traffic


def collect_snapshot():
    """Collect and store one snapshot of app usage."""
    global _LAST_SNAPSHOT_TS
    now = int(time.time())
    if now - _LAST_SNAPSHOT_TS < _SNAPSHOT_INTERVAL:
        return
    _LAST_SNAPSHOT_TS = now

    procs = _get_process_list()
    dockers = _get_docker_traffic()

    conn = _get_db()
    c = conn.cursor()

    # Process CPU + mem
    for app, data in procs.items():
        net_rx = dockers.get(app, {}).get('rx', 0)
        net_tx = dockers.get(app, {}).get('tx', 0)
        c.execute(
            'INSERT INTO snapshots VALUES (?,?,?,?,?,?)',
            (now, app, data['cpu'], data['mem'], net_rx, net_tx)
        )

    # Docker-only apps (no matching process)
    for app, data in dockers.items():
        if app not in procs:
            c.execute(
                'INSERT INTO snapshots VALUES (?,?,?,?,?,?)',
                (now, app, 0, 0, data['rx'], data['tx'])
            )

    conn.commit()

    # Cleanup old snapshots (keep last 7 days of raw data)
    c.execute('DELETE FROM snapshots WHERE ts < ?', (now - 7 * 86400,))
    conn.commit()
    conn.close()

    # Roll up daily/weekly/monthly/yearly
    _rollup_all()


def _rollup_all():
    """Roll up snapshots into daily/weekly/monthly/yearly aggregates."""
    conn = _get_db()
    c = conn.cursor()
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    this_week = now.strftime('%G-W%V')  # ISO week
    this_month = now.strftime('%Y-%m')
    this_year = now.strftime('%Y')

    periods = [
        ('daily_rollup', 'day', today),
        ('weekly_rollup', 'week', this_week),
        ('monthly_rollup', 'month', this_month),
        ('yearly_rollup', 'year', this_year),
    ]

    for table, col, period_val in periods:
        # Aggregate from snapshots (last 24h for daily, etc.)
        cutoff = int(time.time()) - 86400  # last 24h for all rollups
        c.execute(f'''
            INSERT OR REPLACE INTO {table} ({col}, app, cpu_avg, mem_avg, net_rx, net_tx)
            SELECT ?, app,
                   ROUND(AVG(cpu_pct), 1), ROUND(AVG(mem_mb), 1),
                   MAX(net_rx), MAX(net_tx)
            FROM snapshots
            WHERE ts >= ?
            GROUP BY app
            ORDER BY AVG(cpu_pct) DESC
        ''', (period_val, cutoff))
    conn.commit()
    conn.close()


def get_app_ranking(period='total', metric='cpu', limit=15):
    """
    Get app usage ranking.
    period: 'day', 'week', 'month', 'year', 'total'
    metric: 'cpu', 'mem', 'net'
    """
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    this_week = now.strftime('%G-W%V')
    this_month = now.strftime('%Y-%m')
    this_year = now.strftime('%Y')

    conn = _get_db()
    c = conn.cursor()

    if period == 'day':
        table = 'daily_rollup'
        col = 'day'
        val = today
    elif period == 'week':
        table = 'weekly_rollup'
        col = 'week'
        val = this_week
    elif period == 'month':
        table = 'monthly_rollup'
        col = 'month'
        val = this_month
    elif period == 'year':
        table = 'yearly_rollup'
        col = 'year'
        val = this_year
    else:  # total - from snapshots aggregate all time
        table = 'snapshots'
        col = None
        val = None

    if metric == 'cpu':
        order_col = 'cpu_avg'
    elif metric == 'mem':
        order_col = 'mem_avg'
    else:  # net
        order_col = '(net_rx + net_tx)'

    if period == 'total':
        c.execute(f'''
            SELECT app,
                   ROUND(AVG(cpu_pct), 1) as cpu_avg,
                   ROUND(AVG(mem_mb), 1) as mem_avg,
                   MAX(net_rx) as net_rx,
                   MAX(net_tx) as net_tx
            FROM snapshots
            GROUP BY app
            ORDER BY {order_col} DESC
            LIMIT ?
        ''', (limit,))
    else:
        c.execute(f'''
            SELECT app, cpu_avg, mem_avg, net_rx, net_tx
            FROM {table}
            WHERE {col} = ?
            ORDER BY {order_col} DESC
            LIMIT ?
        ''', (val, limit))

    rows = c.fetchall()
    conn.close()

    result = []
    for row in rows:
        app, cpu, mem, rx, tx = row
        result.append({
            'app': app,
            'cpu': cpu or 0,
            'mem': mem or 0,
            'net_rx': rx or 0,
            'net_tx': tx or 0,
            'net_total': (rx or 0) + (tx or 0),
        })
    return result


def get_combined_ranking(period='total', limit=15):
    """Combined ranking: weighted score across CPU + mem + net."""
    apps = {}
    periods = ['day', 'week', 'month', 'year', 'total']
    target = period if period != 'total' else 'total'

    for metric in ['cpu', 'mem', 'net']:
        ranking = get_app_ranking(target, metric, limit=50)
        total_score = max((r.get('cpu', 0) if metric == 'cpu' else
                           r.get('mem', 0) if metric == 'mem' else
                           r.get('net_total', 0)) for r in ranking) if ranking else 1
        if total_score == 0:
            total_score = 1
        for r in ranking:
            app = r['app']
            if app not in apps:
                apps[app] = {'app': app, 'cpu': 0, 'mem': 0, 'net_rx': 0, 'net_tx': 0, 'net_total': 0, 'score': 0}
            apps[app]['cpu'] = max(apps[app]['cpu'], r.get('cpu', 0))
            apps[app]['mem'] = max(apps[app]['mem'], r.get('mem', 0))
            apps[app]['net_rx'] = max(apps[app]['net_rx'], r.get('net_rx', 0))
            apps[app]['net_tx'] = max(apps[app]['net_tx'], r.get('net_tx', 0))
            apps[app]['net_total'] = max(apps[app]['net_total'], r.get('net_total', 0))
            # Weighted score
            val = (r.get('cpu', 0) if metric == 'cpu' else
                   r.get('mem', 0) if metric == 'mem' else
                   r.get('net_total', 0))
            apps[app]['score'] += val / total_score

    # Sort by score
    result = sorted(apps.values(), key=lambda x: x['score'], reverse=True)[:limit]
    return result


if __name__ == '__main__':
    collect_snapshot()
    print("=== CPU Ranking (total) ===")
    for r in get_app_ranking('total', 'cpu', 10):
        print(f"  {r['app']:30s} CPU:{r['cpu']:6.1f}% MEM:{r['mem']:8.1f}MB")
    print("\n=== Combined Ranking ===")
    for r in get_combined_ranking('total', 10):
        print(f"  {r['app']:30s} score:{r['score']:5.2f} CPU:{r['cpu']:5.1f}% MEM:{r['mem']:7.1f}MB net:{r['net_total']}")
