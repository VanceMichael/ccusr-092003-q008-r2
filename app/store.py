"""传播授权领域逻辑。

所有时间均为带偏移量的 ISO 8601 文本；原始材料只保存受控引用或 sha256 指纹。
核心不变量：
1. 素材以 sha256 去重，重复上传是同一素材的多条上传记录；
2. 关键工序对外只有 public_summary，原文每次修订必须与同序号译文配对；
3. 分发（报道/展项）必须被当前有效的许可与同意共同覆盖，否则被拦截；
4. 许可撤回/到期导致不合规的报道与展项进入下线清单并被阻止继续分发；
5. 场次、参与者、上传记录、审计事件只追加——活动事实不因撤回而消失。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

SCOPES = ("event-report", "full-process", "portrait", "internal-archive")
PUBLIC_SCOPES = ("event-report", "full-process", "portrait")


class DomainError(Exception):
    """业务规则冲突。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def parse_iso(value: str) -> datetime:
    """解析带偏移量的 ISO 8601 时间，拒绝无时区文本。"""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DomainError(f"时间格式应为带偏移量的 ISO 8601：{value!r}", 400) from exc
    if parsed.tzinfo is None:
        raise DomainError(f"时间必须带 UTC 偏移量：{value!r}", 400)
    return parsed.astimezone(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, connection: sqlite3.Connection, clock: Callable[[], str] = now_iso) -> None:
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.clock = clock

    # ------------------------------------------------------------------ 基础

    def _audit(self, action: str, entity_kind: str = "", entity_ref: str = "", **detail: Any) -> None:
        self.conn.execute(
            "INSERT INTO audit_events(event_time, actor, action, entity_kind, entity_ref, detail_json)"
            " VALUES (?,?,?,?,?,?)",
            (self.clock(), detail.pop("actor", ""), action, entity_kind, entity_ref,
             _json_dumps(detail)),
        )

    _PRIMARY_REF_COLUMN = {
        "heritage_projects": "heritage_ref",
        "inheritor_consents": "inheritor_ref",
        "sessions": "session_ref",
        "material_kits": "material_kit_ref",
        "youth_works": "work_ref",
        "process_documents": "doc_ref",
        "media_assets": "asset_ref",
        "media_grants": "grant_ref",
        "reports": "report_ref",
        "exhibits": "exhibit_ref",
    }

    def _require(self, table: str, ref: str, column: str | None = None) -> sqlite3.Row:
        col = column or self._PRIMARY_REF_COLUMN.get(table)
        if col is None:
            raise DomainError(f"表 {table} 缺少主键列映射", 500)
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE {col} = ?", (ref,)
        ).fetchone()
        if row is None:
            raise DomainError(f"{table} 不存在：{ref}", 404)
        return row

    def _resolve_asset(self, ref_or_fingerprint: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM media_assets WHERE asset_ref = ? OR fingerprint_sha256 = ?",
            (ref_or_fingerprint, ref_or_fingerprint),
        ).fetchone()
        if row is None:
            raise DomainError(f"素材不存在：{ref_or_fingerprint}", 404)
        return row

    # ------------------------------------------------------------ 登记类操作

    def create_heritage(self, heritage_ref: str, name: str, summary: str = "") -> dict:
        self._insert(
            "INSERT INTO heritage_projects(heritage_ref, name, summary, created_at)"
            " VALUES (?,?,?,?)",
            (heritage_ref, name, summary, self.clock()),
        )
        self._audit("heritage.create", "heritage", heritage_ref, name=name)
        return self.get_heritage(heritage_ref)

    def get_heritage(self, heritage_ref: str) -> dict:
        return dict(self._require("heritage_projects", heritage_ref))

    def set_inheritor_consent(
        self, inheritor_ref: str, heritage_ref: str, allowed_scopes: Iterable[str],
        consent_note: str = "",
    ) -> dict:
        scopes = _normalize_scopes(allowed_scopes, allow_internal=True)
        self._require("heritage_projects", heritage_ref)
        self.conn.execute(
            "INSERT INTO inheritor_consents(inheritor_ref, heritage_ref, allowed_scopes,"
            " consent_note, consented_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(inheritor_ref) DO UPDATE SET"
            " heritage_ref=excluded.heritage_ref, allowed_scopes=excluded.allowed_scopes,"
            " consent_note=excluded.consent_note, consented_at=excluded.consented_at,"
            " withdrawn_at=NULL",
            (inheritor_ref, heritage_ref, ",".join(scopes), consent_note, self.clock()),
        )
        self._audit("inheritor.consent.set", "inheritor", inheritor_ref,
                    heritage_ref=heritage_ref, allowed_scopes=scopes)
        return self.get_inheritor(inheritor_ref)

    def withdraw_inheritor_consent(self, inheritor_ref: str, reason: str) -> dict:
        self._require("inheritor_consents", inheritor_ref)
        self.conn.execute(
            "UPDATE inheritor_consents SET withdrawn_at=? WHERE inheritor_ref=?",
            (self.clock(), inheritor_ref),
        )
        self._audit("inheritor.consent.withdraw", "inheritor", inheritor_ref, reason=reason)
        return self.get_inheritor(inheritor_ref)

    def get_inheritor(self, inheritor_ref: str) -> dict:
        row = self._require("inheritor_consents", inheritor_ref)
        return self._consent_view(row)

    def create_session(
        self, session_ref: str, heritage_ref: str, inheritor_ref: str, title: str,
        occurred_at: str, location_ref: str = "", fact_note: str = "",
    ) -> dict:
        parse_iso(occurred_at)
        self._require("heritage_projects", heritage_ref)
        self._require("inheritor_consents", inheritor_ref)
        self._insert(
            "INSERT INTO sessions(session_ref, heritage_ref, inheritor_ref, title,"
            " occurred_at, location_ref, fact_note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (session_ref, heritage_ref, inheritor_ref, title, occurred_at,
             location_ref, fact_note, self.clock()),
        )
        self._audit("session.create", "session", session_ref, heritage_ref=heritage_ref,
                    inheritor_ref=inheritor_ref, occurred_at=occurred_at)
        return self.get_session(session_ref)

    def get_session(self, session_ref: str) -> dict:
        return dict(self._require("sessions", session_ref))

    def add_participant(
        self, session_ref: str, participant_ref: str, role: str, portrait_consent: bool = False
    ) -> dict:
        self._require("sessions", session_ref)
        self._insert(
            "INSERT INTO participants(session_ref, participant_ref, role, portrait_consent,"
            " created_at) VALUES (?,?,?,?,?)",
            (session_ref, participant_ref, role, int(portrait_consent), self.clock()),
        )
        self._audit("participant.add", "session", session_ref,
                    participant_ref=participant_ref, role=role, portrait_consent=portrait_consent)
        return self.get_participant(session_ref, participant_ref)

    def get_participant(self, session_ref: str, participant_ref: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM participants WHERE session_ref=? AND participant_ref=?",
            (session_ref, participant_ref),
        ).fetchone()
        if row is None:
            raise DomainError(f"参与者不存在：{session_ref}/{participant_ref}", 404)
        return dict(row)

    def set_portrait_consent(
        self, session_ref: str, participant_ref: str, consent: bool
    ) -> dict:
        self.get_participant(session_ref, participant_ref)
        self.conn.execute(
            "UPDATE participants SET portrait_consent=? WHERE session_ref=? AND participant_ref=?",
            (int(consent), session_ref, participant_ref),
        )
        self._audit("participant.portrait.update", "participant", participant_ref,
                    session_ref=session_ref, portrait_consent=consent)
        return self.get_participant(session_ref, participant_ref)

    def create_material_kit(
        self, material_kit_ref: str, heritage_ref: str, name: str, contents_summary: str = ""
    ) -> dict:
        self._require("heritage_projects", heritage_ref)
        self._insert(
            "INSERT INTO material_kits(material_kit_ref, heritage_ref, name,"
            " contents_summary, created_at) VALUES (?,?,?,?,?)",
            (material_kit_ref, heritage_ref, name, contents_summary, self.clock()),
        )
        self._audit("material_kit.create", "material_kit", material_kit_ref, name=name)
        return dict(self._require("material_kits", material_kit_ref))

    def create_youth_work(
        self, work_ref: str, session_ref: str, creator_participant_ref: str,
        title: str, public_summary: str = "", display_permission: bool = False,
        material_kit_ref: str | None = None,
    ) -> dict:
        self._require("sessions", session_ref)
        self.get_participant(session_ref, creator_participant_ref)
        if material_kit_ref:
            self._require("material_kits", material_kit_ref)
        self._insert(
            "INSERT INTO youth_works(work_ref, session_ref, creator_participant_ref,"
            " material_kit_ref, title, public_summary, display_permission, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (work_ref, session_ref, creator_participant_ref, material_kit_ref, title,
             public_summary, int(display_permission), self.clock()),
        )
        self._audit("youth_work.create", "work", work_ref, session_ref=session_ref,
                    creator_participant_ref=creator_participant_ref,
                    display_permission=display_permission)
        return dict(self._require("youth_works", work_ref, "work_ref"))

    def set_work_display_permission(self, work_ref: str, permission: bool) -> dict:
        self._require("youth_works", work_ref, "work_ref")
        self.conn.execute(
            "UPDATE youth_works SET display_permission=? WHERE work_ref=?",
            (int(permission), work_ref),
        )
        self._audit("youth_work.display.update", "work", work_ref, display_permission=permission)
        return dict(self._require("youth_works", work_ref, "work_ref"))

    def create_document(
        self, doc_ref: str, heritage_ref: str, title: str, is_key_process: bool,
        public_summary: str = "", session_ref: str | None = None, stage_key: str = "",
    ) -> dict:
        self._require("heritage_projects", heritage_ref)
        if session_ref:
            self._require("sessions", session_ref)
        self._insert(
            "INSERT INTO process_documents(doc_ref, heritage_ref, session_ref, stage_key,"
            " title, is_key_process, public_summary, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (doc_ref, heritage_ref, session_ref, stage_key, title,
             int(is_key_process), public_summary, self.clock()),
        )
        self._audit("document.create", "document", doc_ref,
                    is_key_process=is_key_process, heritage_ref=heritage_ref)
        return self.get_document(doc_ref)

    def get_document(self, doc_ref: str) -> dict:
        row = self._require("process_documents", doc_ref, "doc_ref")
        return dict(row)

    def add_revision(
        self, doc_ref: str, source_ref: str, source_sha256: str,
        translations: list[dict], change_note: str = "",
    ) -> dict:
        """登记一次原文修订；同一序号必须携带至少一份译文，原文与译文改动保持对应。"""
        self._require("process_documents", doc_ref, "doc_ref")
        _require_sha256(source_sha256)
        if not translations:
            raise DomainError("原文修订必须附带至少一份对应译文（同序号）", 422)
        languages: set[str] = set()
        for item in translations:
            language = item.get("language")
            if not language:
                raise DomainError("译文缺少 language", 400)
            if language in languages:
                raise DomainError(f"译文语言重复：{language}", 400)
            languages.add(language)
            _require_sha256(item.get("translation_sha256", ""))
            if not item.get("translation_ref"):
                raise DomainError("译文缺少 translation_ref", 400)

        next_seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM document_revisions WHERE doc_ref=?", (doc_ref,)
        ).fetchone()[0]
        revised_at = self.clock()
        with self.conn:
            self.conn.execute(
                "INSERT INTO document_revisions(doc_ref, seq, source_ref, source_sha256,"
                " change_note, revised_at) VALUES (?,?,?,?,?,?)",
                (doc_ref, next_seq, source_ref, source_sha256, change_note, revised_at),
            )
            for item in translations:
                self.conn.execute(
                    "INSERT INTO translation_revisions(doc_ref, seq, language,"
                    " translation_ref, translation_sha256, translator_ref, revised_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (doc_ref, next_seq, item["language"], item["translation_ref"],
                     item["translation_sha256"], item.get("translator_ref", ""), revised_at),
                )
        self._audit("document.revision.add", "document", doc_ref, seq=next_seq,
                    languages=sorted(languages), change_note=change_note)
        return self.list_revisions(doc_ref)

    def list_revisions(self, doc_ref: str) -> dict:
        self._require("process_documents", doc_ref, "doc_ref")
        pairs = []
        rows = self.conn.execute(
            "SELECT r.*, t.language, t.translation_ref, t.translation_sha256,"
            " t.translator_ref FROM document_revisions r"
            " LEFT JOIN translation_revisions t ON t.doc_ref=r.doc_ref AND t.seq=r.seq"
            " WHERE r.doc_ref=? ORDER BY r.seq, t.language",
            (doc_ref,),
        ).fetchall()
        by_seq: dict[int, dict] = {}
        for row in rows:
            pair = by_seq.setdefault(row["seq"], {
                "seq": row["seq"], "source_ref": row["source_ref"],
                "source_sha256": row["source_sha256"], "change_note": row["change_note"],
                "revised_at": row["revised_at"], "translations": [],
            })
            if row["language"]:
                pair["translations"].append({
                    "language": row["language"], "translation_ref": row["translation_ref"],
                    "translation_sha256": row["translation_sha256"],
                    "translator_ref": row["translator_ref"],
                })
        return {"doc_ref": doc_ref, "revisions": [by_seq[k] for k in sorted(by_seq)]}

    # ------------------------------------------------------------- 素材与上传

    def register_asset(
        self, asset_ref: str, fingerprint_sha256: str, title: str,
        media_type: str = "video", source_ref: str = "", captured_at: str | None = None,
    ) -> dict:
        """按指纹登记素材；指纹已存在时识别为同一素材（deduped=True），不另建许可主体。"""
        _require_sha256(fingerprint_sha256)
        if captured_at:
            parse_iso(captured_at)
        existing = self.conn.execute(
            "SELECT * FROM media_assets WHERE fingerprint_sha256=?", (fingerprint_sha256,)
        ).fetchone()
        if existing is not None:
            view = dict(existing)
            view["deduped"] = True
            self._audit("asset.dedupe", "asset", existing["asset_ref"],
                        uploaded_ref=asset_ref, fingerprint_sha256=fingerprint_sha256)
            return view
        self._insert(
            "INSERT INTO media_assets(asset_ref, fingerprint_sha256, media_type, title,"
            " source_ref, captured_at, created_at) VALUES (?,?,?,?,?,?,?)",
            (asset_ref, fingerprint_sha256, media_type, title, source_ref,
             captured_at, self.clock()),
        )
        self._audit("asset.register", "asset", asset_ref, fingerprint_sha256=fingerprint_sha256)
        view = self._asset_view(asset_ref)
        view["deduped"] = False
        return view

    def add_capture(self, ref_or_fingerprint: str, uploader_org: str, note: str = "") -> dict:
        asset = self._resolve_asset(ref_or_fingerprint)
        cur = self.conn.execute(
            "INSERT INTO media_captures(asset_ref, uploader_org, uploaded_at, note)"
            " VALUES (?,?,?,?)",
            (asset["asset_ref"], uploader_org, self.clock(), note),
        )
        self._audit("asset.capture", "asset", asset["asset_ref"],
                    capture_id=cur.lastrowid, uploader_org=uploader_org, note=note)
        return {
            "capture_id": cur.lastrowid, "asset_ref": asset["asset_ref"],
            "uploader_org": uploader_org, "note": note,
        }

    def link_asset(self, ref_or_fingerprint: str, target_kind: str, target_ref: str) -> dict:
        asset = self._resolve_asset(ref_or_fingerprint)
        valid = {"heritage", "session", "inheritor", "participant", "work", "document",
                 "material_kit"}
        if target_kind not in valid:
            raise DomainError(f"未知关联类型：{target_kind}", 400)
        if target_kind == "participant":
            # 参与者编号需要配合场次，约定 target_ref 为 "SESSION/PARTICIPANT"
            if "/" not in target_ref:
                raise DomainError("参与者关联需使用 session_ref/participant_ref", 400)
        self._insert(
            "INSERT INTO asset_links(asset_ref, target_kind, target_ref) VALUES (?,?,?)",
            (asset["asset_ref"], target_kind, target_ref),
        )
        self._audit("asset.link", "asset", asset["asset_ref"],
                    target_kind=target_kind, target_ref=target_ref)
        return self._asset_view(asset["asset_ref"])

    def _asset_view(self, asset_ref: str) -> dict:
        return dict(self._require("media_assets", asset_ref))

    # ---------------------------------------------------------------- 许可

    def issue_grant(
        self, grant_ref: str, ref_or_fingerprint: str, grantee_org: str, scope: str,
        valid_from: str, expires_at: str, purpose_note: str = "", authority: str = "",
    ) -> dict:
        """授予媒体用途；授予时即核验传承人展示边界与参与者肖像同意。"""
        asset = self._resolve_asset(ref_or_fingerprint)
        if scope not in SCOPES:
            raise DomainError(f"未知许可范围：{scope}", 400)
        start = parse_iso(valid_from)
        end = parse_iso(expires_at)
        if end <= start:
            raise DomainError("expires_at 必须晚于 valid_from", 400)
        if scope != "internal-archive":
            blockers = self._scope_basis_blockers(asset["asset_ref"], scope, start)
            if blockers:
                self._audit("grant.issue.blocked", "asset", asset["asset_ref"],
                            grantee_org=grantee_org, scope=scope, blockers=blockers)
                raise DomainError("许可授予被拒绝：" + "；".join(blockers), 422)
        self._insert(
            "INSERT INTO media_grants(grant_ref, asset_ref, grantee_org, scope, purpose_note,"
            " authority, granted_at, valid_from, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (grant_ref, asset["asset_ref"], grantee_org, scope, purpose_note, authority,
             self.clock(), valid_from, expires_at),
        )
        self._audit("grant.issue", "grant", grant_ref, asset_ref=asset["asset_ref"],
                    grantee_org=grantee_org, scope=scope, expires_at=expires_at)
        return self.get_grant(grant_ref)

    def get_grant(self, grant_ref: str) -> dict:
        row = self._require("media_grants", grant_ref, "grant_ref")
        return self._grant_view(row)

    def revoke_grant(self, grant_ref: str, reason: str) -> dict:
        """撤回许可，并立即把失去覆盖的报道与展项下线。"""
        self._require("media_grants", grant_ref, "grant_ref")
        self.conn.execute(
            "UPDATE media_grants SET status='revoked', revoked_at=?, revoke_reason=?"
            " WHERE grant_ref=?",
            (self.clock(), reason, grant_ref),
        )
        self._audit("grant.revoke", "grant", grant_ref, reason=reason)
        taken_down = self.apply_takedowns(reason=f"授权撤回（{grant_ref}）：{reason}")
        view = self.get_grant(grant_ref)
        view["taken_down_items"] = taken_down
        return view

    def _grant_view(self, row: sqlite3.Row) -> dict:
        view = dict(row)
        view["currently_valid"] = self._grant_valid_at(row, parse_iso(self.clock()))
        return view

    @staticmethod
    def _grant_valid_at(row: sqlite3.Row, at: datetime) -> bool:
        if row["status"] != "active":
            return False
        return parse_iso(row["valid_from"]) <= at <= parse_iso(row["expires_at"])

    def _valid_grants(self, asset_ref: str, grantee_org: str, at: datetime) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM media_grants WHERE asset_ref=? AND grantee_org=?",
            (asset_ref, grantee_org),
        ).fetchall()
        return [r for r in rows if self._grant_valid_at(r, at)]

    # ------------------------------------------------------- 同意边界核验

    def _inheritor_blockers(self, asset_ref: str, scope: str) -> list[str]:
        # 同意基础只认素材实际关联的传承人，或素材关联场次的传承人；
        # 不能用同项目下“另一位传承人”的同意覆盖本素材。
        holder_refs = {
            r["target_ref"] for r in self.conn.execute(
                "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='inheritor'",
                (asset_ref,))
        }
        for row in self.conn.execute(
            "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='session'",
            (asset_ref,),
        ):
            holder_refs.add(self.get_session(row["target_ref"])["inheritor_ref"])
        if not holder_refs:
            return [f"素材未关联传承人或场次，无法核验 {scope} 展示边界"]
        allowed = False
        for ref in holder_refs:
            consent = self.conn.execute(
                "SELECT * FROM inheritor_consents WHERE inheritor_ref=?", (ref,)
            ).fetchone()
            if consent is None or consent["withdrawn_at"]:
                continue
            if scope in (consent["allowed_scopes"] or "").split(","):
                allowed = True
        if not allowed:
            return [f"传承人当前未允许展示范围：{scope}"]
        return []

    def _depicted_participants(self, asset_ref: str) -> list[dict]:
        refs: set[tuple[str, str]] = set()
        for row in self.conn.execute(
            "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='participant'",
            (asset_ref,),
        ):
            session_ref, participant_ref = row["target_ref"].split("/", 1)
            refs.add((session_ref, participant_ref))
        for row in self.conn.execute(
            "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='session'",
            (asset_ref,),
        ):
            for p in self.conn.execute(
                "SELECT session_ref, participant_ref, role, portrait_consent"
                " FROM participants WHERE session_ref=?", (row["target_ref"],)
            ):
                refs.add((p["session_ref"], p["participant_ref"]))
        return [
            self.get_participant(session_ref, participant_ref)
            for session_ref, participant_ref in sorted(refs)
        ]

    def _scope_basis_blockers(self, asset_ref: str, scope: str, at: datetime | None = None) -> list[str]:
        """核验“许可之外”的授权基础：传承人同意与参与者肖像同意。"""
        blockers: list[str] = []
        if scope in ("event-report", "full-process"):
            blockers.extend(self._inheritor_blockers(asset_ref, scope))
        elif scope == "portrait":
            depicted = self._depicted_participants(asset_ref)
            if not depicted:
                blockers.append("素材未关联可识别参与者，无法授予肖像范围")
            missing = [p["participant_ref"] for p in depicted if not p["portrait_consent"]]
            if missing:
                blockers.append("参与者肖像缺少同意：" + ",".join(missing))
        return blockers

    def evaluate_asset_use(
        self, asset_ref: str, grantee_org: str, scopes: Iterable[str], at: datetime | None = None
    ) -> list[str]:
        """当前分发是否合规，返回拦截原因列表（空列表表示放行）。"""
        at = at or parse_iso(self.clock())
        blockers: list[str] = []
        valid = self._valid_grants(asset_ref, grantee_org, at)
        for scope in _normalize_scopes(scopes):
            if scope == "internal-archive":
                blockers.append("internal-archive 仅可内部存档，不得公开发布")
                continue
            if not any(g["scope"] == scope for g in valid):
                blockers.append(f"缺少当前有效的 {scope} 许可（{grantee_org}）")
                continue
            blockers.extend(self._scope_basis_blockers(asset_ref, scope, at))
        return blockers

    # ------------------------------------------------------------ 报道与展项

    def publish_report(
        self, report_ref: str, grantee_org: str, ref_or_fingerprint: str, title: str,
        required_scopes: Iterable[str], location_ref: str = "",
    ) -> dict:
        asset = self._resolve_asset(ref_or_fingerprint)
        scopes = _normalize_scopes(required_scopes)
        blockers = self.evaluate_asset_use(asset["asset_ref"], grantee_org, scopes)
        if blockers:
            self._audit("report.publish.blocked", "report", report_ref,
                        grantee_org=grantee_org, asset_ref=asset["asset_ref"], blockers=blockers)
            raise DomainError("报道分发被阻止：" + "；".join(blockers), 409)
        self._insert(
            "INSERT INTO reports(report_ref, grantee_org, asset_ref, title, location_ref,"
            " required_scopes, published_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (report_ref, grantee_org, asset["asset_ref"], title, location_ref,
             ",".join(scopes), self.clock(), self.clock()),
        )
        self._audit("report.publish", "report", report_ref, asset_ref=asset["asset_ref"],
                    grantee_org=grantee_org, required_scopes=scopes)
        return self.get_report(report_ref)

    def get_report(self, report_ref: str) -> dict:
        return dict(self._require("reports", report_ref, "report_ref"))

    def create_exhibit(
        self, exhibit_ref: str, grantee_org: str, target_kind: str, target_ref: str,
        title: str, required_scopes: Iterable[str], venue_ref: str = "",
    ) -> dict:
        if target_kind not in ("asset", "work", "document"):
            raise DomainError("展项目标只能是 asset/work/document", 400)
        scopes = _normalize_scopes(required_scopes)
        blockers = self._evaluate_exhibit_target(grantee_org, target_kind, target_ref, scopes)
        if blockers:
            self._audit("exhibit.create.blocked", "exhibit", exhibit_ref, blockers=blockers)
            raise DomainError("展项上线被阻止：" + "；".join(blockers), 409)
        self._insert(
            "INSERT INTO exhibits(exhibit_ref, grantee_org, target_kind, target_ref, title,"
            " venue_ref, required_scopes, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (exhibit_ref, grantee_org, target_kind, target_ref, title, venue_ref,
             ",".join(scopes), self.clock()),
        )
        self._audit("exhibit.create", "exhibit", exhibit_ref, target_kind=target_kind,
                    target_ref=target_ref, required_scopes=scopes)
        return dict(self._require("exhibits", exhibit_ref, "exhibit_ref"))

    def _evaluate_exhibit_target(
        self, grantee_org: str, target_kind: str, target_ref: str, scopes: list[str]
    ) -> list[str]:
        if target_kind == "asset":
            asset = self._resolve_asset(target_ref)
            return self.evaluate_asset_use(asset["asset_ref"], grantee_org, scopes)
        if target_kind == "work":
            work = self._require("youth_works", target_ref, "work_ref")
            blockers: list[str] = []
            if not work["display_permission"]:
                blockers.append(f"青年作品 {target_ref} 未获展示许可")
            blockers.extend(self._inheritor_blockers_for_session(work["session_ref"], scopes))
            return blockers
        doc = self._require("process_documents", target_ref, "doc_ref")
        blockers = []
        if doc["is_key_process"]:
            # 关键工序即使获准展陈，对公众仍只呈现摘要
            if "full-process" not in scopes:
                blockers.append("关键工序展项需要 full-process 范围")
            blockers.extend(self._inheritor_blockers_for_session_or_heritage(
                doc["session_ref"], doc["heritage_ref"], ["full-process"]))
        else:
            blockers.extend(self._inheritor_blockers_for_session_or_heritage(
                doc["session_ref"], doc["heritage_ref"], scopes))
        return blockers

    def _inheritor_blockers_for_session(self, session_ref: str, scopes: list[str]) -> list[str]:
        session = self.get_session(session_ref)
        blockers: list[str] = []
        for scope in scopes:
            if scope in ("event-report", "full-process"):
                consent = self.conn.execute(
                    "SELECT * FROM inheritor_consents WHERE inheritor_ref=?",
                    (session["inheritor_ref"],),
                ).fetchone()
                allowed = (
                    consent is not None and not consent["withdrawn_at"]
                    and scope in (consent["allowed_scopes"] or "").split(",")
                )
                if not allowed:
                    blockers.append(f"传承人当前未允许展示范围：{scope}")
        return blockers

    def _inheritor_blockers_for_session_or_heritage(
        self, session_ref: str | None, heritage_ref: str, scopes: list[str]
    ) -> list[str]:
        if session_ref:
            return self._inheritor_blockers_for_session(session_ref, scopes)
        holders = self.conn.execute(
            "SELECT * FROM inheritor_consents WHERE heritage_ref=?", (heritage_ref,)
        ).fetchall()
        blockers: list[str] = []
        for scope in scopes:
            allowed = any(
                not row["withdrawn_at"]
                and scope in (row["allowed_scopes"] or "").split(",")
                for row in holders
            )
            if not allowed:
                blockers.append(f"传承人当前未允许展示范围：{scope}")
        return blockers

    # --------------------------------------------------------- 下线与拦截

    def _item_blockers(self, row: sqlite3.Row, kind: str) -> list[str]:
        table = "reports" if kind == "report" else "exhibits"
        scopes = (row["required_scopes"] or "").split(",")
        if kind == "report":
            blockers = self.evaluate_asset_use(row["asset_ref"], row["grantee_org"], scopes)
            return blockers
        if row["target_kind"] == "asset":
            asset = self._resolve_asset(row["target_ref"])
            return self.evaluate_asset_use(asset["asset_ref"], row["grantee_org"], scopes)
        return self._evaluate_exhibit_target(
            row["grantee_org"], row["target_kind"], row["target_ref"], scopes
        )

    def takedown_list(self) -> dict:
        """列出当前在线但已不合规、必须下线的报道与展项（不修改状态）。"""
        at = self.clock()
        pending = []
        for kind, table, ref_col in (
            ("report", "reports", "report_ref"),
            ("exhibit", "exhibits", "exhibit_ref"),
        ):
            for row in self.conn.execute(
                f"SELECT * FROM {table} WHERE status='up'"
            ):
                blockers = self._item_blockers(row, kind)
                if blockers:
                    pending.append({
                        "kind": kind, "ref": row[ref_col], "title": row["title"],
                        "grantee_org": row["grantee_org"], "blockers": blockers,
                        "checked_at": at,
                    })
        return {"checked_at": at, "must_take_down": pending}

    def apply_takedowns(self, reason: str = "") -> list[dict]:
        """执行下线：撤回/到期/同意消失导致不合规的在线项立即下线，事实记录保留。"""
        taken = []
        at = self.clock()
        for kind, table, ref_col in (
            ("report", "reports", "report_ref"),
            ("exhibit", "exhibits", "exhibit_ref"),
        ):
            for row in self.conn.execute(f"SELECT * FROM {table} WHERE status='up'").fetchall():
                blockers = self._item_blockers(row, kind)
                if not blockers:
                    continue
                final_reason = reason or "；".join(blockers)
                self.conn.execute(
                    f"UPDATE {table} SET status='taken_down', taken_down_at=?,"
                    f" take_down_reason=? WHERE {ref_col}=?",
                    (at, final_reason, row[ref_col]),
                )
                self._audit(f"{kind}.take_down", kind, row[ref_col],
                            grantee_org=row["grantee_org"], blockers=blockers, reason=final_reason)
                taken.append({"kind": kind, "ref": row[ref_col], "reason": final_reason})
        return taken

    def distribution_check(self, grantee_org: str, ref_or_fingerprint: str,
                           scopes: Iterable[str]) -> dict:
        """分发前闸门：供媒体/馆员在发布前检查某机构对素材的实际使用边界。"""
        asset = self._resolve_asset(ref_or_fingerprint)
        wanted = _normalize_scopes(scopes)
        at = parse_iso(self.clock())
        valid = self._valid_grants(asset["asset_ref"], grantee_org, at)
        blockers = self.evaluate_asset_use(asset["asset_ref"], grantee_org, wanted, at)
        return {
            "asset_ref": asset["asset_ref"], "grantee_org": grantee_org,
            "requested_scopes": wanted, "allowed": not blockers, "blockers": blockers,
            "valid_grants": [{"grant_ref": g["grant_ref"], "scope": g["scope"],
                              "expires_at": g["expires_at"]} for g in valid],
            "checked_at": self.clock(),
        }

    # ------------------------------------------------------------- 馆员追溯

    def trace(self, ref_or_fingerprint: str) -> dict:
        """从任一素材/作品/文档/场次/指纹出发，查清体验者、传承人与实际使用边界。"""
        asset = self.conn.execute(
            "SELECT * FROM media_assets WHERE asset_ref=? OR fingerprint_sha256=?",
            (ref_or_fingerprint, ref_or_fingerprint),
        ).fetchone()
        if asset is not None:
            return self._trace_asset(asset)

        work = self.conn.execute(
            "SELECT * FROM youth_works WHERE work_ref=?", (ref_or_fingerprint,)
        ).fetchone()
        if work is not None:
            assets = self._linked_assets("work", work["work_ref"])
            return {"entry_point": "work", "work": dict(work),
                    "material_kit": self._kit_of(work["material_kit_ref"]),
                    "assets": [self._trace_asset(a, shallow=True) for a in assets]}

        doc = self.conn.execute(
            "SELECT * FROM process_documents WHERE doc_ref=?", (ref_or_fingerprint,)
        ).fetchone()
        if doc is not None:
            assets = self._linked_assets("document", doc["doc_ref"])
            return {"entry_point": "document", "document": self._document_staff_view(doc),
                    "assets": [self._trace_asset(a, shallow=True) for a in assets]}

        session = self.conn.execute(
            "SELECT * FROM sessions WHERE session_ref=?", (ref_or_fingerprint,)
        ).fetchone()
        if session is not None:
            assets = self._linked_assets("session", session["session_ref"])
            return {"entry_point": "session", "session": dict(session),
                    "participants": [dict(p) for p in self.conn.execute(
                        "SELECT * FROM participants WHERE session_ref=?",
                        (session["session_ref"],))],
                    "assets": [self._trace_asset(a, shallow=True) for a in assets]}

        kit = self.conn.execute(
            "SELECT * FROM material_kits WHERE material_kit_ref=?", (ref_or_fingerprint,)
        ).fetchone()
        if kit is not None:
            assets = self._linked_assets("material_kit", kit["material_kit_ref"])
            return {"entry_point": "material_kit", "material_kit": dict(kit),
                    "assets": [self._trace_asset(a, shallow=True) for a in assets]}

        raise DomainError(f"无法定位素材或关联对象：{ref_or_fingerprint}", 404)

    def _linked_assets(self, kind: str, ref: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT a.* FROM media_assets a JOIN asset_links l ON l.asset_ref=a.asset_ref"
            " WHERE l.target_kind=? AND l.target_ref=?", (kind, ref),
        ).fetchall()

    def _kit_of(self, kit_ref: str | None) -> dict | None:
        if not kit_ref:
            return None
        return dict(self._require("material_kits", kit_ref))

    def _trace_asset(self, asset: sqlite3.Row, shallow: bool = False) -> dict:
        asset_ref = asset["asset_ref"]
        links = self.conn.execute(
            "SELECT target_kind, target_ref FROM asset_links WHERE asset_ref=?", (asset_ref,)
        ).fetchall()
        grouped: dict[str, list] = {
            "heritage": [], "session": [], "inheritor": [], "participant": [],
            "work": [], "document": [], "material_kit": [],
        }
        sessions_shown: set[str] = set()
        for link in links:
            grouped[link["target_kind"]].append(link["target_ref"])
            if link["target_kind"] == "session":
                sessions_shown.add(link["target_ref"])

        sessions, participants, inheritors, heritages = [], [], [], []
        for ref in grouped["session"]:
            session = self.get_session(ref)
            sessions.append(session)
            sessions_shown.add(ref)
        for session_ref in sessions_shown:
            for p in self.conn.execute(
                "SELECT * FROM participants WHERE session_ref=?", (session_ref,)
            ):
                participants.append(dict(p))
        for ref in grouped["inheritor"]:
            row = self.conn.execute(
                "SELECT * FROM inheritor_consents WHERE inheritor_ref=?", (ref,)
            ).fetchone()
            if row:
                inheritors.append(self._consent_view(row))
        for session in sessions:
            if session["inheritor_ref"] not in {i["inheritor_ref"] for i in inheritors}:
                row = self.conn.execute(
                    "SELECT * FROM inheritor_consents WHERE inheritor_ref=?",
                    (session["inheritor_ref"],),
                ).fetchone()
                if row:
                    inheritors.append(self._consent_view(row))
        for ref in grouped["heritage"]:
            heritages.append(dict(self._require("heritage_projects", ref)))
        for session in sessions:
            if session["heritage_ref"] not in {h["heritage_ref"] for h in heritages}:
                heritages.append(self.get_heritage(session["heritage_ref"]))
        for target in grouped["participant"]:
            session_ref, participant_ref = target.split("/", 1)
            participants.append(self.get_participant(session_ref, participant_ref))

        works = [dict(self._require("youth_works", ref, "work_ref")) for ref in grouped["work"]]
        documents = [
            self._document_staff_view(self._require("process_documents", ref, "doc_ref"))
            for ref in grouped["document"]
        ]
        kits = [dict(self._require("material_kits", ref)) for ref in grouped["material_kit"]]
        captures = [dict(c) for c in self.conn.execute(
            "SELECT capture_id, uploader_org, uploaded_at, note FROM media_captures"
            " WHERE asset_ref=? ORDER BY capture_id", (asset_ref,))]
        grants = [self._grant_view(g) for g in self.conn.execute(
            "SELECT * FROM media_grants WHERE asset_ref=? ORDER BY granted_at", (asset_ref,))]
        reports = [dict(r) for r in self.conn.execute(
            "SELECT * FROM reports WHERE asset_ref=?", (asset_ref,))]
        exhibits = [dict(e) for e in self.conn.execute(
            "SELECT * FROM exhibits WHERE target_kind='asset' AND target_ref=?", (asset_ref,))]

        # 去重参与者
        dedup = {f"{p['session_ref']}/{p['participant_ref']}": p for p in participants}
        return {
            "entry_point": "asset",
            "asset": dict(asset),
            "captures": captures,
            "heritage_projects": heritages,
            "inheritors": inheritors,
            "sessions": sessions,
            "participants": list(dedup.values()),
            "youth_works": works,
            "documents": documents,
            "material_kits": kits,
            "grants": grants,
            "reports": reports,
            "exhibits": exhibits,
        }

    def _consent_view(self, row: sqlite3.Row) -> dict:
        view = dict(row)
        view["allowed_scopes"] = [s for s in (row["allowed_scopes"] or "").split(",") if s]
        view["currently_effective"] = not row["withdrawn_at"]
        return view

    def _document_staff_view(self, row: sqlite3.Row) -> dict:
        view = dict(row)
        view["revisions"] = self.list_revisions(row["doc_ref"])["revisions"]
        return view

    # -------------------------------------------------------------- 公众视图

    def public_heritage(self) -> list[dict]:
        return [
            {"heritage_ref": r["heritage_ref"], "name": r["name"], "summary": r["summary"]}
            for r in self.conn.execute("SELECT * FROM heritage_projects ORDER BY heritage_ref")
        ]

    def public_sessions(self) -> list[dict]:
        """活动发生过的事实保留：只给场次说明，不给体验者身份。"""
        return [{
            "session_ref": r["session_ref"],
            "heritage_ref": r["heritage_ref"],
            "title": r["title"], "occurred_at": r["occurred_at"],
            "location_ref": r["location_ref"], "fact_note": r["fact_note"],
        } for r in self.conn.execute(
            "SELECT * FROM sessions ORDER BY occurred_at")]

    def public_asset(self, ref_or_fingerprint: str) -> dict:
        asset = self._resolve_asset(ref_or_fingerprint)
        # 公众只能看到“当前存在面向公众的有效许可”的素材说明
        at = parse_iso(self.clock())
        orgs = [r["grantee_org"] for r in self.conn.execute(
            "SELECT DISTINCT grantee_org FROM media_grants WHERE asset_ref=?",
            (asset["asset_ref"],))]
        covered = False
        for org in orgs:
            if not self.evaluate_asset_use(asset["asset_ref"], org, list(PUBLIC_SCOPES), at):
                covered = True
                break
            # 部分范围获准也算可公开（至少 event-report）
            if not self.evaluate_asset_use(asset["asset_ref"], org, ["event-report"], at):
                covered = True
                break
        if not covered:
            raise DomainError("该素材当前无面向公众的有效许可", 404)
        docs = []
        for link in self.conn.execute(
            "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='document'",
            (asset["asset_ref"],),
        ):
            doc = self._require("process_documents", link["target_ref"], "doc_ref")
            docs.append({
                "doc_ref": doc["doc_ref"], "title": doc["title"],
                "is_key_process": bool(doc["is_key_process"]),
                # 关键工序只给摘要，且永不返回原文/译文受控引用
                "public_summary": doc["public_summary"],
            })
        works = []
        for link in self.conn.execute(
            "SELECT target_ref FROM asset_links WHERE asset_ref=? AND target_kind='work'",
            (asset["asset_ref"],),
        ):
            work = self._require("youth_works", link["target_ref"], "work_ref")
            if work["display_permission"]:
                works.append({"work_ref": work["work_ref"], "title": work["title"],
                              "public_summary": work["public_summary"]})
        return {
            "asset_ref": asset["asset_ref"], "media_type": asset["media_type"],
            "title": asset["title"],
            "documents": docs, "youth_works": works,
        }

    def public_reports(self) -> list[dict]:
        """只列出在线且当前仍然合规的报道；下线项不对公众展示，但事实仍保留在馆员侧。"""
        result = []
        for row in self.conn.execute("SELECT * FROM reports WHERE status='up'"):
            blockers = self._item_blockers(row, "report")
            if blockers:
                continue
            result.append({
                "report_ref": row["report_ref"], "grantee_org": row["grantee_org"],
                "title": row["title"], "location_ref": row["location_ref"],
                "published_at": row["published_at"],
                "asset": self.public_asset(row["asset_ref"]),
            })
        return result

    def public_youth_works(self) -> list[dict]:
        return [{
            "work_ref": r["work_ref"], "title": r["title"],
            "public_summary": r["public_summary"], "session_ref": r["session_ref"],
        } for r in self.conn.execute(
            "SELECT * FROM youth_works WHERE display_permission=1 ORDER BY work_ref")]

    # --------------------------------------------------------------- 审计

    def audit_events(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit_events ORDER BY event_id DESC LIMIT ?", (limit,)
        ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json"))
            events.append(item)
        return events

    # ---------------------------------------------------------------- 工具

    def _insert(self, sql: str, params: tuple) -> None:
        try:
            self.conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise DomainError(f"记录冲突或引用不存在：{exc}", 409) from exc


# ---------------------------------------------------------------- 辅助函数

def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _require_sha256(value: str) -> None:
    if not (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdefABCDEF" for c in value)):
        raise DomainError("指纹应为 64 位十六进制 sha256 摘要", 400)


def _normalize_scopes(scopes: Iterable[str], allow_internal: bool = True) -> list[str]:
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    result: list[str] = []
    for scope in scopes:
        if scope not in SCOPES:
            raise DomainError(f"未知许可范围：{scope}", 400)
        if scope == "internal-archive" and not allow_internal:
            raise DomainError("internal-archive 不得用于公开发布范围", 400)
        if scope not in result:
            result.append(scope)
    return result
