# -*- coding: utf-8 -*-
"""Offline synthetic tests for ticker -> price facts -> user conclusion."""
import copy
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pandas as pd

from asxquant import stock_lookup as S
from short_radar.calendars import session

NOW = dt.datetime(2026, 10, 15, 8, tzinfo=dt.timezone.utc)


def fixture(ticker="BHP.AX", direction=1, end="2026-10-15"):
    day = dt.date.fromisoformat(end)
    days = []
    while len(days) < 220:
        if session("AU", day):
            days.append(day.isoformat())
        day -= dt.timedelta(days=1)
    bars = []
    for i, day in enumerate(reversed(days)):
        p = 40 + direction * i * .05
        bars.append(dict(date=day, open=p-.01, high=p+.1, low=p-.1, close=p, volume=1_000_000.))
    return dict(ticker=ticker, name="合成测试，非真实股票行情", bars=bars, events=[], quote={},
                source="synthetic_test", source_url=None, fetched_at=NOW.isoformat(), sha256="fixture",
                snapshot_kind="synthetic", fetch_error=None, events_status="provider_reported")


def response():
    return {"chart": {"result": [{"meta": {"symbol":"BHP.AX", "currency":"AUD", "exchangeName":"ASX",
            "exchangeTimezoneName":"Australia/Sydney", "instrumentType":"EQUITY", "regularMarketTime":1792041640, "regularMarketPrice":10.},
            "timestamp":[1792018800], "indicators":{"quote":[{"open":[10.], "high":[11.], "low":[9.],
            "close":[10.], "volume":[100.]}]}}]}}


class LookupTests(unittest.TestCase):
    def test_old_bad_prices_cannot_silently_feed_recursive_indicators(self):
        f = fixture()
        f["bars"][-80]["close"] = None
        out = S.analyze(f, fixture("STW.AX", 0), NOW)
        self.assertIsNone(out["facts"]["rsi14"])
        self.assertIsNone(out["facts"]["macd"])
        self.assertIsNone(out["facts"]["atr14"])
        self.assertTrue(out["quality"]["calculation_warnings"])
        self.assertIsNotNone(out["facts"]["ma50"])

    def test_reference_benchmark_duplicates_are_not_silently_overwritten(self):
        b = fixture("STW.AX", 0)
        b["bars"].append({**b["bars"][-1], "close": 100.})
        out = S.analyze(fixture(), b, NOW)
        self.assertIsNone(out["facts"]["relative_stw_20d"])
        self.assertEqual(out["scores"]["state"], "paused")

    def test_future_or_unknown_observation_times_cannot_enter_current_analysis(self):
        for timestamp in (None, "2026-10-15T09:00:00+00:00", "2026-10-15T08:00:00"):
            f = fixture(); f["fetched_at"] = timestamp
            out = S.analyze(f, fixture("STW.AX", 0), NOW)
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(out["facts"])
                self.assertEqual(out["scores"]["state"], "paused")
        b = fixture("STW.AX", 0); b["fetched_at"] = "2026-10-15T09:00:00+00:00"
        out = S.analyze(fixture(), b, NOW)
        self.assertIsNone(out["facts"]["relative_stw_20d"])
        self.assertEqual(out["scores"]["state"], "paused")

    def test_ticker_boundary(self):
        for value in ("bhp", " BHP.ax ", "ASX:BHP"):
            self.assertEqual(S.normalize_ticker(value), "BHP.AX")
        self.assertEqual(S.normalize_ticker("360"), "360.AX")
        for value in ("0700.HK", "BHP.US", "../BHP", "https://example.com", "constructor", "toString", "ＢＨＰ"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                S.normalize_ticker(value)

    def test_metadata_identity(self):
        for key, value in (("symbol","CSL.AX"), ("currency","USD"), ("exchangeName","NYSE"),
                           ("exchangeTimezoneName","America/New_York")):
            x = response()
            x["chart"]["result"][0]["meta"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                S.parse_chart(json.dumps(x), "BHP.AX")

    def test_quote_tail_is_not_an_extra_day_or_added_volume(self):
        x = response()
        obj = x["chart"]["result"][0]
        obj["timestamp"].append(obj["meta"]["regularMarketTime"])
        for key, values in obj["indicators"]["quote"][0].items():
            values.append(90. if key == "volume" else values[0])
        out = S.parse_chart(json.dumps(x), "BHP.AX")
        self.assertEqual(len(out["bars"]), 1)
        self.assertEqual(out["bars"][0]["volume"], 100.)
        self.assertTrue(out["normalization_notes"])
        obj["indicators"]["quote"][0]["close"][-1] = 9.
        with self.assertRaises(ValueError):
            S.parse_chart(json.dumps(x), "BHP.AX")

    def test_intraday_null_close_keeps_quote_separate_and_completed_history_unchanged(self):
        # Synthetic reproduction of the provider's live-session shape: today's
        # 10:00 daily close is null, followed by a timestamped current quote.
        base = fixture()
        x = response()
        obj = x["chart"]["result"][0]
        rows = base["bars"] + [dict(date="2026-10-16", open=59.6, high=60.03,
                                    low=59.26, close=None, volume=5601864.)]
        obj["timestamp"] = [int(dt.datetime.combine(dt.date.fromisoformat(r["date"]),
                             dt.time(10), S.SYDNEY).timestamp()) for r in rows]
        q = {k: [r[k] for r in rows] for k in ("open", "high", "low", "close", "volume")}
        obj["indicators"]["quote"] = [q]
        quote_at = dt.datetime(2026, 10, 16, 1, 49, tzinfo=dt.timezone.utc)
        obj["meta"].update(regularMarketTime=int(quote_at.timestamp()), regularMarketPrice=59.375)
        obj["timestamp"].append(int(quote_at.timestamp()))
        for k, v in q.items():
            v.append(59.375 if k == "close" else v[-1])
        raw = json.dumps(x)
        parsed = S.parse_chart(raw, "BHP.AX")
        parsed["fetched_at"] = quote_at.isoformat()  # Explicit synthetic observation receipt.
        self.assertEqual(len(parsed["bars"]), len(rows))
        self.assertIsNone(parsed["bars"][-1]["close"])
        self.assertEqual(parsed["bars"][-1]["volume"], 5601864.)
        self.assertEqual(parsed["quote"]["price"], 59.375)
        before = S.analyze(base, fixture("STW.AX", 0), NOW)
        during = S.analyze(parsed, fixture("STW.AX", 0), quote_at)
        self.assertEqual(during["facts"], before["facts"])
        for key in ("bias", "long", "short"):
            self.assertEqual(during["assessment"][key], before["assessment"][key])
        self.assertEqual(during["quality"]["excluded_unfinished_bars"], 1)
        after = S.analyze(parsed, fixture("STW.AX", 0),
                          dt.datetime(2026, 10, 16, 8, tzinfo=dt.timezone.utc))
        self.assertIsNone(after["facts"])
        self.assertEqual(after["quality"]["state"], "unavailable")
        decimal_quote = copy.deepcopy(x)
        decimal_obj = decimal_quote["chart"]["result"][0]
        decimal_obj["meta"]["regularMarketPrice"] = 59.37
        decimal_obj["indicators"]["quote"][0]["close"][-1] = 59.369998931884766
        self.assertIsNone(S.parse_chart(json.dumps(decimal_quote), "BHP.AX")["bars"][-1]["close"])
        for k in ("open", "high", "low"):
            conflict = copy.deepcopy(x)
            conflict["chart"]["result"][0]["indicators"]["quote"][0][k][-1] += 1
            with self.subTest(conflict=k), self.assertRaises(ValueError):
                S.parse_chart(json.dumps(conflict), "BHP.AX")
        obj["meta"]["regularMarketPrice"] = 60.
        with self.assertRaises(ValueError):
            S.parse_chart(json.dumps(x), "BHP.AX")

    def test_missing_field_not_zero(self):
        x = response()
        for bad in (None,True,False,"not-a-number",float("inf")):
            x["chart"]["result"][0]["indicators"]["quote"][0]["volume"][0] = bad
            self.assertIsNone(S.parse_chart(json.dumps(x), "BHP.AX")["bars"][0]["volume"])
        del x["chart"]["result"][0]["indicators"]["quote"][0]["low"]
        with self.assertRaises(ValueError):
            S.parse_chart(json.dumps(x), "BHP.AX")

    def test_descriptive_bias_both_directions_and_unknown_execution(self):
        for direction, expected in ((1,"long_watch"), (-1,"short_watch")):
            x = S.analyze(fixture(direction=direction), fixture("STW.AX", 0), NOW)
            self.assertEqual(x["assessment"]["bias"], expected)
            for key in ("borrow_available", "cfd_available", "net_expected_return", "predictive_accuracy"):
                self.assertIsNone(x["assessment"][key])
            self.assertFalse(x["quality"]["adjustment_metadata_verified"])
        x = S.analyze(fixture(direction=-1), fixture("STW.AX", 0), NOW)
        self.assertIn("尚不能确认可执行", x["assessment"]["short"])

    def test_condition_scores_count_existing_checks_without_complement_or_probability(self):
        for direction, long_score, short_score in ((1, 4, 1), (-1, 1, 4), (0, 1, 1)):
            out = S.analyze(fixture(direction=direction), fixture("STW.AX", 0), NOW)
            s = out["scores"]
            with self.subTest(direction=direction):
                self.assertEqual(s["state"], "ready")
                self.assertEqual((s["long"]["value"], s["short"]["value"]), (long_score, short_score))
                self.assertEqual(s["max"], 4)
                self.assertEqual(s["as_of"], out["as_of"])
                self.assertEqual(len(s["long"]["checks"]), 4)
                self.assertIsNone(out["assessment"]["predictive_accuracy"])
        f = fixture(direction=1)
        for row in f["bars"]: row["volume"] = 1.
        out = S.analyze(f, fixture("STW.AX", 0), NOW)
        self.assertEqual((out["scores"]["long"]["value"], out["scores"]["short"]["value"]), (3, 0))
        self.assertEqual(out["assessment"]["bias"], "observe")

    def test_missing_or_blocked_information_pauses_both_scores_instead_of_zero(self):
        for defect in ("benchmark", "action", "fetch_error", "gap"):
            f, b = fixture(), fixture("STW.AX", 0)
            if defect == "benchmark": b = None
            if defect == "action": f["events"] = [dict(kind="dividends", date="2026-10-01", amount=.5)]
            if defect == "fetch_error": f["fetch_error"] = "合成来源失败"
            if defect == "gap": del f["bars"][-4]
            s = S.analyze(f, b, NOW)["scores"]
            with self.subTest(defect=defect):
                self.assertEqual(s["state"], "paused")
                self.assertIsNone(s["long"])
                self.assertIsNone(s["short"])
                self.assertTrue(s["blockers"])

    def test_macd_volume_and_volatility_match_independent_calculation(self):
        f = fixture()
        rng, price = random.Random(20261016), 40.
        for row in f["bars"]:
            price *= 1 + rng.uniform(-.025, .025)
            row.update(open=price-.1, high=price+.2, low=price-.2, close=price,
                       volume=float(rng.randrange(1_000_000, 3_000_000)))
        facts = S.analyze(f, fixture("STW.AX", 0), NOW)["facts"]
        cs = [r["close"] for r in f["bars"]]
        def ema(values, span):
            out = [values[0]]
            for v in values[1:]: out.append(out[-1] + 2 / (span + 1) * (v - out[-1]))
            return out
        fast, slow = ema(cs, 12), ema(cs, 26)
        line = [a-b for a, b in zip(fast, slow)][12:]  # Slow EMA emits after 13 observations.
        signal = ema(line, 9)[-1]
        self.assertAlmostEqual(facts["macd"], line[-1], places=10)
        self.assertAlmostEqual(facts["macd_signal"], signal, places=10)
        self.assertAlmostEqual(facts["macd_hist"], line[-1]-signal, places=10)
        volume = [r["volume"] for r in f["bars"]]
        self.assertAlmostEqual(facts["volume_ratio20"], volume[-1] / (sum(volume[-21:-1]) / 20))
        returns = [b/a-1 for a, b in zip(cs[-21:-1], cs[-20:])]
        mean = sum(returns)/20
        expected = math.sqrt(sum((r-mean)**2 for r in returns)/20)*math.sqrt(252)
        self.assertAlmostEqual(facts["volatility20_annualized"], expected)
        self.assertAlmostEqual(facts["atr_pct"], facts["atr14"] / facts["close"])

    def test_scores_and_new_indicators_are_prefix_invariant(self):
        f, b = fixture(), fixture("STW.AX", 0)
        for day in ("2026-09-30", "2026-10-06", "2026-10-12", "2026-10-15"):
            now = dt.datetime.fromisoformat(day + "T08:00:00+00:00")
            trimmed_f, trimmed_b = copy.deepcopy(f), copy.deepcopy(b)
            trimmed_f["bars"] = [r for r in f["bars"] if r["date"] <= day]
            trimmed_b["bars"] = [r for r in b["bars"] if r["date"] <= day]
            full_f, full_b = copy.deepcopy(f), copy.deepcopy(b)
            for snapshot in (full_f, full_b, trimmed_f, trimmed_b): snapshot["fetched_at"] = now.isoformat()
            full = S.analyze(full_f, full_b, now)
            prefix = S.analyze(trimmed_f, trimmed_b, now)
            with self.subTest(day=day):
                self.assertIsNotNone(full["facts"])
                for key in ("facts", "scores", "assessment"):
                    self.assertEqual(full[key], prefix[key])

    def test_quote_and_company_action_times_are_not_assumed_current(self):
        f, b = fixture(), fixture("STW.AX", 0)
        f["quote"] = {"price": 999., "as_of": "2026-10-15T09:00:00+00:00"}
        b["events"] = [{"kind": "dividends", "date": "2026-10-01", "amount": 1.}]
        out = S.analyze(f, b, NOW)
        self.assertIsNone(out["quote"]["price"])
        self.assertTrue(out["quality"]["quote_issue"])
        self.assertTrue(out["quality"]["benchmark_events_in_window"])
        b["events"][0]["date"] = "2026-10-16"
        self.assertFalse(S.analyze(f, b, NOW)["quality"]["benchmark_events_in_window"])

    def test_unfinished_extreme_bar_cannot_change_facts_or_conclusion(self):
        base = fixture()
        out = S.analyze(base, fixture("STW.AX",0), NOW)
        base["bars"].append(dict(date="2026-10-16",open=9000.,high=9999.,low=1.,close=9000.,volume=1e12))
        base["quote"] = dict(price=9000.,as_of="2026-10-16T00:00:00+00:00")
        new = S.analyze(base, fixture("STW.AX",0), NOW)
        self.assertEqual(new["facts"], out["facts"])
        self.assertEqual(new["assessment"], out["assessment"])
        self.assertEqual(new["quality"]["excluded_unfinished_bars"],1)
        self.assertEqual(new["chart"],out["chart"])

    def test_invalid_daily_history_hides_all_calculated_facts(self):
        for defect in ("gap", "zero_volume", "missing_volume", "negative", "bad_ohlc", "stale", "closed_day", "duplicate"):
            x = fixture()
            if defect == "gap": del x["bars"][-8]
            if defect == "zero_volume": x["bars"][-1]["volume"] = 0.
            if defect == "missing_volume": x["bars"][-2]["volume"] = None
            if defect == "negative": x["bars"][-2]["close"] = -1.
            if defect == "bad_ohlc": x["bars"][-1]["high"] = 1.
            if defect == "stale": x["bars"].pop()
            if defect == "closed_day":
                x["bars"].append({**x["bars"][-1],"date":"2026-10-11"})
                x["bars"].sort(key=lambda r:r["date"])
            if defect == "duplicate": x["bars"].append(copy.deepcopy(x["bars"][-1]))
            y = S.analyze(x, fixture("STW.AX",0), NOW)
            with self.subTest(defect=defect):
                self.assertIsNone(y["facts"])
                self.assertEqual(y["scores"]["state"], "paused")
                self.assertIsNone(y["scores"]["long"])
                self.assertEqual(y["assessment"]["bias"],"observe")
                self.assertNotEqual(y["quality"]["state"],"single_source")

    def test_missing_benchmark_and_recent_actions_pause_direction(self):
        x = S.analyze(fixture(), None, NOW)
        self.assertEqual(x["assessment"]["bias"], "observe")
        self.assertIsNone(x["facts"]["relative_stw_20d"])
        for kind in ("dividends", "splits"):
            f = fixture(direction=-1)
            f["events"]=[dict(kind=kind,date="2026-10-15",amount=.2)]
            self.assertEqual(S.analyze(f,fixture("STW.AX",0),NOW)["assessment"]["bias"],"observe")
        b=fixture("STW.AX",0)
        b["bars"][-1]["volume"]=0.
        self.assertEqual(S.analyze(fixture(),b,NOW)["assessment"]["bias"],"observe")

    def test_liquidity_floor(self):
        f = fixture(direction=-1)
        for bar in f["bars"]: bar["volume"] = 1.
        self.assertEqual(S.analyze(f,fixture("STW.AX",0),NOW)["assessment"]["bias"],"observe")

    def test_action_inside_return_window_not_just_last_week(self):
        f=fixture(direction=-1)
        f["events"]=[dict(kind="dividends",date="2026-10-01",amount=1.)]
        out=S.analyze(f,fixture("STW.AX",0),NOW)
        self.assertEqual(out["assessment"]["bias"],"observe")
        self.assertTrue(out["recent_events"])

    def test_reviewed_early_close_and_expiration(self):
        f=fixture(end="2026-12-24")
        b=fixture("STW.AX",0,end="2026-12-24")
        after=dt.datetime(2026,12,24,8,tzinfo=dt.timezone.utc)
        out=S.analyze(f,b,after)
        self.assertEqual(out["as_of"],"2026-12-24")
        self.assertEqual(out["quality"]["state"],"single_source")
        self.assertTrue(out["valid_until"].startswith("2026-12-29T"))
        before=S.analyze(f,b,dt.datetime(2026,12,24,3,tzinfo=dt.timezone.utc))
        self.assertEqual(before["as_of"],"2026-12-23")
        with self.assertRaises(ValueError):
            S.analyze(f,b,dt.datetime(2028,1,3,8,tzinfo=dt.timezone.utc))

    def test_price_measurements_have_independent_hand_calculation(self):
        f = fixture()
        out = S.analyze(f,fixture("STW.AX",0),NOW)["facts"]
        prices = [r["close"] for r in f["bars"]]
        self.assertAlmostEqual(out["ma20"],sum(prices[-20:])/20)
        self.assertAlmostEqual(out["ma50"],sum(prices[-50:])/50)
        self.assertAlmostEqual(out["relative_stw_20d"],prices[-1]/prices[-21]-1)
        self.assertEqual(out["rsi14"],100.)
        self.assertAlmostEqual(out["atr14"],.2)

    def test_raw_snapshots_cache_hash_revisions_and_network_failure(self):
        class Reply:
            def __init__(self, raw): self.raw=raw
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return self.raw
        raw = json.dumps(response()).encode()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(S.urllib.request,"urlopen",return_value=Reply(raw)) as network:
                a=S.fetch_snapshot("BHP",NOW,tmp)
                b=S.fetch_snapshot("BHP",NOW+dt.timedelta(seconds=30),tmp)
                self.assertEqual(network.call_count,1)
                self.assertTrue(b["cached"])
                self.assertEqual(a["sha256"],hashlib.sha256(raw).hexdigest())
            with patch.object(S.urllib.request,"urlopen",side_effect=OSError("offline")):
                b=S.fetch_snapshot("BHP",NOW+dt.timedelta(minutes=2),tmp)
                self.assertIsNotNone(b["fetch_error"])
            for name in (a["sha256"]+".json",a["sha256"]+".first.json"):
                self.assertTrue(os.path.exists(os.path.join(tmp,"ticker-BHP.AX",name)))

    def test_windows_device_name_ticker_has_safe_cache_path(self):
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args):
                x=response();x["chart"]["result"][0]["meta"]["symbol"]="PRN.AX"
                return json.dumps(x).encode()
        with tempfile.TemporaryDirectory() as tmp, patch.object(S.urllib.request,"urlopen",return_value=Reply()):
            S.fetch_snapshot("PRN",NOW,tmp)
            self.assertTrue(os.path.exists(os.path.join(tmp,"ticker-PRN.AX","latest.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp,"PRN.AX")))

    def test_daily_site_and_offline_export_use_caches_without_training(self):
        from asxquant import datafeed as D, export as EX, publish as PB, shortradar as SR
        stocks=[fixture(),fixture("STW.AX",0)]
        panels={k:pd.DataFrame({s["ticker"]:[r[k] for r in s["bars"]] for s in stocks},
                             index=pd.to_datetime([r["date"] for r in stocks[0]["bars"]]))
                for k in ("open","high","low","close","volume")}
        with tempfile.TemporaryDirectory() as tmp:
            raw_path=os.path.join(tmp,"synthetic-cache.pkl")
            with open(raw_path,"wb") as f: f.write(b"synthetic-cache")
            with patch.object(D,"_load",return_value=panels), patch.object(D,"cache_timestamp",return_value=NOW.timestamp()), \
                 patch.object(D,"_cache_path",return_value=raw_path), \
                 patch.object(SR,"universe",return_value={s["ticker"]:(s["name"],"其他") for s in stocks}), \
                 patch.object(S,"fetch_snapshot",side_effect=AssertionError("must not download")):
                count=S.build_site(tmp,now=NOW,log=lambda _:None)
                self.assertEqual(count,2)
                with open(os.path.join(tmp,"stocks","ticker-BHP.AX.json"),encoding="utf-8") as f:
                    row=json.load(f)
                self.assertEqual(row["mode"],"daily_snapshot")
                self.assertEqual(row["source"]["snapshot_kind"],"existing_cache_not_raw_http")
                self.assertFalse(row["quality"]["adjustment_metadata_verified"])
                self.assertEqual(row["scores"]["state"], "ready")
                self.assertEqual(row["scores"]["long"]["value"], 4)
            with patch.object(S,"cached_analyses",return_value={"BHP.AX":row}), \
                 patch.object(PB,"build_short",return_value=None):
                PB.build({"as_of":"2026-10-15"},out_dir=os.path.join(tmp,"site"),log=lambda _:None)
                self.assertTrue(os.path.exists(os.path.join(tmp,"site","stock.js")))
                self.assertTrue(os.path.exists(os.path.join(tmp,"site","stocks","ticker-BHP.AX.json")))
                row["name"]="</script><script>throw 1</script>"
                path,_=EX.export({"as_of":"2026-10-15"},out_dir=tmp)
                with open(path,encoding="utf-8") as f: html=f.read()
                self.assertIn('window.__STOCKS__=',html)
                self.assertIn('id="stockLookup"',html)
                self.assertNotIn('</script><script>throw 1</script>',html)

    def test_cli_one_ticker_does_not_start_global_training(self):
        import run
        from asxquant import engine
        row=S.analyze(fixture(),fixture("STW.AX",0),NOW)
        output=io.StringIO()
        with patch("sys.argv",["run.py","--stock","BHP"]), patch.object(S,"lookup",return_value=row), \
             patch.object(engine,"run",side_effect=AssertionError("must not train")), redirect_stdout(output):
            self.assertEqual(run.main(),0)
        self.assertIn("买入：",output.getvalue())
        self.assertIn("做空：",output.getvalue())
        self.assertIn("多头条件分 4/4 | 空头条件分 1/4",output.getvalue())
        self.assertIn("MACD",output.getvalue())

    def test_http_query_contract_and_no_global_refresh_required(self):
        from asxquant.server import Handler
        srv = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        worker = threading.Thread(target=srv.serve_forever,daemon=True)
        worker.start()
        base="http://127.0.0.1:%s" % srv.server_port
        try:
            with patch.object(S,"lookup",return_value={"ticker":"BHP.AX"}) as lookup:
                with urllib.request.urlopen(base+"/api/stock?ticker=BHP") as r:
                    self.assertEqual(json.load(r)["ticker"],"BHP.AX")
                    self.assertEqual(r.headers["Cache-Control"],"no-store")
                for query in ("", "?ticker=BHP&ticker=CSL", "?ticker=BHP&market=US"):
                    with self.subTest(query=query), self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(base+"/api/stock"+query)
                    self.assertEqual(e.exception.code,400)
                self.assertEqual(lookup.call_count,1)
        finally:
            srv.shutdown(); srv.server_close(); worker.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
