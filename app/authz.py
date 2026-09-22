"""传播授权判定。

综合四层边界，任一不满足即阻止分发：
1. 媒体许可：对象正确（被授权方 == 实际发布方）、范围匹配、未撤回、在有效期内；
2. 传承人边界：允许展示该用途；完整工序需传承人允许且许可明确覆盖；
3. 体验者肖像：素材中每位体验者均已授予该用途的肖像同意（可撤回）；
4. 青年作品与材料包：各自的允许用途覆盖该用途。

判定是纯时间推导：许可到期不修改任何记录，历史事实与判定流水都保留。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import store


class AuthorizationError(ValueError):
    """请求本身不合法（未知用途类型等），区别于边界不满足的拦截。"""


def _license_covers(
    license_row: dict[str, Any], outlet_ref: str, scope: str, at: datetime
) -> tuple[bool, str | None]:
    if license_row["status"] != "active":
        return False, "许可已撤回"
    if license_row["grantee_ref"] != outlet_ref:
        return False, "许可属于其他媒体，不能由本媒体援引"
    if scope not in license_row["usage_scopes"]:
        return False, f"许可范围不含 {scope}"
    if store.parse_dt(license_row["valid_from"]) > at:
        return False, "许可尚未生效"
    if license_row["valid_until"] and store.parse_dt(license_row["valid_until"]) < at:
        return False, "许可已到期"
    return True, None


def evaluate_usage(
    conn: sqlite3.Connection,
    usage: dict[str, Any],
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    """评估单个媒体用途在某时刻可否继续分发。

    返回 {decision: allowed|blocked|taken-down, reasons, license, checks}。
    """
    at = at or datetime.now().astimezone()
    scope = store.USAGE_TYPE_SCOPES.get(usage["usage_type"])
    if scope is None:
        raise AuthorizationError(f"未知用途类型：{usage['usage_type']}")

    if usage["status"] == "taken_down":
        return {
            "decision": "taken-down",
            "reasons": ["该用途已按下线清单下线，事实记录保留"],
            "license_ref": None,
            "checks": {},
        }

    asset = store.get_asset(conn, usage["asset_ref"])
    reasons: list[str] = []
    matched_license: dict[str, Any] | None = None

    # 1. 媒体许可：必须能找到一份覆盖当前发布方、用途、时间的有效许可
    licenses = store.list_asset_licenses(conn, asset["asset_ref"])
    candidates: list[str] = []
    for lic in licenses:
        ok, why = _license_covers(lic, usage["outlet_ref"], scope, at)
        if ok:
            matched_license = lic
            break
        candidates.append(f"{lic['license_ref']}（{why}）")
    if matched_license is None:
        if not licenses:
            reasons.append("素材没有任何传播许可：现场拍摄不构成公开授权")
        else:
            reasons.append("没有覆盖该媒体与该用途的有效许可：" + "；".join(candidates))

    # 2. 传承人边界
    inheritor = None
    if asset.get("inheritor_ref"):
        inheritor = store.get_inheritor(conn, asset["inheritor_ref"])
        if scope not in inheritor["allowed_scopes"]:
            reasons.append(
                f"传承人 {inheritor['ref']} 允许展示的范围不含 {scope}"
            )
        if not inheritor["portrait_allowed"]:
            reasons.append(f"传承人 {inheritor['ref']} 不同意公开肖像")
        if asset["contains_full_process"]:
            if not inheritor["full_process_allowed"]:
                reasons.append("素材含完整工序，传承人只允许公开摘要")
            if matched_license and not matched_license["covers_full_process"]:
                reasons.append("素材含完整工序，但许可未覆盖完整工序")

    # 3. 体验者肖像同意（逐人核对，任何一人不满足都拦截）
    attendee_checks = []
    for attendee in store.list_asset_attendees(conn, asset["asset_ref"]):
        state = attendee["portrait_consent"]
        scopes = attendee["consent_scopes"]
        person_ok = True
        detail = "已授权"
        if state == "withdrawn":
            person_ok, detail = False, "肖像同意已撤回"
        elif state == "denied":
            person_ok, detail = False, "未授予肖像同意"
        elif scopes and scope not in scopes:
            person_ok, detail = False, f"肖像同意范围不含 {scope}"
        attendee_checks.append(
            {"attendee_ref": attendee["ref"], "ok": person_ok, "detail": detail}
        )
        if not person_ok:
            reasons.append(f"体验者 {attendee['ref']}：{detail}")

    # 4a. 青年作品
    youth_checks = []
    for work in store.list_asset_youth_works(conn, asset["asset_ref"]):
        ok = scope in work["allowed_scopes"]
        youth_checks.append({"work_ref": work["ref"], "ok": ok})
        if not ok:
            reasons.append(
                f"青年作品 {work['ref']}（青年 {work['youth_ref']}）未授权 {scope}"
            )

    # 4b. 材料包
    kit_checks = []
    for kit in store.list_asset_material_kits(conn, asset["asset_ref"]):
        ok = scope in kit["allowed_scopes"]
        kit_checks.append({"kit_ref": kit["ref"], "ok": ok})
        if not ok:
            reasons.append(f"材料包 {kit['ref']} 的使用边界不含 {scope}")

    decision = "allowed" if not reasons else "blocked"
    return {
        "decision": decision,
        "reasons": reasons,
        "license_ref": matched_license["license_ref"] if matched_license else None,
        "checks": {
            "scope": scope,
            "inheritor": {
                "ref": inheritor["ref"],
                "portrait_allowed": bool(inheritor["portrait_allowed"]),
                "full_process_allowed": bool(inheritor["full_process_allowed"]),
            } if inheritor else None,
            "attendees": attendee_checks,
            "youth_works": youth_checks,
            "material_kits": kit_checks,
        },
    }


def gate_usage(
    conn: sqlite3.Connection,
    usage_ref: str,
    *,
    at: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """分发闸门：评估并留痕；拦截时不落地下线（下线由撤证流程统一处理）。"""
    usage = store.get_usage(conn, usage_ref)
    result = evaluate_usage(conn, usage, at=at)
    if persist:
        store.record_decision(
            conn, usage_ref, usage["asset_ref"], result["decision"], result["reasons"]
        )
    result["usage_ref"] = usage_ref
    result["asset_ref"] = usage["asset_ref"]
    return result


def takedown_list(
    conn: sqlite3.Connection, *, at: datetime | None = None
) -> list[dict[str, Any]]:
    """列出仍在发布、但当前评估必须下线的报道与展项。"""
    at = at or datetime.now().astimezone()
    items = []
    for usage in store.list_usages(conn):
        if usage["status"] == "taken_down":
            continue
        result = evaluate_usage(conn, usage, at=at)
        if result["decision"] == "blocked":
            items.append(
                {
                    "usage_ref": usage["usage_ref"],
                    "asset_ref": usage["asset_ref"],
                    "outlet_ref": usage["outlet_ref"],
                    "usage_type": usage["usage_type"],
                    "title": usage["title"],
                    "channel": usage["channel"],
                    "reasons": result["reasons"],
                }
            )
    return items


def apply_takedowns(conn: sqlite3.Connection, *, at: datetime | None = None) -> list[str]:
    """按下线清单执行下线并对每项写 taken-down 流水。返回下线的用途编号。"""
    taken: list[str] = []
    for item in takedown_list(conn, at=at):
        ref = item["usage_ref"]
        usage = store.take_down_usage(conn, ref)
        store.record_decision(
            conn, ref, usage["asset_ref"], "taken-down", item["reasons"]
        )
        taken.append(ref)
    return taken


def asset_trace(conn: sqlite3.Connection, asset_ref: str) -> dict[str, Any]:
    """馆员全链路：从任一素材查清体验者、传承人、许可、用途与实际使用边界。"""
    asset = store.get_asset(conn, asset_ref)
    session = store.get_session(conn, asset["session_ref"]) if asset.get("session_ref") else None
    project = store.get_project(conn, asset["heritage_ref"]) if asset.get("heritage_ref") else None
    inheritor = (
        store.get_inheritor(conn, asset["inheritor_ref"])
        if asset.get("inheritor_ref") else None
    )
    translations = {}
    for unit_ref in store.list_asset_translation_units(conn, asset_ref):
        translations[unit_ref] = store.list_translation_revisions(conn, unit_ref)
    return {
        "asset": asset,
        "uploads": store.list_asset_uploads(conn, asset_ref),
        "project": project,
        "session": session,
        "inheritor": inheritor,
        "attendees": store.list_asset_attendees(conn, asset_ref),
        "youth_works": store.list_asset_youth_works(conn, asset_ref),
        "material_kits": store.list_asset_material_kits(conn, asset_ref),
        "translation_units": translations,
        "licenses": store.list_asset_licenses(conn, asset_ref),
        "usages": [
            {**u, "evaluation": evaluate_usage(conn, u)}
            for u in (
                dict(r) for r in conn.execute(
                    "SELECT * FROM media_usages WHERE asset_ref=? ORDER BY first_published_at",
                    (asset_ref,),
                ).fetchall()
            )
        ],
    }


def public_catalog(conn: sqlite3.Connection, *, at: datetime | None = None) -> dict[str, Any]:
    """公众视图：只有获准说明。

    - 项目说明与场次事实可见（活动发生过的事实不消失）；
    - 工序只见 public_summary，restricted_detail 不出现在输出中；
    - 媒体用途只列当前评估 allowed 的发布项；
    - 体验者、内部材料、许可等一律不出现。
    """
    at = at or datetime.now().astimezone()
    projects = []
    for project in store.list_projects(conn):
        projects.append({
            "ref": project["ref"],
            "name": project["name"],
            "public_summary": project["public_summary"],
            "process_steps": store.list_process_steps(
                conn, project["ref"], public_only=True
            ),
        })

    sessions = [
        {
            "ref": s["ref"],
            "heritage_ref": s["heritage_ref"],
            "title": s["title"],
            "venue": s["venue"],
            "occurred_at": s["occurred_at"],
        }
        for s in store.list_sessions(conn)
    ]

    visible_usages = []
    for usage in store.list_usages(conn, published_only=True):
        result = evaluate_usage(conn, usage, at=at)
        if result["decision"] != "allowed":
            continue
        asset = store.get_asset(conn, usage["asset_ref"])
        project = (
            store.get_project(conn, asset["heritage_ref"])
            if asset.get("heritage_ref") else None
        )
        visible_usages.append({
            "usage_ref": usage["usage_ref"],
            "usage_type": usage["usage_type"],
            "title": usage["title"],
            "channel": usage["channel"],
            "about_project": project["ref"] if project else None,
            "summary": project["public_summary"] if project else None,
        })

    return {
        "projects": projects,
        "sessions": sessions,
        "visible_usages": visible_usages,
        "generated_at": at.isoformat(timespec="seconds"),
    }
