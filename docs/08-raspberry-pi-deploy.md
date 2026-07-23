# 树莓派探针部署完整步骤

---

## 需要的东西

- 树莓派 4B（已装好系统、能联网）
- Windows 电脑（你的主力机，已装好本项目）
- 一根网线或同一个 WiFi

---

## 第一步：确认两台机器能互通

### 1.1 在 Windows 上查 IP
按 `Win+R` → 输入 `cmd` → 回车 → 输入：
```
ipconfig
```
找到 `无线局域网适配器 WLAN` 或 `以太网适配器` 下面的 `IPv4 地址`，比如 `192.168.1.100`，**记下来**。

### 1.2 在树莓派上查 IP  
树莓派打开终端，输入：
```bash
hostname -I
```
会显示树莓派的 IP，比如 `192.168.1.101`。

### 1.3 验证互通
Windows 终端里输入：
```
ping 192.168.1.101
```
把 IP 换成树莓派的。看到"回复 from..."就是通的。

---

## 第二步：在树莓派上装软件

树莓派打开终端，一行一行执行：

```bash
# 1. 更新系统
sudo apt update
sudo apt upgrade -y

# 2. 安装 pip
sudo apt install python3-pip -y

# 3. 安装本项目需要的 Python 包
pip3 install numpy requests
```

---

## 第三步：拷贝项目文件到树莓派

### 方法 A：用 U 盘（最简单）
1. U 盘插到 Windows 电脑上
2. 在 U 盘根目录新建文件夹叫 `iot-ids`
3. 拷贝以下 3 个文件到 U 盘的 `iot-ids` 文件夹里：

| Windows 上的位置 | 拷到 U 盘 |
|-----------------|-----------|
| `D:\Project\iot-ids\edge\probe_client.py` | `iot-ids\probe_client.py` |
| `D:\Project\iot-ids\backend\models\inference.py` | `iot-ids\inference.py` |
| `D:\Project\iot-ids\backend\data\best_model.onnx` | `iot-ids\best_model.onnx` |

4. 拔出 U 盘，插到树莓派上
5. 树莓派终端：
```bash
cp -r /media/pi/*/iot-ids ~/iot-ids
```

### 方法 B：用 scp 网络传输
在 Windows 终端（不是树莓派）输入：
```bash
scp D:\Project\iot-ids\edge\probe_client.py pi@192.168.1.101:~/iot-ids/
scp D:\Project\iot-ids\backend\models\inference.py pi@192.168.1.101:~/iot-ids/
scp D:\Project\iot-ids\backend\data\best_model.onnx pi@192.168.1.101:~/iot-ids/
```
把 `192.168.1.101` 换成树莓派 IP，密码是树莓派登录密码（默认 `raspberry`）。

---

## 第四步：Windows 防火墙上打开端口

Windows 终端（**以管理员身份运行**），输入：
```bash
netsh advfirewall firewall add rule name="IoT IDS 5000" dir=in action=allow protocol=tcp localport=5000
```
看到"确定"就成功了。

---

## 第五步：在 Windows 上启动后端

打开终端，输入：
```bash
cd D:\Project\iot-ids\backend
python app.py
```
看到 `Running on http://0.0.0.0:5000` 就是成功了。**这个终端别关**。

---

## 第六步：在树莓派上启动探针

树莓派终端：
```bash
cd ~/iot-ids
python3 probe_client.py --server http://192.168.1.100:5000 --name Pi-楼道A
```
- `192.168.1.100` 换成**你的 Windows IP**（第一步查到的）
- `Pi-楼道A` 可以自己改名字，比如 `Pi-摄像头旁`、`Pi-门禁旁`

看到 `Probe registered` 或类似输出说明连上了。

---

## 第七步：验证结果

打开浏览器 → `http://localhost:3000` → 

1. **资产监控** → 应该出现 `Pi-楼道A` 这个设备
2. **告警中心** → 筛选"真实数据" → 能看到树莓派推送的告警
3. **流量分析** → 切换到"实时网卡检测"

---

## 常见问题

| 问题 | 解决 |
|------|------|
| 树莓派连不上 Windows | 关掉 Windows 防火墙试一下 |
| probe_client.py 报错 | 确认 `best_model.onnx` 和 `inference.py` 在同一个目录 |
| 看不到告警 | 检查 Windows 后端终端有没有报错 |
