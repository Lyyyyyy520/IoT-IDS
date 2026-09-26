# MQTT 心跳订阅客户端

## 1. 安全边界与默认行为

`ManagedMqttHeartbeatSubscriber` 是显式启停的网络适配层。模块导入不会加载
Paho、创建客户端、启动线程或连接 Broker；`IOT_IDS_MQTT_ENABLED` 默认是
`false`。`create_app()` 只把服务工厂放入 app extension；正式入口在验证数据库已存在
且 migration 1～3 checksum 正确后，显式调用 `start_runtime_services(app)`，并在退出
时调用 `stop_runtime_services(app)`。

客户端只订阅 `community/+/status`，QoS 固定为 1。它不会发布设备 control。
Mosquitto 用户名、密码和 ACL 认证发布者；应用层仍然只能校验 topic、payload、
MAC 与数据库绑定，MQTT 回调通常不能读取发布者用户名，不能用应用层校验替代 ACL。

## 2. 配置

| 环境变量 | 默认值 | 约束 |
|---|---:|---|
| `IOT_IDS_MQTT_ENABLED` | `false` | 只有明确设为 true 才能连接 |
| `IOT_IDS_MQTT_HOST` | 空 | 启用时必填 |
| `IOT_IDS_MQTT_PORT` | `8883` | 1～65535 |
| `IOT_IDS_MQTT_BACKEND_USERNAME` | 空 | 启用时必填；后台只读订阅账号 |
| `IOT_IDS_MQTT_BACKEND_PASSWORD` | 空 | 启用时必填；不会进入 repr、日志或异常 |
| `IOT_IDS_MQTT_CLIENT_ID` | `iot-ids-heartbeat-subscriber` | 每个运行实例唯一 |
| `IOT_IDS_MQTT_KEEPALIVE` | `60` | 10～3600 秒 |
| `IOT_IDS_MQTT_QOS` | `1` | 当前只允许 1 |
| `IOT_IDS_MQTT_TLS_ENABLED` | `true` | 关闭时产生明文局域网警告 |
| `IOT_IDS_MQTT_CA_FILE` | 空 | TLS 启用时必须指向存在的 CA 文件 |
| `IOT_IDS_MQTT_QUEUE_SIZE` | `256` | 1～10000 |
| `IOT_IDS_MQTT_RECONNECT_MIN_SECONDS` | `1` | 大于 0 |
| `IOT_IDS_MQTT_RECONNECT_MAX_SECONDS` | `60` | 不小于最小值，且不超过 3600 |
| `IOT_IDS_MQTT_RECONNECT_JITTER` | `0.2` | 0～0.5 |

TLS 使用系统的服务器证书和主机名验证，并加载指定 CA；代码没有
`tls_insecure_set(true)` 路径。隔离可信局域网可以显式配置明文 MQTT，但不能把
1883 端口暴露到公网，日志会持续明确提示该连接仅允许用于隔离网络。

部署依赖已记录为 `paho-mqtt>=2.1,<3.0`。未启用 MQTT 时不要求运行环境已经导入
Paho；部署启用前执行项目既有依赖安装流程，不要在运行中动态下载依赖。

## 3. 生命周期与线程模型

1. `start()` 读取并验证配置；禁用、配置错误、依赖缺失或 Flask debug reloader
   父进程均不会创建网络线程。
2. 启动后组件先写为 `warming_up`，创建一个有界消息队列和独立工作线程，再调用
   Paho 的异步连接与网络循环。
3. 连接成功时只请求订阅 `community/+/status`；收到成功 SUBACK 后才写为 `ready`。
4. 断线或连接失败写为 `degraded`。Paho 后台重连使用有上限的指数退避，每次加入
   配置比例的随机抖动；重新连接并成功 SUBACK 后恢复 `ready`。
5. `stop()` 先禁止新消息入队，再断开客户端、停止网络循环、丢弃尚未处理的排队
   心跳、发送工作线程停止标记并等待退出。重复 `start()`/`stop()` 是幂等的。

Flask debug reloader 父进程不会启动 subscriber，只有带 `WERKZEUG_RUN_MAIN=true`
的实际服务子进程可以启动。当前 subscriber 必须保持单实例：多进程 WSGI 不能在每个
Web worker 调用运行时启动函数，应另设一个专用 subscriber worker，或确保只有一个
指定进程调用它。

网络回调不执行 heartbeat SQLite ingestion，也不直接写组件健康表。普通消息进入
有界队列，健康变化进入单独的小型有界事件队列，由工作线程完成数据库写入。

## 4. QoS、retain 与背压

- QoS 1 允许 Broker 重发；客户端不按 MQTT `dup` 标志猜测，重复消息交给既有
  `MqttHeartbeatIngestor`，由 `boot_id + sequence` 游标可靠拒绝。
- 状态心跳发布时必须使用 retain=false。即使 Broker 错误保留消息，订阅回调也会
  在入队前拒绝 retained 消息，旧心跳不能在后端重启后刷新在线时间。
- 消息队列使用 `put_nowait`。队列满时立即丢弃新消息，不阻塞 Paho 网络线程，
  组件转为 `degraded`，原因是 `message_queue_overflow`。
- 单个校验拒绝或 ingestion 异常不会终止工作线程；日志只含稳定结果码和异常类型，
  不含完整 payload、密码或第三方异常文本。

## 5. Mosquitto 身份与 ACL

参考 `edge/mosquitto/acl-iot-ids.example`：

- 后台订阅账号只有 `topic read community/+/status`；
- 每台设备使用独立用户名，只有自己的 status 写权限和自己的 control 读权限；
- 后台账号不获得 control 发布权限；未来需要控制发布时使用另一套独立身份和 ACL；
- 不使用会意外把后台账号包含在内的全局宽泛写入 pattern。

修改 ACL 后，应先用 `mosquitto_sub`/`mosquitto_pub` 在隔离环境验证允许与拒绝矩阵，
再启用后端订阅客户端。
