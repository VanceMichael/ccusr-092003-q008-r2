# 河湟非遗体验传播授权

西宁非遗馆交流活动后，同一段银铜器演示视频可能被多家媒体分别上传：一家持报道许可，另一家误以为“现场拍摄”即可公开完整工序与参与者肖像。本服务把**非遗项目、传承人展示边界、体验场次、材料包、口译修订、青年作品与媒体用途**建成可追查关系：

- 媒体素材以 **sha256 摘要为唯一身份**，重复影像归并为同一素材，许可只挂在素材上，不会产生两份许可；
- 关键工序只公开摘要，受限细节仅馆员可见；口译每次修订同时保存原文与译文，改动保持对应；
- 许可到期（时间推导）或撤回时，自动列出必须下线的报道与展项，并在分发闸门阻止继续分发；
- **活动发生过的事实不消失**：场次、上传、许可与判定流水都保留；公众只接触获准说明。

服务通过 HTTP JSON 接口交换业务记录，使用 SQLite 保存状态。`PORT` 指定监听端口，`DATABASE_PATH` 指定数据文件。

## 本地开发

```bash
make migrate   # 初始化/升级数据文件
make seed      # 载入 fixtures/example.json 的西宁场景（幂等）
make test      # 执行自动化测试（pytest）
make run       # 启动服务
```

也可以 `docker compose up --build`，宿主机端口由 `APP_PORT` 调整；服务启动时会自动应用待执行迁移。

## 场景数据

`fixtures/example.json` 是不含真实身份的西宁银铜器交流场示例：

| 媒体用途 | 结果 | 原因 |
| --- | --- | --- |
| 青海日报图文报道 `USAGE-QH-REPORT` | 允许（撤证后须下线） | 持 `event-report` 许可，体验者甲已同意肖像 |
| 同城自媒体实录 `USAGE-CITY-REPORT` | 阻止 | 同一素材、同 sha256，但许可属于青海日报，不能援引 |
| 馆内互动屏展项 `USAGE-EXHIBIT-EXPIRED` | 阻止 | 许可已于 2026-09-10 到期，且含未同意肖像的体验者 |
| 观众混剪短视频 `USAGE-CLIP-PORTRAIT` | 阻止 | 无该媒体许可、传承人未开放短视频、体验者乙未同意/丙已撤回 |

载入后可复现：

```bash
make seed
# 必须下线清单（按当前时间评估；可用 ?at= 回放历史时刻）
curl -s "http://localhost:8080/staff/takedowns"
# 执行下线
curl -s -X POST http://localhost:8080/staff/takedowns/apply -d '{}'
# 公众视图：只见获准说明，场次事实仍在
curl -s http://localhost:8080/public/catalog
```

## 分发判定模型

一次媒体用途被判为 `allowed` 必须同时满足：

1. 存在一份**媒体、用途、时间**都匹配且未撤回的许可（现场拍摄不构成授权）；
2. 传承人允许展示该用途，且未拒绝肖像；
3. 含完整工序的素材需传承人允许完整公开，且许可明确覆盖完整工序；
4. 素材中每位体验者的肖像同意都处于 `granted` 且同意范围覆盖该用途；
5. 素材涉及的青年作品与材料包各自允许该用途。

判定支持时间回放：在闸门请求体或查询串传 `at`（带偏移量 ISO 8601），可验证“许可有效时允许、到期后阻止”。

## 接口一览

### 公众（只读，只含获准内容）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/public/catalog` | 项目获准说明、工序摘要、场次事实、当前可用途 |
| GET | `/public/process/{heritage_ref}` | 工序列表，**不返回** `restricted_detail` |
| GET | `/public/usages/{usage_ref}` | 获准→200；边界外→403；已下线→410（事实仍可查） |

### 馆员：登记

`POST /staff/projects`、`/staff/inheritors`、`/staff/sessions`、`/staff/process-steps`、`/staff/attendees`、`/staff/material-kits`、`/staff/youth-works`、`/staff/translations`
另有 `POST /staff/attendees/{ref}/withdraw`（撤回肖像同意）、`POST /staff/sessions/{ref}/material-kits`。

### 馆员：素材与许可

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/staff/assets/uploads` | 登记上传；同 sha256 自动归并，返回 `deduplicated` |
| GET | `/staff/assets/by-sha/{sha256}` | 按摘要定位唯一素材 |
| POST | `/staff/assets/{ref}/links` | 关联体验者、青年作品、材料包、口译单元 |
| GET | `/staff/assets/{ref}/trace` | **全链路追溯**：体验者、传承人、许可、用途与实时边界 |
| POST | `/staff/licenses` | 发放许可（用途范围、有效期、是否覆盖完整工序） |
| POST | `/staff/licenses/{ref}/withdraw` | 撤回许可 |

### 馆员：分发闸门

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/staff/usages` | 登记媒体用途（report/exhibition/clip/documentary） |
| POST | `/staff/usages/{ref}/gate` | 分发闸门；允许 200，阻止 403，并写判定流水 |
| GET | `/staff/usages/{ref}/decisions` | 该用途的全部判定流水 |
| GET | `/staff/takedowns` | 必须下线的报道与展项清单及原因 |
| POST | `/staff/takedowns/apply` | 批量执行下线 |

字段约定见 `contracts/entities.json`，领域规则见 `docs/domain.md`。
