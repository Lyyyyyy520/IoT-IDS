# IoT IDS Mobile（普通用户端）

Expo SDK 54 / React Native 0.81。当前入口仅有“首页、本人设备、设置”。旧管理员页面源码暂时归档，但不在导航或深链中注册；旧 Cookie 客户端已停用。APP 不提供设备管理、全局抓包、GNN 或事件处置。

Expo 的 `exp://<电脑局域网 IP>:8081` 用于打开 APP。进入 APP 配对页后，“服务器地址”应填写后端根地址，例如 `http://<电脑局域网 IP>:5000`；后端须监听局域网可访问的地址，手机或平板与电脑连接同一 Wi-Fi。

## 安装与离线检查

```powershell
cd IoT‑IDS‑Mobile
npm ci
npm test -- --watch=false
npm run typecheck
npx expo config --type public --json
```

依赖 `expo-secure-store` 保存轮换的 refresh token，`expo-crypto` 生成随机 `client_instance_id`；Jest、`jest-expo`、React Native Testing Library 和 `test-renderer` 用于测试。未执行 Android/iOS 原生构建时，不应把测试通过解释为真机安全存储已验证。

## 首次配对

管理员先在 Web 端建立 `user` 账号、授权设备/区域，并生成一次性配对码。APP 输入完整服务器根地址（例如 `https://ids.example.com`，不要附 `/api`）、配对码和用户确认的客户端名称。配对码只在表单内存中，成功或离开页面即清除；APP 不使用 Web 用户名/密码或 Cookie 登录。

开发和发行版本均支持 HTTP 与 HTTPS，无需额外开发开关；地址不能含用户名、密码、查询串、fragment 或路径。后端在所有环境默认支持 HTTP；若显式设置 `IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP=false`，HTTP 请求会返回 `https_required`。HTTPS 仍使用系统证书验证，APP 不忽略证书错误。

## 凭据分类与恢复

| 数据 | 存储 |
| --- | --- |
| access token | 进程内存；不持久化 |
| refresh token | Expo SecureStore：`iot_ids_mobile_refresh_v1` |
| 随机客户端 ID | Expo SecureStore：`iot_ids_mobile_client_instance_v1` |
| 服务器地址、兼容旧版的 HTTP 标志 | AsyncStorage：`iot_ids_mobile_server_v1`（非秘密） |
| 配对码 | 配对表单内存；不持久化 |
| overview | 进程内存；不持久化 |

启动先清理旧版本在 AsyncStorage 中的 Web Cookie 和可能的旧认证字段；不会自动迁移旧登录。若有 refresh token，则先轮换，再取 `/api/v3/mobile/session` 与 `/api/v3/mobile/overview`；离线时保留 refresh token，但不展示缓存的“安全正常”结论。并发 access 失效请求只发起一次 refresh，服务端返回新 refresh token 后，必须先写入 SecureStore，才更新内存 access token。refresh 重放、会话撤销或用户不再有资格时清除本机凭据并要求重新配对。

首页和设备列表只显示后端授权范围内的设备。本人设备可按名称/ID、类型、连接状态和已发布的活动提醒筛选；列表与关系视图使用相同结果。设备详情通过用户级 Bearer API 展示真实状态、留存遥测、温湿度历史、连接变化和流量摘要。关系图只展示授权设备之间真实记录的发送关系。提醒可按设备、时间、严重程度和状态筛选；详情只展示公开处理进度和已授权设备的历史回放，不提供管理员处置操作。

摄像头固件目前只发布 MQTT 心跳和遥测，仓库没有视频流/快照 API；APP 显示“未配置画面接口/当前无画面”，不显示占位影像。移动端没有 CSV 导出入口。协议/速率只来自已保存流量聚合；设备级端口、行为基线和模型异常结论未接入，不生成风险评分。

APP 前台每约 30 秒刷新 overview；从后台恢复时重新验证令牌并获取 session 和 overview。离线时可保留进程内最后一次真实数据，但明确标记过期。用户确认注销后立即清理本机凭据；服务端注销请求失败时提示管理员在 Web 端撤销。重置客户端还会清除随机客户端 ID。

真实联调前请确认服务端 HTTP/HTTPS 配置、v3 migration 已显式完成、管理员已设置授权范围、设备时钟/网络和真机 SecureStore 行为。不要对真实数据库使用测试脚本。

## Android 直装 APK（无需 Expo Go 或 EAS）

本项目仍使用 Expo SDK/React Native 作为代码框架，但生成的 APK 是独立安装包，手机上不需要 Expo Go，也不需要 EAS 云构建。

Windows 本地构建需要 Android SDK、Android NDK、CMake 和 JDK 21。首次生成或 app.json 原生配置变更后，在本目录执行：

```powershell
npx expo prebuild --platform android
cd android
.\gradlew.bat assembleRelease
```

APK 输出在 `android/app/build/outputs/apk/release/app-release.apk`。当前生成的 Gradle 工程使用 debug 签名配置，适合私下安装试用；正式商店发布需要设置自己的签名密钥并妥善保管。

iPhone 包后续需在 Mac 上用 Xcode 编译和签名；可通过 TestFlight 或登记设备的 Ad Hoc 方式安装。Windows 不能直接编译 iPhone 原生包。

安装包不会内置后端。正式配对需要可从手机访问的 HTTP 或 HTTPS 服务：管理员在 Web 端创建 `user` 账号、授权设备/区域并生成一次性配对码；APP 输入完整服务器根地址（不要附 `/api`）、配对码和用户确认的客户端名称。仓库的一键演示服务只监听 `127.0.0.1`；手机连接时需将后端配置为监听局域网可访问的地址。原生网络配置变更后需要重新构建安装包。
