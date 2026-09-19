# 河湟非遗体验传播授权

非遗项目、传承人演示、体验作品、口译文本和媒体素材具有彼此独立的公开范围。

本服务通过 HTTP 接口交换业务记录，并使用 SQLite 文件保存状态。`PORT` 指定监听端口，`DATABASE_PATH` 指定数据文件；`fixtures/example.json` 提供不含真实身份的本地示例，`contracts/entities.json` 记录首批字段约定。

## 本地开发

运行 `make migrate` 初始化数据文件，`make test` 执行现有自动化检查，`make run` 启动服务。也可以使用 `docker compose up --build` 在隔离容器中运行，宿主机端口由 `APP_PORT` 调整。
