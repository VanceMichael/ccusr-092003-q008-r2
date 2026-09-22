"""领域判定测试：去重、授权边界、到期撤回、肖像与工序隔离。"""

import pytest

from app import authz, store


SHA_A = "a" * 64
SHA_B = "b" * 64

AT_BEFORE = "2026-09-01T00:00:00+08:00"
AT_DURING = "2026-09-20T12:00:00+08:00"
AT_AFTER = "2026-10-01T12:00:00+08:00"


def _build_world(conn, *, full_process=False, attendee_state="granted",
                 inheritor_scopes=None, youth_scopes=None, kit_scopes=None):
    store.create_project(conn, "P1", "银铜器", "公开说明")
    store.create_inheritor(
        conn, "I1", "P1", "王师傅",
        inheritor_scopes or ["event-report", "exhibition-display"],
        full_process_allowed=False,
    )
    store.create_session(conn, "S1", "P1", "I1", "交流场", "2026-09-19T10:00:00+08:00")
    store.add_process_step(
        conn, "P1", 1, "关键工序", "公开摘要", restricted_detail="保密配比"
    )
    store.register_attendee(
        conn, "V1", "S1", "体验者甲",
        portrait_consent=attendee_state,
        consent_scopes=["event-report"],
        consented_at="2026-09-19T09:00:00+08:00",
    )
    store.create_material_kit(
        conn, "K1", "P1", "材料包",
        kit_scopes if kit_scopes is not None else ["event-report"],
    )
    store.create_youth_work(
        conn, "W1", "S1", "Y1", "青年习作",
        youth_scopes if youth_scopes is not None else ["event-report"],
    )
    asset, _ = store.register_asset_upload(
        conn, sha256=SHA_A, kind="video", title="演示视频",
        outlet_ref="OUTLET-1", claimed_basis="licensed", asset_ref="A1",
        session_ref="S1", heritage_ref="P1", inheritor_ref="I1",
        contains_full_process=full_process,
    )
    store.link_asset_attendees(conn, "A1", ["V1"])
    store.link_asset_youth_works(conn, "A1", ["W1"])
    store.link_asset_material_kits(conn, "A1", ["K1"])
    return asset


def test_duplicate_uploads_merge_to_one_asset(conn):
    _build_world(conn)
    asset, dedup = store.register_asset_upload(
        conn, sha256=SHA_A, kind="video", title="另一家上传同一段",
        outlet_ref="OUTLET-2", claimed_basis="on-site-assumption",
    )
    assert dedup is True
    assert asset["asset_ref"] == "A1"
    uploads = store.list_asset_uploads(conn, "A1")
    assert [u["outlet_ref"] for u in uploads] == ["OUTLET-1", "OUTLET-2"]
    # 许可挂在素材身份上，只有一条素材
    store.grant_license(
        conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None
    )
    assert len(store.list_asset_licenses(conn, "A1")) == 1


def test_licensee_publishes_other_outlet_blocked(conn):
    _build_world(conn)
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "授权报道")
    store.register_usage(conn, "U2", "A1", "OUTLET-2", "report", "误以为现场可拍")

    assert authz.gate_usage(conn, "U1", persist=False)["decision"] == "allowed"
    blocked = authz.gate_usage(conn, "U2", persist=False)
    assert blocked["decision"] == "blocked"
    assert any("许可属于其他媒体" in r for r in blocked["reasons"])


def test_license_expiry_is_time_derived_and_blocks_after(conn):
    _build_world(conn)
    store.grant_license(
        conn, "L1", "A1", "OUTLET-1", ["event-report"],
        "2026-09-01T00:00:00+08:00", "2026-09-30T23:59:59+08:00",
    )
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "报道")
    assert authz.gate_usage(conn, "U1", at=store.parse_dt(AT_DURING), persist=False)["decision"] == "allowed"
    after = authz.gate_usage(conn, "U1", at=store.parse_dt(AT_AFTER), persist=False)
    assert after["decision"] == "blocked"
    assert any("已到期" in r for r in after["reasons"])
    # 许可记录本身不被改动
    assert store.get_license(conn, "L1")["status"] == "active"


def test_withdrawn_license_appears_in_takedown_list_and_fact_remains(conn):
    _build_world(conn)
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "报道")
    assert authz.gate_usage(conn, "U1", persist=False)["decision"] == "allowed"

    store.withdraw_license(conn, "L1", reason="传承人撤回")
    pending = {i["usage_ref"] for i in authz.takedown_list(conn)}
    assert "U1" in pending

    taken = authz.apply_takedowns(conn)
    assert taken == ["U1"]
    assert store.get_usage(conn, "U1")["status"] == "taken_down"
    # 活动发生过的事实不消失
    assert store.get_session(conn, "S1")["title"] == "交流场"
    gone = authz.evaluate_usage(conn, store.get_usage(conn, "U1"))
    assert gone["decision"] == "taken-down"


def test_portrait_consent_denied_and_withdrawn_block(conn):
    _build_world(conn, attendee_state="denied")
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "报道")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert result["decision"] == "blocked"
    assert any("未授予肖像同意" in r for r in result["reasons"])

    store.register_attendee(
        conn, "V2", "S1", "体验者乙", portrait_consent="granted",
        consent_scopes=["event-report"], consented_at=AT_BEFORE,
    )
    store.link_asset_attendees(conn, "A1", ["V2"])
    store.withdraw_attendee_consent(conn, "V2")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("肖像同意已撤回" in r for r in result["reasons"])


def test_portrait_scope_must_match_usage(conn):
    _build_world(conn, attendee_state="granted")  # V1 只同意 event-report
    store.grant_license(
        conn, "L1", "A1", "OUTLET-1",
        ["event-report", "exhibition-display"], AT_BEFORE, None,
    )
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "exhibition", "展项")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("肖像同意范围不含 exhibition-display" in r for r in result["reasons"])


def test_full_process_requires_inheritor_and_license(conn):
    _build_world(conn, full_process=True)
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "报道")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("完整工序" in r for r in result["reasons"])

    # 传承人放开但许可未覆盖，仍拦截
    conn.execute(
        "UPDATE inheritors SET full_process_allowed=1 WHERE ref='I1'"
    )
    conn.commit()
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("许可未覆盖完整工序" in r for r in result["reasons"])

    conn.execute(
        "UPDATE licenses SET covers_full_process=1 WHERE license_ref='L1'"
    )
    conn.commit()
    assert authz.gate_usage(conn, "U1", persist=False)["decision"] == "allowed"


def test_inheritor_scope_boundary_blocks_clip(conn):
    _build_world(conn)
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["short-clip"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "clip", "短视频")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("传承人" in r and "short-clip" in r for r in result["reasons"])


def test_youth_work_and_kit_scopes_enforced(conn):
    _build_world(conn, youth_scopes=["archive-internal"])
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "报道")
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("青年作品" in r for r in result["reasons"])

    conn.execute(
        "UPDATE material_kits SET allowed_scopes='[\"archive-internal\"]' WHERE ref='K1'"
    )
    conn.commit()
    result = authz.gate_usage(conn, "U1", persist=False)
    assert any("材料包" in r for r in result["reasons"])


def test_public_catalog_hides_restricted_detail_and_blocked_usages(conn):
    _build_world(conn)
    store.grant_license(conn, "L1", "A1", "OUTLET-1", ["event-report"], AT_BEFORE, None)
    store.register_usage(conn, "U1", "A1", "OUTLET-1", "report", "获准报道")
    store.register_usage(conn, "U2", "A1", "OUTLET-2", "report", "无许可报道")

    catalog = authz.public_catalog(conn)
    step = catalog["projects"][0]["process_steps"][0]
    assert "restricted_detail" not in step
    assert step["public_summary"] == "公开摘要"
    assert [u["usage_ref"] for u in catalog["visible_usages"]] == ["U1"]
    # 公众视图不含体验者与内部许可
    assert "attendees" not in catalog and "licenses" not in catalog
    # 场次事实保留
    assert catalog["sessions"][0]["ref"] == "S1"


def test_translation_revisions_keep_source_and_target_aligned(conn):
    _build_world(conn)
    store.add_translation_revision(
        conn, "TU1", 1, "原文一", "translation one", "zh/en", change_note="初版",
        heritage_ref="P1", session_ref="S1",
    )
    store.add_translation_revision(
        conn, "TU1", 2, "原文二（补充受限工序不展示）",
        "translation two (restricted step omitted)", "zh/en",
        change_note="同步修订", heritage_ref="P1", session_ref="S1",
    )
    revisions = store.list_translation_revisions(conn, "TU1")
    assert [r["revision"] for r in revisions] == [1, 2]
    for rev in revisions:
        assert rev["source_text"] and rev["translated_text"]
    with pytest.raises(ValueError):
        store.add_translation_revision(conn, "TU1", 1, "x", "y", "zh/en")


def test_gate_decisions_are_recorded(conn):
    _build_world(conn)
    store.register_usage(conn, "U1", "A1", "OUTLET-2", "report", "无许可")
    result = authz.gate_usage(conn, "U1")
    assert result["decision"] == "blocked"
    decisions = store.list_decisions(conn, "U1")
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "blocked"
    assert decisions[0]["reasons"]


def test_naive_datetime_rejected(conn):
    with pytest.raises(ValueError):
        store.parse_dt("2026-09-20T10:00:00")
