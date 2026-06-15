#!/usr/bin/env python3
"""Generate a self-contained fpk for distribution — includes app code + static assets."""

import os, re, struct, zlib, tarfile, io, json, hashlib, time as tmod

OUTDIR = '/vol2/1000/HD2/fpk'
APP_NAME = 'fnmon'
APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUTDIR, exist_ok=True)

# === Auto-version ===
latest_ver = '0.0.0'
# Also account for legacy cpu-monitor-v* fpks
for f in os.listdir(OUTDIR):
    m = re.match(r'(?:fnmon|cpu-monitor)-v(\d+\.\d+\.\d+)\.fpk', f)
    if m:
        v = tuple(int(x) for x in m.group(1).split('.'))
        if v > tuple(int(x) for x in latest_ver.split('.')):
            latest_ver = m.group(1)
v_parts = [int(x) for x in latest_ver.split('.')]
v_parts[2] += 1
NEW_VER = '.'.join(str(x) for x in v_parts)
OUTPATH = f'{OUTDIR}/fnmon-v{NEW_VER}.fpk'

now_ts = int(tmod.time())


def add_file(tar, path, content=None, filepath=None, mode=0o644):
    """Add a file to the tar archive."""
    info = tarfile.TarInfo(name=f'{APP_NAME}/{path}')
    if filepath:
        with open(filepath, 'rb') as f:
            data = f.read()
    elif isinstance(content, bytes):
        data = content
    else:
        data = content.encode('utf-8')
    info.size = len(data)
    info.mode = mode
    info.mtime = now_ts
    info.uid = info.gid = 0
    info.uname = 'root'
    info.gname = 'root'
    tar.addfile(info, io.BytesIO(data))


# === Build manifest ===
manifest_content = f'''appname               = {APP_NAME}
version               = {NEW_VER}
display_name          = fnmon - 系统监视器
desc                  = 实时系统监控面板 - CPU/内存/网络/磁盘/进程 全能监控
platform              = all
source                = thirdparty
maintainer            = Hermes
maintainer_url        = https://github.com/nousresearch/hermes-agent
distributor           = Community
os_min_version        = 0.8.1
desktop_uidir         = ui
desktop_applaunchname = {APP_NAME}.Application
service_port          = 5000
checkport             = true
ctl_stop              = true
'''
manifest_checksum = hashlib.md5(manifest_content.encode('utf-8')).hexdigest()
manifest_full = manifest_content + f'checksum              = {manifest_checksum}\n'

# === Build tar ===
with tarfile.open(OUTPATH, 'w:gz') as tar:
    add_file(tar, 'manifest', manifest_full)

    # Config
    add_file(tar, 'config/privilege', json.dumps({
        'defaults': {'run-as': 'root'},
    }, indent=2))
    add_file(tar, 'config/resource', '{\n    "systemd-unit": {}\n}\n')

    # Lifecycle scripts
    scripts = {
        'install_init': '#!/bin/bash\nexit 0\n',
        'install_callback': f'''#!/bin/bash
# Create and enable systemd service
cat > /etc/systemd/system/{APP_NAME}.service << 'SERVEOF'
[Unit]
Description=fnmon - 系统监视器
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/apps/{APP_NAME}
ExecStart=/usr/bin/python3 /var/apps/{APP_NAME}/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVEOF
systemctl daemon-reload
systemctl enable {APP_NAME}.service
systemctl start {APP_NAME}.service
exit 0
''',
        'uninstall_init': f'#!/bin/bash\nsystemctl stop {APP_NAME}.service 2>/dev/null\nexit 0\n',
        'uninstall_callback': f'''#!/bin/bash
systemctl disable {APP_NAME}.service 2>/dev/null
rm -f /etc/systemd/system/{APP_NAME}.service
systemctl daemon-reload
exit 0
''',
        'upgrade_init': '#!/bin/bash\nexit 0\n',
        'upgrade_callback': f'#!/bin/bash\nsystemctl restart {APP_NAME}.service 2>/dev/null\nexit 0\n',
        'config_init': '#!/bin/bash\nexit 0\n',
        'config_callback': '#!/bin/bash\nexit 0\n',
    }
    for name, content in scripts.items():
        add_file(tar, f'cmd/{name}', content, mode=0o755)

    # Main lifecycle
    add_file(tar, 'cmd/main', f'''#!/bin/bash
case $1 in
    start)
        systemctl start {APP_NAME}.service 2>/dev/null
        sleep 2
        curl -s -o /dev/null http://localhost:5000 && exit 0
        exit 1
        ;;
    stop)  exit 0 ;;
    status)
        curl -s -o /dev/null http://localhost:5000 && exit 0 || exit 3
        ;;
    *) exit 1 ;;
esac
''', mode=0o755)

    # === Bundle app code ===
    add_file(tar, 'app.py', filepath=os.path.join(APP_DIR, 'app.py'), mode=0o755)
    add_file(tar, 'traffic_tracker.py', filepath=os.path.join(APP_DIR, 'traffic_tracker.py'), mode=0o644)
    add_file(tar, 'app_usage_tracker.py', filepath=os.path.join(APP_DIR, 'app_usage_tracker.py'), mode=0o644)
    add_file(tar, 'static/index.html', filepath=os.path.join(APP_DIR, 'static/index.html'), mode=0o644)

    # UI config
    add_file(tar, 'ui/index.json', json.dumps({
        APP_NAME: {
            'application': {
                'type': 'url',
                'protocol': 'http',
                'port': 5000,
                'url': 'http://${host}:5000',
                'title': 'fnmon - 系统监视器',
                'description': '实时系统监控面板'
            }
        }
    }, ensure_ascii=False, indent=2))

    # Icons
    for fname, fpk_name in [(f'{APP_NAME}-icon-128.png', 'ICON.PNG'),
                              (f'{APP_NAME}-icon-256.png', 'ICON_256.PNG')]:
        ipath = os.path.join(OUTDIR, fname)
        if os.path.exists(ipath):
            add_file(tar, fpk_name, filepath=ipath)
        else:
            # Fallback: simple blue icon
            add_file(tar, fpk_name, _gen_png(128 if '128' in fname else 256, (80, 120, 255)))

# === Done ===
size = os.path.getsize(OUTPATH)
print(f'✅ fpk created: {OUTPATH} ({size} bytes)')
print(f'   version: v{NEW_VER} (previous: v{latest_ver})')
print(f'   checksum: {manifest_checksum}')
print(f'   bundled: app.py + frontend + trackers')

with tarfile.open(OUTPATH) as t:
    for m in t.getmembers():
        print(f'   {m.name}: {m.size}B')


def _gen_png(size, color):
    """Quick PNG generator (fallback if no icon)."""
    r, g, b = color
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    hdr = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
    raw = b''
    for y in range(size):
        raw += b'\x00'
        for x in range(size):
            d = ((x-size/2)**2 + (y-size/2)**2) ** 0.5
            rad = size/2 - 4
            if d < rad:
                t = d / rad
                raw += struct.pack('BBB', int(r-(r-50)*t), int(g-(g-100)*t), int(b-(b-200)*t))
            else:
                raw += b'\x00\x00\x00'
    return hdr + ihdr + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
