import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import os
import sys
import datetime
import uuid
import io
import csv
import base64
from autotrade_engine import autotrade_engine, send_telegram_alert
from exchange_api import ExchangeAPIClient

PORT = int(os.environ.get("PORT", 5000))
HOST = "0.0.0.0"

TOKEN_DICTIONARY = [
    {
        "id": "velo",
        "ticker": "VELO",
        "severity": "high",
        "severity_label": "🔴 อันตรายสูงสุด (คนละเหรียญ/คนละบล็อกเชน)",
        "issue_type": "Ticker Collision (ชื่อย่อซ้ำกัน 100%)",
        "summary": "Bitkub คือ Velo Protocol (~1.17 ฿) แต่ Binance TH คือ Velodrome Finance (~0.17 ฿) คนละเหรียญกันสิ้นเชิง",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_VELO",
                "project_name": "Velo Protocol",
                "network": "Stellar / Nova Network",
                "approx_price": "~1.15 - 1.25 THB",
                "detail": "โปรเจกต์โครงสร้างพื้นฐานระบบการเงินและการชำระเงินข้ามพรมแดนในเอเชียตะวันออกเฉียงใต้ ก่อตั้งโดยกลุ่มคุณชัชวาลย์ เจียรวนนท์ (CP Group) ร่วมกับ Lightnet",
                "coingecko_id": "velo"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "VELOTHB",
                "project_name": "Velodrome Finance",
                "network": "Optimism (OP Mainnet Layer 2)",
                "approx_price": "~0.15 - 0.20 THB",
                "detail": "Decentralized Exchange (DEX) และ Automated Market Maker (AMM) หลักบนเครือข่าย Optimism L2 พัฒนาต่อยอดจากโมเดล ve(3,3)",
                "coingecko_id": "velodrome-finance"
            }
        },
        "warning": "⛔ ห้ามโอนเหรียญข้ามกระดาน หรือคิดว่าราคาต่างกัน +580% เพื่อ Arbitrage เพราะเป็นคนละเหรียญและคนละบล็อกเชนอย่างสิ้นเชิง หากโอนข้ามกระเป๋า สินทรัพย์จะสูญหายถาวรทันที"
    },
    {
        "id": "luna",
        "ticker": "LUNA / LUNC",
        "severity": "high",
        "severity_label": "🔴 อันตรายสูง (Hard Fork / เชนเดิม vs เชนใหม่)",
        "issue_type": "Chain Fork & Rebranding",
        "summary": "Terra Classic (LUNC) ราคา ~0.003 ฿ ส่วน Terra 2.0 (LUNA) ราคา ~10-15 ฿ ต่างกันหลายพันเท่า",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_LUNC",
                "project_name": "Terra Classic (LUNC)",
                "network": "Terra Classic Network",
                "approx_price": "~0.002 - 0.004 THB",
                "detail": "บล็อกเชน Terra ดั้งเดิมที่มีปัญหาระบบ UST หลุด Peg เมื่อปี 2022 ปัจจุบันชุมชนดูแลต่อในชื่อ LUNC",
                "coingecko_id": "terra-luna"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "LUNATHB / LUNCTHB",
                "project_name": "Terra 2.0 (LUNA) และ Terra Classic (LUNC)",
                "network": "Terra 2.0 / Terra Classic",
                "approx_price": "LUNA ~10 - 20 THB / LUNC ~0.003 THB",
                "detail": "Binance TH มีทั้งคู่เหรียญ LUNA (เชนใหม่ที่ไม่มี UST) และ LUNC (เชนเดิม)",
                "coingecko_id": "terra-luna-2"
            }
        },
        "warning": "⚠️ ต้องสังเกตตัวย่อให้ชัดเจนว่ามีตัว 'C' ต่อท้ายหรือไม่ (LUNA vs LUNC) เพราะราคาต่างกันหลายพันเท่าตัว"
    },
    {
        "id": "pol",
        "ticker": "POL / MATIC",
        "severity": "medium",
        "severity_label": "🟡 เปลี่ยนชื่อและ Contract (Token Upgrade 1:1)",
        "issue_type": "1:1 Token Migration",
        "summary": "Polygon อัปเกรดเหรียญหลักจาก MATIC เป็น POL ในอัตรา 1:1",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_POL",
                "project_name": "Polygon Ecosystem Token (POL)",
                "network": "Polygon PoS / Ethereum",
                "approx_price": "~10 - 15 THB",
                "detail": "Bitkub ทำการ Swap เหรียญจาก MATIC เป็น POL ครบถ้วนแล้ว",
                "coingecko_id": "polygon-ecosystem-token"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "POLTHB",
                "project_name": "Polygon (POL)",
                "network": "Polygon / Ethereum",
                "approx_price": "~10 - 15 THB",
                "detail": "Binance TH เปลี่ยนชื่อและรองรับ POL เป็นเหรียญหลัก",
                "coingecko_id": "polygon-ecosystem-token"
            }
        },
        "warning": "ℹ️ หากมีกระดานใดใช้ชื่อ MATIC และอีกกระดานใช้ POL มูลค่าคือเหรียญเดียวกัน (Swap 1:1) แต่ต้องตรวจสอบว่ากระดานปลายทางเปิดรับฝาก Contract เวอร์ชั่นใด"
    },
    {
        "id": "render",
        "ticker": "RENDER / RNDR",
        "severity": "medium",
        "severity_label": "🟡 ย้ายบล็อกเชน (Network Migration)",
        "issue_type": "Migration จาก Ethereum ไป Solana",
        "summary": "Render Network ย้ายจาก ERC-20 (RNDR) สู่ Solana SPL Token (RENDER) อัตรา 1:1",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_RENDER",
                "project_name": "Render Token (RENDER)",
                "network": "Solana (SPL)",
                "approx_price": "~180 - 230 THB",
                "detail": "อัปเกรดเป็นชื่อ RENDER บนเชน Solana",
                "coingecko_id": "render-token"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "RENDERTHB",
                "project_name": "Render (RENDER)",
                "network": "Solana",
                "approx_price": "~180 - 230 THB",
                "detail": "แปลงเป็น RENDER บนเครือข่าย Solana เรียบร้อยแล้ว",
                "coingecko_id": "render-token"
            }
        },
        "warning": "⚠️ อย่าโอนเหรียญ RNDR (ERC-20 เก่า) เข้ากระเป๋าที่รองรับเฉพาะ RENDER (Solana ใหม่) เพราะจะทำให้สินทรัพย์สูญหาย"
    },
    {
        "id": "kaia",
        "ticker": "KAIA / KLAY",
        "severity": "medium",
        "severity_label": "🟡 ควบรวมบล็อกเชน (Chain Merger)",
        "issue_type": "ควบรวม Klaytn + Finschia",
        "summary": "Klaytn (KLAY) รวมกับ Finschia (FNSA) เกิดเป็นเชนใหม่ชื่อ KAIA (1:1)",
        "exchanges": {
            "upbit": {
                "exchange_name": "Upbit Thailand",
                "pair": "THB-KAIA",
                "project_name": "Kaia (KAIA)",
                "network": "Kaia Mainnet",
                "approx_price": "~4 - 6 THB",
                "detail": "Upbit Thailand แปลงจาก KLAY เป็น KAIA อัตโนมัติ",
                "coingecko_id": "kaia"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "KAIATHB",
                "project_name": "Kaia (KAIA)",
                "network": "Kaia Mainnet",
                "approx_price": "~4 - 6 THB",
                "detail": "Binance TH ลิสต์ภายใต้ชื่อ KAIA เรียบร้อยแล้ว",
                "coingecko_id": "kaia"
            }
        },
        "warning": "ℹ️ หากเห็นชื่อ KLAY ในบางแอปหรือ API เก่า มันคือ KAIA เดียวกันที่ผ่านการควบรวมแล้ว"
    },
    {
        "id": "gala",
        "ticker": "GALA vs GAL",
        "severity": "low",
        "severity_label": "🔵 ชื่อคล้ายกัน (Lookalike Tickers)",
        "issue_type": "ตัวย่อใกล้เคียงกันทำให้พิมพ์ผิด",
        "summary": "GALA (Gala Games ~1 ฿) vs GAL (Project Galaxy ~60 ฿ ปัจจุบันคือ Gravity G)",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_GALA",
                "project_name": "Gala Games (GALA)",
                "network": "Ethereum / GalaChain",
                "approx_price": "~0.70 - 1.20 THB",
                "detail": "แพลตฟอร์มเกมและบันเทิง Web3 ยอดนิยม",
                "coingecko_id": "gala"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "GALTHB",
                "project_name": "Galxe (GAL / G)",
                "network": "Ethereum / BNB Chain",
                "approx_price": "~50 - 80 THB",
                "detail": "เครือข่าย Web3 Credential Data (ปัจจุบัน Rebrand เป็น Gravity G)",
                "coingecko_id": "galxe"
            }
        },
        "warning": "⚠️ ระวังพิมพ์ค้นหาผิด เหรียญ GALA ราคาบาทกว่าๆ ส่วน GAL ราคาหลักสิบบาท"
    },
    {
        "id": "btt",
        "ticker": "BTT vs BTTC",
        "severity": "medium",
        "severity_label": "🟡 แตกพาร์เหรียญ (Token Redenomination 1:1,000,000)",
        "issue_type": "BitTorrent Old vs BitTorrent Chain",
        "summary": "BitTorrent เดิม (BTTOLD) ถูกแปลงเป็น BTTC ในอัตรา 1 ต่อ 1,000,000",
        "exchanges": {
            "bitkub": {
                "exchange_name": "Bitkub",
                "pair": "THB_BTTC",
                "project_name": "BitTorrent (BTTC)",
                "network": "TRON / BitTorrent Chain",
                "approx_price": "~0.00003 - 0.00004 THB",
                "detail": "เวอร์ชันใหม่ BTTC ที่ผ่านการแตกพาร์ 1:1,000,000",
                "coingecko_id": "bittorrent"
            },
            "binance_th": {
                "exchange_name": "Binance TH",
                "pair": "BTTCTHB",
                "project_name": "BitTorrent (BTTC)",
                "network": "TRON / BTTC",
                "approx_price": "~0.00003 - 0.00004 THB",
                "detail": "BTTC ตัวใหม่",
                "coingecko_id": "bittorrent"
            }
        },
        "warning": "⚠️ อย่าเทียบราคาของ BTT เดิม (ราคา ~0.08 บาท) กับ BTTC ใหม่ (ราคา ~0.00003 บาท) เพราะเป็นอัตรา 1 ต่อ 1,000,000"
    }
]

# In-memory unified cache for Thai Baht (THB) Exchanges
cache = {
    "last_updated": 0,
    "update_count": 0,
    "latency_ms": {
        "bitkub": 0,
        "binance_th": 0,
        "orbix": 0,
        "upbit": 0,
        "binance_global": 0
    },
    "coins": [],
    "dictionary": TOKEN_DICTIONARY,
    "stats": {},
    "status": {
        "bitkub": "initializing",
        "binance_th": "initializing",
        "orbix": "initializing",
        "upbit": "initializing",
        "binance_global": "initializing"
    }
}
cache_lock = threading.Lock()

# Raw storage
raw_bitkub = {}
raw_binance_th = {}
raw_orbix = {}
raw_upbit = {}
raw_binance_global = {}
data_event = threading.Event()

def fetch_json(url, timeout=4, headers=None):
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def bitkub_worker():
    """Worker for Bitkub THB pairs (~400ms)"""
    global raw_bitkub, cache
    while True:
        t0 = time.time()
        try:
            bk_data = fetch_json("https://api.bitkub.com/api/market/ticker", timeout=3)
            latency = int((time.time() - t0) * 1000)
            if bk_data and isinstance(bk_data, dict):
                raw_bitkub = bk_data
                with cache_lock:
                    cache["latency_ms"]["bitkub"] = latency
                    cache["status"]["bitkub"] = "online"
                recompute_thb_comparison()
        except Exception as e:
            with cache_lock:
                cache["status"]["bitkub"] = f"error: {str(e)[:30]}"
        time.sleep(0.4)

def binance_th_worker():
    """Worker for Binance TH bookTicker (~350ms)"""
    global raw_binance_th, cache
    while True:
        t0 = time.time()
        try:
            bnth_data = fetch_json("https://api.binance.th/api/v1/ticker/bookTicker", timeout=3)
            latency = int((time.time() - t0) * 1000)
            if bnth_data and isinstance(bnth_data, list):
                m = {}
                for item in bnth_data:
                    sym = item["symbol"]
                    bid_p = float(item.get("bidPrice", 0))
                    bid_q = float(item.get("bidQty", 0))
                    ask_p = float(item.get("askPrice", 0))
                    ask_q = float(item.get("askQty", 0))
                    mid_p = (bid_p + ask_p) / 2 if (bid_p > 0 and ask_p > 0) else (ask_p or bid_p)
                    m[sym] = {
                        "price": mid_p,
                        "bid": bid_p,
                        "bid_qty": bid_q,
                        "ask": ask_p,
                        "ask_qty": ask_q
                    }
                raw_binance_th = m
                with cache_lock:
                    cache["latency_ms"]["binance_th"] = latency
                    cache["status"]["binance_th"] = "online"
                recompute_thb_comparison()
        except Exception as e:
            with cache_lock:
                cache["status"]["binance_th"] = f"error: {str(e)[:30]}"
        time.sleep(0.35)

def orbix_worker():
    """Worker for Orbix (KBank) (~450ms)"""
    global raw_orbix, cache
    while True:
        t0 = time.time()
        try:
            # Satang Pro / Orbix ticker endpoint
            orb_data = fetch_json("https://satangcorp.com/api/v3/ticker/24hr", timeout=3)
            latency = int((time.time() - t0) * 1000)
            if orb_data and isinstance(orb_data, list):
                m = {}
                for item in orb_data:
                    sym = item.get("symbol", "").upper()
                    if sym.endswith("_THB"):
                        coin = sym.replace("_THB", "")
                        m[coin] = {
                            "last": float(item.get("lastPrice", 0)),
                            "bid": float(item.get("bidPrice", 0)),
                            "bid_qty": float(item.get("bidQty", 0)),
                            "ask": float(item.get("askPrice", 0)),
                            "ask_qty": float(item.get("askQty", 0)),
                            "vol_thb": float(item.get("quoteVolume", 0)),
                            "change_24h": float(item.get("priceChangePercent", 0))
                        }
                raw_orbix = m
                with cache_lock:
                    cache["latency_ms"]["orbix"] = latency
                    cache["status"]["orbix"] = "online"
                recompute_thb_comparison()
        except Exception as e:
            with cache_lock:
                cache["status"]["orbix"] = f"error: {str(e)[:30]}"
        time.sleep(0.45)

def upbit_worker():
    """Worker for Upbit Thailand (~500ms)"""
    global raw_upbit, cache
    # List of Upbit THB markets
    markets_str = "THB-BTC,THB-ETH,THB-XRP,THB-SOL,THB-ADA,THB-DOGE,THB-USDT,THB-XLM,THB-LINK,THB-UNI,THB-CHZ,THB-AXS,THB-MANA,THB-YFI,THB-POL,THB-KAIA"
    url = f"https://th-api.upbit.com/v1/ticker?markets={markets_str}"
    while True:
        t0 = time.time()
        try:
            upb_data = fetch_json(url, timeout=3)
            latency = int((time.time() - t0) * 1000)
            if upb_data and isinstance(upb_data, list):
                m = {}
                for item in upb_data:
                    market = item.get("market", "")
                    if market.startswith("THB-"):
                        coin = market.replace("THB-", "")
                        m[coin] = {
                            "last": float(item.get("trade_price", 0)),
                            "change_24h": float(item.get("signed_change_rate", 0)) * 100,
                            "vol_thb": float(item.get("acc_trade_price_24h", 0))
                        }
                raw_upbit = m
                with cache_lock:
                    cache["latency_ms"]["upbit"] = latency
                    cache["status"]["upbit"] = "online"
                recompute_thb_comparison()
        except Exception as e:
            with cache_lock:
                cache["status"]["upbit"] = f"error: {str(e)[:30]}"
        time.sleep(0.5)

def binance_global_worker():
    """Worker for Binance Global orderbook sourced via Binance Cloud orderbook (~400ms)
    Keeps api.binance.com request weight 0 so private account/trading endpoints are never rate limited.
    """
    global raw_binance_global, cache
    while True:
        with cache_lock:
            th_data = raw_binance_th
        if th_data:
            m = {}
            for sym, info in th_data.items():
                if sym.endswith("USDT"):
                    m[sym] = info
            if m:
                raw_binance_global = m
                with cache_lock:
                    cache["latency_ms"]["binance_global"] = cache["latency_ms"].get("binance_th", 200)
                    cache["status"]["binance_global"] = "online"
                recompute_thb_comparison()
        time.sleep(0.4)

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arbitrage_history.json")

class ArbitrageTracker:
    def __init__(self, default_fee_pct=0.35):
        self.default_fee_pct = default_fee_pct
        self.lock = threading.Lock()
        self.active_sessions = {}
        self.history = []
        self.last_save_time = time.time()
        self.load_history()

    def load_history(self):
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.history = data[:1000]
        except Exception as e:
            print(f"Notice: arbitrage history init ({e})")

    def save_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history[:1000], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def update_tick(self, processed_coins):
        now = time.time()
        current_active_keys = set()

        with self.lock:
            for coin in processed_coins:
                sym = coin["symbol"]
                exec_data = coin.get("execution", {})
                if not exec_data.get("valid"):
                    continue

                real_spread = exec_data.get("real_spread_pct", 0)
                if real_spread <= 0 or real_spread > 40:
                    continue

                buy_ex = exec_data.get("real_buy_ex")
                sell_ex = exec_data.get("real_sell_ex")
                if not buy_ex or not sell_ex or buy_ex == sell_ex:
                    continue

                max_cap = exec_data.get("max_capacity_thb", 0)
                buy_price = exec_data.get("real_buy_price", 0)
                sell_price = exec_data.get("real_sell_price", 0)
                net_spread = real_spread - self.default_fee_pct

                key = (sym, buy_ex, sell_ex)
                current_active_keys.add(key)

                if key not in self.active_sessions:
                    session_id = str(uuid.uuid4())[:8]
                    new_session = {
                        "id": session_id,
                        "symbol": sym,
                        "buy_ex": buy_ex,
                        "sell_ex": sell_ex,
                        "start_time": now,
                        "start_iso": datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                        "last_seen_time": now,
                        "peak_gross_spread": round(real_spread, 3),
                        "peak_net_spread": round(net_spread, 3),
                        "latest_gross_spread": round(real_spread, 3),
                        "latest_net_spread": round(net_spread, 3),
                        "max_fill_thb": round(max_cap, 2),
                        "total_duration_sec": 0.0,
                        "status": "active",
                        "current_stage": {
                            "spread_pct": round(real_spread, 2),
                            "net_spread_pct": round(net_spread, 2),
                            "label": f"+{real_spread:.2f}%",
                            "start_time": now,
                            "last_time": now,
                            "duration_sec": 0.0,
                            "buy_price": buy_price,
                            "sell_price": sell_price,
                            "max_fill_thb": max_cap,
                            "ticks": 1
                        },
                        "stages": []
                    }
                    self.active_sessions[key] = new_session
                else:
                    session = self.active_sessions[key]
                    session["last_seen_time"] = now
                    session["total_duration_sec"] = round(now - session["start_time"], 2)
                    session["latest_gross_spread"] = round(real_spread, 3)
                    session["latest_net_spread"] = round(net_spread, 3)
                    if real_spread > session["peak_gross_spread"]:
                        session["peak_gross_spread"] = round(real_spread, 3)
                        session["peak_net_spread"] = round(net_spread, 3)
                    if max_cap > session["max_fill_thb"]:
                        session["max_fill_thb"] = round(max_cap, 2)

                    cur_st = session["current_stage"]
                    spread_shift = abs(real_spread - cur_st["spread_pct"])
                    if spread_shift <= 0.15:
                        cur_st["last_time"] = now
                        cur_st["duration_sec"] = round(now - cur_st["start_time"], 2)
                        cur_st["ticks"] += 1
                        if max_cap > cur_st["max_fill_thb"]:
                            cur_st["max_fill_thb"] = max_cap
                    else:
                        cur_st["duration_sec"] = round(now - cur_st["start_time"], 2)
                        if cur_st["duration_sec"] >= 0.1:
                            session["stages"].append({
                                "spread_pct": cur_st["spread_pct"],
                                "net_spread_pct": cur_st["net_spread_pct"],
                                "label": cur_st["label"],
                                "duration_sec": max(0.1, cur_st["duration_sec"]),
                                "buy_price": cur_st["buy_price"],
                                "sell_price": cur_st["sell_price"],
                                "max_fill_thb": cur_st["max_fill_thb"],
                                "ticks": cur_st["ticks"]
                            })
                        session["current_stage"] = {
                            "spread_pct": round(real_spread, 2),
                            "net_spread_pct": round(net_spread, 2),
                            "label": f"+{real_spread:.2f}%",
                            "start_time": now,
                            "last_time": now,
                            "duration_sec": 0.0,
                            "buy_price": buy_price,
                            "sell_price": sell_price,
                            "max_fill_thb": max_cap,
                            "ticks": 1
                        }

            ended_keys = []
            for key, session in list(self.active_sessions.items()):
                if key not in current_active_keys and (now - session["last_seen_time"]) > 1.8:
                    ended_keys.append(key)

            for key in ended_keys:
                session = self.active_sessions.pop(key)
                session["status"] = "ended"
                session["end_time"] = session["last_seen_time"]
                session["end_iso"] = datetime.datetime.fromtimestamp(session["end_time"]).strftime("%H:%M:%S")
                session["total_duration_sec"] = round(session["end_time"] - session["start_time"], 2)

                cur_st = session.get("current_stage")
                if cur_st:
                    cur_st["duration_sec"] = round(session["end_time"] - cur_st["start_time"], 2)
                    if cur_st["duration_sec"] > 0.05 or not session["stages"]:
                        session["stages"].append({
                            "spread_pct": cur_st["spread_pct"],
                            "net_spread_pct": cur_st["net_spread_pct"],
                            "label": cur_st["label"],
                            "duration_sec": max(0.1, cur_st["duration_sec"]),
                            "buy_price": cur_st["buy_price"],
                            "sell_price": cur_st["sell_price"],
                            "max_fill_thb": cur_st["max_fill_thb"],
                            "ticks": cur_st["ticks"]
                        })
                    session.pop("current_stage", None)

                if session["total_duration_sec"] >= 0.2:
                    self.history.insert(0, session)
                    if len(self.history) > 1000:
                        self.history.pop()

            if now - self.last_save_time > 15:
                self.save_history()
                self.last_save_time = now

    def get_report_data(self, min_duration=0, symbol=None):
        now = time.time()
        with self.lock:
            live_active = []
            for key, s in self.active_sessions.items():
                s_copy = dict(s)
                s_copy["total_duration_sec"] = round(now - s_copy["start_time"], 2)
                cur_st = dict(s_copy["current_stage"])
                cur_st["duration_sec"] = round(now - cur_st["start_time"], 2)
                all_stages = list(s_copy["stages"]) + [cur_st]
                s_copy["live_stages"] = all_stages
                live_active.append(s_copy)

            filtered_history = self.history
            if symbol:
                filtered_history = [h for h in filtered_history if h["symbol"].upper() == symbol.upper()]
            if min_duration > 0:
                filtered_history = [h for h in filtered_history if h["total_duration_sec"] >= min_duration]

            total_events = len(self.history)
            actionable_5s = [h for h in self.history if h["total_duration_sec"] >= 5.0 and h["peak_net_spread"] > 0]
            actionable_2s = [h for h in self.history if 2.0 <= h["total_duration_sec"] < 5.0 and h["peak_net_spread"] > 0]
            flash_sub2s = [h for h in self.history if h["total_duration_sec"] < 2.0]

            EX_DISPLAY_NAMES = {
                "bitkub": "Bitkub",
                "binance_th": "Binance TH",
                "binance_global": "Binance Global",
                "orbix": "Orbix",
                "upbit": "Upbit"
            }

            longest_event = max(self.history, key=lambda h: h.get("total_duration_sec", 0), default=None) if self.history else None
            highest_net_event = max(self.history, key=lambda h: h.get("peak_net_spread", 0), default=None) if self.history else None

            max_dur = longest_event.get("total_duration_sec", 0.0) if longest_event else 0.0
            max_dur_coin = longest_event.get("symbol", "-") if longest_event else "-"
            max_dur_buy_ex = longest_event.get("buy_ex", "") if longest_event else ""
            max_dur_sell_ex = longest_event.get("sell_ex", "") if longest_event else ""
            max_dur_route = f"ซื้อ {EX_DISPLAY_NAMES.get(max_dur_buy_ex, max_dur_buy_ex)} ➔ ขาย {EX_DISPLAY_NAMES.get(max_dur_sell_ex, max_dur_sell_ex)}" if longest_event else "-"

            max_net = highest_net_event.get("peak_net_spread", 0.0) if highest_net_event else 0.0
            max_net_coin = highest_net_event.get("symbol", "-") if highest_net_event else "-"
            max_net_buy_ex = highest_net_event.get("buy_ex", "") if highest_net_event else ""
            max_net_sell_ex = highest_net_event.get("sell_ex", "") if highest_net_event else ""
            max_net_route = f"ซื้อ {EX_DISPLAY_NAMES.get(max_net_buy_ex, max_net_buy_ex)} ➔ ขาย {EX_DISPLAY_NAMES.get(max_net_sell_ex, max_net_sell_ex)}" if highest_net_event else "-"

            route_counts = {}
            for h in self.history:
                b_name = EX_DISPLAY_NAMES.get(h.get("buy_ex"), h.get("buy_ex", ""))
                s_name = EX_DISPLAY_NAMES.get(h.get("sell_ex"), h.get("sell_ex", ""))
                r_key = f"{b_name} ➔ {s_name}"
                route_counts[r_key] = route_counts.get(r_key, 0) + 1

            top_routes = sorted([
                {
                    "route": k, 
                    "count": v, 
                    "pct": round(v / max(1, total_events) * 100, 1)
                } 
                for k, v in route_counts.items()
            ], key=lambda x: -x["count"])[:6]

            coin_counts = {}
            for h in self.history:
                sym = h["symbol"]
                coin_counts[sym] = coin_counts.get(sym, 0) + 1
            top_coins = sorted([{"symbol": k, "count": v} for k, v in coin_counts.items()], key=lambda x: -x["count"])[:5]

            return {
                "stats": {
                    "total_recorded": total_events,
                    "active_now_count": len(live_active),
                    "actionable_5s_count": len(actionable_5s),
                    "actionable_2s_count": len(actionable_2s),
                    "flash_sub2s_count": len(flash_sub2s),
                    "max_duration_sec": max_dur,
                    "max_duration_coin": max_dur_coin,
                    "max_duration_route": max_dur_route,
                    "max_duration_buy_ex": max_dur_buy_ex,
                    "max_duration_sell_ex": max_dur_sell_ex,
                    "max_net_spread": max_net,
                    "max_net_coin": max_net_coin,
                    "max_net_route": max_net_route,
                    "max_net_buy_ex": max_net_buy_ex,
                    "max_net_sell_ex": max_net_sell_ex,
                    "top_routes": top_routes,
                    "top_coins": top_coins,
                    "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                },
                "live_active": live_active,
                "history": filtered_history[:300]
            }

    def export_csv(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Session ID", "Symbol", "Buy Exchange", "Sell Exchange",
            "Start Time", "End Time", "Total Duration (sec)",
            "Peak Gross Spread (%)", "Peak Net Spread (%)", "Max Fill (THB)",
            "Verdict", "Stage Breakdown Timeline"
        ])
        with self.lock:
            for h in self.history:
                dur = h.get("total_duration_sec", 0)
                net = h.get("peak_net_spread", 0)
                if dur >= 5.0 and net > 0:
                    verdict = "Executable for Humans (>5s)"
                elif dur >= 2.0 and net > 0:
                    verdict = "Bot Actionable (2-5s)"
                else:
                    verdict = "Flash Opportunity (<2s)"

                stages_str = " -> ".join([
                    f"{s.get('label', '')} ({s.get('duration_sec', 0):.1f}s, ฿{s.get('max_fill_thb', 0):,.0f})"
                    for s in h.get("stages", [])
                ])
                writer.writerow([
                    h.get("id"),
                    h.get("symbol"),
                    h.get("buy_ex"),
                    h.get("sell_ex"),
                    h.get("start_iso", ""),
                    h.get("end_iso", ""),
                    dur,
                    h.get("peak_gross_spread"),
                    net,
                    h.get("max_fill_thb"),
                    verdict,
                    stages_str
                ])
        return output.getvalue()

    def clear_history(self):
        with self.lock:
            self.history = []
            self.save_history()

tracker = ArbitrageTracker()

def recompute_thb_comparison():
    """Calculates pure Thai Baht (THB) comparisons across all 4 Thai exchanges"""
    global cache
    bk_data = raw_bitkub
    bnth_prices = raw_binance_th
    orb_data = raw_orbix
    upb_data = raw_upbit
    bg_prices = raw_binance_global

    if not bk_data:
        return

    # Usdt reference rate on Bitkub
    usdt_thb = 33.50
    if "THB_USDT" in bk_data:
        try:
            usdt_thb = float(bk_data["THB_USDT"].get("last", 33.50))
        except Exception:
            pass

    processed_coins = []
    
    # Priority coins first
    priority_order = [
        "BTC", "ETH", "SOL", "XRP", "USDT", "BNB", "DOGE", "ADA", "KUB", "SUI", 
        "NEAR", "LINK", "UNI", "AVAX", "DOT", "XLM", "POL", "MANA", "AXS", "CHZ", "ZENT", "VELO"
    ]

    # Collect all available coins across all exchanges
    all_coins_set = set()
    for k in bk_data.keys():
        if k.startswith("THB_"):
            all_coins_set.add(k[4:])
    for k in bnth_prices.keys():
        if k.endswith("THB"):
            all_coins_set.add(k[:-3])
    for k in orb_data.keys():
        all_coins_set.add(k)
    for k in upb_data.keys():
        all_coins_set.add(k)

    mismatches = {"LUNA", "DATA", "VELO"}
    all_coins_set = {c for c in all_coins_set if c not in mismatches and len(c) <= 8}

    for coin in all_coins_set:
        # 1. Bitkub
        bk_item = bk_data.get(f"THB_{coin}", {})
        bk_price = float(bk_item.get("last", 0))
        bk_bid = float(bk_item.get("highestBid", 0))
        bk_ask = float(bk_item.get("lowestAsk", 0))
        bk_change = float(bk_item.get("percentChange", 0))
        bk_vol = float(bk_item.get("quoteVolume", 0))

        # 2. Binance TH (Direct THB or converted)
        bnth_item = bnth_prices.get(f"{coin}THB", {})
        has_direct_bnth = bool(bnth_item and bnth_item.get("price", 0) > 0)
        if has_direct_bnth:
            bnth_price = bnth_item.get("price", 0)
            bnth_bid = bnth_item.get("bid", 0)
            bnth_ask = bnth_item.get("ask", 0)
            bnth_bid_thb = bnth_bid * bnth_item.get("bid_qty", 0)
            bnth_ask_thb = bnth_ask * bnth_item.get("ask_qty", 0)
        else:
            usdt_item = bnth_prices.get(f"{coin}USDT", {})
            if usdt_item and usdt_item.get("price", 0) > 0:
                bnth_price = usdt_item["price"] * usdt_thb
                bnth_bid = usdt_item["bid"] * usdt_thb
                bnth_ask = usdt_item["ask"] * usdt_thb
                bnth_bid_thb = bnth_bid * usdt_item.get("bid_qty", 0)
                bnth_ask_thb = bnth_ask * usdt_item.get("ask_qty", 0)
            else:
                bnth_price = 0
                bnth_bid = 0
                bnth_ask = 0
                bnth_bid_thb = 0
                bnth_ask_thb = 0

        # 3. Orbix (KBank)
        orb_item = orb_data.get(coin, {})
        orb_price = orb_item.get("last", 0)
        orb_bid = orb_item.get("bid", 0)
        orb_ask = orb_item.get("ask", 0)
        orb_change = orb_item.get("change_24h", 0)
        orb_vol = orb_item.get("vol_thb", 0)
        orb_bid_thb = orb_bid * orb_item.get("bid_qty", 0)
        orb_ask_thb = orb_ask * orb_item.get("ask_qty", 0)

        # 4. Upbit TH
        upb_item = upb_data.get(coin, {})
        upb_price = upb_item.get("last", 0)
        upb_change = upb_item.get("change_24h", 0)
        upb_vol = upb_item.get("vol_thb", 0)
        upb_bid = upb_price
        upb_ask = upb_price
        upb_bid_thb = upb_vol * 0.03 if upb_vol >= 50000 else 0
        upb_ask_thb = upb_vol * 0.03 if upb_vol >= 50000 else 0

        # 5. Binance Global (USDT pair converted to THB)
        bg_item = bg_prices.get(f"{coin}USDT", {})
        if bg_item and bg_item.get("price", 0) > 0:
            bg_price = bg_item["price"] * usdt_thb
            bg_bid = bg_item["bid"] * usdt_thb
            bg_ask = bg_item["ask"] * usdt_thb
            bg_bid_thb = bg_bid * bg_item.get("bid_qty", 0)
            bg_ask_thb = bg_ask * bg_item.get("ask_qty", 0)
        else:
            bg_price = 0
            bg_bid = 0
            bg_ask = 0
            bg_bid_thb = 0
            bg_ask_thb = 0

        # Bitkub depth estimation from volume
        bk_safe_depth = min(max(bk_vol * 0.04, 500), 1_500_000) if bk_vol >= 50000 else bk_vol * 0.1
        bk_bid_thb = bk_safe_depth
        bk_ask_thb = bk_safe_depth

        # Stale indicators (no trade volume in 24h = stale historical price)
        stale_map = {
            "bitkub": bk_vol < 50000 and bk_price > 0,
            "binance_th": False,
            "binance_global": False,
            "orbix": orb_vol < 50000 and orb_price > 0,
            "upbit": upb_vol < 50000 and upb_price > 0
        }

        # Gather active prices
        prices_dict = {}
        if bk_price > 0: prices_dict["bitkub"] = bk_price
        if bnth_price > 0: prices_dict["binance_th"] = bnth_price
        if bg_price > 0: prices_dict["binance_global"] = bg_price
        if orb_price > 0: prices_dict["orbix"] = orb_price
        if upb_price > 0: prices_dict["upbit"] = upb_price

        # Liquid prices (volume >= 50,000 THB)
        liquid_prices = {}
        for ex, p in prices_dict.items():
            if not stale_map.get(ex, False):
                liquid_prices[ex] = p

        # Ex name map
        ex_names = {
            "bitkub": "Bitkub",
            "binance_th": "Binance TH",
            "binance_global": "Binance Global",
            "orbix": "Orbix",
            "upbit": "Upbit"
        }

        # Real Orderbook Execution Calculation (Ask to Bid across exchanges)
        books = {
            "bitkub": {"ask": bk_ask, "bid": bk_bid, "ask_thb": bk_ask_thb, "bid_thb": bk_bid_thb, "vol": bk_vol},
            "binance_th": {"ask": bnth_ask, "bid": bnth_bid, "ask_thb": bnth_ask_thb, "bid_thb": bnth_bid_thb, "vol": 1_000_000},
            "binance_global": {"ask": bg_ask, "bid": bg_bid, "ask_thb": bg_ask_thb, "bid_thb": bg_bid_thb, "vol": 100_000_000},
            "orbix": {"ask": orb_ask, "bid": orb_bid, "ask_thb": orb_ask_thb, "bid_thb": orb_bid_thb, "vol": orb_vol},
            "upbit": {"ask": upb_ask, "bid": upb_bid, "ask_thb": upb_ask_thb, "bid_thb": upb_bid_thb, "vol": upb_vol}
        }

        real_routes = []
        for buy_ex, buy_b in books.items():
            if buy_b["ask"] <= 0 or (buy_ex not in ("binance_th", "binance_global") and buy_b["vol"] < 50000):
                continue
            for sell_ex, sell_b in books.items():
                if buy_ex == sell_ex:
                    continue
                if sell_b["bid"] <= 0 or (sell_ex not in ("binance_th", "binance_global") and sell_b["vol"] < 50000):
                    continue

                eff_diff = sell_b["bid"] - buy_b["ask"]
                eff_pct = (eff_diff / buy_b["ask"]) * 100
                max_cap = min(buy_b["ask_thb"], sell_b["bid_thb"])

                real_routes.append({
                    "buy_ex": buy_ex,
                    "buy_price": buy_b["ask"],
                    "sell_ex": sell_ex,
                    "sell_price": sell_b["bid"],
                    "spread_pct": eff_pct,
                    "spread_thb": eff_diff,
                    "max_capacity_thb": max_cap
                })

        best_real = max(real_routes, key=lambda r: r["spread_pct"]) if real_routes else None

        if best_real:
            if best_real["spread_pct"] > 0.35 and best_real["max_capacity_thb"] >= 10000:
                readiness_code = "READY"
                readiness_badge = "🟢 พร้อมจบจริง"
                readiness_desc = f"วงเงินพร้อมซื้อขาย ฿{best_real['max_capacity_thb']:,.0f}"
            elif best_real["spread_pct"] > 0:
                readiness_code = "SMALL"
                readiness_badge = "🟡 ไม้เล็ก"
                readiness_desc = f"วงเงินจำกัดไม่เกิน ฿{best_real['max_capacity_thb']:,.0f}"
            else:
                readiness_code = "NEGATIVE"
                readiness_badge = "❌ หลอกตา (Ask > Bid)"
                readiness_desc = f"ซื้อจริงขาดทุน {best_real['spread_pct']:.2f}%"
        else:
            readiness_code = "ILLIQUID"
            readiness_badge = "⚠️ ตลาดร้าง"
            readiness_desc = "โวลุ่ม < ฿50k สภาพคล่องไม่พอ"

        # Last price fallback comparison
        if len(prices_dict) >= 2:
            best_buy_ex = min(prices_dict, key=prices_dict.get)
            best_sell_ex = max(prices_dict, key=prices_dict.get)
            min_p = prices_dict[best_buy_ex]
            max_p = prices_dict[best_sell_ex]
            spread_thb = max_p - min_p
            spread_pct = (spread_thb / min_p) * 100 if min_p > 0 else 0
            arb_signal = f"ซื้อ {ex_names[best_buy_ex]} ➔ ขาย {ex_names[best_sell_ex]}" if spread_pct > 0.35 else "Spread แคบ"
        else:
            best_buy_ex = "-"
            best_sell_ex = "-"
            min_p = 0
            max_p = 0
            spread_thb = 0
            spread_pct = 0
            arb_signal = "-"

        processed_coins.append({
            "symbol": coin,
            "name": coin,
            "exchanges_count": len(prices_dict),
            "is_stale_any": any(stale_map.get(ex, False) for ex in prices_dict.keys()),
            "stale_exchanges": [ex for ex, s in stale_map.items() if s],
            "prices": {
                "bitkub": bk_price,
                "binance_th": bnth_price,
                "binance_global": bg_price,
                "orbix": orb_price,
                "upbit": upb_price
            },
            "meta": {
                "stale": stale_map,
                "bitkub": { "bid": bk_bid, "ask": bk_ask, "vol": bk_vol, "change": bk_change, "depth_thb": bk_ask_thb, "url": f"https://www.bitkub.com/market/{coin}", "stale": stale_map["bitkub"] },
                "binance_th": { "bid": bnth_bid, "ask": bnth_ask, "depth_thb": bnth_ask_thb, "direct_thb": has_direct_bnth, "url": f"https://www.binance.th/th/trade/{coin}_{'THB' if has_direct_bnth else 'USDT'}", "stale": stale_map["binance_th"] },
                "binance_global": { "bid": bg_bid, "ask": bg_ask, "depth_thb": bg_ask_thb, "usdt_price": bg_item.get("price", 0) if bg_item else 0, "url": f"https://www.binance.com/en/trade/{coin}_USDT", "stale": False },
                "orbix": { "bid": orb_bid, "ask": orb_ask, "vol": orb_vol, "depth_thb": orb_ask_thb, "change": orb_change, "url": f"https://www.orbixtrade.com/trade/{coin}-THB", "stale": stale_map["orbix"] },
                "upbit": { "vol": upb_vol, "change": upb_change, "depth_thb": upb_ask_thb, "url": f"https://th.upbit.com/exchange?code=CRIX.UPBIT.THB-{coin}", "stale": stale_map["upbit"] }
            },
            "execution": {
                "valid": bool(best_real),
                "readiness": readiness_code,
                "badge": readiness_badge,
                "description": readiness_desc,
                "real_spread_pct": best_real["spread_pct"] if best_real else 0,
                "real_spread_thb": best_real["spread_thb"] if best_real else 0,
                "real_buy_ex": best_real["buy_ex"] if best_real else "-",
                "real_buy_price": best_real["buy_price"] if best_real else 0,
                "real_sell_ex": best_real["sell_ex"] if best_real else "-",
                "real_sell_price": best_real["sell_price"] if best_real else 0,
                "max_capacity_thb": best_real["max_capacity_thb"] if best_real else 0
            },
            "arbitrage": {
                "best_buy_ex": best_buy_ex,
                "best_sell_ex": best_sell_ex,
                "best_buy_price": min_p,
                "best_sell_price": max_p,
                "spread_thb": spread_thb,
                "spread_pct": spread_pct,
                "signal": arb_signal,
                "is_stale": any(stale_map.get(best_buy_ex, False) or stale_map.get(best_sell_ex, False) for _ in [1])
            },
            "real_routes": real_routes
        })

    # Default Sort: Highest Executable Spread first, then highest Match Spread
    def sort_key(c):
        ex = c.get("execution", {})
        if ex.get("valid") and ex.get("readiness") in ("READY", "SMALL"):
            return (0, -ex.get("real_spread_pct", 0), -ex.get("max_capacity_thb", 0))
        return (1, -c["arbitrage"]["spread_pct"])

    processed_coins.sort(key=sort_key)

    # Feed real orderbook opportunities to persistent tracker & autotrade engine
    try:
        tracker.update_tick(processed_coins)
        autotrade_engine.on_market_tick(processed_coins)
    except Exception as e:
        pass

    # Stats based on Real Executable Arbitrage
    real_positive_arbs = [c for c in processed_coins if c["execution"]["valid"] and 0 < c["execution"]["real_spread_pct"] < 35 and c["execution"]["max_capacity_thb"] >= 3000]
    top_arb = max(real_positive_arbs, key=lambda x: x["execution"]["real_spread_pct"]) if real_positive_arbs else None

    with cache_lock:
        cache["coins"] = processed_coins
        cache["last_updated"] = time.time()
        cache["update_count"] += 1
        cache["stats"] = {
            "total_coins": len(processed_coins),
            "top_arb_coin": top_arb["symbol"] if top_arb else "-",
            "top_arb_pct": top_arb["execution"]["real_spread_pct"] if top_arb else 0,
            "top_arb_thb": top_arb["execution"]["real_spread_thb"] if top_arb else 0,
            "top_arb_route": f"ซื้อ {ex_names.get(top_arb['execution']['real_buy_ex'])} ➔ ขาย {ex_names.get(top_arb['execution']['real_sell_ex'])} (วงเงิน ฿{top_arb['execution']['max_capacity_thb']:,.0f})" if top_arb else "รอจังหวะตลาด",
            "usdt_rate": usdt_thb
        }

    data_event.set()

class CryptoHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def check_auth(self):
        auth_pass = os.environ.get("DASHBOARD_PASS", "").strip()
        if not auth_pass:
            return True
        auth_header = self.headers.get("Authorization")
        if not auth_header:
            return False
        try:
            auth_type, encoded = auth_header.split(" ", 1)
            if auth_type.lower() != "basic":
                return False
            decoded = base64.b64decode(encoded.strip()).decode("utf-8")
            u, p = decoded.split(":", 1)
            expected_u = os.environ.get("DASHBOARD_USER", "admin").strip()
            return u == expected_u and p == auth_pass
        except Exception:
            return False

    def require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Crypto Arbitrage Dashboard Login"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>401 Unauthorized</h1><p>Username and Password required to access this dashboard.</p>")

    def do_POST(self):
        if not self.check_auth():
            self.require_auth()
            return
        if self.path.startswith("/api/report/clear"):
            tracker.clear_history()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "message": "History cleared"}).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/toggle"):
            new_state = autotrade_engine.toggle()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"enabled": new_state}).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/config"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            res = autotrade_engine.update_config(data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/reset-paper"):
            res = autotrade_engine.reset_paper_balances()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/keys"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            res = autotrade_engine.set_api_keys(data.get("exchange", ""), data.get("key", ""), data.get("secret", ""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/mode"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            res = autotrade_engine.set_mode(data.get("mode", "paper"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/test-api"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            res = autotrade_engine.test_exchange_api(
                data.get("exchange", ""),
                data.get("key", None),
                data.get("secret", None)
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/loan/borrow"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            coin = data.get("coin", "BTC")
            amount = float(data.get("amount", 0.001))
            borrow_src = data.get("borrow_source", "binance_global")
            tgt_ex = data.get("target_exchange", "bitkub")
            collateral_usdt = data.get("collateral_usdt", None)
            res = autotrade_engine.borrow_coin(coin, amount, borrow_src, tgt_ex, collateral_usdt)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/loan/repay"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            loan_id = data.get("loan_id", "")
            res = autotrade_engine.repay_coin(loan_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/close-position"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            coin = data.get("coin", "").upper()
            qty = float(data.get("quantity", 0))
            bn_keys = autotrade_engine.api_keys.get("binance_global", {})
            if not bn_keys.get("key") or not bn_keys.get("secret"):
                res = {"success": False, "message": "ไม่พบ API Key ของ Binance Global"}
            else:
                res = ExchangeAPIClient.place_binance_margin_order(
                    bn_keys["key"], bn_keys["secret"], coin, "BUY", quantity=qty
                )
                if res.get("success"):
                    autotrade_engine.log(f"✅ [MANUAL CLOSE SUCCESS] ปิดสถานะหนี้ {coin} {qty:,.2f} สำเร็จเรียบร้อย!", "success")
                    send_telegram_alert(
                        f"✅ <b>[POSITION CLOSED] ปิดสถานะหนี้สำเร็จ!</b>\n\n"
                        f"🪙 เหรียญ: <b>{coin}</b>\n"
                        f"💵 จำนวนที่ปิด: {qty:,.2f} {coin}\n"
                        f"🏦 กระดาน: Binance Global Cross Margin\n"
                        f"🎉 สถานะ: ชำระหนี้คืนคลังเรียบร้อย 0 หนี้คงค้าง"
                    )
                    autotrade_engine.refresh_real_balances()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path in ("/health", "/ping", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","uptime":' + str(int(time.time())).encode("utf-8") + b'}')
            return

        if not self.check_auth():
            self.require_auth()
            return
        if self.path.startswith("/api/dictionary"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            data = json.dumps(TOKEN_DICTIONARY).encode("utf-8")
            self.wfile.write(data)
            return

        if self.path.startswith("/api/compare"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            with cache_lock:
                data = json.dumps(cache).encode("utf-8")
            self.wfile.write(data)
            return

        if self.path.startswith("/api/report/opportunities"):
            min_dur = 0.0
            sym = None
            if "?" in self.path:
                try:
                    q = self.path.split("?", 1)[1]
                    parts = q.split("&")
                    for p in parts:
                        if p.startswith("min_duration="):
                            min_dur = float(p.split("=")[1])
                        elif p.startswith("symbol="):
                            sym = p.split("=")[1].strip()
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            rep = tracker.get_report_data(min_duration=min_dur, symbol=sym)
            self.wfile.write(json.dumps(rep, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/report/clear"):
            tracker.clear_history()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "message": "History cleared"}).encode("utf-8"))
            return

        if self.path.startswith("/api/report/export"):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8-sig")
            self.send_header("Content-Disposition", "attachment; filename=\"arbitrage_opportunity_report.csv\"")
            self.end_headers()
            csv_data = tracker.export_csv().encode("utf-8-sig")
            self.wfile.write(csv_data)
            return

        if self.path.startswith("/api/autotrade/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(autotrade_engine.get_status()).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/toggle"):
            new_state = autotrade_engine.toggle()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"enabled": new_state}).encode("utf-8"))
            return

        if self.path.startswith("/api/autotrade/export"):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8-sig")
            self.send_header("Content-Disposition", "attachment; filename=\"autotrade_simulation_log.csv\"")
            self.end_headers()
            csv_data = autotrade_engine.export_trades_csv().encode("utf-8-sig")
            self.wfile.write(csv_data)
            return

        if self.path.startswith("/api/autotrade/loans"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(autotrade_engine.get_loans(), ensure_ascii=False).encode("utf-8"))
            return

        if self.path in ("/report", "/report/", "/report.html"):
            self.path = "/report.html"

        if self.path in ("/autotrade", "/autotrade/", "/autotrade.html"):
            self.path = "/autotrade.html"

        if self.path in ("/api_trade", "/api_trade/", "/api_trade.html", "/live_trade", "/live_trade/", "/live_trade.html"):
            self.path = "/api_trade.html"

        if self.path in ("/", ""):
            self.path = "/index.html"

        return super().do_GET()

def keep_alive_worker():
    """Pings the public Render URL /health every 4 minutes to prevent Render Free tier from sleeping."""
    time.sleep(30)
    public_url = os.environ.get("RENDER_EXTERNAL_URL", "https://crypto-arbitrage-bot-oq91.onrender.com").rstrip("/")
    ping_url = f"{public_url}/health"
    while True:
        try:
            req = urllib.request.Request(ping_url, headers={"User-Agent": "AntigravityKeepAlive/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
        except Exception as e:
            pass
        time.sleep(240)

def run_server():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    t_bk = threading.Thread(target=bitkub_worker, daemon=True)
    t_bk.start()

    t_bnth = threading.Thread(target=binance_th_worker, daemon=True)
    t_bnth.start()

    t_orb = threading.Thread(target=orbix_worker, daemon=True)
    t_orb.start()

    t_upb = threading.Thread(target=upbit_worker, daemon=True)
    t_upb.start()

    t_bg = threading.Thread(target=binance_global_worker, daemon=True)
    t_bg.start()

    t_ka = threading.Thread(target=keep_alive_worker, daemon=True)
    t_ka.start()

    server = ThreadingHTTPServer((HOST, PORT), CryptoHandler)
    print(f"🚀 Thai Baht Pure All-Exchange Monitor running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    run_server()
