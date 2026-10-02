# IoT IDS Mobile（普通用户端）

Expo SDK 57 / React Native 0.86。当前入口仅有“首页、本人设备、设置”。旧管理员页面源码暂时归档，但不在导航或深链中注册；旧 Cookie 客户端已停用。APP 不提供设备管理、全局抓包、GNN 或事件处置。

## Windows 上用 iPhone 预览（Expo Go）

不需要 Mac 或 Apple Developer 付费会员。先在 iPhone App Store 安装或更新最新版 Expo Go；Expo Go 当前支持 SDK 57。Expo Go 和命令行需要登录同一个 Expo 账号。

在 Windows PowerShell 中进入本目录，执行：

```powershell
npm ci
npx expo login
npx expo start
```

如果命令行已经登录，可跳过 `npx expo login`。确认手机和电脑连在同一个 Wi‑Fi，在 iPhone 相机中扫描终端显示的二维码，并选择用 Expo Go 打开。电脑上的终端和 Metro 服务要保持运行。若局域网扫描无法连接，可停止服务后改用 `npx expo start --tunnel`。

这会在 Expo Go 里临时运行开发版，不会生成可独立安装的 IPA；关闭电脑上的服务后，手机就无法继续打开此开发预览。要连接后端时，服务器地址也必须能从手机访问；`127.0.0.1` 指向手机自身，不能用来访问电脑上的后端。

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

生产地址必须为 HTTPS，不能含用户名、密码、查询串、fragment 或路径。仅 Expo 开发模式可由用户明确开启隔离局域网 HTTP；界面持续警告，且只接受本机/私有局域网主机。服务端仍可能返回 `https_required`（当前后端仅允许 loopback 或测试环境的不安全 HTTP），这不是证书忽略开关。APP 不信任任意自签名证书或代理。

## 凭据分类与恢复

| 数据 | 存储 |
| --- | --- |
| access token | 进程内存；不持久化 |
| refresh token | Expo SecureStore：`iot_ids_mobile_refresh_v1` |
| 随机客户端 ID | Expo SecureStore：`iot_ids_mobile_client_instance_v1` |
| 服务器地址、开发 HTTP 标志 | AsyncStorage：`iot_ids_mobile_server_v1`（非秘密） |
| 配对码 | 配对表单内存；不持久化 |
| overview | 进程内存；不持久化 |

启动先清理旧版本在 AsyncStorage 中的 Web Cookie 和可能的旧认证字段；不会自动迁移旧登录。若有 refresh token，则先轮换，再取 `/api/v3/mobile/session` 与 `/api/v3/mobile/overview`；离线时保留 refresh token，但不展示缓存的“安全正常”结论。并发 access 失效请求只发起一次 refresh，服务端返回新 refresh token 后，必须先写入 SecureStore，才更新内存 access token。refresh 重放、会话撤销或用户不再有资格时清除本机凭据并要求重新配对。

首页和设备列表只显示后端授权范围内的设备。本人设备可按名称/ID、类型、连接状态和已发布的活动提醒筛选；列表与关系视图使用相同结果。设备详情通过用户级 Bearer API 展示真实状态、留存遥测、温湿度历史、连接变化和流量摘要。关系图只展示授权设备之间真实记录的发送关系。提醒可按设备、时间、严重程度和状态筛选；详情只展示公开处理进度和已授权设备的历史回放，不提供管理员处置操作。

摄像头固件目前只发布 MQTT 心跳和遥测，仓库没有视频流/快照 API；APP 显示“未配置画面接口/当前无画面”，不显示占位影像。移动端没有 CSV 导出入口。协议/速率只来自已保存流量聚合；设备级端口、行为基线和模型异常结论未接入，不生成风险评分。

APP 前台每约 30 秒刷新 overview；从后台恢复时重新验证令牌并获取 session 和 overview。离线时可保留进程内最后一次真实数据，但明确标记过期。用户确认注销后立即清理本机凭据；服务端注销请求失败时提示管理员在 Web 端撤销。重置客户端还会清除随机客户端 ID。

真实联调前请确认服务端 HTTPS、v3 migration 已显式完成、管理员已设置授权范围、设备时钟/网络和真机 SecureStore 行为。不要对真实数据库使用测试脚本。本轮未连接真实后端、Broker 或探针。

## Android 直装 APK（无需 Expo Go 或 EAS）

本项目仍使用 Expo SDK/React Native 作为代码框架，但生成的 APK 是独立安装包，手机上不需要 Expo Go，也不需要 EAS 云构建。

Windows 本地构建需要 Android SDK、Android NDK、CMake 和 JDK 17。项目和 Android SDK 路径建议只使用英文/ASCII 字符；含中文或特殊字符时，Gradle/NDK 的 CMake、Ninja 子进程可能无法处理路径。首次生成或 app.json 原生配置变更后，在本目录执行：

```powershell
npx expo prebuild --platform android
cd android
.\gradlew.bat assembleRelease
```

APK 输出在 `android/app/build/outputs/apk/release/app-release.apk`；已生成的 `dist/IoT-IDS-Android.apk` 仍可安装，但它是在本次 SDK 升级前构建的。要生成匹配 SDK 57 的新 APK，先运行 `npx expo prebuild --platform android --clean`，再运行上面的 Gradle 命令。手机上可下载或通过 USB/文件传输复制 APK，打开后按提示允许当前文件管理器安装应用并完成安装。当前使用 debug 签名，适合私下安装试用；正式商店发布需要设置自己的签名密钥并妥善保管。

iPhone 原生 Xcode 工程已生成在 ``ios/IoTIDS.xcodeproj``。Windows 不能编译 iOS 应用。该工程是在 SDK 54 时生成的；以后在 Mac 上做原生个人测试时，先运行 ``npm ci`` 和 ``npx expo prebuild --platform ios --clean`` 以 SDK 57 重建工程，再运行 ``npx expo run:ios --device``。免费 Personal Team 的签名配置 7 天后过期，需要重新构建并安装；它适合个人测试。TestFlight、Ad Hoc 或 App Store 分发需要加入 Apple Developer Program。

安装包不会内置后端。正式配对需要可从手机访问的 HTTPS 服务：管理员在 Web 端创建 `user` 账号、授权设备/区域并生成一次性配对码；APP 输入完整 HTTPS 服务器根地址（不要附 `/api`）、配对码和用户确认的客户端名称。仓库的一键演示服务只监听 `127.0.0.1`，且发布版 APP 不允许 HTTP，因此不能直接用它连接手机。
