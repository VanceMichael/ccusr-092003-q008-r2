
"""加载 fixtures/example.json 的西宁银铜器场景示例。

用法：python -m scripts.seed [--reset]
重复执行默认跳过；--reset 会清空授权领域表后重建（场次等事实表也一并重建）。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from app.store import DomainError, Store

FIXTURE = Path("fixtures/example.json")
SEED_MARKER = "SILVER-COPPER-XINING"

# 外键依赖顺序下的清空列表
TABLES = [
    "audit_events", "exhibits", "reports", "media_grants", "media_captures",
    "asset_links", "media_assets", "translation_revisions", "document_revisions",
    "process_documents", "youth_works", "participants", "sessions",
    "material_kits", "inheritor_consents", "heritage_projects",
]


def reset(connection: sqlite3.Connection) -> None:
    for table in TABLES:
        connection.execute(f"DELETE FROM {table}")
    connection.commit()


def seed(store: Store, data: dict) -> None:
    for item in data["heritage"]:
        store.create_heritage(**item)
    for item in data["inheritors"]:
        store.set_inheritor_consent(**item)
    for item in data["sessions"]:
        store.create_session(**item)
    for item in data["participants"]:
        store.add_participant(**item)
    for item in data["material_kits"]:
        store.create_material_kit(**item)
    for item in data["youth_works"]:
        store.create_youth_work(**item)
    for entry in data["documents"]:
        store.create_document(**entry["doc"])
        for revision in entry["revisions"]:
            store.add_revision(
                entry["doc"]["doc_ref"],
                source_ref=revision["source_ref"],
                source_sha256=revision["source_sha256"],
                translations=revision["translations"],
                change_note=revision.get("change_note", ""),
            )

    seen_fingerprints: set[str] = set()
    for entry in data["assets"]:
        result = store.register_asset(**entry["asset"])
        if result.get("deduped"):
            print(f"重复影像识别为同一素材：{entry['asset']['asset_ref']} -> {result['asset_ref']}")
        seen_fingerprints.add(entry["asset"]["fingerprint_sha256"])
        # 二次上传：指纹相同，只产生上传记录，不产生新素材/新许可主体
        for dup in entry.get("duplicate_uploads", []):
            dup_result = store.register_asset(**dup)
            print(f"二次上传去重：{dup['asset_ref']} -> {dup_result['asset_ref']}")
        asset_ref = result["asset_ref"]
        for capture in entry.get("captures", []):
            store.add_capture(asset_ref, **capture)
        for link in entry.get("links", []):
            store.link_asset(asset_ref, **link)

    for item in data["grants"]:
        store.issue_grant(**item)
    for item in data["reports"]:
        store.publish_report(**item)
    for item in data["exhibits"]:
        store.create_exhibit(**item)

    # 场景中的越界尝试：真实调用并确认被系统拒绝（拒绝事件进入审计）
    for attempt in data.get("blocked_attempts", []):
        kind, payload = attempt["type"], attempt["payload"]
        fn = store.issue_grant if kind == "grant" else store.publish_report
        try:
            fn(**payload)
        except DomainError as exc:
            print(f"已阻止越界{ '授权' if kind == 'grant' else '报道' }：{exc}")
        else:
            raise RuntimeError(f"越界尝试未被拦截：{attempt['expect']}")


def main() -> None:
    do_reset = "--reset" in sys.argv[1:]
    database_path = Path(os.getenv("DATABASE_PATH", "data/app.sqlite3"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with sqlite3.connect(database_path) as connection:
        if do_reset:
            reset(connection)
        elif connection.execute(
            "SELECT 1 FROM heritage_projects WHERE heritage_ref=?", (SEED_MARKER,)
        ).fetchone():
            print(f"示例数据已存在，跳过；如需重建请加 --reset：{database_path}")
            return
        store = Store(connection)
        seed(store, data)
        connection.commit()
    print(f"示例场景加载完成：{database_path}")


if __name__ == "__main__":
    main()
