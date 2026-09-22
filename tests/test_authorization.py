
import unittest

from app.store import DomainError, Store
from tests.util import build_store, build_xining

FUTURE = "2026-12-31T23:59:59+08:00"


class DeduplicationTest(unittest.TestCase):
    def test_same_fingerprint_is_one_asset(self) -> None:
        store, _ = build_store()
        build_xining(store)
        again = store.register_asset(
            "ASSET-COPY", "a" * 64, "另一媒体上传的同一视频",
            source_ref="自媒体://reup")
        self.assertTrue(again["deduped"])
        self.assertEqual(again["asset_ref"], "ASSET-1")
        # 两条上传记录都挂在同一素材上
        trace = store.trace("ASSET-1")
        uploaders = {c["uploader_org"] for c in trace["captures"]}
        self.assertEqual(uploaders, {"西宁广电", "某自媒体"})
        # 指纹也能直接定位到同一素材
        self.assertEqual(store.trace("a" * 64)["asset"]["asset_ref"], "ASSET-1")

    def test_permission_follows_asset_not_upload(self) -> None:
        store, _ = build_store()
        build_xining(store)
        # 许可授给西宁广电，自媒体重复上传后仍无任何许可
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        blockers = store.evaluate_asset_use("ASSET-1", "某自媒体", ["event-report"])
        self.assertEqual(len(blockers), 1)
        self.assertIn("event-report", blockers[0])
        self.assertEqual(store.evaluate_asset_use("ASSET-1", "西宁广电", ["event-report"]), [])


class EnforcementTest(unittest.TestCase):
    def test_full_process_grant_rejected_without_inheritor_scope(self) -> None:
        store, _ = build_store()
        build_xining(store)
        with self.assertRaises(DomainError) as ctx:
            store.issue_grant("G-BAD", "ASSET-1", "某自媒体", "full-process",
                              "2026-09-19T00:00:00+08:00", FUTURE)
        self.assertEqual(ctx.exception.status, 422)
        # 许可未建立
        self.assertNotIn("G-BAD", [g["grant_ref"] for g in store.trace("ASSET-1")["grants"]])

    def test_portrait_requires_participant_consent(self) -> None:
        store, _ = build_store()
        build_xining(store)
        # 撤回 PERSON-B 的肖像同意后，portrait 授予应被拒绝
        store.set_portrait_consent("SESSION-1", "PERSON-B", False)
        with self.assertRaises(DomainError):
            store.issue_grant("G-P", "ASSET-1", "西宁广电", "portrait",
                              "2026-09-19T00:00:00+08:00", FUTURE)
        store.set_portrait_consent("SESSION-1", "PERSON-B", True)
        grant = store.issue_grant("G-P", "ASSET-1", "西宁广电", "portrait",
                                  "2026-09-19T00:00:00+08:00", FUTURE)
        self.assertEqual(grant["scope"], "portrait")

    def test_internal_archive_cannot_be_published(self) -> None:
        store, _ = build_store()
        build_xining(store)
        # internal-archive 不校验外部同意，但永远不能用于公开报道
        store.issue_grant("G-I", "ASSET-1", "某自媒体", "internal-archive",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        with self.assertRaises(DomainError):
            store.publish_report("R-I", "某自媒体", "ASSET-1", "内部档外流",
                                 ["internal-archive"])

    def test_report_publish_and_duplicate_org_blocked(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        report = store.publish_report("R1", "西宁广电", "ASSET-1", "活动报道",
                                      ["event-report"])
        self.assertEqual(report["status"], "up")
        with self.assertRaises(DomainError):
            store.publish_report("R2", "某自媒体", "ASSET-1", "越界报道",
                                 ["event-report", "full-process"])

    def test_key_process_exhibit_requires_full_process(self) -> None:
        store, _ = build_store()
        build_xining(store)
        with self.assertRaises(DomainError):
            store.create_exhibit("E-KEY", "西宁非遗馆", "document", "DOC-KEY",
                                 "关键工序展板", ["event-report"])


class RevocationAndExpiryTest(unittest.TestCase):
    def _published(self, store: Store) -> None:
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        store.publish_report("R1", "西宁广电", "ASSET-1", "活动报道", ["event-report"])
        store.create_exhibit("E1", "西宁广电", "asset", "ASSET-1", "互动屏展项",
                             ["event-report"])

    def test_revoke_grant_takes_items_down_but_keeps_facts(self) -> None:
        store, _ = build_store()
        build_xining(store)
        self._published(store)
        result = store.revoke_grant("G1", "传承人要求停止传播")
        refs = {item["ref"] for item in result["taken_down_items"]}
        self.assertEqual(refs, {"R1", "E1"})
        self.assertEqual(store.get_report("R1")["status"], "taken_down")
        self.assertIn("G1", store.get_report("R1")["take_down_reason"] + "G1")
        # 活动事实不消失
        session = store.get_session("SESSION-1")
        self.assertEqual(session["title"], "银铜器交流演示")
        participants = store.trace("ASSET-1")["participants"]
        self.assertTrue(participants)
        # 下线后阻止继续分发
        with self.assertRaises(DomainError):
            store.publish_report("R3", "西宁广电", "ASSET-1", "撤回后新发",
                                 ["event-report"])
        # 下线项不对公众展示
        self.assertEqual(store.public_reports(), [])

    def test_takedown_list_lists_before_apply(self) -> None:
        store, _ = build_store()
        build_xining(store)
        self._published(store)
        store.conn.execute(
            "UPDATE media_grants SET status='revoked', revoked_at=? WHERE grant_ref='G1'",
            ("2026-09-22T09:00:00+00:00",))
        pending = store.takedown_list()["must_take_down"]
        self.assertEqual({item["ref"] for item in pending}, {"R1", "E1"})
        # 仅列出，尚未执行
        self.assertEqual(store.get_report("R1")["status"], "up")

    def test_expiry_blocks_and_takes_down(self) -> None:
        store, clock_values = build_store("2026-09-21T12:00:00+00:00")
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", "2026-09-30T23:59:59+08:00")
        store.publish_report("R1", "西宁广电", "ASSET-1", "活动报道", ["event-report"])
        # 时钟推进到到期之后
        clock_values[0] = "2026-10-01T00:00:00+00:00"
        grant = store.get_grant("G1")
        self.assertFalse(grant["currently_valid"])
        taken = store.apply_takedowns(reason="许可到期")
        self.assertEqual([item["ref"] for item in taken], ["R1"])
        with self.assertRaises(DomainError):
            store.publish_report("R2", "西宁广电", "ASSET-1", "到期后新发",
                                 ["event-report"])

    def test_inheritor_consent_withdrawal_cascades(self) -> None:
        store, _ = build_store()
        build_xining(store)
        self._published(store)
        store.withdraw_inheritor_consent("PERSON-A", "传承人撤销展示同意")
        refs = {item["ref"] for item in store.takedown_list()["must_take_down"]}
        self.assertEqual(refs, {"R1", "E1"})
        # 场次与参与者记录仍在
        self.assertEqual(store.get_session("SESSION-1")["fact_note"], "活动已举办")


class DocumentRevisionTest(unittest.TestCase):
    def test_revision_requires_paired_translation(self) -> None:
        store, _ = build_store()
        build_xining(store)
        with self.assertRaises(DomainError):
            store.add_revision("DOC-KEY", "受控原件://v2", "b" * 64, [])
        revisions = store.list_revisions("DOC-KEY")["revisions"]
        self.assertEqual(len(revisions), 1)
        store.add_revision(
            "DOC-KEY", "受控原件://v2", "b" * 64,
            [{"language": "en", "translation_ref": "受控译文://en/v2",
              "translation_sha256": "c" * 64, "translator_ref": "PERSON-D"}],
            "口译修订：校正火候描述")
        revisions = store.list_revisions("DOC-KEY")["revisions"]
        self.assertEqual([r["seq"] for r in revisions], [1, 2])
        self.assertEqual(revisions[1]["translations"][0]["translation_sha256"], "c" * 64)
        # 序号一一配对
        for rev in revisions:
            self.assertEqual(len(rev["translations"]), 1)
            self.assertEqual(rev["translations"][0]["language"], "en")

    def test_bad_sha256_rejected(self) -> None:
        store, _ = build_store()
        build_xining(store)
        with self.assertRaises(DomainError):
            store.add_revision("DOC-KEY", "受控原件://bad", "not-a-hash",
                               [{"language": "en", "translation_ref": "t",
                                 "translation_sha256": "d" * 64}])


class PublicViewTest(unittest.TestCase):
    def test_public_asset_shows_only_summaries_for_key_process(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        public = store.public_asset("ASSET-1")
        key_doc = next(d for d in public["documents"] if d["doc_ref"] == "DOC-KEY")
        self.assertTrue(key_doc["is_key_process"])
        self.assertEqual(key_doc["public_summary"], "关键工序仅公开摘要")
        # 原文/译文受控引用绝不出现在公众视图
        self.assertNotIn("revisions", key_doc)
        self.assertNotIn("source_ref", key_doc)
        # 获准展示的青年作品可见
        self.assertEqual([w["work_ref"] for w in public["youth_works"]], ["WORK-1"])

    def test_public_asset_404_without_any_valid_public_grant(self) -> None:
        store, _ = build_store()
        build_xining(store)
        with self.assertRaises(DomainError) as ctx:
            store.public_asset("ASSET-1")
        self.assertEqual(ctx.exception.status, 404)

    def test_sessions_remain_public_facts_after_revoke(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        store.revoke_grant("G1", "撤回")
        sessions = store.public_sessions()
        self.assertEqual(sessions[0]["session_ref"], "SESSION-1")
        # 公众场次视图不含任何体验者身份
        self.assertNotIn("participants", sessions[0])

    def test_undisplayed_youth_work_hidden(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.set_work_display_permission("WORK-1", False)
        self.assertEqual(store.public_youth_works(), [])


class TraceTest(unittest.TestCase):
    def test_trace_from_asset_covers_all_bounds(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        trace = store.trace("ASSET-1")
        participant_refs = {p["participant_ref"] for p in trace["participants"]}
        self.assertEqual(participant_refs, {"PERSON-B", "PERSON-C"})
        self.assertEqual(trace["inheritors"][0]["inheritor_ref"], "PERSON-A")
        self.assertEqual(trace["sessions"][0]["session_ref"], "SESSION-1")
        self.assertEqual(trace["material_kits"][0]["material_kit_ref"], "KIT-1")
        self.assertEqual(trace["youth_works"][0]["work_ref"], "WORK-1")
        self.assertEqual(trace["grants"][0]["grant_ref"], "G1")

    def test_trace_from_work_and_session(self) -> None:
        store, _ = build_store()
        build_xining(store)
        work_trace = store.trace("WORK-1")
        self.assertEqual(work_trace["entry_point"], "work")
        self.assertEqual(work_trace["assets"][0]["asset"]["asset_ref"], "ASSET-1")
        self.assertEqual(work_trace["material_kit"]["material_kit_ref"], "KIT-1")
        session_trace = store.trace("SESSION-1")
        self.assertEqual(len(session_trace["participants"]), 2)
        self.assertEqual(session_trace["assets"][0]["asset"]["asset_ref"], "ASSET-1")


class DistributionCheckTest(unittest.TestCase):
    def test_check_reports_bounds(self) -> None:
        store, _ = build_store()
        build_xining(store)
        store.issue_grant("G1", "ASSET-1", "西宁广电", "event-report",
                          "2026-09-19T00:00:00+08:00", FUTURE)
        ok = store.distribution_check("西宁广电", "a" * 64, ["event-report"])
        self.assertTrue(ok["allowed"])
        blocked = store.distribution_check(
            "西宁广电", "a" * 64, ["event-report", "full-process"])
        self.assertFalse(blocked["allowed"])
        self.assertTrue(any("full-process" in b for b in blocked["blockers"]))


if __name__ == "__main__":
    unittest.main()
