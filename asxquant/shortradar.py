# -*- coding: utf-8 -*-
"""把 ChatGPT 写的「AI Short Radar」并进澳股雷达：用真实行情跑它自己的数据检查与评分。

`short_radar/` 是原样搬进来的 AI Short Radar 0.12.0 计算核心（评分公式与权重冻结，
一个数字都没改）。这里只做一件事：把本项目已经从 Yahoo 拉好的日线，转成它导入器
认可的 CSV，再原样走它的导入检查（日历、漏日、拆股版本、收盘口径）和 technical-v1
评分，最后把三个持有期的结果写成静态 JSON，给手机页面读。

为什么走 CSV 而不是直接构造对象：它的导入器本身就是质量闸门（OHLC 边界、休市日
出现行情、未收盘、单位……）。绕过它就等于把那些检查全扔掉。被拒的行不会被悄悄
丢掉——数量和前几条原因写进 import.json，页面上照实显示。

关于诚实标注（见 web/short/evidence.json 与 tools/eval_short_radar.py）：
这个评分在 152 只澳股 12 年数据上实测**没有预测力**（Newey-West t 约 0.7-1.0，
方向还略反）。页面顶部必须一直写明这一点，它只能描述「最近走势弱」。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
import time

import pandas as pd

from .config import SECTORS as AQ_SECTORS
from .config import all_stock_tickers

SOURCE = "yahoo-finance-daily"
BENCH = "STW.AX"          # ChatGPT 版规定的澳股对照基金；本项目主行情表里没有它
# 用户自选、但不在澳股雷达股票池里的名字。单独抓，不碰主行情表——主表一改，
# 澳股雷达的特征指纹就变，会触发整套模型重训。
EXTRA = {
    "GYG.AX": ("Guzman y Gomez", "餐饮"),
    "360.AX": ("Life360", "信息科技"),
    "TYR.AX": ("Tyro Payments", "金融/银行"),
    "AMA.AX": ("AMA Group", "非必需消费"),
}
# 它的日历只核查过 2025-2027 年；更早的行会被判「交易时间还要核实」，所以只送这之后的。
FIRST_DAY = dt.date(2025, 1, 1)
SESSIONS = 270            # 至少 252 个完整交易日才排名，留一点余量
CHUNK = 40                # 每批导入的股票数；它的导入器单个文件上限 5 万行
HORIZONS = (5, 10, 20)

# 关注的六个行业优先，其余沿用澳股雷达的 GICS 分组。
SECTOR_OVERRIDE = {"QBE.AX": "保险", "SUN.AX": "保险", "IAG.AX": "保险", "MPL.AX": "保险",
                   "DMP.AX": "餐饮"}
GROUP_SECTOR = {"healthcare": "医药", "realestate": "REIT"}

NAMES = {
    "BHP.AX": "BHP Group", "RIO.AX": "Rio Tinto", "FMG.AX": "Fortescue", "S32.AX": "South32",
    "MIN.AX": "Mineral Resources", "PLS.AX": "Pilbara Minerals", "IGO.AX": "IGO",
    "LYC.AX": "Lynas Rare Earths", "NST.AX": "Northern Star", "EVN.AX": "Evolution Mining",
    "ORI.AX": "Orica", "JHX.AX": "James Hardie", "BSL.AX": "BlueScope Steel",
    "SFR.AX": "Sandfire Resources", "LTR.AX": "Liontown Resources", "SGM.AX": "Sims",
    "RRL.AX": "Regis Resources", "WAF.AX": "West African Resources", "AMC.AX": "Amcor",
    "NIC.AX": "Nickel Industries", "ILU.AX": "Iluka Resources", "GMD.AX": "Genesis Minerals",
    "CMM.AX": "Capricorn Metals", "RMS.AX": "Ramelius Resources", "WGX.AX": "Westgold Resources",
    "CHN.AX": "Chalice Mining",
    "CBA.AX": "Commonwealth Bank", "NAB.AX": "National Australia Bank", "WBC.AX": "Westpac",
    "ANZ.AX": "ANZ Group", "MQG.AX": "Macquarie Group", "QBE.AX": "QBE Insurance",
    "SUN.AX": "Suncorp", "IAG.AX": "Insurance Australia Group", "BEN.AX": "Bendigo and Adelaide Bank",
    "BOQ.AX": "Bank of Queensland", "ASX.AX": "ASX Ltd", "MPL.AX": "Medibank", "AMP.AX": "AMP",
    "CGF.AX": "Challenger", "NWL.AX": "Netwealth", "HUB.AX": "HUB24", "PNI.AX": "Pinnacle Investment",
    "GQG.AX": "GQG Partners", "HLI.AX": "Helia Group", "MFG.AX": "Magellan Financial",
    "PPT.AX": "Perpetual",
    "CSL.AX": "CSL", "RMD.AX": "ResMed", "COH.AX": "Cochlear", "SHL.AX": "Sonic Healthcare",
    "RHC.AX": "Ramsay Health Care", "PME.AX": "Pro Medicus", "TLX.AX": "Telix Pharmaceuticals",
    "NEU.AX": "Neuren Pharmaceuticals", "CU6.AX": "Clarity Pharmaceuticals",
    "SIG.AX": "Sigma Healthcare", "EBO.AX": "EBOS Group", "ANN.AX": "Ansell", "MSB.AX": "Mesoblast",
    "NAN.AX": "Nanosonics", "IMU.AX": "Imugene",
    "WES.AX": "Wesfarmers", "ALL.AX": "Aristocrat Leisure", "JBH.AX": "JB Hi-Fi",
    "HVN.AX": "Harvey Norman", "DMP.AX": "Domino's Pizza Enterprises", "LOV.AX": "Lovisa",
    "SUL.AX": "Super Retail Group", "PMV.AX": "Premier Investments", "WEB.AX": "Web Travel Group",
    "FLT.AX": "Flight Centre", "TAH.AX": "Tabcorp", "ARB.AX": "ARB Corporation",
    "BRG.AX": "Breville Group", "AX1.AX": "Accent Group", "NCK.AX": "Nick Scali",
    "TPW.AX": "Temple & Webster", "CTD.AX": "Corporate Travel Management",
    "IDX.AX": "Integral Diagnostics", "EVT.AX": "EVT Ltd", "SGR.AX": "The Star Entertainment",
    "LNW.AX": "Light & Wonder",
    "WOW.AX": "Woolworths", "COL.AX": "Coles", "TWE.AX": "Treasury Wine Estates",
    "A2M.AX": "a2 Milk", "MTS.AX": "Metcash", "ELD.AX": "Elders", "GNC.AX": "GrainCorp",
    "BGA.AX": "Bega Cheese",
    "WDS.AX": "Woodside Energy", "STO.AX": "Santos", "WHC.AX": "Whitehaven Coal",
    "YAL.AX": "Yancoal", "NHC.AX": "New Hope", "BPT.AX": "Beach Energy", "KAR.AX": "Karoon Energy",
    "VEA.AX": "Viva Energy", "PDN.AX": "Paladin Energy", "BOE.AX": "Boss Energy",
    "DYL.AX": "Deep Yellow",
    "TCL.AX": "Transurban", "BXB.AX": "Brambles", "QAN.AX": "Qantas", "IPH.AX": "IPH",
    "REH.AX": "Reece", "ALQ.AX": "ALS", "DOW.AX": "Downer EDI", "SGH.AX": "SGH",
    "MND.AX": "Monadelphous", "QUB.AX": "Qube Holdings", "CWY.AX": "Cleanaway",
    "ALX.AX": "Atlas Arteria", "NWH.AX": "NRW Holdings", "AIA.AX": "Auckland Airport",
    "WTC.AX": "WiseTech Global", "XRO.AX": "Xero", "TNE.AX": "TechnologyOne", "NXT.AX": "NextDC",
    "MP1.AX": "Megaport", "APX.AX": "Appen", "DTL.AX": "Data#3", "IRE.AX": "IRESS",
    "CPU.AX": "Computershare", "SDR.AX": "SiteMinder", "HSN.AX": "Hansen Technologies",
    "TLS.AX": "Telstra", "TPG.AX": "TPG Telecom", "CAR.AX": "CAR Group", "REA.AX": "REA Group",
    "SEK.AX": "Seek", "NEC.AX": "Nine Entertainment", "SXL.AX": "Southern Cross Media",
    "NWS.AX": "News Corp",
    "AGL.AX": "AGL Energy", "APA.AX": "APA Group", "ORG.AX": "Origin Energy", "MCY.AX": "Mercury NZ",
    "IFT.AX": "Infratil",
    "GMG.AX": "Goodman Group", "SCG.AX": "Scentre Group", "SGP.AX": "Stockland", "MGR.AX": "Mirvac",
    "DXS.AX": "Dexus", "VCX.AX": "Vicinity Centres", "CHC.AX": "Charter Hall", "GPT.AX": "GPT Group",
    "LLC.AX": "Lendlease", "CQR.AX": "Charter Hall Retail REIT", "HMC.AX": "HMC Capital",
    "ARF.AX": "Arena REIT", "CIP.AX": "Centuria Industrial REIT",
    BENCH: "SPDR S&P/ASX 200 Fund (STW)",
}


def universe():
    """{ticker: (name, sector)} — 澳股雷达的全部股票 + 自选 + 对照基金。"""
    from short_radar.contracts import SECTORS
    out = {}
    for key, grp in AQ_SECTORS.items():
        for t in grp["tickers"]:
            sector = SECTOR_OVERRIDE.get(t) or GROUP_SECTOR.get(key) or grp["name"]
            out[t] = (NAMES.get(t, t.replace(".AX", "")), sector if sector in SECTORS else "其他")
    out.update(EXTRA)
    out[BENCH] = (NAMES[BENCH], "基准")
    return out


# --------------------------------------------------------------------------
# 数据：复用澳股雷达的主行情；自选和 STW 单独抓、单独缓存
# --------------------------------------------------------------------------
def fetch_extra(force=False, log=print):
    from . import datafeed as D
    if not force:
        cached = D._load("shortradar_extra.pkl", max_age_sec=3600 * 6)
        if cached is not None:
            return cached
    import yfinance as yf
    tickers = sorted(list(EXTRA) + [BENCH])
    log("AI Short Radar: 从 Yahoo 下载 %d 个自选/对照标的 ..." % len(tickers))
    raw = yf.download(tickers, period="2y", interval="1d", progress=False,
                      auto_adjust=False, threads=True, group_by="column")
    if raw is None or raw.empty:   # 抓到空数据时不覆盖上一份能用的缓存
        raise RuntimeError("Yahoo 自选/STW 返回空数据，保留原缓存")
    out = {}
    for field, key in [("Close", "close"), ("Open", "open"), ("High", "high"),
                       ("Low", "low"), ("Volume", "volume")]:
        df = raw[field].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[key] = df.sort_index()
    D._save("shortradar_extra.pkl", out)
    return out


def _fetched_at(cache_name):
    """缓存文件写入时刻 = 这批价格被我们看到的时刻（observation 时钟）。"""
    from . import datafeed as D
    ts = D.cache_timestamp(cache_name)   # 记录下来的那一刻，不用「现在减年龄」事后推算
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


# --------------------------------------------------------------------------
# 转成它导入器的 CSV 行
# --------------------------------------------------------------------------
def _fmt(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    return "%.6f" % x if x == x else ""   # NaN 留空 -> 导入器会拒绝这一行并记原因


def to_rows(frames, tickers, meta, fetched_at, sessions=SESSIONS):
    """frames: {'close','open','high','low','volume'} 宽表。只送 FIRST_DAY 之后最近 `sessions` 个交易日。

    一行价格只要收盘价是 NaN 就不送（那天它没有成交记录）——漏掉的日子由它自己的
    「漏了应有的交易日」检查去发现，而不是我们在这里补零或前值填充。
    """
    from short_radar import asx_calendar as AC
    from short_radar.models import stamp
    from short_radar.calendars import session, CalendarUnavailable

    stamp_at = stamp(fetched_at)
    adj_id = "yahoo-split-adj-" + fetched_at.strftime("%Y%m%dT%H%MZ")
    sydney = AC.ZoneInfo("Australia/Sydney")
    close = frames["close"]
    rows = []
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        s = s[s.index.date >= FIRST_DAY].tail(sessions)
        name, sector = meta[t]
        for ts in s.index:
            d = ts.date()
            try:
                sess = session("AU", d)
            except CalendarUnavailable:
                continue
            if sess:
                op, cl = sess["open"], sess["close"]
            else:   # 休市日却有行情：照送，让导入器按「不是交易日」拒绝并记录
                op = dt.datetime.combine(d, dt.time(9, 59), sydney)
                cl = dt.datetime.combine(d, dt.time(16, 42), sydney)
            vol = frames["volume"][t].get(ts)
            rows.append({
                "market": "AU", "symbol": t, "name": name, "sector": sector, "currency": "AUD",
                "session_date": d.isoformat(),
                "open": _fmt(frames["open"][t].get(ts)), "high": _fmt(frames["high"][t].get(ts)),
                "low": _fmt(frames["low"][t].get(ts)), "close": _fmt(s[ts]),
                "volume": "" if vol is None or vol != vol else str(int(round(float(vol)))),
                "volume_unit": "shares",
                "session_open_utc": stamp(op), "session_close_utc": stamp(cl),
                "available_at": stamp_at, "availability_kind": "observation",
                "source": SOURCE, "basis": "split", "close_definition": "asx_eod",
                "calendar_source": AC.CALENDAR_VERSION, "calendar_verified": "true",
                "demo": "false", "incomplete": "false",
                "adjustment_id": adj_id, "volume_basis": "split",
            })
    return rows


# --------------------------------------------------------------------------
# 跑它的导入 + 评分
# --------------------------------------------------------------------------
def build(px=None, extra=None, now=None, log=print, px_fetched_at=None, extra_fetched_at=None):
    """返回 {'radar': {h: 它的 /v1/radar 结果}, 'import': 导入摘要}。不联网（数据由调用方给）。"""
    from short_radar.contracts import ACTIVE_MARKETS
    from short_radar.ingest import preview_csv, to_csv
    from short_radar.store import Store
    from short_radar.analysis import radar

    now = now or dt.datetime.now(dt.timezone.utc)
    uni = universe()
    jobs = []
    if px is not None and px_fetched_at is not None:
        jobs.append((px, [t for t in all_stock_tickers() if t in uni], px_fetched_at))
    if extra is not None and extra_fetched_at is not None:
        jobs.append((extra, sorted(list(EXTRA) + [BENCH]), extra_fetched_at))

    tmp = tempfile.mkdtemp(prefix="shortradar-")
    t0 = time.time()
    summary = {"source": SOURCE, "total": 0, "accepted": 0, "rejected": 0,
               "states": {}, "issue_examples": [], "fetched_at": {}}
    try:
        store = Store(os.path.join(tmp, "radar.sqlite3"))
        for frames, tickers, fetched in jobs:
            summary["fetched_at"][SOURCE if frames is px else "extra"] = fetched.isoformat()
            for i in range(0, len(tickers), CHUNK):
                rows = to_rows(frames, tickers[i:i + CHUNK], uni, fetched)
                if not rows:
                    continue
                pv = preview_csv(to_csv(rows), now,
                                 calendar_overrides=store.calendar_overrides(now, ACTIVE_MARKETS),
                                 allowed_markets=ACTIVE_MARKETS)
                s = pv.summary()
                summary["total"] += s["total"]
                summary["accepted"] += s["accepted"]
                summary["rejected"] += s["rejected"]
                for k, v in s["states"].items():
                    summary["states"][k] = summary["states"].get(k, 0) + v
                for iss in s["issues"]:
                    if len(summary["issue_examples"]) < 20:
                        summary["issue_examples"].append(iss["reason"])
                if pv.rows:
                    # 被拒的行已经记进 summary；其余照常入库，不让一行坏数据挡住整批。
                    store.commit_preview(pv, now, allow_rejections=True)
        out = {str(h): radar(store, "imported", now, h) for h in HORIZONS}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    summary["seconds"] = round(time.time() - t0, 1)
    r10 = out["10"]
    cov = r10["coverage"]["AU"]
    log("AI Short Radar: 导入 %d 行（拒 %d），%d 只股票，%d 只可排名，%.1fs" % (
        summary["accepted"], summary["rejected"], cov["observed_instruments"],
        cov["technical_eligible"], summary["seconds"]))
    return {"radar": out, "import": summary}


def build_from_cache(log=print, fetch_missing=True):
    """生产路径：读澳股雷达刚抓好的主行情缓存 + 自选/STW 缓存。"""
    from . import datafeed as D
    px = D.fetch_prices(log=log)
    try:
        extra = fetch_extra(log=log) if fetch_missing else D._load("shortradar_extra.pkl")
    except Exception as e:  # 自选抓不到时主股票池照常出结果，只是没有市场对照 -> 全部不排名
        log("AI Short Radar: 自选/STW 下载失败 (%s)，本次没有市场对照" % e)
        extra = None
    return build(px, extra, log=log, px_fetched_at=_fetched_at("prices.pkl"),
                 extra_fetched_at=_fetched_at("shortradar_extra.pkl") if extra is not None else None)


# --------------------------------------------------------------------------
# 前端契约：plain.js 里的枚举由后端生成，两边不会各改各的
# --------------------------------------------------------------------------
START = "// BEGIN GENERATED BACKEND CONTRACT\n"
END = "// END GENERATED BACKEND CONTRACT\n"


def contract_block():
    from short_radar.contracts import frontend_contract
    payload = json.dumps(frontend_contract(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (START + "const contractData=" + payload + ";\n" +
            "function freezeContract(v){return Array.isArray(v)?Object.freeze(v.map(freezeContract)):\n" +
            " v&&typeof v==='object'?Object.freeze(Object.assign(Object.create(null),Object.fromEntries(Object.entries(v).map(([k,x])=>[k,freezeContract(x)])))):v;}\n" +
            "const contract=freezeContract(contractData);\n" + END)


def sync_contract(path):
    """把 plain.js 里的生成块替换成当前后端契约。返回是否有改动。"""
    import re
    with open(path, encoding="utf-8") as f:
        text = f.read()
    block = contract_block()
    m = re.search(re.escape(START) + r".*?" + re.escape(END), text, re.S)
    new = text[:m.start()] + block + text[m.end():] if m else text
    if new != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    return new != text


def write_site(out_dir, snap):
    """site/short/data/ 下写三个持有期的结果 + 导入摘要。"""
    d = os.path.join(out_dir, "data")
    os.makedirs(d, exist_ok=True)
    for h, data in snap["radar"].items():
        with open(os.path.join(d, "radar-%s.json" % h), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with open(os.path.join(d, "import.json"), "w", encoding="utf-8") as f:
        json.dump(snap["import"], f, ensure_ascii=False, separators=(",", ":"))


def record_and_score(out_dir, snap, log=print):
    """前瞻记录：把今天 10 日口径的排名写进存档，再给存档里所有过去的记录打分，
    结果写成 data/forward.json 给页面读。

    存档随每日流程提交进仓库，记录时刻早于结果——这是这个分数唯一不能被事后修饰的证据。
    （ChatGPT 2026-10-15 那轮提出「前瞻反馈」的想法；它的实现需要用户提供授权原始行情
    才算得出结果，而用户没有，所以这里改用澳股雷达本来就在抓的价格自动打分。）
    """
    from . import archive as AR
    from . import datafeed as D
    from . import forward as FW
    rec = AR.record_shortradar(snap["radar"]["10"])
    px = D.fetch_prices(log=lambda m: None)
    uni = [t for t in all_stock_tickers() if t in px["close"].columns]
    out = FW.from_shortradar_archive(px, uni)
    out["recorded_today"] = rec
    d = os.path.join(out_dir, "data")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "forward.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log("看空雷达前瞻记录: 存档 %s 天，今天 %s" % (out["days_recorded"], rec))
    return out
