# -*- coding: utf-8 -*-
"""Universe, GICS sector map and model parameters for the ASX market radar."""

BENCHMARK = "^AXJO"          # S&P/ASX 200

# Macro / cross-asset context. Australia is a commodity-levered, rate-sensitive market,
# so the cross-asset block carries more weight here than it would elsewhere.
MACRO = {
    "^AXJO":    "标普/ASX 200",
    "^AXKO":    "ASX 300",
    "^AXJR":    "ASX 小型股",
    "^AXVI":    "ASX 200 波动率指数",
    "AUDUSD=X": "澳元兑美元",
    "^VIX":     "美股VIX",
    "^GSPC":    "标普500",
    "^TNX":     "美债10年收益率",
    "GC=F":     "黄金",
    "HG=F":     "铜",
    "CL=F":     "原油WTI",
    "000001.SS": "上证综指",
    "^HSI":     "恒生指数",
}

# Official S&P/ASX 200 GICS sector indices (Yahoo tickers), used as a cross-check
# against the bottom-up baskets built from constituents.
SECTOR_INDEX = {
    "materials": "^AXMJ", "financials": "^AXFJ", "energy": "^AXEJ",
    "healthcare": "^AXHJ", "industrials": "^AXNJ", "discretionary": "^AXDJ",
    "staples": "^AXSJ", "infotech": "^AXIJ", "telecom": "^AXTJ",
    "utilities": "^AXUJ", "realestate": "^AXPJ",
}

SECTORS = {
    "materials": {
        "name": "原材料/矿业", "en": "Materials",
        "tickers": ["BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "MIN.AX", "PLS.AX", "IGO.AX",
                    "LYC.AX", "NST.AX", "EVN.AX", "ORI.AX", "JHX.AX", "BSL.AX", "SFR.AX",
                    "LTR.AX", "SGM.AX", "RRL.AX", "WAF.AX", "AMC.AX", "NIC.AX", "ILU.AX",
                    "GMD.AX", "CMM.AX", "RMS.AX", "WGX.AX", "CHN.AX"],
    },
    "financials": {
        "name": "金融/银行", "en": "Financials",
        "tickers": ["CBA.AX", "NAB.AX", "WBC.AX", "ANZ.AX", "MQG.AX", "QBE.AX", "SUN.AX",
                    "IAG.AX", "BEN.AX", "BOQ.AX", "ASX.AX", "MPL.AX", "AMP.AX", "CGF.AX",
                    "NWL.AX", "HUB.AX", "PNI.AX", "GQG.AX", "HLI.AX", "MFG.AX", "PPT.AX"],
    },
    "healthcare": {
        "name": "医疗保健", "en": "Health Care",
        "tickers": ["CSL.AX", "RMD.AX", "COH.AX", "SHL.AX", "RHC.AX", "PME.AX", "TLX.AX",
                    "NEU.AX", "CU6.AX", "SIG.AX", "EBO.AX", "ANN.AX", "MSB.AX", "NAN.AX",
                    "IMU.AX"],
    },
    "discretionary": {
        "name": "非必需消费", "en": "Consumer Discretionary",
        "tickers": ["WES.AX", "ALL.AX", "JBH.AX", "HVN.AX", "DMP.AX", "LOV.AX", "SUL.AX",
                    "PMV.AX", "WEB.AX", "FLT.AX", "TAH.AX", "ARB.AX", "BRG.AX", "AX1.AX",
                    "NCK.AX", "TPW.AX", "CTD.AX", "IDX.AX", "EVT.AX", "SGR.AX", "LNW.AX"],
    },
    "staples": {
        "name": "必需消费", "en": "Consumer Staples",
        "tickers": ["WOW.AX", "COL.AX", "TWE.AX", "A2M.AX", "MTS.AX", "ELD.AX", "GNC.AX",
                    "BGA.AX"],
    },
    "energy": {
        "name": "能源", "en": "Energy",
        "tickers": ["WDS.AX", "STO.AX", "WHC.AX", "YAL.AX", "NHC.AX", "BPT.AX", "KAR.AX",
                    "VEA.AX", "PDN.AX", "BOE.AX", "DYL.AX"],
    },
    "industrials": {
        "name": "工业", "en": "Industrials",
        "tickers": ["TCL.AX", "BXB.AX", "QAN.AX", "IPH.AX", "REH.AX", "ALQ.AX", "DOW.AX",
                    "SGH.AX", "MND.AX", "QUB.AX", "CWY.AX", "ALX.AX", "NWH.AX", "AIA.AX"],
    },
    "infotech": {
        "name": "信息科技", "en": "Information Technology",
        "tickers": ["WTC.AX", "XRO.AX", "TNE.AX", "NXT.AX", "MP1.AX", "APX.AX", "DTL.AX",
                    "IRE.AX", "CPU.AX", "SDR.AX", "HSN.AX"],
    },
    "telecom": {
        "name": "通信服务", "en": "Communication Services",
        "tickers": ["TLS.AX", "TPG.AX", "CAR.AX", "REA.AX", "SEK.AX", "NEC.AX", "SXL.AX",
                    "NWS.AX"],
    },
    "utilities": {
        "name": "公用事业", "en": "Utilities",
        "tickers": ["AGL.AX", "APA.AX", "ORG.AX", "MCY.AX", "IFT.AX"],
    },
    "realestate": {
        "name": "房地产/REITs", "en": "Real Estate",
        "tickers": ["GMG.AX", "SCG.AX", "SGP.AX", "MGR.AX", "DXS.AX", "VCX.AX", "CHC.AX",
                    "GPT.AX", "LLC.AX", "CQR.AX", "HMC.AX", "ARF.AX", "CIP.AX"],
    },
}


def all_stock_tickers():
    out = []
    for s in SECTORS.values():
        out.extend(s["tickers"])
    return sorted(set(out))


def ticker_to_sector():
    m = {}
    for k, s in SECTORS.items():
        for t in s["tickers"]:
            m[t] = k
    return m


def asx_code(ticker):
    """BHP.AX -> BHP  (ASIC files key on the bare ASX code)."""
    return ticker.replace(".AX", "")


# ---------------- Model parameters ----------------
HORIZONS = [1, 5, 10, 20]
HISTORY_PERIOD = "12y"
MIN_TRAIN = 750
EMBARGO_EXTRA = 5
SHORT_YEARS = 5              # how many ASIC year-to-date files to pull
CACHE_DIR = "cache"

# 交易摩擦（横截面验证与策略曲线共用）
COST_BPS = 15.0              # 单边冲击+佣金，ASX 中大盘的保守值
BORROW_BPS_PA = 300.0        # 空头借券费年化，高做空拥挤名字实际常在 200-800bps

# Stamped into models.pkl. The walk-forward cache reuses predictions computed by an
# EARLIER version of the modelling code, so changing that code without bumping this
# would silently mix old and new predictions in one series forever. Bump on any change
# to model.py's fitting, calibration or ensembling.
# v4 (2026-08-25): calibration rejects a negative Platt slope and now leaves such a
# block EMPTY (v3 briefly wrote the base rate there, which drifted and became a signal
# of its own). Cached `cal` blocks from v2/v3 are stale and must be recomputed.
# v5 (2026-08-25): three volatility LEVEL features added, so the feature matrix
# itself changed -- every cached walk-forward prediction is stale.
# v6 (升级第一步，纯 bug 修复):
#     · 跨市场序列改为**交易时段对齐**（修掉时区前视偏差）——8 个 xa_/vol_vix 因子
#       的数值全部改变，这是必须 bump 的主因；
#     · RSI/MFI 退化情形修正，凡是用到 RSI 的因子数值都变了；
#     · 广度指标加最小横截面闸门、avg_correlation 改按代表性取样。
#     从本版起，缓存还会额外用 features.feature_fingerprint() 校验，
#     改 config 里的股票池也能自动失效。
#     ⚠️ 建模层（model.py 的拟合/校准/集成）本步**未改动**，是刻意的：
#        这样 AUC 的变化只能归因于泄漏被堵住，而不是别的东西。
MODEL_VERSION = "v6"
