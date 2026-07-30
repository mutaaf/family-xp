#!/usr/bin/env python3
"""End-to-end tests for the family portal server.

Spawns the real server in an isolated HOME on a test port, then exercises
auth, roles, onboarding, kids' spaces, quests, chores and path safety over
real HTTP. Stdlib only — run with:  python3 tests/test_portal.py
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

PORT = int(os.environ.get("TEST_PORT", 8199))
BASE = f"http://127.0.0.1:{PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(path, body=None, cookie=None, raw=False):
    req = urllib.request.Request(BASE + path)
    if cookie:
        req.add_header("Cookie", cookie)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"{}")), \
                r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data or b"{}"), ""
        except json.JSONDecodeError:
            return e.code, {}, ""


def login(user, passcode=""):
    status, body, setc = call("/api/login", {"user": user, "passcode": passcode})
    assert body.get("ok"), f"login failed for {user}: {body}"
    return setc.split(";")[0]


class PortalTest(unittest.TestCase):
    proc = None
    home = None

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fxptest-")
        web = os.path.join(cls.home, "projects", "family-portal")
        os.makedirs(web)
        shutil.copy(os.path.join(ROOT, "index.html"), web)
        shutil.copy(os.path.join(ROOT, "guestbook.js"), web)
        env = dict(os.environ, HOME=cls.home, PORTAL_PORT=str(PORT))
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "family_portal.py")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            time.sleep(0.25)
            try:
                call("/api/me")
                return
            except (urllib.error.URLError, ConnectionError, OSError):
                continue
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.send_signal(signal.SIGTERM)
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.home, ignore_errors=True)

    # ---- auth + roles ----
    def test_01_anonymous(self):
        status, body, _ = call("/api/me")
        self.assertFalse(body["ok"])
        status, body, _ = call("/api/projects")
        self.assertEqual(status, 401)

    def test_02_wrong_passcode(self):
        status, body, _ = call("/api/login",
                               {"user": "family", "passcode": "nope"})
        self.assertEqual(status, 403)

    def test_03_parent_login(self):
        c = login("family", "bliss")
        status, body, _ = call("/api/me", cookie=c)
        self.assertEqual(body["user"]["role"], "parent")

    def test_04_fresh_kid_login_no_passcode(self):
        c = login("astro")
        status, body, _ = call("/api/me", cookie=c)
        self.assertEqual(body["user"]["id"], "astro")

    # ---- onboarding ----
    def test_05_onboarding(self):
        c = login("astro")
        status, body, _ = call("/api/onboard", {
            "name": "Zed", "avatar": "dino", "theme": "night",
            "passcode": "sneaky"}, cookie=c)
        self.assertTrue(body.get("ok"), body)
        # empty passcode no longer accepted
        status, body, _ = call("/api/login", {"user": "astro", "passcode": ""})
        self.assertEqual(status, 403)
        c2 = login("astro", "sneaky")
        status, body, _ = call("/api/me", cookie=c2)
        self.assertEqual(body["user"]["name"], "Zed")

    def test_06_onboard_validates(self):
        c = login("robo")
        status, body, _ = call("/api/onboard", {
            "name": "R", "avatar": "hacker", "theme": "day",
            "passcode": "abc"}, cookie=c)
        self.assertEqual(status, 400)

    # ---- kids' spaces ----
    def test_10_kid_project_lifecycle(self):
        c = login("robo")
        status, body, _ = call("/api/kids/new",
                               {"kid": "robo", "name": "Test Page"}, cookie=c)
        self.assertTrue(body.get("ok"), body)
        status, body, _ = call("/api/kids/new",
                               {"kid": "astro", "name": "sneak"}, cookie=c)
        self.assertEqual(status, 403)
        status, body, _ = call(
            "/api/kids/file?kid=robo&proj=test-page", cookie=c)
        self.assertIn("Test Page", body["content"])
        status, body, _ = call("/api/kids/file", {
            "kid": "robo", "proj": "test-page",
            "content": "<h1>seekrit-xyzzy-42</h1>"}, cookie=c)
        self.assertTrue(body.get("ok"))

    def test_11_kid_pages_need_auth(self):
        status, body, _ = call("/kids/robo/test-page/", raw=True)
        self.assertIn(status, (302, 200))  # urllib follows the 302 to /
        if status == 200:
            self.assertNotIn(b"seekrit-xyzzy-42", body)

    def test_12_path_traversal_blocked(self):
        c = login("robo")
        status, body, _ = call(
            "/api/kids/file?kid=robo&proj=..%2F..%2Fserver", cookie=c)
        self.assertEqual(status, 404)
        status, body, _ = call("/server/family_portal.py", raw=True)
        self.assertEqual(status, 404)
        status, body, _ = call("/.git/config", raw=True)
        self.assertEqual(status, 404)

    # ---- quests + chores ----
    def test_20_quests(self):
        c = login("robo")
        status, body, _ = call("/api/quests", cookie=c)
        self.assertEqual(body["track"], "explorer")
        status, body, _ = call("/api/quests/check",
                               {"mission": "e1"}, cookie=c)
        self.assertIn("e1", body["new"])
        self.assertGreater(body["xp"], 0)

    def test_21_robot_progress(self):
        c = login("robo")
        status, body, _ = call("/api/robot", {"level": 3}, cookie=c)
        self.assertEqual(body["robot"], 3)
        status, body, _ = call("/api/robot", {"level": 1}, cookie=c)
        self.assertEqual(body["robot"], 3)  # never regresses

    def test_22_chores_lifecycle(self):
        kid = login("robo")
        parent = login("family", "bliss")
        status, body, _ = call("/api/chores/claim", {"chore": "bed"}, cookie=kid)
        self.assertTrue(body.get("ok"), body)
        status, body, _ = call("/api/chores/claim", {"chore": "bed"}, cookie=kid)
        self.assertEqual(status, 400)  # no double-claim
        status, body, _ = call("/api/chores", cookie=parent)
        ts = body["kids"]["robo"]["pending"][0]["ts"]
        before = body["kids"]["robo"]["balance"]
        status, body, _ = call("/api/chores/approve",
                               {"kid": "robo", "ts": ts, "ok": True}, cookie=parent)
        self.assertEqual(body["balance"], before + 10)

    def test_23_rewards_rules(self):
        kid = login("robo")
        status, body, _ = call("/api/rewards/redeem",
                               {"reward": "vacation"}, cookie=kid)
        self.assertEqual(status, 400)  # can't afford 2500
        status, body, _ = call("/api/chores/approve",
                               {"kid": "robo", "ts": 1, "ok": True}, cookie=kid)
        self.assertEqual(status, 403)  # kids can't approve

    # ---- admin ----
    def test_30_admin(self):
        parent = login("family", "bliss")
        kid = login("robo")
        status, body, _ = call("/api/admin/config", cookie=kid)
        self.assertEqual(status, 403)
        status, body, _ = call("/api/admin/config", cookie=parent)
        self.assertEqual(len(body["users"]), 3)
        status, body, _ = call("/api/admin/save",
                               {"which": "missions", "content": "{bad"},
                               cookie=parent)
        self.assertEqual(status, 400)

    def test_31_add_export_remove_kid(self):
        parent = login("family", "bliss")
        status, body, _ = call("/api/admin/adduser", {"name": "Zip Zap"},
                               cookie=parent)
        self.assertTrue(body.get("ok"), body)
        kid_id = body["id"]
        status, users_body, _ = call("/api/users")
        self.assertIn(kid_id, [u["id"] for u in users_body["users"]])
        # new kid signs in passcode-free and lands un-onboarded
        c = login(kid_id)
        status, me, _ = call("/api/me", cookie=c)
        self.assertEqual(me["user"]["id"], kid_id)
        # export is a real zip
        status, blob, _ = call(f"/api/admin/export?kid={kid_id}",
                               cookie=parent, raw=True)
        self.assertEqual(blob[:2], b"PK")
        status, blob, _ = call("/api/admin/export?kid=all",
                               cookie=parent, raw=True)
        self.assertEqual(blob[:2], b"PK")
        # remove archives the account
        status, body, _ = call("/api/admin/removeuser", {"id": kid_id},
                               cookie=parent)
        self.assertTrue(body.get("ok"))
        status, users_body, _ = call("/api/users")
        self.assertNotIn(kid_id, [u["id"] for u in users_body["users"]])
        # parents can't be removed
        status, body, _ = call("/api/admin/removeuser", {"id": "family"},
                               cookie=parent)
        self.assertEqual(status, 404)

    def test_32_ten_kid_cap(self):
        parent = login("family", "bliss")
        added = []
        while True:
            status, body, _ = call("/api/admin/adduser",
                                   {"name": "Extra"}, cookie=parent)
            if status != 200 or not body.get("ok"):
                self.assertEqual(status, 400)
                self.assertIn("10", body["error"])
                break
            added.append(body["id"])
            self.assertLess(len(added), 12, "cap never enforced")
        for kid in added:   # leave config as we found it
            call("/api/admin/removeuser", {"id": kid}, cookie=parent)


class HardeningTest(PortalTest):
    """Data-integrity guarantees: ledger, resets, corruption recovery."""

    def test_50_ledger_records_every_xp_move(self):
        parent = login("family", "bliss")
        kid = login("robo")
        call("/api/arcade", {"game": "math", "xp": 6, "level": 1}, cookie=kid)
        status, body, _ = call("/api/admin/ledger?kid=robo", cookie=parent)
        self.assertEqual(status, 200)
        sources = [e["source"] for e in body["ledger"]]
        self.assertIn("game:math", sources)
        self.assertTrue(all("delta" in e and "ts" in e for e in body["ledger"]))

    def test_51_integrity_clean(self):
        parent = login("family", "bliss")
        status, body, _ = call("/api/admin/integrity", cookie=parent)
        self.assertTrue(body["ok"], body)
        self.assertIn("robo", body["kids"])

    def test_52_corruption_recovers_from_bak(self):
        kid = login("robo")
        status, before, _ = call("/api/quests", cookie=kid)
        pf = os.path.join(self.home, "projects", "family-portal",
                          "kids", "robo", ".progress.json")
        with open(pf, "w") as f:
            f.write("{corrupt garbage!!")
        status, after, _ = call("/api/quests", cookie=kid)
        self.assertEqual(status, 200)
        self.assertEqual(after["xp"], before["xp"])   # .bak saved the day
        self.assertTrue(any(".corrupt-" in n
                            for n in os.listdir(os.path.dirname(pf))))

    def test_53_progress_reset(self):
        parent = login("family", "bliss")
        kid = login("robo")
        status, before, _ = call("/api/quests", cookie=kid)
        self.assertGreater(before["xp"], 0)
        status, body, _ = call("/api/admin/reset",
                               {"scope": "progress", "kid": "robo"}, cookie=parent)
        self.assertTrue(body.get("ok"), body)
        status, after, _ = call("/api/quests", cookie=kid)
        # pages survive a reset, so auto-verified page missions re-award —
        # but manual awards, robot, chores, adjustments and games are gone
        self.assertLessEqual(after["xp"], before["xp"])
        self.assertEqual(after["robot"], 0)
        self.assertEqual(after["balance"], after["xp"])  # nothing but quests left
        arc = os.path.join(self.home, "projects", "family-portal",
                           "kids", ".archive")
        self.assertTrue(any(n.startswith("progress-robo-")
                            for n in os.listdir(arc)))
        status, body, _ = call("/api/admin/reset",
                               {"scope": "progress", "kid": "robo"}, cookie=kid)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
