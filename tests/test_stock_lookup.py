# -*- coding: utf-8 -*-
"""Offline synthetic tests for ticker -> price facts -> user conclusion."""
import copy
import datetime as dt
import hashlib
import io
import json
import os
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
                snapshot_kind="synthetic", fetch_error=None)


def response():
    return {"chart": {"result": [{"meta": {"symbol":"BHP.AX", "currency":"AUD", "exchangeName":"ASX",
            "exchangeTimezoneName":"Australia/Sydney", "instrumentType":"EQUITY", "regularMarketTime":1792041640, "regularMarketPrice":10.},
            "timestamp":[1792018800], "indicators":{"quote":[{"open":[10.], "high":[11.], "low":[9.],
            "close":[10.], "volume":[100.]}]}}]}}


class LookupTests(unittest.TestCase):
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
