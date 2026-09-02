"""
用国内镜像（ghproxy.com）下载 ESP32 3.3.11 平台文件，并校验大小，
残缺的文件自动删除重下。

用法:
    python download_esp32_platform.py
"""
import json
import os
import urllib.request

STAGING = os.path.join(os.path.expandvars(r'%LOCALAPPDATA%'), 'Arduino15', 'staging', 'packages')
os.makedirs(STAGING, exist_ok=True)

GH_PROXIES = [
    'https://ghfast.top/',
    'https://gh-proxy.com/',
    'https://ghproxy.net/',
]

INDEX_URL = 'https://espressif.github.io/arduino-esp32/package_esp32_index.json'
TARGET_VERSION = '3.0.7'


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def download(url, dest):
    for proxy in GH_PROXIES:
        try:
            req = urllib.request.Request(proxy + url, headers={'User-Agent': 'Mozilla/5.0'})
            urllib.request.urlretrieve(proxy + url, dest)
            if os.path.getsize(dest) > 0:
                return True
        except Exception as e:
            print(f'    [镜像失败] {proxy}: {str(e)[:50]}')
    return False


def main():
    print('获取索引...')
    data = fetch_json(INDEX_URL)
    pkg = data['packages'][0]
    platform = next(p for p in pkg['platforms'] if p['version'] == TARGET_VERSION)

    # (filename, url, expected_size)
    files = []

    # 1. 核心包
    core_url = platform['url']
    core_size = int(platform.get('size', 0) or 0)
    files.append((os.path.basename(core_url), core_url, core_size))

    # 2. 所有工具（含芯片库），从 systems 取准确 URL + size
    dep_map = {(t['name'], t['version']) for t in platform['toolsDependencies']}
    for tool in pkg['tools']:
        if (tool['name'], tool['version']) not in dep_map:
            continue
        for s in tool['systems']:
            host = s.get('host', '').lower()
            if 'mingw' in host or 'windows' in host:
                files.append((os.path.basename(s['url']), s['url'], int(s.get('size', 0) or 0)))
                break

    print(f'共 {len(files)} 个文件，缓存目录: {STAGING}\n')

    ok = 0
    fail = []
    for i, (fn, url, expected) in enumerate(files, 1):
        dest = os.path.join(STAGING, fn)
        # 校验：文件存在且大小和预期一致（允许 1KB 误差）
        if os.path.exists(dest):
            actual = os.path.getsize(dest)
            if expected > 0 and abs(actual - expected) <= 1024:
                print(f'[{i}/{len(files)}] 完整 {fn}')
                ok += 1
                continue
            else:
                print(f'[{i}/{len(files)}] 残缺 {fn} (实际 {actual} 字节, 预期 {expected})，删除重下')
                os.remove(dest)

        print(f'[{i}/{len(files)}] 下载 {fn} ...')
        if download(url, dest):
            actual = os.path.getsize(dest)
            if expected > 0 and abs(actual - expected) > 1024 * 10:  # 10KB 误差
                print(f'    警告: 大小不匹配 (实际 {actual}, 预期 {expected})')
            print(f'    完成 ({actual // 1024 // 1024} MB)')
            ok += 1
        else:
            print(f'    失败!')
            fail.append(fn)

    print(f'\n=== 完成: {ok}/{len(files)} 个文件 ===')
    if fail:
        print('失败的文件:')
        for fn in fail:
            print(f'  - {fn}')
    else:
        print('全部下载完成！回到 Arduino IDE 重新安装 ESP32 即可。')


if __name__ == '__main__':
    main()
