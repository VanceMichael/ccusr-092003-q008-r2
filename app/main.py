"""河湟非遗体验传播授权 HTTP 服务。

路由分两组：
- /public/*：公众视图，只出现获准说明；许可失效的用途返回 410，
  但场次事实仍然可查（活动发生过的事实不消失）。
- /staff/*：馆员登记与追溯，从任一素材可查清体验者、传承人与使用边界。

仅依赖标准库；状态全部保存在 SQLite（DATABASE_PATH）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import authz, db, store

_DB_LOCK = threading.Lock()


def _connection():
    conn = db.connect()
    db.migrate(conn)
    return conn


def _parse_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.pop("at", None) if payload else None
    return store.parse_dt(raw) if raw else None


class Handler(BaseHTTPRequestHandler):
    server_version = "HeritageAuthz/1.0"

    # ---------- 基础收发 ----------

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise _HttpError(400, f"请求体不是合法 JSON：{exc}")
        if not isinstance(payload, dict):
            raise _HttpError(400, "请求体必须是 JSON 对象")
        return payload

    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return

    # ---------- 路由 ----------

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            with _DB_LOCK, _connection() as conn:
                handler = self._match(method, path, conn)
                if handler is None:
                    self._send(404, {"error": "未找到接口", "path": path})
                    return
                handler()
        except _HttpError as exc:
            self._send(exc.status, {"error": exc.message})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'")})
        except (ValueError, authz.AuthorizationError) as exc:
            self._send(400, {"error": str(exc)})
        except sqlite3.IntegrityError as exc:  # type: ignore[name-defined]
            self._send(409, {"error": f"记录冲突：{exc}"})

    def _match(self, method: str, path: str, conn) -> Callable[[], None] | None:
        rules: list[tuple[str, str, Callable[..., Any]]] = [
            ("GET", "/health", lambda: self._send(200, {"status": "ok"})),

            # 公众视图
            ("GET", "/public/catalog", lambda: self._public_catalog(conn)),
            ("GET", r"/public/process/(?P<ref>[^/]+)", lambda m: self._public_process(conn, m["ref"])),
            ("GET", r"/public/usages/(?P<ref>[^/]+)", lambda m: self._public_usage(conn, m["ref"])),

            # 馆员：基础登记
            ("POST", "/staff/projects", lambda: self._create_project(conn)),
            ("GET", "/staff/projects", lambda: self._send(200, {"projects": store.list_projects(conn)})),
            ("POST", "/staff/inheritors", lambda: self._create_inheritor(conn)),
            ("POST", "/staff/sessions", lambda: self._create_session(conn)),
            ("GET", "/staff/sessions", lambda: self._send(200, {"sessions": store.list_sessions(conn)})),
            ("POST", "/staff/process-steps", lambda: self._add_process_step(conn)),
            ("GET", r"/staff/projects/(?P<ref>[^/]+)/process-steps",
             lambda m: self._send(200, {"steps": store.list_process_steps(conn, m["ref"])})),
            ("GET", r"/staff/sessions/(?P<ref>[^/]+)/attendees",
             lambda m: self._send(200, {"attendees": store.list_session_attendees(conn, m["ref"])})),

            # 馆员：体验者、材料包、青年作品、口译
            ("POST", "/staff/attendees", lambda: self._register_attendee(conn)),
            ("POST", r"/staff/attendees/(?P<ref>[^/]+)/withdraw",
             lambda m: self._withdraw_attendee(conn, m["ref"])),
            ("POST", "/staff/material-kits", lambda: self._create_kit(conn)),
            ("POST", r"/staff/sessions/(?P<ref>[^/]+)/material-kits",
             lambda m: self._assign_kit(conn, m["ref"])),
            ("POST", "/staff/youth-works", lambda: self._create_youth_work(conn)),
            ("POST", "/staff/translations", lambda: self._add_translation(conn)),
            ("GET", r"/staff/translations/(?P<ref>[^/]+)",
             lambda m: self._send(200, {"revisions": store.list_translation_revisions(conn, m["ref"])})),

            # 馆员：素材与许可
            ("POST", "/staff/assets/uploads", lambda: self._upload_asset(conn)),
            ("GET", r"/staff/assets/by-sha/(?P<sha>[0-9a-fA-F]{64})",
             lambda m: self._send(200, {"asset": store.get_asset_by_sha(conn, m["sha"])})),
            ("POST", r"/staff/assets/(?P<ref>[^/]+)/links",
             lambda m: self._link_asset(conn, m["ref"])),
            ("GET", r"/staff/assets/(?P<ref>[^/]+)/trace",
             lambda m: self._send(200, authz.asset_trace(conn, m["ref"]))),
            ("POST", "/staff/licenses", lambda: self._grant_license(conn)),
            ("GET", r"/staff/assets/(?P<ref>[^/]+)/licenses",
             lambda m: self._send(200, {"licenses": store.list_asset_licenses(conn, m["ref"])})),
            ("POST", r"/staff/licenses/(?P<ref>[^/]+)/withdraw",
             lambda m: self._withdraw_license(conn, m["ref"])),

            # 馆员：用途与分发闸门
            ("POST", "/staff/usages", lambda: self._register_usage(conn)),
            ("GET", "/staff/usages", lambda: self._send(200, {"usages": store.list_usages(conn)})),
            ("POST", r"/staff/usages/(?P<ref>[^/]+)/gate",
             lambda m: self._gate_usage(conn, m["ref"])),
            ("GET", r"/staff/usages/(?P<ref>[^/]+)/decisions",
             lambda m: self._send(200, {"decisions": store.list_decisions(conn, m["ref"])})),
            ("GET", "/staff/takedowns", lambda: self._takedowns(conn)),
            ("POST", "/staff/takedowns/apply", lambda: self._apply_takedowns(conn)),
        ]
        for rule_method, pattern, fn in rules:
            if rule_method != method:
                continue
            if pattern.startswith("/") and "(" not in pattern:
                if path == pattern:
                    return fn
                continue
            match = re.fullmatch(pattern, path)
            if match:
                return lambda fn=fn, match=match: fn(match.groupdict())
        return None

    # ---------- 公众接口 ----------

    def _public_catalog(self, conn) -> None:
        payload = self._read_json_safe()
        at = _parse_at(payload)
        self._send(200, authz.public_catalog(conn, at=at))

    def _read_json_safe(self) -> dict[str, Any]:
        # GET 请求没有请求体；?at= 可用于按时间回放
        query = urlparse(self.path).query
        if not query:
            return {}
        values = parse_qs(query)
        return {"at": values["at"][0]} if "at" in values else {}

    def _public_process(self, conn, ref: str) -> None:
        # 关键工序只公开摘要：restricted_detail 字段根本不返回
        steps = store.list_process_steps(conn, ref, public_only=True)
        project = store.get_project(conn, ref)
        self._send(200, {
            "project": {"ref": project["ref"], "name": project["name"]},
            "steps": steps,
        })

    def _public_usage(self, conn, ref: str) -> None:
        query = urlparse(self.path).query
        at = None
        if query:
            values = parse_qs(query)
            at = store.parse_dt(values["at"][0]) if "at" in values else None
        usage = store.get_usage(conn, ref)
        result = authz.evaluate_usage(conn, usage, at=at)
        if result["decision"] == "taken-down":
            self._send(410, {
                "status": "gone",
                "usage_ref": ref,
                "message": "该报道/展项已下线；活动发生过的事实仍可在场次记录中查阅。",
                "reasons": result["reasons"],
            })
            return
        if result["decision"] == "blocked":
            self._send(403, {
                "status": "unavailable",
                "usage_ref": ref,
                "message": "该内容当前不在授权边界内，已阻止继续分发。",
                "reasons": result["reasons"],
            })
            return
        asset = store.get_asset(conn, usage["asset_ref"])
        project = store.get_project(conn, asset["heritage_ref"]) if asset.get("heritage_ref") else None
        self._send(200, {
            "status": "available",
            "usage_ref": ref,
            "usage_type": usage["usage_type"],
            "title": usage["title"],
            "channel": usage["channel"],
            "about_project": project["ref"] if project else None,
            "summary": project["public_summary"] if project else None,
        })

    # ---------- 馆员登记接口 ----------

    def _create_project(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "name", "public_summary"])
        self._send(201, store.create_project(conn, p["ref"], p["name"], p["public_summary"]))

    def _create_inheritor(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "heritage_ref", "public_name", "allowed_scopes"])
        self._send(201, store.create_inheritor(
            conn, p["ref"], p["heritage_ref"], p["public_name"],
            p["allowed_scopes"],
            full_process_allowed=p.get("full_process_allowed", False),
            portrait_allowed=p.get("portrait_allowed", True),
            notes=p.get("notes"),
        ))

    def _create_session(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "heritage_ref", "inheritor_ref", "title", "occurred_at"])
        self._send(201, store.create_session(
            conn, p["ref"], p["heritage_ref"], p["inheritor_ref"], p["title"],
            p["occurred_at"], venue=p.get("venue"),
        ))

    def _add_process_step(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["heritage_ref", "step_no", "name", "public_summary"])
        self._send(201, store.add_process_step(
            conn, p["heritage_ref"], int(p["step_no"]), p["name"],
            p["public_summary"], restricted_detail=p.get("restricted_detail"),
        ))

    def _register_attendee(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "session_ref", "public_label"])
        self._send(201, store.register_attendee(
            conn, p["ref"], p["session_ref"], p["public_label"],
            portrait_consent=p.get("portrait_consent", "denied"),
            consent_scopes=p.get("consent_scopes", []),
            consented_at=p.get("consented_at"),
        ))

    def _withdraw_attendee(self, conn, ref: str) -> None:
        self._read_json()
        self._send(200, store.withdraw_attendee_consent(conn, ref))

    def _create_kit(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "heritage_ref", "name", "allowed_scopes"])
        self._send(201, store.create_material_kit(
            conn, p["ref"], p["heritage_ref"], p["name"], p["allowed_scopes"],
            public_note=p.get("public_note"),
        ))

    def _assign_kit(self, conn, session_ref: str) -> None:
        p = self._read_json()
        self._require_fields(p, ["kit_ref"])
        store.assign_kit_to_session(conn, session_ref, p["kit_ref"], note=p.get("note"))
        self._send(200, {"session_ref": session_ref, "kit_ref": p["kit_ref"], "linked": True})

    def _create_youth_work(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["ref", "session_ref", "youth_ref", "title", "allowed_scopes"])
        self._send(201, store.create_youth_work(
            conn, p["ref"], p["session_ref"], p["youth_ref"], p["title"],
            p["allowed_scopes"],
        ))

    def _add_translation(self, conn) -> None:
        p = self._read_json()
        self._require_fields(
            p, ["unit_ref", "revision", "source_text", "translated_text", "language_pair"]
        )
        self._send(201, store.add_translation_revision(
            conn, p["unit_ref"], int(p["revision"]), p["source_text"],
            p["translated_text"], p["language_pair"],
            change_note=p.get("change_note"),
            heritage_ref=p.get("heritage_ref"),
            session_ref=p.get("session_ref"),
        ))

    def _upload_asset(self, conn) -> None:
        p = self._read_json()
        self._require_fields(p, ["sha256", "kind", "title", "outlet_ref", "claimed_basis"])
        asset, deduplicated = store.register_asset_upload(
            conn,
            sha256=p["sha256"], kind=p["kind"], title=p["title"],
            outlet_ref=p["outlet_ref"], claimed_basis=p["claimed_basis"],
            asset_ref=p.get("asset_ref"),
            session_ref=p.get("session_ref"),
            heritage_ref=p.get("heritage_ref"),
            inheritor_ref=p.get("inheritor_ref"),
            contains_full_process=p.get("contains_full_process", False),
            technical_fingerprint=p.get("technical_fingerprint"),
            note=p.get("note"),
        )
        self._send(201 if not deduplicated else 200, {
            "asset": asset,
            "deduplicated": deduplicated,
            "message": "同一 sha256 素材已存在，本次上传已归并，不产生新许可"
            if deduplicated else "新素材已登记",
            "uploads": store.list_asset_uploads(conn, asset["asset_ref"]),
        })

    def _link_asset(self, conn, ref: str) -> None:
        p = self._read_json()
        if "attendee_refs" in p:
            store.link_asset_attendees(conn, ref, p["attendee_refs"])
        if "youth_work_refs" in p:
            store.link_asset_youth_works(conn, ref, p["youth_work_refs"])
        if "material_kit_refs" in p:
            store.link_asset_material_kits(conn, ref, p["material_kit_refs"])
        if "translation_unit_refs" in p:
            store.link_asset_translations(conn, ref, p["translation_unit_refs"])
        self._send(200, {"asset_ref": ref, "linked": True})

    def _grant_license(self, conn) -> None:
        p = self._read_json()
        self._require_fields(
            p, ["license_ref", "asset_ref", "grantee_ref", "usage_scopes", "valid_from"]
        )
        self._send(201, store.grant_license(
            conn, p["license_ref"], p["asset_ref"], p["grantee_ref"],
            p["usage_scopes"], p["valid_from"],
            valid_until=p.get("valid_until"),
            covers_full_process=p.get("covers_full_process", False),
        ))

    def _withdraw_license(self, conn, ref: str) -> None:
        p = self._read_json()
        self._send(200, store.withdraw_license(conn, ref, reason=p.get("reason")))

    def _register_usage(self, conn) -> None:
        p = self._read_json()
        self._require_fields(
            p, ["usage_ref", "asset_ref", "outlet_ref", "usage_type", "title"]
        )
        self._send(201, store.register_usage(
            conn, p["usage_ref"], p["asset_ref"], p["outlet_ref"],
            p["usage_type"], p["title"], channel=p.get("channel"),
            first_published_at=p.get("first_published_at"),
        ))

    def _gate_usage(self, conn, ref: str) -> None:
        p = self._read_json()
        at = _parse_at(p)
        persist = p.get("persist", True)
        result = authz.gate_usage(conn, ref, at=at, persist=persist)
        status = 200 if result["decision"] == "allowed" else 403
        self._send(status, result)

    def _takedowns(self, conn) -> None:
        query = urlparse(self.path).query
        at = None
        if query:
            values = parse_qs(query)
            if "at" in values:
                at = store.parse_dt(values["at"][0])
        items = authz.takedown_list(conn, at=at)
        self._send(200, {"count": len(items), "must_take_down": items})

    def _apply_takedowns(self, conn) -> None:
        p = self._read_json()
        at = _parse_at(p)
        taken = authz.apply_takedowns(conn, at=at)
        self._send(200, {"taken_down": taken, "count": len(taken)})

    # ---------- 工具 ----------

    @staticmethod
    def _require_fields(payload: dict[str, Any], fields: list[str]) -> None:
        missing = [f for f in fields if f not in payload]
        if missing:
            raise _HttpError(400, f"缺少必填字段：{', '.join(missing)}")


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# sqlite3 在异常处理中按名称引用（模块顶部导入）。


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    with _connection() as conn:
        db.migrate(conn)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
