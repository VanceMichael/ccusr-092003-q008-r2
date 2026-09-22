"""把 fixtures/example.json 载入数据库（幂等：已存在的记录跳过）。

用法：python -m scripts.seed [fixtures/example.json]
"""

import json
import sys
from pathlib import Path

from app import db, store


def _skip(existing: set[str], key: str) -> bool:
    if key in existing:
        print(f"  跳过已存在：{key}")
        return True
    return False


def seed(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    conn = db.connect()
    db.migrate(conn)

    print(f"载入场景：{data.get('scenario', fixture_path)}")

    for p in data.get("projects", []):
        try:
            store.create_project(conn, **p)
        except Exception:
            print(f"  跳过已存在项目：{p['ref']}")

    for i in data.get("inheritors", []):
        try:
            store.create_inheritor(
                conn, i["ref"], i["heritage_ref"], i["public_name"],
                i["allowed_scopes"],
                full_process_allowed=i.get("full_process_allowed", False),
                portrait_allowed=i.get("portrait_allowed", True),
                notes=i.get("notes"),
            )
        except Exception:
            print(f"  跳过已存在传承人：{i['ref']}")

    for s in data.get("sessions", []):
        try:
            store.create_session(
                conn, s["ref"], s["heritage_ref"], s["inheritor_ref"],
                s["title"], s["occurred_at"], venue=s.get("venue"),
            )
        except Exception:
            print(f"  跳过已存在场次：{s['ref']}")

    for step in data.get("process_steps", []):
        exists = conn.execute(
            "SELECT 1 FROM process_steps WHERE heritage_ref=? AND step_no=?",
            (step["heritage_ref"], step["step_no"]),
        ).fetchone()
        if exists:
            continue
        store.add_process_step(conn, **step)

    for a in data.get("attendees", []):
        try:
            store.register_attendee(
                conn, a["ref"], a["session_ref"], a["public_label"],
                portrait_consent=a.get("portrait_consent", "denied"),
                consent_scopes=a.get("consent_scopes", []),
                consented_at=a.get("consented_at"),
            )
        except Exception:
            print(f"  跳过已存在体验者：{a['ref']}")

    for ref in data.get("withdraw_attendees", []):
        attendee = store.get_attendee(conn, ref)
        if attendee["portrait_consent"] != "withdrawn":
            store.withdraw_attendee_consent(conn, ref)
            print(f"  肖像同意已撤回：{ref}")

    for k in data.get("material_kits", []):
        try:
            store.create_material_kit(
                conn, k["ref"], k["heritage_ref"], k["name"],
                k["allowed_scopes"], public_note=k.get("public_note"),
            )
        except Exception:
            print(f"  跳过已存在材料包：{k['ref']}")

    for link in data.get("session_kits", []):
        store.assign_kit_to_session(
            conn, link["session_ref"], link["kit_ref"], note=link.get("note")
        )

    for w in data.get("youth_works", []):
        try:
            store.create_youth_work(
                conn, w["ref"], w["session_ref"], w["youth_ref"],
                w["title"], w["allowed_scopes"],
            )
        except Exception:
            print(f"  跳过已存在青年作品：{w['ref']}")

    for t in data.get("translations", []):
        try:
            store.add_translation_revision(
                conn, t["unit_ref"], t["revision"], t["source_text"],
                t["translated_text"], t["language_pair"],
                change_note=t.get("change_note"),
                heritage_ref=t.get("heritage_ref"),
                session_ref=t.get("session_ref"),
            )
        except Exception:
            print(f"  跳过已存在口译修订：{t['unit_ref']} r{t['revision']}")

    for asset in data.get("assets", []):
        for upload in asset.get("uploads", []):
            try:
                _, dedup = store.register_asset_upload(
                    conn,
                    sha256=asset["sha256"], kind=asset["kind"], title=asset["title"],
                    outlet_ref=upload["outlet_ref"],
                    claimed_basis=upload["claimed_basis"],
                    asset_ref=asset.get("asset_ref"),
                    session_ref=asset.get("session_ref"),
                    heritage_ref=asset.get("heritage_ref"),
                    inheritor_ref=asset.get("inheritor_ref"),
                    contains_full_process=asset.get("contains_full_process", False),
                    technical_fingerprint=asset.get("technical_fingerprint"),
                    note=upload.get("note"),
                )
                if dedup:
                    print(f"  重复影像归并到同一素材：{asset['sha256'][:12]}…（{upload['outlet_ref']}）")
            except Exception as exc:
                print(f"  上传记录跳过：{upload['outlet_ref']}（{exc}）")

        links = asset.get("links", {})
        ref = asset["asset_ref"]
        if links.get("attendee_refs"):
            store.link_asset_attendees(conn, ref, links["attendee_refs"])
        if links.get("youth_work_refs"):
            store.link_asset_youth_works(conn, ref, links["youth_work_refs"])
        if links.get("material_kit_refs"):
            store.link_asset_material_kits(conn, ref, links["material_kit_refs"])
        if links.get("translation_unit_refs"):
            store.link_asset_translations(conn, ref, links["translation_unit_refs"])

    for lic in data.get("licenses", []):
        try:
            store.grant_license(
                conn, lic["license_ref"], lic["asset_ref"], lic["grantee_ref"],
                lic["usage_scopes"], lic["valid_from"],
                valid_until=lic.get("valid_until"),
                covers_full_process=lic.get("covers_full_process", False),
            )
        except Exception:
            print(f"  跳过已存在许可：{lic['license_ref']}")

    for u in data.get("usages", []):
        try:
            store.register_usage(
                conn, u["usage_ref"], u["asset_ref"], u["outlet_ref"],
                u["usage_type"], u["title"], channel=u.get("channel"),
                first_published_at=u.get("first_published_at"),
            )
        except Exception:
            print(f"  跳过已存在媒体用途：{u['usage_ref']}")

    print("场景载入完成。")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures/example.json")
    seed(path)


if __name__ == "__main__":
    main()
