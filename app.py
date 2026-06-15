#!/usr/bin/env python3
"""fnOS System Monitor — CPU, 内存, 网络, 磁盘实时监控 (SSE + API)."""

import json
import os
import time
import glob
import re
import sqlite3
from collections import deque
from datetime import datetime
from flask import Flask, Response, jsonify, request
from traffic_tracker import get_traffic_stats
import traffic_tracker
from app_usage_tracker import collect_snapshot, get_app_ranking, get_combined_ranking
import app_usage_tracker

app = Flask(__name__, static_folder='static', static_url_path='')

SYSFS_CPU = '/sys/devices/system/cpu'
THERMAL = '/sys/class/thermal'
UPDATE_INTERVAL = 2  # seconds

# Network history (30 samples = 60s at 2s interval)
NET_HISTORY = deque(maxlen=30)
DISK_HISTORY = deque(maxlen=30)
CPU_HISTORY = {}  # { 'cpu0': deque(maxlen=30), ... }


def read_sysfs(path, default='N/A'):
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return default


def get_cpu_stats():
    """CPU: per-core freq, governor, EPP, temp, load."""
    stats = {}
    freqs = {}
    for cpu_dir in sorted(glob.glob(f'{SYSFS_CPU}/cpu[0-9]*')):
        core = os.path.basename(cpu_dir)
        freq = read_sysfs(f'{cpu_dir}/cpufreq/scaling_cur_freq')
        freqs[core] = int(freq) // 1000 if freq != 'N/A' else 0
    stats['freqs'] = freqs

    stats['governor'] = read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/scaling_governor')
    stats['epp'] = read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/energy_performance_preference')
    stats['driver'] = read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/scaling_driver')
    stats['min_freq'] = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/scaling_min_freq', '0')) // 1000
    stats['max_freq'] = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/scaling_max_freq', '0')) // 1000
    stats['hw_max'] = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/cpuinfo_max_freq', '0')) // 1000
    stats['hw_min'] = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/cpuinfo_min_freq', '0')) // 1000

    # CPU info
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if 'model name' in line:
                    stats['model'] = line.split(':')[1].strip()
                    break
        # Brand
        stats['brand'] = 'Intel' if 'Intel' in stats.get('model', '') else \
                         'AMD' if 'AMD' in stats.get('model', '') else 'Unknown'
        # Shorter display model
        stats['model_short'] = stats.get('model', '').replace('Intel(R) Core(TM) ', '').replace(' CPU @', '@')

        # Core/Thread count
        with open('/proc/cpuinfo') as f:
            lines = f.read()
        stats['physical_cores'] = len(set(re.findall(r'core id\s+:\s+(\d+)', lines)))
        stats['threads'] = len(re.findall(r'processor\s+:\s+(\d+)', lines))
        stats['sockets'] = len(set(re.findall(r'physical id\s+:\s+(\d+)', lines)))
    except Exception:
        stats['model'] = 'Unknown'
        stats['model_short'] = 'Unknown'
        stats['physical_cores'] = 0
        stats['threads'] = 0
        stats['sockets'] = 0

    # Benchmark data (hardcoded known scores for this CPU)
    stats['bench'] = {
        'passmark_single': 1850,
        'passmark_multi': 5500,
        'cinebench_r23_single': 850,
        'cinebench_r23_multi': 3200,
        'geekbench6_single': 1100,
        'geekbench6_multi': 3500,
    }

    # Comparison: nearby CPUs
    stats['compare'] = [
        {'name': 'N100', 'score': 4200, 'tdp': 6, 'note': '入门NAS神U'},
        {'name': 'J4125', 'score': 2800, 'tdp': 10, 'note': '极低功耗'},
        {'name': 'i3-9100T ←你', 'score': 5500, 'tdp': 35, 'note': '35W均衡'},
        {'name': 'i5-8400T', 'score': 8500, 'tdp': 35, 'note': '35W升级首选'},
        {'name': 'i5-10500T', 'score': 10800, 'tdp': 35, 'note': '6C/12T'},
        {'name': 'i3-12100', 'score': 12400, 'tdp': 60, 'note': '质变之选'},
    ]

    # Overclock check
    cur_freq = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/scaling_cur_freq', '0')) // 1000
    base_freq = int(read_sysfs(f'{SYSFS_CPU}/cpu0/cpufreq/base_frequency', '0')) // 1000
    if cur_freq > stats['hw_max']:
        stats['oc'] = True
    elif cur_freq > base_freq and base_freq > 0:
        stats['turbo'] = True
        stats['base_freq'] = base_freq
    else:
        stats['turbo'] = False
        stats['base_freq'] = base_freq

    # Fan speed (check hwmon)
    stats['fan_rpm'] = None
    try:
        for hw in glob.glob('/sys/class/hwmon/hwmon*'):
            hw_name = read_sysfs(f'{hw}/name', '')
            for fan_in in sorted(glob.glob(f'{hw}/fan*_input')):
                rpm = read_sysfs(fan_in)
                if rpm and rpm != 'N/A' and int(rpm) > 0:
                    label = read_sysfs(fan_in.replace('_input', '_label'), os.path.basename(fan_in))
                    if not stats['fan_rpm']:
                        stats['fan_rpm'] = {'rpm': int(rpm), 'label': label}
    except Exception:
        pass

    temps = []
    for tz in sorted(glob.glob(f'{THERMAL}/thermal_zone*')):
        raw = read_sysfs(f'{tz}/temp')
        if raw != 'N/A':
            temps.append(round(int(raw) / 1000, 1))
    stats['temps'] = temps
    stats['temp_max'] = max(temps) if temps else 0

    try:
        with open('/proc/loadavg') as f:
            parts = f.read().strip().split()
            stats['load_1'] = float(parts[0])
            stats['load_5'] = float(parts[1])
            stats['load_15'] = float(parts[2])
    except OSError:
        stats['load_1'] = stats['load_5'] = stats['load_15'] = 0

    return stats


def get_mem_stats():
    """Memory: total, used, available, swap, cached, buffers."""
    stats = {}
    try:
        with open('/proc/meminfo') as f:
            data = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    val = parts[1].strip().split()[0]
                    try:
                        data[parts[0]] = int(val)
                    except ValueError:
                        pass

        total = data.get('MemTotal', 0)
        free = data.get('MemFree', 0)
        avail = data.get('MemAvailable', 0)
        cached = data.get('Cached', 0)
        buffers = data.get('Buffers', 0)
        swap_total = data.get('SwapTotal', 0)
        swap_free = data.get('SwapFree', 0)

        stats['total'] = total // 1024
        stats['used'] = (total - avail) // 1024
        stats['free'] = free // 1024
        stats['available'] = avail // 1024
        stats['cached'] = cached // 1024
        stats['buffers'] = buffers // 1024
        stats['swap_total'] = swap_total // 1024
        stats['swap_free'] = swap_free // 1024
        stats['swap_used'] = (swap_total - swap_free) // 1024

        # Percentage
        stats['used_pct'] = round((total - avail) / total * 100, 1) if total else 0
        stats['swap_pct'] = round((swap_total - swap_free) / swap_total * 100, 1) if swap_total else 0
    except OSError:
        pass
    return stats


def get_net_stats():
    """Network: per-interface bytes in/out, cumulative."""
    stats = {}
    try:
        with open('/proc/net/dev') as f:
            f.readline()  # header
            f.readline()  # header
            interfaces = {}
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 10:
                    iface = parts[0].rstrip(':')
                    # Skip loopback
                    if iface == 'lo':
                        continue
                    # Skip veth (Docker internal)
                    if iface.startswith('veth'):
                        continue

                    # Skip individual Docker bridges, show aggregated later
                    if re.match(r'^br-[a-f0-9]{12}$', iface):
                        continue

                    # Skip unused bridges
                    if iface in ('br-ikuai-lan',):
                        continue

                    rx_bytes = int(parts[1])
                    tx_bytes = int(parts[9])
                    interfaces[iface] = {
                        'display': iface,
                        'rx_bytes': rx_bytes,
                        'tx_bytes': tx_bytes,
                    }

            # Calculate speed from history
            now = time.time()
            prev = {}
            if NET_HISTORY:
                prev = NET_HISTORY[-1]

            for iface, data in interfaces.items():
                if iface in prev:
                    dt = now - prev.get('_ts', now)
                    if dt > 0:
                        rx_speed = (data['rx_bytes'] - prev[iface]['rx_bytes']) / dt
                        tx_speed = (data['tx_bytes'] - prev[iface]['tx_bytes']) / dt
                        data['rx_speed'] = round(rx_speed)
                        data['tx_speed'] = round(tx_speed)
                    else:
                        data['rx_speed'] = 0
                        data['tx_speed'] = 0
                else:
                    data['rx_speed'] = 0
                    data['tx_speed'] = 0

            # Store for next speed calc
            current_snapshot = {iface: {'rx_bytes': d['rx_bytes'], 'tx_bytes': d['tx_bytes']}
                                for iface, d in interfaces.items()}
            current_snapshot['_ts'] = now
            NET_HISTORY.append(current_snapshot)

            # Sort: main interfaces first (eno1, enx...), then bridges
            def sort_key(iface):
                name = iface[0]
                if name.startswith('eno') or name.startswith('enx'):
                    return (0, name)
                elif name.startswith('tun'):
                    return (1, name)
                elif name.startswith('br-') or name.startswith('docker'):
                    return (2, name)
                else:
                    return (3, name)

            sorted_ifaces = sorted(interfaces.items(), key=lambda x: sort_key(x[0]))
            stats['interfaces'] = [v for _, v in sorted_ifaces]
            stats['interface_names'] = [k for k, _ in sorted_ifaces]
    except OSError:
        pass
    return stats


def get_drive_health():
    """Physical drive health: type, temp, lifespan, reallocated sectors."""
    drives = []
    try:
        # Parse lsblk for physical disks
        blk = os.popen("lsblk -d -o NAME,SIZE,TYPE,ROTA,TRAN,MODEL --json 2>/dev/null").read()
        if blk:
            data = json.loads(blk)
            for dev in data.get('blockdevices', []):
                if dev.get('type') != 'disk':
                    continue
                name = dev['name']
                size = dev.get('size', '?')
                # lsblk JSON: rota can be bool (True/False) or int (0/1)
                rota_raw = dev.get('rota', True)
                if isinstance(rota_raw, bool):
                    rota = '1' if rota_raw else '0'
                else:
                    rota = str(rota_raw)
                tran = dev.get('tran', 'sata') or 'sata'
                model = dev.get('model', name)

                drive_type = 'NVMe' if (tran or '').lower() == 'nvme' else ('SSD' if rota == '0' else 'HDD')
                dev_path = f'/dev/{name}'
                if tran == 'nvme':
                    # NVMe might be named nvme0n1, smartctl needs nvme0
                    dev_path = '/dev/' + re.sub(r'n\d+$', '', name)

                health = {}
                try:
                    result = os.popen(f'sudo smartctl -A {dev_path} 2>/dev/null').read()
                    if 'SMART/Health' in result or 'SMART Attributes' in result:
                        # Temperature
                        if 'Temperature:' in result:
                            # NVMe: "Temperature:  48 Celsius"
                            m = re.search(r'Temperature:\s+(\d+)', result)
                            if m:
                                health['temp'] = int(m.group(1))
                        else:
                            # SATA: line "194 Temperature_Celsius ... - 39 (Min/Max 22/60)"
                            for line in result.split('\n'):
                                if 'Temperature_Celsius' in line:
                                    # Get the last number before the parenthetical
                                    parts = line.split()
                                    # Find the raw value (after rightmost '-' or last number)
                                    for p in reversed(parts):
                                        if p.isdigit():
                                            health['temp'] = int(p)
                                            break
                                    break
                        # Power on hours
                        pwr = re.findall(r'Power_On_Hours.*?(\d+)\s*$', result, re.MULTILINE)
                        if pwr:
                            hours = int(pwr[-1])
                            health['power_on_hours'] = hours
                            health['power_on_days'] = round(hours / 24, 1)
                        # Reallocated sectors
                        realloc = re.findall(r'Reallocated_Sector_Ct.*?(\d+)\s*$', result, re.MULTILINE)
                        if realloc:
                            health['reallocated'] = int(realloc[-1])
                        # Percentage Used (NVMe)
                        pct = re.findall(r'Percentage Used[:\s]+(\d+)%', result)
                        if pct:
                            health['pct_used'] = int(pct[0])
                            health['life_remaining'] = 100 - int(pct[0])
                        # Available Spare (NVMe)
                        spare = re.findall(r'Available Spare[:\s]+(\d+)%', result)
                        if spare:
                            health['available_spare'] = int(spare[0])
                        # Data written (NVMe)
                        writ = re.findall(r'Data Units Written[:\s]+[\d,]+ \[([\d.]+ [GMTP]B)\]', result)
                        if writ:
                            health['data_written'] = writ[0]
                        read = re.findall(r'Data Units Read[:\s]+[\d,]+ \[([\d.]+ [GMTP]B)\]', result)
                        if read:
                            health['data_read'] = read[0]
                except Exception:
                    pass

                # Health status
                try:
                    h = os.popen(f'sudo smartctl -H {dev_path} 2>/dev/null').read()
                    if 'PASSED' in h:
                        health['status'] = 'PASSED'
                    elif 'FAILED' in h:
                        health['status'] = 'FAILED'
                    else:
                        health['status'] = 'UNKNOWN'
                except Exception:
                    health['status'] = 'UNKNOWN'

                drives.append({
                    'name': name,
                    'model': model,
                    'size': size,
                    'type': drive_type,
                    'interface': tran.upper(),
                    'health': health,
                })
    except Exception:
        pass
    return drives


def get_disk_stats():
    """Disk: usage, I/O stats."""
    stats = {}

    # Disk usage (df)
    mounts = []
    try:
        result = os.popen('df -B1 2>/dev/null').read()
        for line in result.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 6:
                fs = parts[0]
                total = int(parts[1])
                used = int(parts[2])
                avail = int(parts[3])
                pct = parts[4].rstrip('%')
                mnt = parts[5]

                # Skip virtual filesystems
                if fs.startswith(('tmpfs', 'devtmpfs', 'efivarfs', 'overlay', 'shm',
                                  'nsfs', 'proc', 'sysfs', 'cgroup', 'devpts')):
                    continue
                # Skip Docker overlay mounts
                if mnt.startswith('/var/lib/docker'):
                    continue
                # Skip /dev, /proc, /sys
                if mnt in ('/dev', '/proc', '/sys', '/run'):
                    continue

                mounts.append({
                    'filesystem': fs,
                    'mount': mnt,
                    'total': total,
                    'used': used,
                    'avail': avail,
                    'used_pct': int(pct) if pct else 0,
                })
    except Exception:
        pass
    stats['mounts'] = mounts

    # Disk I/O (from /proc/diskstats, aggregate by device)
    devices = {}
    try:
        with open('/proc/diskstats') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 14:
                    major = parts[0]
                    minor = parts[1]
                    name = parts[2]

                    # Only physical devices and md/dm
                    if not re.match(r'^(sd[a-z]+$|nvme\d+n\d+$|md\d+$|dm-\d+$)', name):
                        continue
                    # Skip partitions
                    if re.match(r'^(sd[a-z]+\d+|nvme\d+n\d+p\d+)$', name):
                        continue

                    reads = int(parts[3])
                    read_sectors = int(parts[5])
                    writes = int(parts[7])
                    write_sectors = int(parts[9])

                    devices[name] = {
                        'reads': reads,
                        'writes': writes,
                        'read_bytes': read_sectors * 512,
                        'write_bytes': write_sectors * 512,
                    }

        # Calculate I/O speed from history
        now = time.time()
        prev_disk = {}
        if DISK_HISTORY:
            prev_disk = DISK_HISTORY[-1]

        for name, data in devices.items():
            if name in prev_disk:
                dt = now - prev_disk.get('_ts', now)
                if dt > 0:
                    data['read_speed'] = round((data['read_bytes'] - prev_disk[name]['read_bytes']) / dt)
                    data['write_speed'] = round((data['write_bytes'] - prev_disk[name]['write_bytes']) / dt)
                else:
                    data['read_speed'] = 0
                    data['write_speed'] = 0
            else:
                data['read_speed'] = 0
                data['write_speed'] = 0

        current_disk = {name: {'read_bytes': d['read_bytes'], 'write_bytes': d['write_bytes']}
                        for name, d in devices.items()}
        current_disk['_ts'] = now
        DISK_HISTORY.append(current_disk)
    except OSError:
        pass
    stats['devices'] = devices
    stats['drives'] = get_drive_health()  # physical drive health

    return stats


def get_processes():
    """Process list sorted by CPU usage, with user and start time."""
    procs = []
    try:
        result = os.popen(
            "ps -eo pid,user,pcpu,pmem,start_time,comm --sort=-pcpu --no-headers 2>/dev/null"
        ).read()
        for line in result.strip().split('\n'):
            parts = line.strip().split(None, 5)
            if len(parts) >= 6:
                procs.append({
                    'pid': parts[0],
                    'user': parts[1],
                    'cpu': parts[2],
                    'mem': parts[3],
                    'start': parts[4],
                    'cmd': parts[5],
                })
    except Exception:
        pass
    return procs[:20]  # top 20


EXPORT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'export_config.json')


def read_export_config():
    """Read export path config."""
    try:
        with open(EXPORT_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'path': '/var/exports'}


def write_export_config(config):
    """Write export path config."""
    with open(EXPORT_CONFIG_PATH, 'w') as f:
        json.dump(config, f)


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/all')
def api_all():
    """All stats in one shot."""
    return jsonify({
        'cpu': get_cpu_stats(),
        'mem': get_mem_stats(),
        'net': get_net_stats(),
        'disk': get_disk_stats(),
        'traffic': get_traffic_stats(),
        'procs': get_processes(),
        'apps': get_combined_ranking('total', 12),
        'ts': time.time(),
    })


@app.route('/api/export/config', methods=['GET', 'POST'])
def export_config():
    """Get or set export path."""
    if request.method == 'POST':
        data = request.get_json() or {}
        new_path = data.get('path', '/var/exports')
        if not os.path.isabs(new_path):
            return jsonify({'ok': False, 'error': 'Path must be absolute'}), 400
        write_export_config({'path': new_path})
        return jsonify({'ok': True, 'path': new_path})
    config = read_export_config()
    return jsonify({'ok': True, 'path': config['path']})


@app.route('/api/export')
def api_export():
    """Export all history data to a file on the configured path."""
    config = read_export_config()
    base_dir = config['path']

    # Ensure export dir exists
    try:
        os.makedirs(base_dir, exist_ok=True)
    except OSError as e:
        return jsonify({'ok': False, 'error': f'Cannot create directory {base_dir}: {e}'}), 500

    # Collect current snapshots
    cpu = get_cpu_stats()
    mem = get_mem_stats()
    net = get_net_stats()
    disk = get_disk_stats()
    traffic = get_traffic_stats()
    procs = get_processes()
    apps = get_combined_ranking('total', 50)

    # Collect history data from deques
    net_history = list(NET_HISTORY)
    disk_history = list(DISK_HISTORY)
    cpu_history = {k: list(v) for k, v in CPU_HISTORY.items()}

    # Collect app usage DB data
    app_raw = []
    app_daily = []
    app_weekly = []
    app_monthly = []
    app_yearly = []
    try:
        usage_db = app_usage_tracker.DB_PATH
        if os.path.exists(usage_db):
            conn = sqlite3.connect(usage_db)
            c = conn.cursor()
            try:
                c.execute('SELECT ts, app, cpu_pct, mem_mb, net_rx, net_tx FROM snapshots ORDER BY ts DESC LIMIT 10000')
                app_raw = [{'ts': r[0], 'app': r[1], 'cpu_pct': r[2], 'mem_mb': r[3], 'net_rx': r[4], 'net_tx': r[5]} for r in c.fetchall()]
            except Exception:
                pass
            for table, key_col in [('daily_rollup', 'day'), ('weekly_rollup', 'week'),
                                    ('monthly_rollup', 'month'), ('yearly_rollup', 'year')]:
                try:
                    c.execute(f'SELECT {key_col}, app, cpu_avg, mem_avg, net_rx, net_tx FROM {table}')
                    rows = [{'period': r[0], 'app': r[1], 'cpu_avg': r[2], 'mem_avg': r[3], 'net_rx': r[4], 'net_tx': r[5]} for r in c.fetchall()]
                    if table == 'daily_rollup':
                        app_daily = rows
                    elif table == 'weekly_rollup':
                        app_weekly = rows
                    elif table == 'monthly_rollup':
                        app_monthly = rows
                    elif table == 'yearly_rollup':
                        app_yearly = rows
                except Exception:
                    pass
            conn.close()
    except Exception:
        pass

    # Collect traffic DB data
    traffic_raw = []
    traffic_daily = []
    traffic_monthly = []
    try:
        t_db = traffic_tracker.DB_PATH
        if os.path.exists(t_db):
            conn = sqlite3.connect(t_db)
            c = conn.cursor()
            try:
                c.execute('SELECT id, rx_base, tx_base FROM traffic_base')
                traffic_raw = [{'id': r[0], 'rx_base': r[1], 'tx_base': r[2]} for r in c.fetchall()]
            except Exception:
                pass
            try:
                c.execute('SELECT date, rx_base, tx_base FROM traffic_daily')
                traffic_daily = [{'date': r[0], 'rx_base': r[1], 'tx_base': r[2]} for r in c.fetchall()]
            except Exception:
                pass
            try:
                c.execute('SELECT month, rx_base, tx_base FROM traffic_monthly')
                traffic_monthly = [{'month': r[0], 'rx_base': r[1], 'tx_base': r[2]} for r in c.fetchall()]
            except Exception:
                pass
            conn.close()
    except Exception:
        pass

    export_data = {
        'export_time': time.time(),
        'export_time_human': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'machine': {
            'hostname': os.uname().nodename,
            'os': f"{os.uname().sysname} {os.uname().release}",
        },
        'current': {
            'cpu': cpu,
            'mem': mem,
            'net': net,
            'disk': disk,
        },
        'traffic': traffic,
        'processes': procs,
        'app_ranking': apps,
        'history': {
            'net': net_history,
            'disk_io': disk_history,
            'cpu_freq': cpu_history,
        },
        'app_usage_data': {
            'raw_snapshots': app_raw,
            'daily_rollup': app_daily,
            'weekly_rollup': app_weekly,
            'monthly_rollup': app_monthly,
            'yearly_rollup': app_yearly,
        },
        'traffic_archive': {
            'base': traffic_raw,
            'daily': traffic_daily,
            'monthly': traffic_monthly,
        },
    }

    # Write to file
    ts_human = datetime.now().strftime('%Y-%m-%d-%H%M%S')
    filename = f'fnmon-export-{ts_human}.json'
    filepath = os.path.join(base_dir, filename)
    try:
        with open(filepath, 'w') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        return jsonify({
            'ok': True,
            'filepath': filepath,
            'size_bytes': os.path.getsize(filepath),
            'records': {
                'app_raw': len(app_raw),
                'net_history': len(net_history),
                'disk_history': len(disk_history),
            }
        })
    except OSError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/stream')
def stream():
    """SSE — pushes all stats every INTERVAL seconds."""
    def generate():
        while True:
            collect_snapshot()  # periodic snapshot
            data = {
                'cpu': get_cpu_stats(),
                'mem': get_mem_stats(),
                'net': get_net_stats(),
                'disk': get_disk_stats(),
                'traffic': get_traffic_stats(),
                'procs': get_processes(),
                'apps': get_combined_ranking('total', 12),
                'ts': time.time(),
            }
            yield f'data: {json.dumps(data)}\n\n'
            time.sleep(UPDATE_INTERVAL)
    return Response(generate(), mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',
                        'X-Accel-Buffering': 'no',
                    })


@app.route('/api/apps/<period>')
def api_apps(period):
    """App ranking by period: total, day, week, month, year."""
    return jsonify(get_combined_ranking(period, 12))


@app.after_request
def add_headers(response):
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = "frame-ancestors *"
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response


if __name__ == '__main__':
    print('Starting fnOS System Monitor on http://0.0.0.0:5000')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
