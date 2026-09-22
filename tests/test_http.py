"""HTTP 端到端测试：去重上传、闸门、下线、公众边界。"""

SHA = "c" * 64


def _setup(client):
    status, _ = client.request("POST", "/staff/projects", {
        "ref": "P1", "name": "银铜器", "public_summary": "公开说明",
    })
    assert status == 201
    assert client.request("POST", "/staff/inheritors", {
        "ref": "I1", "heritage_ref": "P1", "public_name": "王师傅",
        "allowed_scopes": ["event-report"],
    })[0] == 201
    assert client.request("POST", "/staff/sessions", {
        "ref": "S1", "heritage_ref": "P1", "inheritor_ref": "I1",
        "title": "交流场", "venue": "西宁非遗馆",
        "occurred_at": "2026-09-19T10:00:00+08:00",
    })[0] == 201
    assert client.request("POST", "/staff/process-steps", {
        "heritage_ref": "P1", "step_no": 1, "name": "关键工序",
        "public_summary": "仅摘要", "restricted_detail": "保密",
    })[0] == 201
    assert client.request("POST", "/staff/attendees", {
        "ref": "V1", "session_ref": "S1", "public_label": "体验者甲",
        "portrait_consent": "granted", "consent_scopes": ["event-report"],
        "consented_at": "2026-09-19T09:00:00+08:00",
    })[0] == 201
    assert client.request("POST", "/staff/material-kits", {
        "ref": "K1", "heritage_ref": "P1", "name": "材料包",
        "allowed_scopes": ["event-report"],
    })[0] == 201
    assert client.request("POST", "/staff/youth-works", {
        "ref": "W1", "session_ref": "S1", "youth_ref": "Y1",
        "title": "习作", "allowed_scopes": ["event-report"],
    })[0] == 201


def test_health(client):
    status, body = client.request("GET", "/health")
    assert status == 200 and body["status"] == "ok"


def test_duplicate_upload_deduplicates(client):
    _setup(client)
    payload = {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    }
    status, body = client.request("POST", "/staff/assets/uploads", payload)
    assert status == 201 and body["deduplicated"] is False

    payload["outlet_ref"] = "OUTLET-2"
    payload["claimed_basis"] = "on-site-assumption"
    payload.pop("asset_ref")
    status, body = client.request("POST", "/staff/assets/uploads", payload)
    assert status == 200 and body["deduplicated"] is True
    assert body["asset"]["asset_ref"] == "A1"
    assert len(body["uploads"]) == 2

    # sha 反查得到同一素材
    status, body = client.request("GET", f"/staff/assets/by-sha/{SHA}")
    assert status == 200 and body["asset"]["asset_ref"] == "A1"


def test_full_gate_and_takedown_flow(client):
    _setup(client)
    client.request("POST", "/staff/assets/uploads", {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    })
    client.request("POST", "/staff/assets/A1/links", {"attendee_refs": ["V1"]})
    client.request("POST", "/staff/licenses", {
        "license_ref": "L1", "asset_ref": "A1", "grantee_ref": "OUTLET-1",
        "usage_scopes": ["event-report"],
        "valid_from": "2026-09-01T00:00:00+08:00",
        "valid_until": "2026-12-31T23:59:59+08:00",
    })
    client.request("POST", "/staff/usages", {
        "usage_ref": "U1", "asset_ref": "A1", "outlet_ref": "OUTLET-1",
        "usage_type": "report", "title": "授权报道",
    })
    client.request("POST", "/staff/usages", {
        "usage_ref": "U2", "asset_ref": "A1", "outlet_ref": "OUTLET-2",
        "usage_type": "report", "title": "无许可报道",
    })

    status, body = client.request("POST", "/staff/usages/U1/gate", {})
    assert status == 200 and body["decision"] == "allowed"
    status, body = client.request("POST", "/staff/usages/U2/gate", {})
    assert status == 403 and body["decision"] == "blocked"

    # 下线清单包含 U2，执行下线
    status, body = client.request("GET", "/staff/takedowns")
    pending = [i["usage_ref"] for i in body["must_take_down"]]
    assert status == 200 and "U2" in pending and "U1" not in pending
    status, body = client.request("POST", "/staff/takedowns/apply", {})
    assert status == 200 and body["taken_down"] == ["U2"]

    # 撤证 → U1 进入下线清单
    assert client.request("POST", "/staff/licenses/L1/withdraw",
                          {"reason": "传承人撤回"})[0] == 200
    status, body = client.request("GET", "/staff/takedowns")
    assert "U1" in [i["usage_ref"] for i in body["must_take_down"]]


def test_withdrawn_attendee_blocks_via_http(client):
    _setup(client)
    client.request("POST", "/staff/assets/uploads", {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    })
    client.request("POST", "/staff/assets/A1/links", {"attendee_refs": ["V1"]})
    client.request("POST", "/staff/licenses", {
        "license_ref": "L1", "asset_ref": "A1", "grantee_ref": "OUTLET-1",
        "usage_scopes": ["event-report"], "valid_from": "2026-09-01T00:00:00+08:00",
    })
    client.request("POST", "/staff/usages", {
        "usage_ref": "U1", "asset_ref": "A1", "outlet_ref": "OUTLET-1",
        "usage_type": "report", "title": "报道",
    })
    assert client.request("POST", "/staff/usages/U1/gate", {})[0] == 200

    assert client.request("POST", "/staff/attendees/V1/withdraw", {})[0] == 200
    status, body = client.request("POST", "/staff/usages/U1/gate", {})
    assert status == 403
    assert any("肖像同意已撤回" in r for r in body["reasons"])


def test_public_endpoints_respect_boundaries(client):
    _setup(client)
    client.request("POST", "/staff/assets/uploads", {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    })
    client.request("POST", "/staff/assets/A1/links", {"attendee_refs": ["V1"]})
    client.request("POST", "/staff/licenses", {
        "license_ref": "L1", "asset_ref": "A1", "grantee_ref": "OUTLET-1",
        "usage_scopes": ["event-report"], "valid_from": "2026-09-01T00:00:00+08:00",
    })
    client.request("POST", "/staff/usages", {
        "usage_ref": "U1", "asset_ref": "A1", "outlet_ref": "OUTLET-1",
        "usage_type": "report", "title": "获准报道",
    })

    # 工序只见摘要
    status, body = client.request("GET", "/public/process/P1")
    assert status == 200
    assert body["steps"][0]["public_summary"] == "仅摘要"
    assert "restricted_detail" not in body["steps"][0]

    # 公众目录：场次事实在、用途可见
    status, catalog = client.request("GET", "/public/catalog")
    assert status == 200
    assert catalog["sessions"][0]["ref"] == "S1"
    assert [u["usage_ref"] for u in catalog["visible_usages"]] == ["U1"]

    # 公众用途页可访问
    assert client.request("GET", "/public/usages/U1")[0] == 200

    # 撤证并下线 → 410；场次事实仍在
    client.request("POST", "/staff/licenses/L1/withdraw", {})
    client.request("POST", "/staff/takedowns/apply", {})
    status, body = client.request("GET", "/public/usages/U1")
    assert status == 410 and body["status"] == "gone"
    status, catalog = client.request("GET", "/public/catalog")
    assert catalog["sessions"][0]["ref"] == "S1"
    assert catalog["visible_usages"] == []


def test_expired_license_blocks_using_at_query(client):
    _setup(client)
    client.request("POST", "/staff/assets/uploads", {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    })
    client.request("POST", "/staff/assets/A1/links", {"attendee_refs": ["V1"]})
    client.request("POST", "/staff/licenses", {
        "license_ref": "L1", "asset_ref": "A1", "grantee_ref": "OUTLET-1",
        "usage_scopes": ["event-report"],
        "valid_from": "2026-09-01T00:00:00+08:00",
        "valid_until": "2026-09-10T23:59:59+08:00",
    })
    client.request("POST", "/staff/usages", {
        "usage_ref": "U1", "asset_ref": "A1", "outlet_ref": "OUTLET-1",
        "usage_type": "report", "title": "展项",
    })
    status, body = client.request("POST", "/staff/usages/U1/gate",
                                  {"at": "2026-09-05T00:00:00+08:00"})
    assert status == 200 and body["decision"] == "allowed"
    status, body = client.request("POST", "/staff/usages/U1/gate",
                                  {"at": "2026-09-20T00:00:00+08:00"})
    assert status == 403 and any("已到期" in r for r in body["reasons"])


def test_asset_trace_links_all_parties(client):
    _setup(client)
    client.request("POST", "/staff/assets/uploads", {
        "sha256": SHA, "kind": "video", "title": "演示视频",
        "outlet_ref": "OUTLET-1", "claimed_basis": "licensed",
        "asset_ref": "A1", "session_ref": "S1", "heritage_ref": "P1",
        "inheritor_ref": "I1",
    })
    client.request("POST", "/staff/assets/A1/links", {
        "attendee_refs": ["V1"], "youth_work_refs": ["W1"],
        "material_kit_refs": ["K1"],
    })
    client.request("POST", "/staff/translations", {
        "unit_ref": "TU1", "revision": 1, "source_text": "原文",
        "translated_text": "source", "language_pair": "zh/en",
        "heritage_ref": "P1", "session_ref": "S1",
    })
    client.request("POST", "/staff/assets/A1/links",
                   {"translation_unit_refs": ["TU1"]})

    status, trace = client.request("GET", "/staff/assets/A1/trace")
    assert status == 200
    assert trace["inheritor"]["ref"] == "I1"
    assert [a["ref"] for a in trace["attendees"]] == ["V1"]
    assert [w["ref"] for w in trace["youth_works"]] == ["W1"]
    assert [k["ref"] for k in trace["material_kits"]] == ["K1"]
    assert trace["translation_units"]["TU1"][0]["source_text"] == "原文"
    assert trace["session"]["ref"] == "S1"


def test_validation_errors(client):
    assert client.request("POST", "/staff/projects", {"ref": "X"})[0] == 400
    assert client.request("GET", "/public/process/NOPE")[0] == 404
    assert client.request("GET", "/no-such-path")[0] == 404
