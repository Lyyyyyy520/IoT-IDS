# 17 - 现场演示执行清单（6 台物理设备版）

> 状态：操作手册。更新日期：2026-10-09。
> 用途：答辩/展示当天，从零把 6 台物理设备 + Pi 网关 + 前端跑通完整 IDS 闭环。

## 0. 关键配置备忘（勿改）

| 项 | 值 |
|----|----|
| 热点 | `iot-community` / 密码 `12345678` |
| Pi 网关 | `192.168.4.1`（子网 `192.168.4.x`，模型硬编码，勿改网段） |
| 后端 | Pi 上 `tmux ids`，端口 5000，`run_backend_pi.sh` |
| 前端 | 笔记本 `npm run dev`，端口 3000，代理 `/api` → `http://192.168.4.1:5000` |
| 管理员 | `admin`（密码现场自设，≥12 位） |
| 6 台设备 IP | .10 camera / .11 door / .12 light / .13 plug / .14 sensor / .15 speaker |
| 受害机 | `192.168.4.200`（第二台设备静态配置） |

## 1. 开场前准备（家里/宿舍做完）

- [ ] 6 块板烧录（**Flash Mode = DIO**，`LINK_SIM_MODE=1`，各自 `device_secrets.h`），烧完贴标签
- [ ] 接执行器（door 舵机 GPIO0 / light LED GPIO1 / plug 继电器 GPIO2 / speaker 蜂鸣器 GPIO4 / camera 云台 GPIO2 / sensor DHT11 GPIO3）
- [ ] **plug、camera 的 GPIO2 上电实测**：起不来就换 GPIO5/6/7/10
- [ ] 板子全部上电，串口 115200 确认 `设备启动: <id> 联动仿真:1`
- [ ] Pi 跑 `sudo bash edge/setup_pi_gateway.sh --with-backend`（生成密码对照表）
- [ ] 前端 `npm install` 完成
- [ ] 受害机脚本 `edge/victim_udp_counter.py` 就绪

## 2. 现场搭建（到场地后）

1. Pi 上电 → 等热点 `iot-community` 出现
2. 笔记本连 `iot-community`，静态配 `192.168.4.9`
3. 受害机（第二台设备）静态配 `192.168.4.200`，跑 `victim_udp_counter.py`
4. Pi 上启动后端：`sudo tmux new -s ids` → `sudo bash /home/pi/iot-ids/backend/run_backend_pi.sh`
5. 验证：`curl http://192.168.4.1:5000/api/health` 返回 ok
6. 笔记本启动前端：`cd d:/Project/iot-ids/frontend && npm run dev`（默认代理到 192.168.4.1）
7. 6 块板上电（**统一上电 = 演示起点**）

## 3. 演示流程（约 20 分钟）

1. 前端登录 admin → `/devices`「待确认设备」认领 6 台（照抄表：device_id / 显示名 / device_type；档案来源=physical）
2. **认领完 → 6 块板全部断电重启一次**（否则 `invalid_initial_sequence` 不显示在线）
3. 确认 6 台全部 `online`（`/devices`「可信设备」）
4. `/monitor` 看拓扑：6 节点 + 6 条联动边
5. **提前 5 分钟开抓包**：登录 admin 后 `POST /api/capture/start {"use_scapy":true,"interface":"wlan0"}`（或跑 `backend/demo_detect_real.bat`）
6. 攻击：`mosquitto_pub -h 192.168.4.1 -u iot-ids-backend -P <密码> -t community/door-01/control -m attack`
   - 受害机计数器爬升 + door 舵机"自己开门"
7. 等 30~60 秒 → `POST /api/device/detect` → door-01 判红、其余绿
8. `/incidents` 看事件 → `block` 处置（舵机锁死）→ `normal` 恢复 → 结案
9. （可选）移动端 `/mobile-access` 出配对码

## 4. 关键坑速查（今天踩过的）

| 现象 | 原因 | 处理 |
|------|------|------|
| 上传成功但程序不跑/无串口/不连网 | **Flash Mode 用了 QIO** | 改 DIO 重烧 |
| 认领后设备不显示在线 | 认领后没重启板子（序号不是 0/1） | 6 板全部断电重启 |
| MQTT `not authorised` | 密码文件带 `\r` 或哈希不一致 | 重建 passwd + `tr -d '\r'`（脚本已内置） |
| 串口看不到 | 打开监视器晚了/端口错/波特率非 115200 | 先开监视器再按 RST |
| 判红不出现/全橙 | 窗口未满 300s 或模型未做真实流量校准 | 提前 5 分钟开抓包；必要时做校准 |
| 攻击洪水看不到 | 受害机 .200 不存在（ARP 失败） | 第二台设备静态配 .200 + 计数器 |

## 5. 每场演示之间

- 后端**换全新数据库**（或把旧事件结案），避免上场的残留事件
- 板子断电重启（新会话、序号从 1）
- 前端代理已默认指向 192.168.4.1（宿舍验证时才需临时切 192.168.1.78）
