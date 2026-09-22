
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.main import Handler

ROOT = Path(__file__).resolve().parent.parent
FUTURE = "2026-12-31T23:59:59+08:00"


class HttpScenarioTest(unittest.TestCase):
    server = None
    thread = None
    base_url = ""
    db_path = ""

    @classmethod
    def setUpClass(cls) -> None:
        fd, cls.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(cls.db_path)
        os.environ["DATABASE_PATH"] = cls.db_path
        with sqlite3.connect(cls.db_path) as conn:
            for name in ("001_bootstrap.sql", "002_authorization.sql"):
                conn.executescript((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        Path(cls.db_path).unlink(missing_ok=True)

    def request(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_end_to_end_scenario(self) -> None:
        # 健康检查
        status, body = self.request("GET", "/health")
        self.assertEqual((status, body["status"]), (200, "ok"))

        # 登记
        self.assertEqual(self.request("POST", "/staff/heritage", {
            "heritage_ref": "H1", "name": "河湟银铜器", "summary": "省级非遗"})[0], 201)
        self.assertEqual(self.request("POST", "/staff/inheritors/consents", {
            "inheritor_ref": "P-A", "heritage_ref": "H1",
            "allowed_scopes": ["event-report", "portrait"]})[0], 201)
        self.assertEqual(self.request("POST", "/staff/sessions", {
            "session_ref": "S1", "heritage_ref": "H1", "inheritor_ref": "P-A",
            "title": "交流演示", "occurred_at": "2026-09-20T10:00:00+08:00",
            "location_ref": "西宁非遗馆"})[0], 201)
        self.assertEqual(self.request("POST", "/staff/sessions/S1/participants", {
            "participant_ref": "P-B", "role": "体验者",
            "portrait_consent": True})[0], 201)

        # 同一视频两家媒体上传：第二份识别为同一素材
        status, first = self.request("POST", "/staff/assets", {
            "asset_ref": "A1", "fingerprint_sha256": "f" * 64, "title": "演示视频"})
        self.assertEqual(status, 201)
        self.assertFalse(first["deduped"])
        status, second = self.request("POST", "/staff/assets", {
            "asset_ref": "A2", "fingerprint_sha256": "f" * 64, "title": "重复上传"})
        self.assertEqual(status, 201)
        self.assertTrue(second["deduped"])
        self.assertEqual(second["asset_ref"], "A1")

        self.assertEqual(self.request("POST", "/staff/assets/A1/links", {
            "target_kind": "session", "target_ref": "S1"})[0], 201)
        self.assertEqual(self.request("POST", "/staff/assets/A1/captures", {
            "uploader_org": "西宁广电"})[0], 201)
        self.assertEqual(self.request("POST", "/staff/assets/A1/captures", {
            "uploader_org": "某自媒体"})[0], 201)

        # 西宁广电取得 event-report 许可
        self.assertEqual(self.request("POST", "/staff/grants", {
            "grant_ref": "G1", "ref_or_fingerprint": "A1", "grantee_org": "西宁广电",
            "scope": "event-report", "valid_from": "2026-09-19T00:00:00+08:00",
            "expires_at": FUTURE})[0], 201)

        # 自媒体试图发布完整工序报道：被阻止
        status, body = self.request("POST", "/staff/reports", {
            "report_ref": "R-BAD", "grantee_org": "某自媒体",
            "ref_or_fingerprint": "f" * 64, "title": "绝密全过程",
            "required_scopes": ["event-report", "full-process"]})
        self.assertEqual(status, 409)
        self.assertIn("阻止", body["error"])

        # 西宁广电正常发布，公众可见
        self.assertEqual(self.request("POST", "/staff/reports", {
            "report_ref": "R1", "grantee_org": "西宁广电",
            "ref_or_fingerprint": "A1", "title": "活动报道",
            "required_scopes": ["event-report"]})[0], 201)
        status, body = self.request("GET", "/public/reports")
        self.assertEqual([r["report_ref"] for r in body["items"]], ["R1"])

        # 分发闸门
        status, body = self.request("POST", "/staff/distribution-check", {
            "grantee_org": "某自媒体", "ref_or_fingerprint": "f" * 64,
            "scopes": ["event-report"]})
        self.assertEqual((status, body["allowed"]), (200, False))

        # 馆员从指纹追溯体验者、传承人、上传方与许可
        status, trace = self.request("GET", "/staff/trace/" + "f" * 64)
        self.assertEqual(status, 200)
        self.assertEqual(trace["asset"]["asset_ref"], "A1")
        self.assertEqual({p["participant_ref"] for p in trace["participants"]}, {"P-B"})
        self.assertEqual(trace["inheritors"][0]["inheritor_ref"], "P-A")
        self.assertEqual({c["uploader_org"] for c in trace["captures"]},
                         {"西宁广电", "某自媒体"})

        # 撤回许可：报道进入下线清单并被执行，场次事实仍在
        status, body = self.request("POST", "/staff/grants/G1/revoke",
                                    {"reason": "传承人撤回"})
        self.assertEqual(status, 201)
        self.assertEqual([i["ref"] for i in body["taken_down_items"]], ["R1"])
        status, body = self.request("GET", "/public/reports")
        self.assertEqual(body["items"], [])
        status, body = self.request("GET", "/public/sessions")
        self.assertEqual(body["items"][0]["session_ref"], "S1")

        # 审计可追查
        status, body = self.request("GET", "/staff/audit")
        actions = {e["action"] for e in body["events"]}
        self.assertIn("grant.revoke", actions)
        self.assertIn("report.publish.blocked", actions)
        self.assertIn("asset.dedupe", actions)

    def test_validation_errors(self) -> None:
        status, body = self.request("POST", "/staff/sessions", {
            "session_ref": "SX", "heritage_ref": "H1", "inheritor_ref": "P-A",
            "title": "x", "occurred_at": "2026-09-20T10:00:00"})  # 缺偏移量
        self.assertEqual(status, 400)
        self.assertIn("偏移量", body["error"])
        status, _ = self.request("GET", "/public/assets/unknown")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
