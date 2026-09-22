
import sqlite3
from pathlib import Path

from app.store import Store

ROOT = Path(__file__).resolve().parent.parent


def build_store(clock_value: str = "2026-09-21T12:00:00+00:00") -> tuple[Store, list]:
    """在内存库上构建 Store；clock_values 弹出式控制当前时间（用于到期测试）。"""
    conn = sqlite3.connect(":memory:")
    clock_values = [clock_value]

    def clock() -> str:
        return clock_values[0] if len(clock_values) == 1 else clock_values.pop(0)

    for name in ("001_bootstrap.sql", "002_authorization.sql"):
        conn.executescript((ROOT / "migrations" / name).read_text(encoding="utf-8"))
    return Store(conn, clock=clock), clock_values


def build_xining(store: Store) -> None:
    """西宁银铜器场景的最小可复用搭建。"""
    store.create_heritage("SILVER-COPPER-XINING", "河湟银铜器锻制技艺", "省级非遗")
    store.set_inheritor_consent(
        "PERSON-A", "SILVER-COPPER-XINING", ["event-report", "portrait"],
        "关键工序完整流程不公开",
    )
    store.create_session(
        "SESSION-1", "SILVER-COPPER-XINING", "PERSON-A", "银铜器交流演示",
        "2026-09-20T10:00:00+08:00", "西宁非遗馆", "活动已举办",
    )
    store.add_participant("SESSION-1", "PERSON-B", "体验者", portrait_consent=True)
    store.add_participant("SESSION-1", "PERSON-C", "青年创作者", portrait_consent=True)
    store.create_material_kit("KIT-1", "SILVER-COPPER-XINING", "錾刻材料包", "铜片与錾刀")
    store.create_youth_work(
        "WORK-1", "SESSION-1", "PERSON-C", "铜书签", "青年錾刻习作",
        display_permission=True, material_kit_ref="KIT-1",
    )
    store.create_document(
        "DOC-KEY", "SILVER-COPPER-XINING", "关键焊接工序", is_key_process=True,
        public_summary="关键工序仅公开摘要", session_ref="SESSION-1",
    )
    store.add_revision(
        "DOC-KEY", "受控原件://v1", "1" * 64,
        [{"language": "en", "translation_ref": "受控译文://en/v1",
          "translation_sha256": "2" * 64, "translator_ref": "PERSON-D"}],
        "首版",
    )
    store.register_asset(
        "ASSET-1", "a" * 64, "演示完整视频", source_ref="母带://demo",
        captured_at="2026-09-20T10:05:00+08:00",
    )
    for kind, ref in (
        ("heritage", "SILVER-COPPER-XINING"),
        ("session", "SESSION-1"),
        ("inheritor", "PERSON-A"),
        ("participant", "SESSION-1/PERSON-B"),
        ("participant", "SESSION-1/PERSON-C"),
        ("document", "DOC-KEY"),
        ("work", "WORK-1"),
        ("material_kit", "KIT-1"),
    ):
        store.link_asset("ASSET-1", kind, ref)
    store.add_capture("ASSET-1", "西宁广电", "获许可上传")
    store.add_capture("ASSET-1", "某自媒体", "误以为拍摄即可公开")
