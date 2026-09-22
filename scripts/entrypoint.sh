#!/bin/sh
# 容器入口：先对挂载卷执行迁移，再启动服务
set -e
python -m scripts.migrate
exec python -m app.main
