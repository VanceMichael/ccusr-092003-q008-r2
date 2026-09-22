# 河湟非遗体验传播授权

非遗项目、传承人演示、体验作品、口译文本和媒体素材具有彼此独立的公开范围。本服务把它们建立为可追查关系：素材按指纹去重，许可挂在素材而非某次上传；关键工序只公开摘要且原文/译文修订一一对应；许可到期或撤回时自动列出并下线不合规报道与展项，但活动事实永久保留。

通过 HTTP 接口交换业务记录，使用 SQLite 文件保存状态。`PORT` 指定监听端口，`DATABASE_PATH` 指定数据文件；`fixtures/example.json` 提供不含真实身份的西宁银铜器场景，`contracts/entities.json` 记录字段约定，业务规则见 `docs/domain.md`。

## 本地开发

```bash
make migrate   # 初始化数据库（自动应用 migrations/ 下未执行的迁移）
make seed      # 载入西宁银铜器示例场景（重复执行跳过，加 --reset 重建）
make test      # 执行全部自动化检查
make run       # 启动服务（默认 :8080）
```

也可使用 `docker compose up --build` 在隔离容器中运行，宿主机端口由 `APP_PORT` 调整。

## 快速体验（seed 后）

```bash
curl localhost:8080/public/reports                 # 公众：只有合规在线报道
curl localhost:8080/public/sessions                # 公众：活动事实（无体验者身份）
curl localhost:8080/staff/trace/ASSET-DEMO-VIDEO   # 馆员：素材全貌与实际使用边界
curl localhost:8080/staff/takedowns                # 馆员：待下线清单
```

## HTTP 接口

请求/响应均为 `application/json; charset=utf-8`；时间字段为带偏移量的 ISO 8601。
馆员侧 `201` 表示创建，查询型 POST（分发闸门、批量下线）返回 `200`；业务拒绝返回 `409/422`，错误体形如 `{"error": "..."}`。

### 登记（馆员侧 `/staff`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/staff/heritage` | 非遗项目 |
| POST | `/staff/inheritors/consents` | 传承人允许展示范围（可更新，撤回状态随之清除） |
| POST | `/staff/inheritors/{ref}/withdraw` | 撤回传承人展示同意（级联下线） |
| POST | `/staff/sessions` | 体验场次（只追加的活动事实） |
| POST | `/staff/sessions/{ref}/participants` | 参与者及肖像同意 |
| POST | `/staff/material-kits` | 材料包 |
| POST | `/staff/youth-works` | 青年体验作品（可关联材料包） |
| POST | `/staff/youth-works/{ref}/display` | 变更作品展示许可 |
| POST | `/staff/documents` | 工序文档（`is_key_process` 标记关键工序） |
| POST | `/staff/documents/{ref}/revisions` | 原文修订（必须携带同序号 `translations`） |
| POST | `/staff/assets` | 素材登记；相同 `fingerprint_sha256` 返回同一素材（`deduped`） |
| POST | `/staff/assets/{ref|指纹}/captures` | 上传记录（同一素材可多家媒体上传） |
| POST | `/staff/assets/{ref|指纹}/links` | 关联 heritage/session/inheritor/participant/work/document/material_kit |
| POST | `/staff/grants` | 授予媒体用途（授予即核验同意基础） |
| POST | `/staff/grants/{ref}/revoke` | 撤回许可并立即级联下线 |
| POST | `/staff/reports` | 发布报道（缺许可/超范围返回 409） |
| POST | `/staff/exhibits` | 创建展项（目标为 asset/work/document） |
| POST | `/staff/distribution-check` | 发布前闸门：`{grantee_org, ref_or_fingerprint, scopes}` |
| POST | `/staff/takedowns/apply` | 执行全部待下线项 |
| GET | `/staff/takedowns` | 待下线报道与展项（含逐条拦截原因） |
| GET | `/staff/trace/{ref|指纹}` | 从素材/作品/文档/场次/材料包追溯全貌 |
| GET | `/staff/grants/{ref}` `/staff/documents/{ref}` `/staff/assets/{ref}` | 单体详情 |
| GET | `/staff/audit` | 审计事件（含拦截与撤回留痕） |

### 公众侧（`/public`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/public/heritage` | 项目与摘要 |
| GET | `/public/sessions` | 场次事实（无体验者身份） |
| GET | `/public/assets/{ref\|指纹}` | 获准素材说明；关键工序只有 `public_summary` |
| GET | `/public/reports` | 当前在线且合规的报道 |
| GET | `/public/youth-works` | 获准展示的青年作品 |

## 许可范围

`event-report`（活动报道）、`full-process`（完整工序，关键工序默认不授）、`portrait`（肖像，需每个可识别参与者同意）、`internal-archive`（仅存档，永不可公开发布）。
