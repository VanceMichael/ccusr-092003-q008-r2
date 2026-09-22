
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN if [ -s requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
RUN chmod +x scripts/entrypoint.sh && python -m scripts.migrate
ENV PORT=8080 DATABASE_PATH=/data/app.sqlite3
EXPOSE 8080
# 运行时挂载的 /data 卷可能为空，启动前再次执行迁移（已应用的版本会跳过）
CMD ["sh", "scripts/entrypoint.sh"]
