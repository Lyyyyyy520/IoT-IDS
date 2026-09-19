# 阶段 0～1 测试与准入计划

## 1. 当前基线

- Python 源码使用 AST 解析作为无副作用语法检查。
- 后端新增 pytest 契约测试，不加载或创建真实 `backend/data/ids.db`。
- Web 使用 `npm run typecheck` 和 `npm run build`。
- APP 使用 `npm run typecheck`；依赖未安装时不得声称已验证。
- 训练数据不在仓库中，现有 GNN 指标在数据恢复前视为不可复现历史记录。

## 2. 每次提交的快速门禁

```powershell
python -m pytest backend/tests -q
python -c "import ast,pathlib; files=[p for r in ['backend','edge','training'] for p in pathlib.Path(r).rglob('*.py')]; [ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p)) for p in files]; print(len(files))"
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix IoT‑IDS‑Mobile run typecheck
```

## 3. 阶段 1 后端自动化测试

测试必须使用临时 SQLite 文件或内存数据库：

- v3 建表和迁移重复执行不破坏数据；旧表不被删除。
- `plan` 严格只读；不存在路径、非 SQLite 文件和活动 WAL 边车会被拒绝。
- `apply` 先用 SQLite backup API 生成并验证备份，迁移失败时完整回滚。
- 迁移版本/checksum 不重复登记；旧 `assets` 身份冲突只报告、不自动导入。
- device_id、MAC 唯一性和 IP 变化不创建重复设备。
- online → stale → offline、重连、维护、停用和预热转换。
- admin/operator/user 权限矩阵及 user 对设备 ID 的越权访问。
- 未认证探针、错误令牌、重复序号、乱序时间、超大批次全部拒绝。
- 退役保留历史；有引用设备不能彻底删除。
- API 故障响应中不出现演示数据。

## 4. 集成测试

- 本地 Mosquitto 启用密码和 ACL，验证设备只能写自己的 status 主题。
- REST 首次快照后通过 SSE 接收增量；断线重连不会用旧版本覆盖新状态。
- APP SSE 失败后回退约 5 秒轮询，回前台先重新拉快照。
- MQTT、探针、抓包、GNN 任一故障时只降级对应组件，不显示全绿。

## 5. 固定回放样本

需要建立并版本化元数据、不保存敏感载荷的五组夹具：正常心跳、设备断电、外部源
攻击本地设备、本地感染设备向外攻击、本地设备互相攻击。每组断言受影响 device_id、
角色、状态转换、事件合并和恢复结果。

## 6. 硬件安全准入

任何攻击实验前必须人工签核：

1. 固件目标是允许 CIDR 内的本地流量接收器，不是公网地址。
2. 树莓派转发/防火墙规则阻止实验流量离开测试网段。
3. MQTT 匿名连接关闭，设备 ACL 生效。
4. 攻击速率和最长时间硬限制生效，断电和 `normal` 指令均可停止。
5. 第一版不会自动发送 `block` 或断电指令。

未完成以上签核时，只能运行离线回放和不发包的影子检测。
