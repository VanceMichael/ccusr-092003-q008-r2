"""领域存储：所有实体的登记、查询与可追查关系。

约定：
- 业务主体使用引用编号，不保存真实身份；
- 时间一律使用带偏移量的 ISO 8601 字符串；
- 媒体素材以 sha256 为唯一身份，重复上传归并到同一素材；
- 原始材料不落库，只保存受控引用与摘要。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable


# 规范的媒体用途范围
SCOPE_EVENT_REPORT = "event-report"
SCOPE_EXHIBITION = "exhibition-display"
SCOPE_SHORT_CLIP = "short-clip"
SCOPE_DOCUMENTARY = "documentary"
SCOPE_ARCHIVE = "archive-internal"

PUBLIC_SCOPES = (
    SCOPE_EVENT_REPORT,
    SCOPE_EXHIBITION,
    SCOPE_SHORT_CLIP,
    SCOPE_DOCUMENTARY,
)

# 媒体用途类型 → 所需授权范围
USAGE_TYPE_SCOPES = {
    "report": SCOPE_EVENT_REPORT,
    "exhibition": SCOPE_EXHIBITION,
    "clip": SCOPE_SHORT_CLIP,
    "documentary": SCOPE_DOCUMENTARY,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str) -> datetime:
    """解析带偏移量的 ISO 8601；拒绝无时区时间，避免到期比较歧义。"""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"时间必须带时区偏移量：{value}")
    return dt


def _dumps(value: Any) -> str:
    if value is None:
        return "[]"
    if not isinstance(value, (list, tuple)):
        raise ValueError("范围字段必须是字符串数组")
    return json.dumps(list(value), ensure_ascii=False)


def _loads(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    return list(parsed)


def _require(conn: sqlite3.Connection, table: str, ref: str, column: str = "ref") -> None:
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE {column} = ?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"{table} 中不存在引用：{ref}")


# ---------- 非遗项目 ----------

def create_project(
    conn: sqlite3.Connection,
    ref: str,
    name: str,
    public_summary: str,
) -> dict[str, Any]:
    conn.execute(
        """INSERT INTO heritage_projects(ref, name, public_summary, created_at)
           VALUES (?, ?, ?, ?)""",
        (ref, name, public_summary, now_iso()),
    )
    conn.commit()
    return get_project(conn, ref)


def get_project(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM heritage_projects WHERE ref = ?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"非遗项目不存在：{ref}")
    return dict(row)


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM heritage_projects ORDER BY ref"
    ).fetchall()]


# ---------- 传承人 ----------

def create_inheritor(
    conn: sqlite3.Connection,
    ref: str,
    heritage_ref: str,
    public_name: str,
    allowed_scopes: Iterable[str],
    full_process_allowed: bool = False,
    portrait_allowed: bool = True,
    notes: str | None = None,
) -> dict[str, Any]:
    _require(conn, "heritage_projects", heritage_ref)
    conn.execute(
        """INSERT INTO inheritors(ref, heritage_ref, public_name, allowed_scopes,
                  full_process_allowed, portrait_allowed, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ref, heritage_ref, public_name, _dumps(allowed_scopes),
            int(full_process_allowed), int(portrait_allowed), notes, now_iso(),
        ),
    )
    conn.commit()
    return get_inheritor(conn, ref)


def get_inheritor(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM inheritors WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise KeyError(f"传承人不存在：{ref}")
    data = dict(row)
    data["allowed_scopes"] = _loads(data["allowed_scopes"])
    return data


# ---------- 体验场次（事实记录，不删除） ----------

def create_session(
    conn: sqlite3.Connection,
    ref: str,
    heritage_ref: str,
    inheritor_ref: str,
    title: str,
    occurred_at: str,
    venue: str | None = None,
) -> dict[str, Any]:
    _require(conn, "heritage_projects", heritage_ref)
    _require(conn, "inheritors", inheritor_ref)
    parse_dt(occurred_at)
    conn.execute(
        """INSERT INTO sessions(ref, heritage_ref, inheritor_ref, title, venue,
                  occurred_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ref, heritage_ref, inheritor_ref, title, venue, occurred_at, now_iso()),
    )
    conn.commit()
    return get_session(conn, ref)


def get_session(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sessions WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise KeyError(f"体验场次不存在：{ref}")
    return dict(row)


def list_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM sessions ORDER BY occurred_at, ref"
    ).fetchall()]


# ---------- 工序 ----------

def add_process_step(
    conn: sqlite3.Connection,
    heritage_ref: str,
    step_no: int,
    name: str,
    public_summary: str,
    restricted_detail: str | None = None,
) -> dict[str, Any]:
    _require(conn, "heritage_projects", heritage_ref)
    conn.execute(
        """INSERT INTO process_steps(heritage_ref, step_no, name, public_summary,
                  restricted_detail)
           VALUES (?, ?, ?, ?, ?)""",
        (heritage_ref, step_no, name, public_summary, restricted_detail),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM process_steps WHERE heritage_ref = ? AND step_no = ?",
        (heritage_ref, step_no),
    ).fetchone()
    return dict(row)


def list_process_steps(
    conn: sqlite3.Connection, heritage_ref: str, *, public_only: bool = False
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM process_steps WHERE heritage_ref = ? ORDER BY step_no",
        (heritage_ref,),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        if public_only:
            data.pop("restricted_detail", None)
        result.append(data)
    return result


# ---------- 体验者 ----------

def register_attendee(
    conn: sqlite3.Connection,
    ref: str,
    session_ref: str,
    public_label: str,
    portrait_consent: str = "denied",
    consent_scopes: Iterable[str] | None = None,
    consented_at: str | None = None,
) -> dict[str, Any]:
    _require(conn, "sessions", session_ref)
    if portrait_consent not in ("granted", "denied", "withdrawn"):
        raise ValueError("portrait_consent 必须是 granted / denied / withdrawn")
    if portrait_consent == "granted":
        consented_at = consented_at or now_iso()
        parse_dt(consented_at)
    conn.execute(
        """INSERT INTO attendees(ref, session_ref, public_label, portrait_consent,
                  consent_scopes, consented_at, withdrawn_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
        (
            ref, session_ref, public_label, portrait_consent,
            _dumps(consent_scopes or []), consented_at, now_iso(),
        ),
    )
    conn.commit()
    return get_attendee(conn, ref)


def withdraw_attendee_consent(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    _require(conn, "attendees", ref)
    conn.execute(
        "UPDATE attendees SET portrait_consent='withdrawn', withdrawn_at=? WHERE ref=?",
        (now_iso(), ref),
    )
    conn.commit()
    return get_attendee(conn, ref)


def get_attendee(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM attendees WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise KeyError(f"体验者不存在：{ref}")
    data = dict(row)
    data["consent_scopes"] = _loads(data["consent_scopes"])
    return data


def list_session_attendees(conn: sqlite3.Connection, session_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM attendees WHERE session_ref = ? ORDER BY ref", (session_ref,)
    ).fetchall()
    return [_attendee_row(r) for r in rows]


def _attendee_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["consent_scopes"] = _loads(data["consent_scopes"])
    return data


# ---------- 材料包 ----------

def create_material_kit(
    conn: sqlite3.Connection,
    ref: str,
    heritage_ref: str,
    name: str,
    allowed_scopes: Iterable[str],
    public_note: str | None = None,
) -> dict[str, Any]:
    _require(conn, "heritage_projects", heritage_ref)
    conn.execute(
        """INSERT INTO material_kits(ref, heritage_ref, name, public_note,
                  allowed_scopes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ref, heritage_ref, name, public_note, _dumps(allowed_scopes), now_iso()),
    )
    conn.commit()
    return get_material_kit(conn, ref)


def get_material_kit(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM material_kits WHERE ref = ?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"材料包不存在：{ref}")
    data = dict(row)
    data["allowed_scopes"] = _loads(data["allowed_scopes"])
    return data


def assign_kit_to_session(
    conn: sqlite3.Connection, session_ref: str, kit_ref: str, note: str | None = None
) -> None:
    _require(conn, "sessions", session_ref)
    _require(conn, "material_kits", kit_ref)
    conn.execute(
        "INSERT OR IGNORE INTO session_material_kits(session_ref, kit_ref, note)"
        " VALUES (?, ?, ?)",
        (session_ref, kit_ref, note),
    )
    conn.commit()


# ---------- 青年作品 ----------

def create_youth_work(
    conn: sqlite3.Connection,
    ref: str,
    session_ref: str,
    youth_ref: str,
    title: str,
    allowed_scopes: Iterable[str],
) -> dict[str, Any]:
    _require(conn, "sessions", session_ref)
    conn.execute(
        """INSERT INTO youth_works(ref, session_ref, youth_ref, title,
                  allowed_scopes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ref, session_ref, youth_ref, title, _dumps(allowed_scopes), now_iso()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM youth_works WHERE ref = ?", (ref,)).fetchone()
    data = dict(row)
    data["allowed_scopes"] = _loads(data["allowed_scopes"])
    return data


def get_youth_work(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM youth_works WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise KeyError(f"青年作品不存在：{ref}")
    data = dict(row)
    data["allowed_scopes"] = _loads(data["allowed_scopes"])
    return data


# ---------- 口译修订 ----------

def add_translation_revision(
    conn: sqlite3.Connection,
    unit_ref: str,
    revision: int,
    source_text: str,
    translated_text: str,
    language_pair: str,
    change_note: str | None = None,
    heritage_ref: str | None = None,
    session_ref: str | None = None,
) -> dict[str, Any]:
    """登记一次口译修订；同一单元每次修订都同时保存原文与译文。"""
    if heritage_ref:
        _require(conn, "heritage_projects", heritage_ref)
    if session_ref:
        _require(conn, "sessions", session_ref)
    exists = conn.execute(
        "SELECT 1 FROM translation_units WHERE unit_ref=? AND revision=?",
        (unit_ref, revision),
    ).fetchone()
    if exists:
        raise ValueError(f"口译修订已存在：{unit_ref} r{revision}")
    conn.execute(
        """INSERT INTO translation_units(unit_ref, revision, heritage_ref,
                  session_ref, language_pair, source_text, translated_text,
                  change_note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            unit_ref, revision, heritage_ref, session_ref, language_pair,
            source_text, translated_text, change_note, now_iso(),
        ),
    )
    conn.commit()
    return get_translation_revision(conn, unit_ref, revision)


def get_translation_revision(
    conn: sqlite3.Connection, unit_ref: str, revision: int
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM translation_units WHERE unit_ref=? AND revision=?",
        (unit_ref, revision),
    ).fetchone()
    if row is None:
        raise KeyError(f"口译修订不存在：{unit_ref} r{revision}")
    return dict(row)


def list_translation_revisions(conn: sqlite3.Connection, unit_ref: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM translation_units WHERE unit_ref=? ORDER BY revision",
        (unit_ref,),
    ).fetchall()]


# ---------- 媒体素材与上传 ----------

def register_asset_upload(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    kind: str,
    title: str,
    outlet_ref: str,
    claimed_basis: str,
    asset_ref: str | None = None,
    session_ref: str | None = None,
    heritage_ref: str | None = None,
    inheritor_ref: str | None = None,
    contains_full_process: bool = False,
    technical_fingerprint: dict[str, Any] | None = None,
    note: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """登记一次媒体上传。以 sha256 归并：重复影像返回既有素材与 deduplicated=True。

    许可只挂在素材身份上，因此归并后不会产生第二份许可。
    """
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise ValueError("sha256 必须是 64 位十六进制摘要")
    if kind not in ("video", "audio", "image", "text", "document"):
        raise ValueError("kind 必须是 video/audio/image/text/document")

    existing = conn.execute(
        "SELECT * FROM media_assets WHERE sha256=?", (sha256.lower(),)
    ).fetchone()
    if existing is not None:
        asset = dict(existing)
        deduplicated = True
    else:
        if not asset_ref:
            raise ValueError("首次上传该摘要必须提供 asset_ref")
        if session_ref:
            _require(conn, "sessions", session_ref)
        if heritage_ref:
            _require(conn, "heritage_projects", heritage_ref)
        if inheritor_ref:
            _require(conn, "inheritors", inheritor_ref)
        conn.execute(
            """INSERT INTO media_assets(asset_ref, sha256, kind, title, session_ref,
                      heritage_ref, inheritor_ref, contains_full_process,
                      technical_fingerprint, first_seen_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_ref, sha256.lower(), kind, title, session_ref, heritage_ref,
                inheritor_ref, int(contains_full_process),
                json.dumps(technical_fingerprint or {}, ensure_ascii=False),
                now_iso(), now_iso(),
            ),
        )
        asset = get_asset(conn, asset_ref)
        deduplicated = False

    conn.execute(
        """INSERT INTO media_uploads(asset_ref, outlet_ref, uploaded_at,
                  claimed_basis, note)
           VALUES (?, ?, ?, ?, ?)""",
        (asset["asset_ref"], outlet_ref, now_iso(), claimed_basis, note),
    )
    conn.commit()
    return asset, deduplicated


def get_asset(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM media_assets WHERE asset_ref=?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"媒体素材不存在：{ref}")
    data = dict(row)
    data["technical_fingerprint"] = json.loads(data["technical_fingerprint"] or "{}")
    data["contains_full_process"] = bool(data["contains_full_process"])
    return data


def get_asset_by_sha(conn: sqlite3.Connection, sha256: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM media_assets WHERE sha256=?", (sha256.lower(),)
    ).fetchone()
    if row is None:
        raise KeyError(f"没有该摘要的素材：{sha256}")
    data = dict(row)
    data["technical_fingerprint"] = json.loads(data["technical_fingerprint"] or "{}")
    data["contains_full_process"] = bool(data["contains_full_process"])
    return data


def list_asset_uploads(conn: sqlite3.Connection, asset_ref: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM media_uploads WHERE asset_ref=? ORDER BY id", (asset_ref,)
    ).fetchall()]


def link_asset_attendees(conn: sqlite3.Connection, asset_ref: str, attendee_refs: Iterable[str]) -> None:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    for attendee_ref in attendee_refs:
        _require(conn, "attendees", attendee_ref)
        conn.execute(
            "INSERT OR IGNORE INTO asset_attendees(asset_ref, attendee_ref) VALUES (?, ?)",
            (asset_ref, attendee_ref),
        )
    conn.commit()


def link_asset_youth_works(conn: sqlite3.Connection, asset_ref: str, work_refs: Iterable[str]) -> None:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    for work_ref in work_refs:
        _require(conn, "youth_works", work_ref)
        conn.execute(
            "INSERT OR IGNORE INTO asset_youth_works(asset_ref, work_ref) VALUES (?, ?)",
            (asset_ref, work_ref),
        )
    conn.commit()


def link_asset_material_kits(conn: sqlite3.Connection, asset_ref: str, kit_refs: Iterable[str]) -> None:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    for kit_ref in kit_refs:
        _require(conn, "material_kits", kit_ref)
        conn.execute(
            "INSERT OR IGNORE INTO asset_material_kits(asset_ref, kit_ref) VALUES (?, ?)",
            (asset_ref, kit_ref),
        )
    conn.commit()


def link_asset_translations(conn: sqlite3.Connection, asset_ref: str, unit_refs: Iterable[str]) -> None:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    for unit_ref in unit_refs:
        conn.execute(
            "INSERT OR IGNORE INTO asset_translation_units(asset_ref, unit_ref) VALUES (?, ?)",
            (asset_ref, unit_ref),
        )
    conn.commit()


def list_asset_attendees(conn: sqlite3.Connection, asset_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT a.* FROM attendees a
           JOIN asset_attendees aa ON aa.attendee_ref = a.ref
           WHERE aa.asset_ref = ? ORDER BY a.ref""",
        (asset_ref,),
    ).fetchall()
    return [_attendee_row(r) for r in rows]


def list_asset_youth_works(conn: sqlite3.Connection, asset_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT y.* FROM youth_works y
           JOIN asset_youth_works ay ON ay.work_ref = y.ref
           WHERE ay.asset_ref = ? ORDER BY y.ref""",
        (asset_ref,),
    ).fetchall()
    return [_scope_row(dict(r), "allowed_scopes") for r in rows]


def list_asset_material_kits(conn: sqlite3.Connection, asset_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT k.* FROM material_kits k
           JOIN asset_material_kits ak ON ak.kit_ref = k.ref
           WHERE ak.asset_ref = ? ORDER BY k.ref""",
        (asset_ref,),
    ).fetchall()
    return [_scope_row(dict(r), "allowed_scopes") for r in rows]


def list_asset_translation_units(conn: sqlite3.Connection, asset_ref: str) -> list[str]:
    return [r["unit_ref"] for r in conn.execute(
        "SELECT unit_ref FROM asset_translation_units WHERE asset_ref=? ORDER BY unit_ref",
        (asset_ref,),
    ).fetchall()]


def _scope_row(data: dict[str, Any], key: str) -> dict[str, Any]:
    data[key] = _loads(data[key])
    return data


# ---------- 许可 ----------

def grant_license(
    conn: sqlite3.Connection,
    license_ref: str,
    asset_ref: str,
    grantee_ref: str,
    usage_scopes: Iterable[str],
    valid_from: str,
    valid_until: str | None = None,
    covers_full_process: bool = False,
) -> dict[str, Any]:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    parse_dt(valid_from)
    if valid_until:
        until = parse_dt(valid_until)
        if until <= parse_dt(valid_from):
            raise ValueError("valid_until 必须晚于 valid_from")
    scopes = list(usage_scopes)
    if not scopes:
        raise ValueError("许可至少包含一个用途范围")
    unknown = [s for s in scopes if s not in PUBLIC_SCOPES + (SCOPE_ARCHIVE,)]
    if unknown:
        raise ValueError(f"未知用途范围：{', '.join(unknown)}")
    conn.execute(
        """INSERT INTO licenses(license_ref, asset_ref, grantee_ref, usage_scopes,
                  valid_from, valid_until, status, covers_full_process,
                  granted_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
        (
            license_ref, asset_ref, grantee_ref, _dumps(scopes),
            valid_from, valid_until, int(covers_full_process), now_iso(), now_iso(),
        ),
    )
    conn.commit()
    return get_license(conn, license_ref)


def withdraw_license(
    conn: sqlite3.Connection, license_ref: str, reason: str | None = None
) -> dict[str, Any]:
    _require(conn, "licenses", license_ref, column="license_ref")
    conn.execute(
        """UPDATE licenses SET status='withdrawn', withdrawn_at=?, withdraw_reason=?
           WHERE license_ref=?""",
        (now_iso(), reason, license_ref),
    )
    conn.commit()
    return get_license(conn, license_ref)


def get_license(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM licenses WHERE license_ref=?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"许可不存在：{ref}")
    return _license_row(row)


def list_asset_licenses(conn: sqlite3.Connection, asset_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM licenses WHERE asset_ref=? ORDER BY granted_at", (asset_ref,)
    ).fetchall()
    return [_license_row(r) for r in rows]


def _license_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["usage_scopes"] = _loads(data["usage_scopes"])
    data["covers_full_process"] = bool(data["covers_full_process"])
    return data


# ---------- 媒体用途 ----------

def register_usage(
    conn: sqlite3.Connection,
    usage_ref: str,
    asset_ref: str,
    outlet_ref: str,
    usage_type: str,
    title: str,
    channel: str | None = None,
    first_published_at: str | None = None,
) -> dict[str, Any]:
    _require(conn, "media_assets", asset_ref, column="asset_ref")
    if usage_type not in USAGE_TYPE_SCOPES:
        raise ValueError(
            f"usage_type 必须是 {', '.join(USAGE_TYPE_SCOPES)}"
        )
    conn.execute(
        """INSERT INTO media_usages(usage_ref, asset_ref, outlet_ref, usage_type,
                  title, channel, status, first_published_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?)""",
        (
            usage_ref, asset_ref, outlet_ref, usage_type, title, channel,
            first_published_at or now_iso(), now_iso(),
        ),
    )
    conn.commit()
    return get_usage(conn, usage_ref)


def take_down_usage(conn: sqlite3.Connection, usage_ref: str) -> dict[str, Any]:
    _require(conn, "media_usages", usage_ref, column="usage_ref")
    conn.execute(
        """UPDATE media_usages SET status='taken_down', taken_down_at=?
           WHERE usage_ref=?""",
        (now_iso(), usage_ref),
    )
    conn.commit()
    return get_usage(conn, usage_ref)


def get_usage(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM media_usages WHERE usage_ref=?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"媒体用途不存在：{ref}")
    return dict(row)


def list_usages(
    conn: sqlite3.Connection, *, published_only: bool = False
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM media_usages"
    if published_only:
        sql += " WHERE status='published'"
    sql += " ORDER BY first_published_at, usage_ref"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ---------- 判定流水 ----------

def record_decision(
    conn: sqlite3.Connection,
    usage_ref: str,
    asset_ref: str,
    decision: str,
    reasons: Iterable[str],
) -> dict[str, Any]:
    conn.execute(
        """INSERT INTO distribution_decisions(usage_ref, asset_ref, decision,
                  reasons, decided_at)
           VALUES (?, ?, ?, ?, ?)""",
        (usage_ref, asset_ref, decision, _dumps(reasons), now_iso()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM distribution_decisions WHERE id=last_insert_rowid()"
    ).fetchone()
    data = dict(row)
    data["reasons"] = _loads(data["reasons"])
    return data


def list_decisions(conn: sqlite3.Connection, usage_ref: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM distribution_decisions WHERE usage_ref=? ORDER BY id",
        (usage_ref,),
    ).fetchall()
    return [_scope_row(dict(r), "reasons") for r in rows]
