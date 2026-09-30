"""Run with:  python -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from html import escape
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("sb", os.path.join(ROOT, "shift-bot.py"))
sb = importlib.util.module_from_spec(spec)
sys.modules["sb"] = sb
spec.loader.exec_module(sb)

A = "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA"
B = "BBBBB-BBBBB-BBBBB-BBBBB-BBBBB"
C = "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC"
POSTED = datetime(2026, 9, 23, tzinfo=timezone.utc)


def codes(title, body, posted=POSTED):
    return [(c, str(e) if e else None) for c, e in sb.extract_codes(title, body, posted)]


def atom_feed(posts):
    """posts: list of (title, body_html, datetime). Mimics Reddit's Atom RSS output."""
    entries = ""
    for n, (title, body, dt) in enumerate(posts):
        content = f'<!-- SC_OFF --><div class="md">{body}</div><!-- SC_ON --> &#32; submitted by &#32; <a href="https://www.reddit.com/user/x"> /u/x </a>'
        entries += f"""<entry><title>{escape(title)}</title>
        <link href="https://www.reddit.com/r/Borderlandsshiftcodes/comments/id{n}/x/"/>
        <updated>{dt.isoformat()}</updated><published>{dt.isoformat()}</published>
        <content type="html">{escape(content)}</content></entry>"""
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


class ExtractCodesTests(unittest.TestCase):
    def test_bl4_title_with_expiry_on_next_line(self):
        self.assertEqual(codes("BL4", f"{A}\nExpires 9/29."), [(A, "2026-09-29")])

    def test_other_game_only_is_rejected(self):
        self.assertEqual(codes("BL3", A), [])

    def test_no_game_mentioned_is_rejected(self):
        self.assertEqual(codes("code", A), [])

    def test_multi_post_is_accepted(self):
        self.assertEqual(codes("MULTI", f"Golden keys in Borderlands 1,2,3,4, Pre-Sequel and Wonderlands:\n{A}"), [(A, None)])

    def test_bl4_and_other_games_in_same_line_is_accepted(self):
        self.assertEqual(codes("BL4", f"2 keys for BL4 and 5 for BL3, BL2, BL1:\n{A}"), [(A, None)])

    def test_per_line_labels_do_not_leak(self):
        self.assertEqual(codes("lists", f"BL3: {A}\nBL4: {B}"), [(B, None)])
        self.assertEqual(codes("lists", f"BL4: {A}\nBL3: {B}"), [(A, None)])
        self.assertEqual(codes("lists", f"BL4:\n{A}\nBL3:\n{B}\nBL4:\n{C}"), [(A, None), (C, None)])

    def test_multiple_codes_on_one_line(self):
        self.assertEqual(codes("BL4", f"{A} {B}"), [(A, None), (B, None)])

    def test_code_embedded_in_longer_token_is_ignored(self):
        self.assertEqual(codes("BL4", f"X{A}"), [])
        self.assertEqual(codes("BL4", f"{A}-EXTRA"), [])

    def test_lowercase_code_is_uppercased(self):
        self.assertEqual(codes("BL4", A.lower()), [(A, None)])

    def test_expiry_only_applies_to_its_own_code(self):
        self.assertEqual(codes("BL4", f"{A}\nExpires 9/29\n{B}\nno expiry known"), [(A, "2026-09-29"), (B, None)])

    def test_single_code_expiry_may_be_anywhere_in_post(self):
        self.assertEqual(codes("BL4", f"{A}\n\n\n\nGood luck!\nExpires 10/5"), [(A, "2026-10-05")])


class ExpiryTests(unittest.TestCase):
    def check(self, text, expected, posted=POSTED):
        d = sb.parse_expiry(text, posted)
        self.assertEqual(str(d) if d else None, expected, text)

    def test_formats(self):
        self.check("Expires 9/29.", "2026-09-29")
        self.check("expires 9/29/2026", "2026-09-29")
        self.check("expires 9/29/26", "2026-09-29")
        self.check("valid until Oct 5, 2026", "2026-10-05")
        self.check("Expiry: September 30", "2026-09-30")
        self.check("expires 1st October", "2026-10-01")
        self.check("until Oct. 3rd", "2026-10-03")

    def test_year_rollover(self):
        self.check("expires 1/3", "2027-01-03", datetime(2026, 12, 28, tzinfo=timezone.utc))
        self.check("expires 12/30", "2026-12-30", datetime(2026, 12, 28, tzinfo=timezone.utc))

    def test_garbage_is_ignored(self):
        self.check("expires 13/45", None)
        self.check("no date here", None)
        self.check("expires soon", None)


class ParseFeedTests(unittest.TestCase):
    def test_parse_feed(self):
        now = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
        feed = atom_feed([
            ("MULTI", f"<p>Keys for Borderlands 1,2,3,4:</p><p>{A}</p>", now),
            ("BL4", f"<p>{B}</p><p>Expires 9/29.</p>", now - timedelta(days=1)),
            ("BL3", f"<p>{C}</p>", now - timedelta(days=2)),
            ("BL4", f"<p>OLDAA-AAAAA-AAAAA-AAAAA-AAAAA</p>", datetime(2025, 9, 1, tzinfo=timezone.utc)),  # before release
        ])
        res = sb.parse_feed(feed)
        self.assertEqual([r["code"] for r in res], [A, B])
        self.assertEqual(res[1]["expires_date"], "2026-09-29")
        self.assertTrue(res[0]["source_url"].startswith("https://www.reddit.com/"))
        self.assertTrue(res[0]["posted_at"].startswith("2026-09-28T20:00"))

    def test_html_entities_and_submitted_by_footer(self):
        text = sb.html_to_text('<p>a &amp; b</p><p>x</p> &#32; submitted by &#32; <a>/u/BL4-fan</a>')
        self.assertIn("a & b", text)
        self.assertNotIn("BL4", text)  # username in the footer must not count as game context

    def test_empty_and_garbage_feed(self):
        self.assertEqual(sb.parse_feed(b""), [])
        self.assertEqual(sb.parse_feed(b"<html>not a feed</html>"), [])


class FetchTests(unittest.TestCase):
    def fake(self, status, headers=None, content=b""):
        resp = mock.Mock(status_code=status, headers=headers or {}, content=content)
        return mock.patch.object(sb.session, "get", return_value=resp)

    def test_429_uses_retry_hint(self):
        with self.fake(429, {"x-ratelimit-reset": "44"}):
            res, err, wait = sb.fetch_reddit_codes()
        self.assertEqual((res, wait), ([], 49.0))
        self.assertIn("429", err)

    def test_429_retry_after_header_wins(self):
        with self.fake(429, {"Retry-After": "300", "x-ratelimit-reset": "44"}):
            self.assertEqual(sb.fetch_reddit_codes()[2], 305.0)

    def test_429_without_hint_and_bad_hint(self):
        with self.fake(429):
            self.assertEqual(sb.fetch_reddit_codes()[2], 65.0)
        with self.fake(429, {"Retry-After": "Wed, 21 Oct"}):
            self.assertEqual(sb.fetch_reddit_codes()[2], 65.0)

    def test_403_backs_off_15_minutes(self):
        with self.fake(403):
            _, err, wait = sb.fetch_reddit_codes()
        self.assertEqual(wait, 900)
        self.assertIn("403", err)

    def test_other_status_and_network_error(self):
        with self.fake(500):
            self.assertIn("500", sb.fetch_reddit_codes()[1])
        with mock.patch.object(sb.session, "get", side_effect=sb.requests.ConnectionError("boom")):
            self.assertIn("boom", sb.fetch_reddit_codes()[1])

    def test_success_parses(self):
        feed = atom_feed([("BL4", f"<p>{A}</p>", datetime.now(timezone.utc))])
        with self.fake(200, content=feed):
            res, err, wait = sb.fetch_reddit_codes()
        self.assertEqual((len(res), err, wait), (1, None, None))

    def test_user_agent_is_descriptive_not_browser(self):
        ua = sb.session.headers["User-Agent"]
        self.assertNotIn("Mozilla", ua)
        self.assertIn("bl4-shift-tracker", ua)
        self.assertTrue(sb.REDDIT_URL.endswith("limit=50") and ".rss" in sb.REDDIT_URL)


class StateTestCase(unittest.TestCase):
    """Base class: isolated data dir, clean globals."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(sb, "DATA_DIR", self.tmp),
            mock.patch.object(sb, "STORAGE_FILE", os.path.join(self.tmp, "shift_codes_state.json")),
            mock.patch.object(sb, "LEGACY_STORAGE_FILE", os.path.join(self.tmp, "nope.json")),
            mock.patch.object(sb, "DISCORD_DELAY", 0),
            mock.patch.object(sb, "DISCORD_WEBHOOK_URL", "http://discord.invalid/hook"),
        ]
        for p in self.patches:
            p.start()
        sb.last_error = sb.last_checked_at = None
        self.client = sb.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entry(self, code, posted, **kw):
        e = {"code": code, "found_at": posted, "posted_at": posted, "expires_date": "", "activated": False,
             "expired_manually": False, "source": "Reddit", "source_url": "https://www.reddit.com/x"}
        e.update(kw)
        return e

    def item(self, code, posted, expires=""):
        return {"code": code, "posted_at": posted.isoformat(), "expires_date": expires, "source": "Reddit",
                "source_url": "https://www.reddit.com/x"}


class StorageTests(StateTestCase):
    def test_roundtrip_and_no_temp_files_left(self):
        sb.save_state({A: self.entry(A, "2026-09-01T00:00:00+00:00")})
        self.assertEqual(list(sb.load_state()), [A])
        self.assertEqual(os.listdir(self.tmp), ["shift_codes_state.json"])

    def test_creates_missing_data_dir(self):
        shutil.rmtree(self.tmp)
        sb.save_state({})
        self.assertTrue(os.path.exists(sb.STORAGE_FILE))

    def test_corrupt_file_is_survived(self):
        with open(sb.STORAGE_FILE, "w") as f:
            f.write("{not json")
        self.assertEqual(sb.load_state(), {})

    def test_failed_write_keeps_old_file(self):
        sb.save_state({A: self.entry(A, "2026-09-01T00:00:00+00:00")})
        with mock.patch.object(sb.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sb.save_state({})
        self.assertEqual(list(sb.load_state()), [A])
        self.assertEqual(os.listdir(self.tmp), ["shift_codes_state.json"])

    def test_legacy_state_is_migrated(self):
        legacy = os.path.join(self.tmp, "legacy.json")
        with open(legacy, "w") as f:
            json.dump({A: {"code": A, "found": "Sep 12, 2025, 10:00", "activated": True, "expires": "Sep 20, 2025"}}, f)
        with mock.patch.object(sb, "LEGACY_STORAGE_FILE", legacy):
            state = sb.load_state()
        self.assertNotIn("found", state[A])
        self.assertTrue(state[A]["found_at"].startswith("2025-09-1"))
        self.assertTrue(state[A]["activated"])

    def test_very_old_entries_missing_fields_do_not_break_the_api(self):
        # Shape found in a real old state file: no code/activated/source fields at all
        with open(sb.STORAGE_FILE, "w") as f:
            json.dump({A: {"expires": "", "source_url": "https://www.reddit.com/r/x/", "found_at": "2025-12-14T09:07:00+00:00"},
                       B: {"expires": "Oct 05, 2026"},
                       C: "garbage"}, f)
        client = sb.app.test_client()
        data = client.get("/api/codes").get_json()
        self.assertEqual({c["code"] for c in data["codes"]}, {A, B, C})
        first = next(c for c in data["codes"] if c["code"] == A)
        self.assertFalse(first["activated"] or first["expired"])
        self.assertEqual(first["source"], "Reddit")
        self.assertEqual(next(c for c in data["codes"] if c["code"] == B)["expires"], "Oct 05, 2026")
        self.assertEqual(client.get("/api/steam-json").status_code, 200)
        self.assertEqual(client.get("/api/steam-bbcode").status_code, 200)
        self.assertEqual(client.post("/api/codes", json={"code": A, "state": "activated", "value": True}).status_code, 200)

    def test_new_file_wins_over_legacy(self):
        legacy = os.path.join(self.tmp, "legacy.json")
        with open(legacy, "w") as f:
            json.dump({B: {"code": B, "found": "Sep 12, 2025, 10:00"}}, f)
        sb.save_state({A: self.entry(A, "2026-09-01T00:00:00+00:00")})
        with mock.patch.object(sb, "LEGACY_STORAGE_FILE", legacy):
            self.assertEqual(list(sb.load_state()), [A])


class CheckOnceTests(StateTestCase):
    def run_check(self, items=None, error=None, retry=None):
        with mock.patch.object(sb, "fetch_reddit_codes", return_value=(items or [], error, retry)), \
                mock.patch.object(sb, "send_discord_notification") as notify:
            result = sb.check_once()
        return result, notify

    def test_new_codes_saved_and_recent_ones_notified(self):
        now = datetime.now(timezone.utc)
        items = [self.item(A, now - timedelta(hours=2)),
                 self.item(B, now - timedelta(days=10)),                      # old: saved, not announced
                 self.item(C, now - timedelta(hours=1), expires="2020-01-01")]  # already expired: not announced
        (ok, _), notify = self.run_check(items)
        self.assertTrue(ok)
        self.assertEqual(set(sb.load_state()), {A, B, C})
        self.assertEqual([c.args[0]["code"] for c in notify.call_args_list], [A])
        self.assertIsNotNone(sb.last_checked_at)
        self.assertIsNone(sb.last_error)

    def test_second_run_does_not_duplicate_or_renotify(self):
        items = [self.item(A, datetime.now(timezone.utc))]
        self.run_check(items)
        (_, _), notify = self.run_check(items)
        notify.assert_not_called()
        self.assertEqual(len(sb.load_state()), 1)

    def test_expiry_update_keeps_user_flags_and_never_erases(self):
        now = datetime.now(timezone.utc)
        self.run_check([self.item(A, now)])
        state = sb.load_state()
        state[A]["activated"] = True
        sb.save_state(state)
        self.run_check([self.item(A, now, expires="2099-01-01")])
        self.assertEqual(sb.load_state()[A]["expires_date"], "2099-01-01")
        self.run_check([self.item(A, now, expires="")])  # post edited, date no longer parsed
        s = sb.load_state()[A]
        self.assertEqual(s["expires_date"], "2099-01-01")
        self.assertTrue(s["activated"])

    def test_error_leaves_state_untouched(self):
        sb.save_state({A: self.entry(A, "2026-09-01T00:00:00+00:00")})
        (ok, retry), _ = self.run_check(error="Reddit rate limit hit (429)", retry=49)
        self.assertEqual((ok, retry), (False, 49))
        self.assertEqual(sb.last_error, "Reddit rate limit hit (429)")
        self.assertEqual(list(sb.load_state()), [A])

    def test_discord_payload(self):
        with mock.patch.object(sb.requests, "post") as post:
            sb.send_discord_notification(self.entry(A, "2026-09-01T00:00:00+00:00", expires_date="2026-09-29"))
        embed = post.call_args.kwargs["json"]["embeds"][0]
        self.assertIn(A, embed["fields"][0]["value"])
        self.assertEqual(embed["fields"][1]["value"], "Sep 29, 2026")
        self.assertEqual(embed["footer"]["text"], "BL4 SHiFT Code Tracker")

    def test_discord_failure_and_disabled(self):
        with mock.patch.object(sb.requests, "post", side_effect=sb.requests.ConnectionError("down")):
            sb.send_discord_notification(self.entry(A, "2026-09-01T00:00:00+00:00"))  # must not raise
        with mock.patch.object(sb, "DISCORD_WEBHOOK_URL", ""), mock.patch.object(sb.requests, "post") as post:
            sb.send_discord_notification(self.entry(A, "2026-09-01T00:00:00+00:00"))
            post.assert_not_called()


class BackoffAndOutageTests(StateTestCase):
    def test_next_wait(self):
        self.assertEqual(sb.next_wait(0, None), sb.CHECK_INTERVAL)
        self.assertAlmostEqual(sb.next_wait(1, None), 120, delta=15)
        self.assertAlmostEqual(sb.next_wait(2, None), 240, delta=15)
        self.assertAlmostEqual(sb.next_wait(3, 900), 900, delta=15)  # Reddit's hint wins
        self.assertLessEqual(sb.next_wait(30, None), sb.CHECK_INTERVAL + 15)  # capped

    def test_alert_sent_once_then_recovery_once(self):
        with mock.patch.object(sb, "post_discord") as post:
            alerted = False
            for failures in (1, 2):
                alerted = sb.track_outage(failures, "err", alerted)
            self.assertFalse(alerted)
            post.assert_not_called()

            alerted = sb.track_outage(3, "Reddit refused the request (403)", alerted)
            self.assertTrue(alerted)
            self.assertEqual(post.call_count, 1)
            self.assertIn("403", post.call_args.args[0]["description"])

            for failures in (4, 5, 6):  # no repeat spam while still down
                alerted = sb.track_outage(failures, "err", alerted)
            self.assertEqual(post.call_count, 1)

            alerted = sb.track_outage(0, None, alerted)
            self.assertFalse(alerted)
            self.assertEqual(post.call_count, 2)
            self.assertIn("working again", post.call_args.args[0]["title"])

            sb.track_outage(0, None, alerted)  # healthy stays quiet
            self.assertEqual(post.call_count, 2)

    def test_short_blip_sends_nothing(self):
        with mock.patch.object(sb, "post_discord") as post:
            alerted = sb.track_outage(1, "x", False)
            alerted = sb.track_outage(0, None, alerted)
            post.assert_not_called()


class ApiTests(StateTestCase):
    def seed(self):
        sb.save_state({
            "OLD": self.entry("OLD", "2026-09-01T10:00:00+00:00", expires_date="2026-09-02"),
            "MID": self.entry("MID", "2026-09-20T10:00:00+00:00"),
            "NEW": self.entry("NEW", "2026-09-28T10:00:00+00:00"),
            "MAN": self.entry("MAN", "2026-09-25T10:00:00+00:00", expired_manually=True),
            "FUT": self.entry("FUT", "2026-09-10T10:00:00+00:00", expires_date="2099-01-01"),
        })

    def test_sorted_newest_first_and_expiry_flags(self):
        self.seed()
        data = self.client.get("/api/codes").get_json()
        self.assertEqual([c["code"] for c in data["codes"]], ["NEW", "MAN", "MID", "FUT", "OLD"])
        flags = {c["code"]: c["expired"] for c in data["codes"]}
        self.assertEqual(flags, {"NEW": False, "MAN": True, "MID": False, "FUT": False, "OLD": True})
        self.assertEqual(next(c for c in data["codes"] if c["code"] == "OLD")["expires"], "Sep 02, 2026")

    def test_sort_is_chronological_not_alphabetical(self):
        # "Sep" > "Oct" alphabetically, which is what the old string sort got wrong
        sb.save_state({"SEP": self.entry("SEP", "2026-09-30T00:00:00+00:00"),
                       "OCT": self.entry("OCT", "2026-10-02T00:00:00+00:00"),
                       "AUG": self.entry("AUG", "2026-08-31T00:00:00+00:00")})
        codes_ = [c["code"] for c in self.client.get("/api/codes").get_json()["codes"]]
        self.assertEqual(codes_, ["OCT", "SEP", "AUG"])

    def test_sort_uses_post_time_over_discovery_time(self):
        sb.save_state({"FIRST": self.entry("FIRST", "2026-09-01T00:00:00+00:00", found_at="2026-09-30T00:00:00+00:00"),
                       "SECOND": self.entry("SECOND", "2026-09-05T00:00:00+00:00", found_at="2026-09-29T00:00:00+00:00")})
        codes_ = [c["code"] for c in self.client.get("/api/codes").get_json()["codes"]]
        self.assertEqual(codes_, ["SECOND", "FIRST"])

    def test_toggle_flags(self):
        self.seed()
        r = self.client.post("/api/codes", json={"code": "NEW", "state": "activated", "value": True})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/codes", json={"code": "NEW", "state": "expired", "value": True})
        self.assertEqual(r.status_code, 200)
        new = next(c for c in self.client.get("/api/codes").get_json()["codes"] if c["code"] == "NEW")
        self.assertTrue(new["activated"] and new["expired"] and new["expired_manually"])
        self.client.post("/api/codes", json={"code": "NEW", "state": "expired", "value": False})
        new = next(c for c in self.client.get("/api/codes").get_json()["codes"] if c["code"] == "NEW")
        self.assertFalse(new["expired"])

    def test_post_validation(self):
        self.seed()
        bad = [{"code": "NEW", "state": "bogus", "value": True},
               {"code": "NEW", "state": "activated", "value": "yes"},
               {"code": "NEW", "state": "activated", "value": 1},
               {"code": 5, "state": "activated", "value": True},
               {"state": "activated", "value": True},
               {}]
        for body in bad:
            self.assertEqual(self.client.post("/api/codes", json=body).status_code, 400, body)
        self.assertEqual(self.client.post("/api/codes", data="not json", content_type="text/plain").status_code, 400)
        self.assertEqual(self.client.post("/api/codes", json={"code": "NOPE", "state": "activated", "value": True}).status_code, 404)

    def test_steam_exports_exclude_expired(self):
        self.seed()
        js = self.client.get("/api/steam-json").get_json()
        self.assertEqual(list(js), ["NEW", "MID", "FUT"])
        bb = self.client.get("/api/steam-bbcode").get_json()["content"]
        self.assertTrue(bb.startswith("[table]") and bb.endswith("[/table]"))
        self.assertIn("NEW", bb)
        self.assertNotIn("OLD", bb)
        self.assertNotIn("MAN", bb)
        self.assertLess(bb.index("NEW"), bb.index("MID"))

    def test_check_now_cooldown(self):
        with mock.patch.object(sb, "last_manual_check", 0.0):
            self.assertEqual(self.client.post("/api/check-now").status_code, 200)
            r = self.client.post("/api/check-now")
        self.assertEqual(r.status_code, 429)
        self.assertGreater(r.get_json()["retry_in"], 0)

    def test_logs_and_index(self):
        sb.log("hello test")
        self.assertIn("hello test", self.client.get("/api/logs").get_json()[0])
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"BL4 SHiFT Code Tracker", r.data)
        r.close()

    def test_empty_state(self):
        data = self.client.get("/api/codes").get_json()
        self.assertEqual(data["codes"], [])
        self.assertEqual(self.client.get("/api/steam-json").get_json(), {})

    def test_log_survives_unencodable_console(self):
        class Narrow:
            def write(self, s):
                s.encode("cp1252")
            def flush(self):
                pass
        with mock.patch.object(sys, "stdout", Narrow()):
            sb.log("✨ emoji on a cp1252 console")


class IsExpiredTests(unittest.TestCase):
    def test_boundaries(self):
        today = date.today()
        self.assertFalse(sb.is_expired({"expires_date": today.isoformat()}))  # valid through the day itself
        self.assertTrue(sb.is_expired({"expires_date": (today - timedelta(days=1)).isoformat()}))
        self.assertFalse(sb.is_expired({}))
        self.assertFalse(sb.is_expired({"expires_date": "garbage"}))
        self.assertTrue(sb.is_expired({"expired_manually": True}))


class ProjectFilesTests(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_compose_mounts_data_folder_and_binds_localhost(self):
        compose = self.read("docker-compose.yml")
        self.assertIn("./data:/app/data", compose)
        self.assertIn("127.0.0.1:5500:5000", compose)
        self.assertIn("CHECK_INTERVAL_MINUTES:-120", compose)

    def test_dockerfile_state_dir_matches_mount(self):
        df = self.read("Dockerfile")
        self.assertIn("DATA_DIR=/app/data", df)
        self.assertIn("HOST=0.0.0.0", df)

    def test_default_interval_is_120_minutes(self):
        self.assertEqual(sb.CHECK_INTERVAL, 120 * 60)

    def test_no_twitter_left(self):
        self.assertNotIn("twitter", self.read("shift-bot.py").lower())
        self.assertNotIn("twitter", self.read("static", "index.html").lower())

    def test_requirements(self):
        reqs = self.read("requirements.txt").split()
        for pkg in ("requests", "feedparser", "flask", "waitress"):
            self.assertIn(pkg, reqs)


if __name__ == "__main__":
    unittest.main()
