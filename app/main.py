"""河湟非遗体验传播授权 HTTP 服务。

/staff/* 为馆员侧登记、授权与追溯接口；/public/* 只返回获准公开的说明。
所有请求与响应均为 application/json；时间字段使用带偏移量的 ISO 8601。
"""

from __future__ import annotations

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from app.store import DomainError, Store


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(os.getenv("DATABASE_PATH", "data/app.sqlite3"))


class Handler(BaseHTTPRequestHandler):
    server_version = "HeritageAuth/1.0"

    # ------------------------------------------------------------------ GET

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/health":
                self._write_json({"status": "ok"})
                return
            with _connect() as conn:
                store = Store(conn)
                if path == "/public/heritage":
                    self._write_json({"items": store.public_heritage()})
                    return
                if path == "/public/sessions":
                    self._write_json({"items": store.public_sessions()})
                    return
                if path == "/public/reports":
                    self._write_json({"items": store.public_reports()})
                    return
                if path == "/public/youth-works":
                    self._write_json({"items": store.public_youth_works()})
                    return
                if path.startswith("/public/assets/"):
                    self._write_json(store.public_asset(path.rsplit("/", 1)[1]))
                    return
                if path == "/staff/takedowns":
                    self._write_json(store.takedown_list())
                    return
                if path == "/staff/audit":
                    self._write_json({"events": store.audit_events()})
                    return
                if path.startswith("/staff/trace/"):
                    self._write_json(store.trace(path.split("/staff/trace/", 1)[1]))
                    return
                if path.startswith("/staff/grants/"):
                    self._write_json(store.get_grant(path.rsplit("/", 1)[1]))
                    return
                if path.startswith("/staff/documents/"):
                    doc = store.get_document(path.rsplit("/", 1)[1])
                    doc["revisions"] = store.list_revisions(doc["doc_ref"])["revisions"]
                    self._write_json(doc)
                    return
                if path.startswith("/staff/assets/"):
                    asset = store._resolve_asset(path.rsplit("/", 1)[1])
                    self._write_json(dict(asset))
                    return
            self._write_json({"error": f"未知路径：{path}"}, 404)
        except DomainError as exc:
            self._write_json({"error": str(exc)}, exc.status)

    # ----------------------------------------------------------------- POST

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        conn = _connect()
        store = Store(conn)
        try:
            payload = self._read_json()
            result = self._dispatch(store, path, payload)
            conn.commit()
        except DomainError as exc:
            # 业务拒绝（如分发拦截）也要保留审计痕迹
            conn.commit()
            self._write_json({"error": str(exc)}, exc.status)
            return
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            conn.rollback()
            self._write_json({"error": f"请求体无效：{exc}"}, 400)
            return
        finally:
            conn.close()
        if result is not None:
            # 查询型 POST（分发闸门、批量下线）不创建资源，返回 200
            query_actions = ("/staff/distribution-check", "/staff/takedowns/apply")
            status = 200 if path in query_actions else 201
            self._write_json(result, status)

    def _dispatch(self, store: Store, path: str, payload: dict):
        routes = (
            ("/staff/heritage", store.create_heritage,
             ("heritage_ref", "name"), ("summary",)),
            ("/staff/sessions", store.create_session,
             ("session_ref", "heritage_ref", "inheritor_ref", "title", "occurred_at"),
             ("location_ref", "fact_note")),
            ("/staff/material-kits", store.create_material_kit,
             ("material_kit_ref", "heritage_ref", "name"), ("contents_summary",)),
            ("/staff/assets", store.register_asset,
             ("asset_ref", "fingerprint_sha256", "title"),
             ("media_type", "source_ref", "captured_at")),
            ("/staff/distribution-check", store.distribution_check,
             ("grantee_org", "ref_or_fingerprint", "scopes"), ()),
        )
        for route, fn, required, optional in routes:
            if path == route:
                kwargs = self._kwargs(payload, required, optional)
                return fn(**kwargs)

        if path == "/staff/inheritors/consents":
            return store.set_inheritor_consent(
                **self._kwargs(payload, ("inheritor_ref", "heritage_ref", "allowed_scopes"),
                               ("consent_note",)))
        if path == "/staff/youth-works":
            return store.create_youth_work(
                **self._kwargs(payload, ("work_ref", "session_ref", "creator_participant_ref",
                                         "title"),
                               ("public_summary", "display_permission", "material_kit_ref")))
        if path == "/staff/documents":
            return store.create_document(
                **self._kwargs(payload, ("doc_ref", "heritage_ref", "title",
                                         "is_key_process"),
                               ("public_summary", "session_ref", "stage_key")))
        if path == "/staff/grants":
            return store.issue_grant(
                **self._kwargs(payload, ("grant_ref", "ref_or_fingerprint", "grantee_org",
                                         "scope", "valid_from", "expires_at"),
                               ("purpose_note", "authority")))
        if path == "/staff/reports":
            return store.publish_report(
                **self._kwargs(payload, ("report_ref", "grantee_org", "ref_or_fingerprint",
                                         "title", "required_scopes"),
                               ("location_ref",)))
        if path == "/staff/exhibits":
            return store.create_exhibit(
                **self._kwargs(payload, ("exhibit_ref", "grantee_org", "target_kind",
                                         "target_ref", "title", "required_scopes"),
                               ("venue_ref",)))
        if path == "/staff/takedowns/apply":
            return {"taken_down": store.apply_takedowns(payload.get("reason", ""))}

        prefix_routes = (
            ("/staff/sessions/", self._session_subroute),
            ("/staff/inheritors/", self._inheritor_subroute),
            ("/staff/youth-works/", self._work_subroute),
            ("/staff/documents/", self._document_subroute),
            ("/staff/assets/", self._asset_subroute),
            ("/staff/grants/", self._grant_subroute),
        )
        for prefix, fn in prefix_routes:
            if path.startswith(prefix):
                return fn(store, path[len(prefix):], payload)
        raise DomainError(f"未知路径：{path}", 404)

    # ----------------------------------------------------- 子资源路由（POST）

    def _session_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        session_ref, _, action = tail.partition("/")
        if not session_ref:
            raise DomainError("缺少场次编号", 404)
        if action == "participants":
            return store.add_participant(
                **self._kwargs(payload, ("participant_ref", "role"),
                               ("portrait_consent",)), session_ref=session_ref)
        raise DomainError(f"场次下未知操作：{action}", 404)

    def _inheritor_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        inheritor_ref, _, action = tail.partition("/")
        if action == "withdraw":
            return store.withdraw_inheritor_consent(
                inheritor_ref, payload.get("reason", ""))
        raise DomainError(f"传承人下未知操作：{action}", 404)

    def _work_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        work_ref, _, action = tail.partition("/")
        if action == "display":
            return store.set_work_display_permission(work_ref, bool(payload.get("permission")))
        raise DomainError(f"作品下未知操作：{action}", 404)

    def _document_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        doc_ref, _, action = tail.partition("/")
        if action == "revisions":
            return store.add_revision(
                doc_ref, payload["source_ref"], payload["source_sha256"],
                payload["translations"], payload.get("change_note", ""))
        raise DomainError(f"文档下未知操作：{action}", 404)

    def _asset_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        ref, _, action = tail.partition("/")
        if action == "captures":
            return store.add_capture(ref, payload["uploader_org"], payload.get("note", ""))
        if action == "links":
            return store.link_asset(ref, payload["target_kind"], payload["target_ref"])
        raise DomainError(f"素材下未知操作：{action}", 404)

    def _grant_subroute(self, store: Store, tail: str, payload: dict) -> dict:
        grant_ref, _, action = tail.partition("/")
        if action == "revoke":
            return store.revoke_grant(grant_ref, payload.get("reason", ""))
        raise DomainError(f"许可下未知操作：{action}", 404)

    # ------------------------------------------------------------- 序列化

    @staticmethod
    def _kwargs(payload: dict, required: tuple, optional: tuple) -> dict:
        missing = [key for key in required if key not in payload]
        if missing:
            raise DomainError(f"缺少必填字段：{', '.join(missing)}", 400)
        kwargs = {key: payload[key] for key in required}
        for key in optional:
            if key in payload and payload[key] is not None:
                kwargs[key] = payload[key]
        return kwargs

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, body: object, status: int = 200) -> None:
        encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
