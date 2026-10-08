import os
import json
import time
import datetime
import threading
import uuid
import random
import csv
import io
import urllib.request
from exchange_api import ExchangeAPIClient

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autotrade_state.json")
KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autotrade_keys.json")
COMMON_PERPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common_perps.json")

BORROWABLE_ASSETS = {
    "BTC": {"name": "Bitcoin", "daily_rate": 0.00015, "min_borrow": 0.0005, "collateral_ratio": 1.5, "chain": "Bitcoin / BEP20", "ref_usd": 64500.0},
    "ETH": {"name": "Ethereum", "daily_rate": 0.00018, "min_borrow": 0.005, "collateral_ratio": 1.5, "chain": "Ethereum / Arbitrum", "ref_usd": 2650.0},
    "SOL": {"name": "Solana", "daily_rate": 0.00025, "min_borrow": 0.05, "collateral_ratio": 1.5, "chain": "Solana", "ref_usd": 155.0},
    "XRP": {"name": "Ripple", "daily_rate": 0.00012, "min_borrow": 5.0, "collateral_ratio": 1.4, "chain": "XRP Ledger", "ref_usd": 0.58},
    "DOGE": {"name": "Dogecoin", "daily_rate": 0.00030, "min_borrow": 20.0, "collateral_ratio": 1.5, "chain": "Dogecoin / BSC", "ref_usd": 0.12},
    "SUI": {"name": "Sui", "daily_rate": 0.00035, "min_borrow": 5.0, "collateral_ratio": 1.5, "chain": "Sui Mainnet", "ref_usd": 1.95},
    "ADA": {"name": "Cardano", "daily_rate": 0.00020, "min_borrow": 10.0, "collateral_ratio": 1.4, "chain": "Cardano", "ref_usd": 0.38},
    "NEAR": {"name": "NEAR Protocol", "daily_rate": 0.00025, "min_borrow": 2.0, "collateral_ratio": 1.5, "chain": "NEAR", "ref_usd": 4.80},
    "AVAX": {"name": "Avalanche", "daily_rate": 0.00022, "min_borrow": 0.2, "collateral_ratio": 1.5, "chain": "Avalanche C-Chain", "ref_usd": 28.0},
    "ZIL": {"name": "Zilliqa", "daily_rate": 0.00025, "min_borrow": 200.0, "collateral_ratio": 1.4, "chain": "Zilliqa", "ref_usd": 0.016},
    "USDT": {"name": "Tether USD", "daily_rate": 0.00022, "min_borrow": 20.0, "collateral_ratio": 1.2, "chain": "TRC20 / BEP20", "ref_usd": 1.0}
}

def truncate_lot_size(amount, price):
    """Truncate coin quantity to real exchange decimal lot sizes"""
    if price > 50000:       # e.g. BTC, ETH (max 6 decimals)
        return round(amount, 6)
    elif price > 1000:      # e.g. SOL, BNB (max 4 decimals)
        return round(amount, 4)
    elif price > 10:        # e.g. XRP, ADA, NEAR (max 2 decimals)
        return round(amount, 2)
    elif price > 1:         # e.g. DOGE, STG (max 1 decimal)
        return round(amount, 1)
    else:                   # Sub-baht tokens like ZIL, SC, JFIN (integers or 0 decimals)
        return max(1.0, float(int(amount)))

def calculate_vwap_slippage(price, trade_val, book_depth, is_buy=True):
    """
    Calculate realistic Orderbook Depth Consumption (VWAP Slippage).
    Consuming deeper into the book increases Buy Ask and lowers Sell Bid.
    """
    depth = max(300.0, float(book_depth))
    # Depth consumption ratio
    impact_ratio = min(1.0, trade_val / depth)
    # Base taker impact 0.02% + up to 0.08% depth exhaustion
    slippage_pct = 0.0002 + (0.0008 * (impact_ratio ** 1.5))
    
    if is_buy:
        filled_price = price * (1.0 + slippage_pct)
    else:
        filled_price = price * (1.0 - slippage_pct)
        
    return filled_price, slippage_pct

def send_telegram_alert(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "8223980053:AAEB7EY61T55TjAhVDs7R5T-bzUzStzEpKY").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "822022855").strip()
    if not token or not chat_id:
        return
    def _send():
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                pass
        except Exception as e:
            print(f"[Telegram Alert Error] {e}")
    threading.Thread(target=_send, daemon=True).start()

class AutoTradeEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.enabled = True   # Automatically activate simulation so user can observe all day
        self.mode = "paper"   # Realistic Paper Trading
        
        # Strategy Parameters
        self.min_net_spread_pct = 0.01      # Minimum net profit after fees (+0.01% net per trade)
        self.trade_size_thb = 200.0         # Smallest viable trade size: ฿200 per order (~$5.9 USDT)
        self.max_daily_loss_thb = 500.0     # Strict circuit breaker: stop bot if total loss reaches ฿500
        self.max_consecutive_losses = 5     # Stop bot immediately if 5 consecutive losses occur
        self.consecutive_losses = 0         # Real-time consecutive loss counter
        self.cooldown_sec = 10.0            # Seconds between triggers on the same coin
        self.max_book_cap_pct = 85.0        # Order depth safety cap
        
        # Ultra-Fast Execution Delay Parameters (0.1 to 0.25 seconds)
        self.sim_delay_min_sec = 0.1
        self.sim_delay_max_sec = 0.25
        
        self.allowed_exchanges = ["bitkub", "binance_th", "binance_global", "orbix", "upbit"]
        self.allowed_coins = []             # Empty = All coins
        
        # Runtime State
        self.started_at = time.time()
        self.last_trade_time_per_coin = {}
        self.pending_orders = []            # Orders in queue awaiting 1-3s simulated delay
        self.trades = []
        self.terminal_logs = []
        self.daily_pnl_thb = 0.0
        self.total_volume_thb = 0.0
        self.fees_paid_thb = 0.0
        self.total_slippage_thb = 0.0
        self.total_triggered_count = 0
        self.filled_count = 0
        self.missed_count = 0
        self.rebalance_count = 0
        
        # Realistic Pre-Funded Balances across 5 exchanges (฿100,000 each + Coin seed)
        self.default_balances = {
            "bitkub": {"THB": 100000.0, "BTC": 0.05, "ETH": 0.5, "SOL": 5.0, "USDT": 1000.0, "XRP": 500.0, "ZENT": 10000.0, "ZIL": 10000.0},
            "binance_th": {"THB": 100000.0, "BTC": 0.05, "ETH": 0.5, "SOL": 5.0, "USDT": 1000.0, "XRP": 500.0, "ZENT": 10000.0, "ZIL": 10000.0},
            "binance_global": {"THB": 100000.0, "BTC": 0.05, "ETH": 0.5, "SOL": 5.0, "USDT": 1000.0, "XRP": 500.0, "ZENT": 10000.0, "ZIL": 10000.0},
            "orbix": {"THB": 100000.0, "BTC": 0.05, "ETH": 0.5, "SOL": 5.0, "USDT": 1000.0, "XRP": 500.0, "ZENT": 10000.0, "ZIL": 10000.0},
            "upbit": {"THB": 100000.0, "BTC": 0.05, "ETH": 0.5, "SOL": 5.0, "USDT": 1000.0, "XRP": 500.0, "ZENT": 10000.0, "ZIL": 10000.0}
        }
        self.balances = json.loads(json.dumps(self.default_balances))
        
        # Exchange API Keys (Real Only: Bitkub, Binance Global, Bybit, OKX)
        self.api_keys = {
            "bitkub": {"key": "", "secret": "", "connected": False},
            "binance_global": {"key": "", "secret": "", "connected": False},
            "bybit": {"key": "", "secret": "", "connected": False},
            "okx": {"key": "", "secret": "", "passphrase": "", "connected": False}
        }

        # Real Inventory & Balances
        self.auto_loan_enabled = False
        self.loans = []
        self.borrowable_assets = BORROWABLE_ASSETS
        self.real_balances = {}
        self.last_real_bal_refresh = 0.0

        self.verified_common_perps = set()
        try:
            if os.path.exists(COMMON_PERPS_FILE):
                with open(COMMON_PERPS_FILE, "r", encoding="utf-8") as f:
                    self.verified_common_perps = set(json.load(f))
        except Exception:
            pass

        self.load_keys()
        self.load_state()
        self.refresh_real_balances()
        threading.Thread(target=self.unwind_unhedged_bitkub_coins, daemon=True).start()
        self.log(f"🤖 Real Cross-Exchange Arbitrage Engine Online! ทุนไม้ละ: ฿{self.trade_size_thb:,.0f} | Delay: {self.sim_delay_min_sec:.1f}-{self.sim_delay_max_sec:.1f}s", "success")

    def unwind_unhedged_bitkub_coins(self):
        """Immediately sell back any unhedged coins in Bitkub to restore 100% THB cash."""
        time.sleep(3)
        bk = self.api_keys.get("bitkub", {})
        if not bk.get("key") or not bk.get("secret"):
            return
        real_coins = self.real_balances.get("bitkub", {}).get("coins", {})
        for sym, amt in list(real_coins.items()):
            if sym in ("THB", "USDT", "SAND", "MOVR") or amt <= 0:
                continue
            self.log(f"🔄 [AUTO-UNWIND CASH RESTORE] ขายคืน {amt:,.2f} {sym} ใน Bitkub เพื่อเปลี่ยนกลับเป็นเงินสด THB...", "warning")
            res = ExchangeAPIClient.place_bitkub_order(bk["key"], bk["secret"], sym, "SELL", coin_amount=amt)
            if res.get("success"):
                self.log(f"✅ [CASH RESTORED] ขาย {sym} สำเร็จ! ได้รับเงินสด THB คืนเข้ากระเป๋าเรียบร้อย", "success")
                send_telegram_alert(
                    f"💵 <b>[CASH RESTORED] ปิดเหรียญรับเงินสด Bitkub สำเร็จ!</b>\n\n"
                    f"🪙 เหรียญ: <b>{sym}</b> ({amt:,.2f})\n"
                    f"🟢 เปลี่ยนกลับเป็นเงินสด THB เข้ากระเป๋าเรียบร้อย 100%"
                )
            else:
                self.log(f"⚠️ [CASH RESTORE FAIL] {sym}: {res.get('message')}", "warning")
        self.refresh_real_balances()

    def refresh_real_balances(self):
        """Fetch actual balances directly from connected exchange APIs."""
        real = {}
        try:
            bk = self.api_keys.get("bitkub", {})
            if bk.get("connected") and bk.get("key") and bk.get("secret"):
                r = ExchangeAPIClient.test_bitkub(bk["key"], bk["secret"])
                if r.get("success"):
                    all_bals = r.get("balances", {})
                    thb_bal = all_bals.get("THB", {})
                    free_thb = thb_bal.get("free", 0.0) if isinstance(thb_bal, dict) else float(thb_bal or 0.0)
                    locked_thb = thb_bal.get("locked", 0.0) if isinstance(thb_bal, dict) else 0.0
                    coins_detail = {}
                    for sym, b in all_bals.items():
                        c_free = b.get("free", 0.0) if isinstance(b, dict) else float(b or 0.0)
                        if c_free > 0:
                            coins_detail[sym] = c_free

                    display_str = f"฿{free_thb:,.2f} THB"
                    if "PENDLE" in coins_detail:
                        display_str += f" | {coins_detail['PENDLE']:.2f} PENDLE"

                    real["bitkub"] = {
                        "connected": True,
                        "currency": "THB",
                        "free": free_thb,
                        "locked": locked_thb,
                        "total": free_thb + locked_thb,
                        "coins": coins_detail,
                        "display": display_str
                    }
        except Exception:
            pass

        try:
            bn = self.api_keys.get("binance_global", {})
            if bn.get("connected") and bn.get("key") and bn.get("secret"):
                r = ExchangeAPIClient.test_binance(bn["key"], bn["secret"], is_th=False)
                if r.get("success"):
                    all_m = r.get("margin_balances", {})
                    m_bal = all_m.get("USDT", {})
                    free_usdt = m_bal.get("free", 0.0) if isinstance(m_bal, dict) else float(m_bal or 0.0)
                    borrowed_assets = {}
                    for asset, b in all_m.items():
                        b_amt = b.get("borrowed", 0.0)
                        if b_amt > 0.0001:
                            borrowed_assets[asset] = {
                                "borrowed": b_amt,
                                "interest": b.get("interest", 0.0),
                                "net": b.get("net", 0.0)
                            }
                    disp = f"{free_usdt:,.2f} USDT"
                    if borrowed_assets:
                        disp += f" (หนี้: {', '.join(borrowed_assets.keys())})"
                    real["binance_global"] = {
                        "connected": True,
                        "currency": "USDT (Margin)",
                        "free": free_usdt,
                        "borrowed": m_bal.get("borrowed", 0.0),
                        "borrowed_assets": borrowed_assets,
                        "margin_level": r.get("margin_level", "999"),
                        "display": disp
                    }
        except Exception:
            pass

        try:
            bb = self.api_keys.get("bybit", {})
            if bb.get("connected") and bb.get("key") and bb.get("secret"):
                r = ExchangeAPIClient.test_bybit(bb["key"], bb["secret"])
                if r.get("success"):
                    tot_usd = r.get("total_equity_usd", 0.0)
                    real["bybit"] = {
                        "connected": True,
                        "currency": "USDT (Unified)",
                        "free": tot_usd,
                        "total": tot_usd,
                        "display": f"${tot_usd:,.2f} USDT"
                    }
        except Exception:
            pass

        try:
            ok = self.api_keys.get("okx", {})
            if ok.get("connected") and ok.get("key") and ok.get("secret") and ok.get("passphrase"):
                r = ExchangeAPIClient.test_okx(ok["key"], ok["secret"], ok["passphrase"])
                if r.get("success"):
                    tot_usd = r.get("total_equity_usd", 0.0)
                    real["okx"] = {
                        "connected": True,
                        "currency": "USDT (Trading)",
                        "free": tot_usd,
                        "total": tot_usd,
                        "display": f"${tot_usd:,.2f} USDT"
                    }
        except Exception:
            pass

        self.real_balances = real
        self.last_real_bal_refresh = time.time()
        return real

    def log(self, message, log_type="info"):
        now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = {
            "id": str(uuid.uuid4())[:8],
            "time": now_str,
            "message": message,
            "type": log_type # "info", "success", "warning", "error", "trade"
        }
        self.terminal_logs.append(entry)
        if len(self.terminal_logs) > 300:
            self.terminal_logs.pop(0)

    def load_keys(self):
        # 1. Environment variables (for Cloud / Render deployment)
        bk_key = os.environ.get("BITKUB_API_KEY", "")
        bk_sec = os.environ.get("BITKUB_API_SECRET", "")
        bn_key = os.environ.get("BINANCE_API_KEY", "")
        bn_sec = os.environ.get("BINANCE_API_SECRET", "")
        bb_key = os.environ.get("BYBIT_API_KEY", "")
        bb_sec = os.environ.get("BYBIT_API_SECRET", "")
        ok_key = os.environ.get("OKX_API_KEY", "")
        ok_sec = os.environ.get("OKX_API_SECRET", "")
        ok_pass = os.environ.get("OKX_PASSPHRASE", "")

        if bk_key and bk_sec:
            self.api_keys["bitkub"]["key"] = bk_key
            self.api_keys["bitkub"]["secret"] = bk_sec
            self.api_keys["bitkub"]["connected"] = True
        if bn_key and bn_sec:
            self.api_keys["binance_global"]["key"] = bn_key
            self.api_keys["binance_global"]["secret"] = bn_sec
            self.api_keys["binance_global"]["connected"] = True
        if bb_key and bb_sec:
            self.api_keys["bybit"]["key"] = bb_key
            self.api_keys["bybit"]["secret"] = bb_sec
            self.api_keys["bybit"]["connected"] = True
        if ok_key and ok_sec and ok_pass:
            self.api_keys["okx"]["key"] = ok_key
            self.api_keys["okx"]["secret"] = ok_sec
            self.api_keys["okx"]["passphrase"] = ok_pass
            self.api_keys["okx"]["connected"] = True

        try:
            if os.path.exists(KEYS_FILE):
                with open(KEYS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    for ex, v in saved.items():
                        if ex in self.api_keys and not self.api_keys[ex]["key"]:
                            self.api_keys[ex]["key"] = v.get("key", "")
                            self.api_keys[ex]["secret"] = v.get("secret", "")
                            if "passphrase" in self.api_keys[ex]:
                                self.api_keys[ex]["passphrase"] = v.get("passphrase", "")
                            conn = bool(v.get("key") and v.get("secret"))
                            if ex == "okx":
                                conn = conn and bool(v.get("passphrase"))
                            self.api_keys[ex]["connected"] = conn
        except Exception as e:
            print(f"Error loading keys: {e}")

    def save_keys(self):
        try:
            with open(KEYS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.api_keys, f, indent=2)
        except Exception as e:
            print(f"Error saving keys: {e}")

    def load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.min_net_spread_pct = data.get("min_net_spread_pct", self.min_net_spread_pct)
                    self.trade_size_thb = data.get("trade_size_thb", self.trade_size_thb)
                    self.max_daily_loss_thb = data.get("max_daily_loss_thb", self.max_daily_loss_thb)
                    self.max_consecutive_losses = data.get("max_consecutive_losses", 5)
                    self.consecutive_losses = data.get("consecutive_losses", 0)
                    self.cooldown_sec = data.get("cooldown_sec", self.cooldown_sec)
                    self.sim_delay_min_sec = data.get("sim_delay_min_sec", self.sim_delay_min_sec)
                    self.sim_delay_max_sec = data.get("sim_delay_max_sec", self.sim_delay_max_sec)
                    self.allowed_exchanges = data.get("allowed_exchanges", self.allowed_exchanges)
                    self.allowed_coins = data.get("allowed_coins", self.allowed_coins)
                    self.mode = data.get("mode", "paper")
                    self.auto_loan_enabled = data.get("auto_loan_enabled", True)
                    self.loans = data.get("loans", [])
                    if "balances" in data and isinstance(data["balances"], dict):
                        self.balances = data["balances"]
                    self.trades = data.get("trades", [])
                    self.daily_pnl_thb = data.get("daily_pnl_thb", 0.0)
                    self.total_volume_thb = data.get("total_volume_thb", 0.0)
                    self.fees_paid_thb = data.get("fees_paid_thb", 0.0)
                    self.total_slippage_thb = data.get("total_slippage_thb", 0.0)
                    self.total_triggered_count = data.get("total_triggered_count", len(self.trades))
                    self.filled_count = len([t for t in self.trades if t.get("status") == "FILLED"])
                    self.missed_count = len([t for t in self.trades if t.get("status") == "MISSED"])
                    self.rebalance_count = data.get("rebalance_count", 0)
        except Exception as e:
            print(f"Notice: Initialized fresh autotrade state ({e})")

    def save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                payload = {
                    "min_net_spread_pct": self.min_net_spread_pct,
                    "trade_size_thb": self.trade_size_thb,
                    "max_daily_loss_thb": self.max_daily_loss_thb,
                    "max_consecutive_losses": self.max_consecutive_losses,
                    "consecutive_losses": self.consecutive_losses,
                    "cooldown_sec": self.cooldown_sec,
                    "sim_delay_min_sec": self.sim_delay_min_sec,
                    "sim_delay_max_sec": self.sim_delay_max_sec,
                    "allowed_exchanges": self.allowed_exchanges,
                    "allowed_coins": self.allowed_coins,
                    "mode": self.mode,
                    "auto_loan_enabled": self.auto_loan_enabled,
                    "loans": self.loans[:100],
                    "balances": self.balances,
                    "trades": self.trades[-500:],
                    "daily_pnl_thb": self.daily_pnl_thb,
                    "total_volume_thb": self.total_volume_thb,
                    "fees_paid_thb": self.fees_paid_thb,
                    "total_slippage_thb": self.total_slippage_thb,
                    "total_triggered_count": self.total_triggered_count,
                    "rebalance_count": self.rebalance_count
                }
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving autotrade state: {e}")

    def toggle(self, state=None):
        with self.lock:
            if state is None:
                self.enabled = not self.enabled
            else:
                self.enabled = bool(state)
            
            if self.enabled:
                self.started_at = time.time()
                self.log(f"🟢 AUTO TRADE ACTIVATED! Mode: [PAPER]. ทุนไม้ละ: ฿{self.trade_size_thb:,.0f} | Min Spread: +{self.min_net_spread_pct:.2f}% | Latency Delay: {self.sim_delay_min_sec:.1f}-{self.sim_delay_max_sec:.1f}s", "success")
            else:
                self.log("🔴 Auto Trade Bot Paused / Stopped by user.", "warning")
            return self.enabled

    def update_config(self, cfg):
        with self.lock:
            if "min_net_spread_pct" in cfg:
                self.min_net_spread_pct = max(0.001, float(cfg["min_net_spread_pct"]))
            if "trade_size_thb" in cfg:
                self.trade_size_thb = max(50.0, float(cfg["trade_size_thb"]))
            if "max_daily_loss_thb" in cfg:
                self.max_daily_loss_thb = max(100.0, float(cfg["max_daily_loss_thb"]))
            if "max_consecutive_losses" in cfg:
                self.max_consecutive_losses = max(1, int(cfg["max_consecutive_losses"]))
            if "cooldown_sec" in cfg:
                self.cooldown_sec = max(1.0, float(cfg["cooldown_sec"]))
            if "sim_delay_min_sec" in cfg:
                self.sim_delay_min_sec = max(0.2, float(cfg["sim_delay_min_sec"]))
            if "sim_delay_max_sec" in cfg:
                self.sim_delay_max_sec = max(self.sim_delay_min_sec, float(cfg["sim_delay_max_sec"]))
            if "mode" in cfg and cfg["mode"] in ("paper", "live"):
                self.mode = cfg["mode"]
            if "allowed_exchanges" in cfg and isinstance(cfg["allowed_exchanges"], list):
                if self.mode == "live":
                    connected = [ex for ex, k in self.api_keys.items() if k.get("connected") and k.get("key")]
                    filtered = [ex for ex in cfg["allowed_exchanges"] if ex in connected]
                    self.allowed_exchanges = filtered if filtered else connected
                else:
                    self.allowed_exchanges = cfg["allowed_exchanges"]
            if "allowed_coins" in cfg and isinstance(cfg["allowed_coins"], list):
                self.allowed_coins = [c.upper().strip() for c in cfg["allowed_coins"] if c.strip()]
            self.save_state()
            self.log(f"⚙️ ปรับแต่งค่ากลยุทธ์: ทุน ฿{self.trade_size_thb:,.0f} | Min Spread +{self.min_net_spread_pct:.2f}% | ดีเลย์ {self.sim_delay_min_sec:.1f}-{self.sim_delay_max_sec:.1f}s", "info")
            return self.get_status()

    def reset_paper_balances(self):
        with self.lock:
            self.balances = json.loads(json.dumps(self.default_balances))
            self.daily_pnl_thb = 0.0
            self.total_volume_thb = 0.0
            self.fees_paid_thb = 0.0
            self.total_slippage_thb = 0.0
            self.trades = []
            self.pending_orders = []
            self.total_triggered_count = 0
            self.filled_count = 0
            self.missed_count = 0
            self.rebalance_count = 0
            self.save_state()
            self.log("🔄 รีเซ็ตพอร์ตโฟลิโอจำลองและประวัติการเทรดเริ่มต้นใหม่ (฿100,000 ต่อกระดาน)", "warning")
            return {"status": "ok"}

    def set_api_keys(self, exchange, key, secret, passphrase=None):
        with self.lock:
            if exchange in self.api_keys:
                self.api_keys[exchange]["key"] = key.strip()
                self.api_keys[exchange]["secret"] = secret.strip()
                if passphrase is not None:
                    self.api_keys[exchange]["passphrase"] = passphrase.strip()
                conn = bool(key.strip() and secret.strip())
                if exchange == "okx":
                    conn = conn and bool(self.api_keys[exchange].get("passphrase"))
                self.api_keys[exchange]["connected"] = conn
                self.save_keys()
                self.log(f"🔑 บันทึก API Key สำหรับ {exchange.upper()} (Connected: {conn})", "info")
                return {"status": "ok", "connected": conn}
            return {"status": "error", "message": "Unknown exchange"}

    def set_mode(self, mode):
        with self.lock:
            if mode in ("paper", "dry_run", "live"):
                self.mode = mode
                self.save_state()
                mode_names = {"paper": "Simulation (Paper)", "dry_run": "Dry-Run (Live API Test)", "live": "Live Real Trade (เทรดจริง)"}
                self.log(f"🔄 ปรับเปลี่ยนโหมดการเทรดเป็น: [{mode_names.get(mode, mode)}]", "warning" if mode == "live" else "info")
                return {"status": "ok", "mode": self.mode}
            return {"status": "error", "message": "Invalid mode"}

    def toggle_auto_loan(self, state=None):
        with self.lock:
            if state is None:
                self.auto_loan_enabled = not self.auto_loan_enabled
            else:
                self.auto_loan_enabled = bool(state)
            self.save_state()
            status_text = "เปิดใช้งาน (Active)" if self.auto_loan_enabled else "ปิดการใช้งาน (Disabled)"
            self.log(f"🏦 ระบบ Auto-Loan & Cross-Transfer: {status_text}", "info")
            return {"status": "ok", "auto_loan_enabled": self.auto_loan_enabled}

    def test_exchange_api(self, exchange, key=None, secret=None, passphrase=None):
        with self.lock:
            saved = self.api_keys.get(exchange, {})
            api_k = key.strip() if (key and key.strip()) else saved.get("key", "")
            api_s = secret.strip() if (secret and secret.strip()) else saved.get("secret", "")
            api_p = passphrase.strip() if (passphrase and passphrase.strip()) else saved.get("passphrase", "")

            if exchange == "binance_global":
                res = ExchangeAPIClient.test_binance(api_k, api_s, is_th=False)
            elif exchange == "bitkub":
                res = ExchangeAPIClient.test_bitkub(api_k, api_s)
            elif exchange == "bybit":
                res = ExchangeAPIClient.test_bybit(api_k, api_s)
            elif exchange == "okx":
                res = ExchangeAPIClient.test_okx(api_k, api_s, api_p)
            else:
                res = ExchangeAPIClient.test_generic_exchange(exchange, api_k, api_s)

            if res.get("success"):
                if exchange in self.api_keys:
                    self.api_keys[exchange]["key"] = api_k
                    self.api_keys[exchange]["secret"] = api_s
                    if "passphrase" in self.api_keys[exchange]:
                        self.api_keys[exchange]["passphrase"] = api_p
                    self.api_keys[exchange]["connected"] = True
                    self.save_keys()
                self.log(f"✅ ทดสอบเชื่อมต่อ {exchange.upper()} สำเร็จ: {res.get('message', '')}", "success")
                self.refresh_real_balances()
            else:
                self.log(f"❌ ทดสอบเชื่อมต่อ {exchange.upper()} ไม่สำเร็จ: {res.get('message', '')}", "error")
            return res

    def borrow_coin(self, coin, amount, borrow_source="binance_global", target_exchange="bitkub", collateral_usdt=None, auto=False):
        with self.lock:
            coin = coin.upper()
            asset_info = self.borrowable_assets.get(coin, {
                "name": coin, "daily_rate": 0.00025, "collateral_ratio": 1.5, "chain": "Direct", "ref_usd": 1.0
            })
            approx_usd_price = asset_info.get("ref_usd", 1.0)
            loan_val_usd = amount * approx_usd_price
            needed_collateral = round(loan_val_usd * asset_info.get("collateral_ratio", 1.5), 2)
            if collateral_usdt is not None and float(collateral_usdt) > 0:
                needed_collateral = float(collateral_usdt)

            src_bal = self.balances.setdefault(borrow_source, {})
            current_usdt = src_bal.get("USDT", 0.0)
            if current_usdt < needed_collateral:
                src_bal["USDT"] = current_usdt + (needed_collateral * 2.0)
                current_usdt = src_bal["USDT"]

            src_bal["USDT"] = max(0.0, current_usdt - needed_collateral)

            tgt_bal = self.balances.setdefault(target_exchange, {})
            tgt_bal[coin] = tgt_bal.get(coin, 0.0) + amount

            loan_id = "LN-" + str(uuid.uuid4())[:6].upper()
            now_t = time.time()
            daily_pct = asset_info.get("daily_rate", 0.00025) * 100.0

            loan_record = {
                "id": loan_id,
                "coin": coin,
                "coin_name": asset_info.get("name", coin),
                "amount": round(amount, 6),
                "borrow_source": borrow_source,
                "target_exchange": target_exchange,
                "collateral_asset": "USDT",
                "collateral_amount": needed_collateral,
                "borrow_time": now_t,
                "borrow_time_str": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "daily_interest_rate_pct": round(daily_pct, 4),
                "hourly_rate_pct": round(daily_pct / 24.0, 5),
                "approx_usd_price": approx_usd_price,
                "status": "ACTIVE",
                "accrued_interest_thb": 0.0,
                "auto_triggered": auto
            }
            self.loans.insert(0, loan_record)
            self.save_state()

            ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}
            tag = "⚡ [AUTO-LOAN TRIGGERED]" if auto else "🏦 [CRYPTO LOAN CREATED]"
            self.log(
                f"{tag} กู้ยืม {amount:,.4f} {coin} จาก {ex_names.get(borrow_source, borrow_source)} "
                f"(ค้ำ {needed_collateral:,.2f} USDT @ ดอกเบี้ย {daily_pct:.3f}%/วัน) ➔ โยกไปวางที่ {ex_names.get(target_exchange, target_exchange)} สำเร็จ!",
                "success"
            )
            return {"status": "ok", "loan": loan_record}

    def repay_coin(self, loan_id):
        with self.lock:
            loan = next((l for l in self.loans if l["id"] == loan_id and l["status"] == "ACTIVE"), None)
            if not loan:
                return {"status": "error", "message": "ไม่พบรายการกู้ยืมหรือรายการนี้ถูกชำระคืนแล้ว"}

            coin = loan["coin"]
            amount = loan["amount"]
            src_ex = loan["borrow_source"]
            tgt_ex = loan["target_exchange"]
            collateral = loan["collateral_amount"]

            elapsed_hours = max(0.05, (time.time() - loan["borrow_time"]) / 3600.0)
            daily_rate = loan["daily_interest_rate_pct"] / 100.0
            interest_pct = daily_rate * (elapsed_hours / 24.0)
            coin_val_thb = amount * loan["approx_usd_price"] * 33.54
            interest_thb = round(coin_val_thb * interest_pct, 2)

            src_bal = self.balances.setdefault(src_ex, {})
            src_bal["USDT"] = src_bal.get("USDT", 0.0) + collateral

            tgt_bal = self.balances.setdefault(tgt_ex, {})
            current_coin = tgt_bal.get(coin, 0.0)
            tgt_bal[coin] = max(0.0, current_coin - amount)

            self.daily_pnl_thb -= interest_thb
            self.fees_paid_thb += interest_thb

            loan["status"] = "REPAID"
            loan["repaid_time"] = time.time()
            loan["repaid_time_str"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            loan["accrued_interest_thb"] = interest_thb
            loan["elapsed_hours"] = round(elapsed_hours, 2)

            self.save_state()
            ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}
            self.log(
                f"✅ [LOAN REPAID] คืนเหรียญ {amount:,.4f} {coin} ให้ {ex_names.get(src_ex, src_ex)} เรียบร้อย! "
                f"ปลดล็อคหลักประกัน {collateral:,.2f} USDT คืนเข้าพอร์ต (ดอกเบี้ยสะสมที่จ่าย: ฿{interest_thb:,.2f} ใน {elapsed_hours:.1f} ชม.)",
                "info"
            )
            return {"status": "ok", "interest_thb": interest_thb, "loan": loan}

    def get_loans(self):
        with self.lock:
            now_t = time.time()
            total_accrued = 0.0
            active_borrowed_val_thb = 0.0
            active_collateral_usdt = 0.0
            active_count = 0

            for l in self.loans:
                if l["status"] == "ACTIVE":
                    active_count += 1
                    elapsed_hours = max(0.05, (now_t - l["borrow_time"]) / 3600.0)
                    daily_rate = l["daily_interest_rate_pct"] / 100.0
                    interest_pct = daily_rate * (elapsed_hours / 24.0)
                    approx_usd = l.get("approx_usd_price", 1.0)
                    val_thb = l["amount"] * approx_usd * 33.54
                    interest_thb = round(val_thb * interest_pct, 2)
                    l["accrued_interest_thb"] = interest_thb
                    l["elapsed_hours"] = round(elapsed_hours, 2)
                    total_accrued += interest_thb
                    active_borrowed_val_thb += val_thb
                    active_collateral_usdt += l.get("collateral_amount", 0.0)

            return {
                "auto_loan_enabled": self.auto_loan_enabled,
                "borrowable_assets": self.borrowable_assets,
                "active_count": active_count,
                "total_accrued_interest_thb": round(total_accrued, 2),
                "active_borrowed_val_thb": round(active_borrowed_val_thb, 2),
                "active_collateral_usdt": round(active_collateral_usdt, 2),
                "loans": self.loans[:100]
            }

    def check_and_rebalance_inventory(self, buy_ex, trade_val_thb):
        """
        Realistic Inventory Balancing:
        If Buy Exchange THB balance drops below required order capital,
        automatically simulate an on-chain transfer from the most liquid exchange.
        """
        ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}
        buy_bal = self.balances.get(buy_ex, {})
        current_thb = buy_bal.get("THB", 0.0)

        if current_thb < trade_val_thb:
            # Find the exchange with the highest THB balance
            wealthiest_ex = max(self.balances.keys(), key=lambda k: self.balances[k].get("THB", 0.0))
            if wealthiest_ex != buy_ex and self.balances[wealthiest_ex].get("THB", 0.0) > (trade_val_thb * 3):
                rebal_amount = 20000.0  # transfer batch
                self.balances[wealthiest_ex]["THB"] -= rebal_amount
                # Deduct realistic bank/exchange transfer fee (20 THB)
                buy_bal["THB"] = current_thb + (rebal_amount - 20.0)
                self.rebalance_count += 1
                self.fees_paid_thb += 20.0
                self.log(
                    f"🔄 [AUTO REBALANCE] ยอด THB ใน {ex_names.get(buy_ex)} ไม่พอ (เหลือ ฿{current_thb:,.0f}) "
                    f"โอนเติมเงิน ฿{rebal_amount:,.0f} จาก {ex_names.get(wealthiest_ex)} (หัก Fee โอน ฿20)",
                    "warning"
                )
                return True
            else:
                return False
        return True

    def on_market_tick(self, processed_coins):
        """Called on every market data tick from server.py (approx every 350-450ms)"""
        now = time.time()
        ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}
        coin_map = {c["symbol"]: c for c in processed_coins}

        # Periodic non-blocking refresh of live real wallet balances every 120 seconds (2 mins)
        # Keeps request weight low to strictly prevent Binance HTTP 418 / 429 rate limit bans
        if (now - getattr(self, "last_real_bal_refresh", 0)) > 120.0:
            self.last_real_bal_refresh = now
            threading.Thread(target=self.refresh_real_balances, daemon=True).start()

        # Hourly Heartbeat disabled as requested - Alert ONLY on real trade execution

        with self.lock:
            # =========================================================================
            # PHASE 1: Process Ready Pending Orders (Simulated 1 - 3s Delay Completed)
            # =========================================================================
            ready_orders = [p for p in self.pending_orders if now >= p["execute_time"]]
            for p in ready_orders:
                self.pending_orders.remove(p)
                sym = p["coin"]
                buy_ex = p["buy_ex"]
                sell_ex = p["sell_ex"]
                delay_actual = round(now - p["trigger_time"], 2)

                current_coin = coin_map.get(sym)

                if current_coin:
                    # Look up the specific route for this pending order
                    routes = current_coin.get("real_routes", [])
                    cur_route = next((r for r in routes if r.get("buy_ex") == buy_ex and r.get("sell_ex") == sell_ex), None)
                    if not cur_route:
                        # Fallback to direct book calculation from coin meta
                        b_ask = current_coin.get("meta", {}).get(buy_ex, {}).get("ask", 0)
                        s_bid = current_coin.get("meta", {}).get(sell_ex, {}).get("bid", 0)
                        if b_ask > 0 and s_bid > 0:
                            s_pct = (s_bid - b_ask) / b_ask * 100.0
                            cur_route = {
                                "buy_ex": buy_ex,
                                "sell_ex": sell_ex,
                                "buy_price": b_ask,
                                "sell_price": s_bid,
                                "spread_pct": s_pct,
                                "max_capacity_thb": p.get("initial_depth", 10000)
                            }

                    if cur_route:
                        cur_spread = cur_route.get("spread_pct", 0)
                        cur_depth = cur_route.get("max_capacity_thb", p.get("initial_depth", 10000))

                        fee_rate_buy = p["fee_rate_buy"]
                        fee_rate_sell = p["fee_rate_sell"]
                        total_fee_pct = (fee_rate_buy + fee_rate_sell) * 100.0
                        cur_net_spread = cur_spread - total_fee_pct

                        # Check if the route is still valid and still profitable (> 0)
                        if cur_net_spread > 0.0:
                            cur_buy_p = cur_route.get("buy_price", p["initial_buy_price"])
                            cur_sell_p = cur_route.get("sell_price", p["initial_sell_price"])

                            # Check inventory balance
                            has_funds = self.check_and_rebalance_inventory(buy_ex, p["trade_val_thb"])
                            if not has_funds:
                                self.record_missed_trade(
                                    p=p,
                                    reason="INVENTORY_DEPLETED",
                                    reason_th=f"เงินสด THB ในกระดาน {ex_names.get(buy_ex)} ไม่เพียงพอ",
                                    final_spread=cur_net_spread,
                                    delay_actual=delay_actual
                                )
                                continue

                            self.execute_ultra_realistic_fill(
                                p=p,
                                base_buy_price=cur_buy_p,
                                base_sell_price=cur_sell_p,
                                book_depth=cur_depth,
                                raw_net_spread=cur_net_spread,
                                raw_gross_spread=cur_spread,
                                delay_actual=delay_actual
                            )
                        else:
                            # Spread decayed below break-even during the 1-3s delay!
                            self.record_missed_trade(
                                p=p,
                                reason="SLIPPAGE_DECAY",
                                reason_th="สเปรดหุบต่ำกว่าต้นทุนค่าธรรมเนียมก่อนหมดดีเลย์",
                                final_spread=cur_net_spread,
                                delay_actual=delay_actual
                            )
                    else:
                        # Price shifted / Route flipped
                        self.record_missed_trade(
                            p=p,
                            reason="ROUTE_FLIPPED",
                            reason_th="ออเดอร์ใน Orderbook เปลี่ยนฝั่งระหว่างรอดีเลย์ 1-3 วิ",
                            final_spread=0.0,
                            delay_actual=delay_actual
                        )
                else:
                    self.record_missed_trade(
                        p=p,
                        reason="VANISHED",
                        reason_th="ออเดอร์ใน Orderbook ถูกช้อนหมดก่อนดีเลย์เสร็จสิ้น",
                        final_spread=0.0,
                        delay_actual=delay_actual
                    )

            if not self.enabled:
                return

            # =========================================================================
            # PHASE 1.5: Auto-Unwind Monitor for Hedged Positions (e.g. PENDLE, QI)
            # When Bitkub price rises and reverse spread is profitable (> 0.05%), close both legs into cash!
            # =========================================================================
            if self.mode == "live":
                bk_keys = self.api_keys.get("bitkub", {})
                bn_keys = self.api_keys.get("binance_global", {})
                if bk_keys.get("key") and bk_keys.get("secret") and bn_keys.get("key") and bn_keys.get("secret"):
                    bk_coins = self.real_balances.get("bitkub", {}).get("coins", {})
                    for hedged_sym, hedged_amt in bk_coins.items():
                        if hedged_sym in ("THB", "USDT") or hedged_amt <= 0:
                            continue
                        last_unwind_attempt = getattr(self, "last_unwind_time", {}).get(hedged_sym, 0)
                        if (now - last_unwind_attempt) < 15.0:
                            continue

                        target_coin_data = coin_map.get(hedged_sym)
                        if not target_coin_data:
                            continue

                        bk_bid = target_coin_data.get("meta", {}).get("bitkub", {}).get("bid", 0)
                        bn_ask = target_coin_data.get("meta", {}).get("binance_global", {}).get("ask", 0)

                        if bk_bid > 0 and bn_ask > 0:
                            unwind_val_thb = hedged_amt * bk_bid
                            # Binance Cross Margin minNotional is $5.00 USD (~175 THB). Skip dust amounts.
                            if unwind_val_thb < 180.0:
                                continue

                            gross_unwind = (bk_bid - bn_ask) / bn_ask * 100.0
                            net_unwind = gross_unwind - 0.35  # 0.25% BK fee + 0.10% BN fee

                            if net_unwind >= 0.05:
                                if not hasattr(self, "last_unwind_time"):
                                    self.last_unwind_time = {}
                                self.last_unwind_time[hedged_sym] = now

                                self.log(
                                    f"🎯 [AUTO-UNWIND TRIGGERED] {hedged_sym} สเปรดพลิกกลับมากำไร +{net_unwind:.2f}% "
                                    f"(Bid Bitkub ฿{bk_bid:,.2f} > Ask Binance ฿{bn_ask:,.2f}) ➔ ปิดสถานะทำกำไรเข้ากระเป๋า!",
                                    "success"
                                )
                                bk_sell_res = ExchangeAPIClient.place_bitkub_order(
                                    bk_keys["key"], bk_keys["secret"], hedged_sym, "SELL", coin_amount=hedged_amt
                                )
                                bn_buy_res = ExchangeAPIClient.place_binance_margin_order(
                                    bn_keys["key"], bn_keys["secret"], hedged_sym, "BUY", quantity=hedged_amt
                                )
                                if bk_sell_res.get("success") and bn_buy_res.get("success"):
                                    self.log(
                                        f"🎉 [UNWIND SUCCESS] ปิดสถานะ {hedged_sym} สำเร็จทั้ง 2 ฝั่ง! รับเงินบาทและ USDT คืนเข้าพอร์ตพร้อมกำไร",
                                        "success"
                                    )
                                    send_telegram_alert(
                                        f"💎 <b>[AUTO-UNWIND SUCCESS] ปิดสถานะรับเงินสดสำเร็จ!</b>\n\n"
                                        f"🪙 เหรียญ: <b>{hedged_sym}</b> ({hedged_amt})\n"
                                        f"📈 สเปรดพลิกกลับมา: +{net_unwind:.2f}%\n"
                                        f"💵 สถานะ: ปิดหนี้ Margin + รับเงินสด THB เข้าพอร์ตแล้ว 100% 🟢"
                                    )
                                    threading.Thread(target=self.refresh_real_balances, daemon=True).start()
                                else:
                                    self.log(
                                        f"⚠️ [UNWIND PARTIAL] BK: {bk_sell_res.get('message')} | BN: {bn_buy_res.get('message')}",
                                        "warning"
                                    )

            # =========================================================================
            # PHASE 2: Detect New Opportunities and Queue with Realistic Jitter Latency
            # =========================================================================
            pending_coins = {p["coin"] for p in self.pending_orders}

            for coin in processed_coins:
                sym = coin["symbol"]
                if sym in pending_coins:
                    continue

                if self.allowed_coins and sym not in self.allowed_coins:
                    continue

                # Search through all available routes on this coin between connected/allowed exchanges
                routes = coin.get("real_routes", [])
                if not routes:
                    exec_data = coin.get("execution", {})
                    if exec_data.get("valid"):
                        routes = [{
                            "buy_ex": exec_data.get("real_buy_ex"),
                            "sell_ex": exec_data.get("real_sell_ex"),
                            "spread_pct": exec_data.get("real_spread_pct", 0),
                            "buy_price": exec_data.get("real_buy_price", 0),
                            "sell_price": exec_data.get("real_sell_price", 0),
                            "max_capacity_thb": exec_data.get("max_capacity_thb", 0)
                        }]

                connected_exchanges = [ex for ex, k in self.api_keys.items() if k.get("connected") and k.get("key")] if self.mode == "live" else self.allowed_exchanges
                valid_routes = []

                for r in routes:
                    b_ex = r.get("buy_ex")
                    s_ex = r.get("sell_ex")
                    if b_ex not in connected_exchanges or s_ex not in connected_exchanges:
                        continue
                    if self.mode == "live":
                        # Only require Binance Margin check if trading with Binance
                        if "binance_global" in (b_ex, s_ex):
                            if not ExchangeAPIClient.is_binance_margin(sym):
                                continue
                        # Ensure Bybit / OKX only trade verified common perpetual contracts
                        if s_ex in ("bybit", "okx"):
                            if sym not in self.verified_common_perps:
                                held = self.real_balances.get(s_ex, {}).get("coins", {}).get(sym, 0.0)
                                if held <= 0:
                                    continue
                        if b_ex in ("bybit", "okx") and s_ex == "bitkub":
                            held_bk = self.real_balances.get("bitkub", {}).get("coins", {}).get(sym, 0.0)
                            if held_bk <= 0:
                                continue
                    f_buy = 0.0005 if b_ex in ("bybit", "okx") else (0.001 if "binance" in b_ex else 0.0025)
                    f_sell = 0.0005 if s_ex in ("bybit", "okx") else (0.001 if "binance" in s_ex else 0.0025)
                    tot_fee = (f_buy + f_sell) * 100.0
                    n_spread = r.get("spread_pct", 0) - tot_fee
                    if n_spread >= self.min_net_spread_pct and r.get("buy_price", 0) > 0 and r.get("sell_price", 0) > 0:
                        valid_routes.append((r, n_spread))

                if not valid_routes:
                    continue

                best_route, net_spread = max(valid_routes, key=lambda x: x[1])
                buy_ex = best_route["buy_ex"]
                sell_ex = best_route["sell_ex"]
                real_spread = best_route["spread_pct"]
                fee_rate_buy = 0.0005 if buy_ex in ("bybit", "okx") else (0.001 if "binance" in buy_ex else 0.0025)
                fee_rate_sell = 0.0005 if sell_ex in ("bybit", "okx") else (0.001 if "binance" in sell_ex else 0.0025)

                last_traded = self.last_trade_time_per_coin.get(sym, 0)
                if (now - last_traded) < self.cooldown_sec:
                    continue

                # Dynamic trade sizing:
                # Base size: self.trade_size_thb (฿500)
                # If net_spread >= 1.5%: Scale up to maximum safe capacity / available balance ("ใส่เต็มที่เลย")
                target_size = self.trade_size_thb
                is_boosted = False
                if net_spread >= 1.5:
                    is_boosted = True
                    if self.mode == "live":
                        bk_free = self.real_balances.get("bitkub", {}).get("free", 0.0)
                        # Use up to 98% of free Bitkub THB (leaving 2% buffer for fees/dust, min 500 THB)
                        target_size = max(self.trade_size_thb, bk_free * 0.98)
                    else:
                        paper_free = self.balances.get(buy_ex, {}).get("THB", 5000.0)
                        target_size = max(self.trade_size_thb, min(5000.0, paper_free * 0.5))

                max_cap = best_route.get("max_capacity_thb", 0)
                trade_val = min(target_size, max_cap * (self.max_book_cap_pct / 100.0))
                if trade_val < 50.0:
                    continue

                buy_price = best_route.get("buy_price", 0)
                sell_price = best_route.get("sell_price", 0)
                if buy_price <= 0 or sell_price <= 0 or sell_price <= buy_price:
                    continue

                # Ultra-Fast Execution Latency:
                # Live mode: 0.10s - 0.25s (Next market tick debounce - fastest possible without reacting to 1ms glitches)
                # Paper mode: configured sim_delay_min_sec to sim_delay_max_sec
                if self.mode == "live":
                    delay_sec = round(random.uniform(0.10, 0.25), 2)
                else:
                    delay_sec = round(random.uniform(self.sim_delay_min_sec, self.sim_delay_max_sec), 2)

                meta_buy = coin.get("meta", {}).get(buy_ex, {})
                meta_sell = coin.get("meta", {}).get(sell_ex, {})
                is_direct_thb = meta_buy.get("direct_thb", True) and meta_sell.get("direct_thb", True)
                pair_type = "DIRECT_THB" if is_direct_thb else "SYNTHETIC_USDT"

                order_id = str(uuid.uuid4())[:8]
                pending_item = {
                    "order_id": order_id,
                    "coin": sym,
                    "buy_ex": buy_ex,
                    "sell_ex": sell_ex,
                    "trigger_time": now,
                    "execute_time": now + delay_sec,
                    "delay_sec": delay_sec,
                    "pair_type": pair_type,
                    "is_direct_thb": is_direct_thb,
                    "initial_spread": round(net_spread, 3),
                    "initial_gross": round(real_spread, 3),
                    "initial_buy_price": buy_price,
                    "initial_sell_price": sell_price,
                    "initial_depth": max_cap,
                    "trade_val_thb": round(trade_val, 2),
                    "fee_rate_buy": fee_rate_buy,
                    "fee_rate_sell": fee_rate_sell,
                    "is_boosted": is_boosted
                }
                self.pending_orders.append(pending_item)
                self.last_trade_time_per_coin[sym] = now + delay_sec + self.cooldown_sec
                self.total_triggered_count += 1

                boost_msg = f" 🚀 [SPREAD BOOST >= 1.5% ขยายไม้เต็มพิกัด ฿{trade_val:,.0f}!]" if is_boosted else ""
                self.log(
                    f"⏳ [TRIGGERED] {sym} พบ Spread +{net_spread:.2f}% (ซื้อ {ex_names.get(buy_ex)} ➔ ขาย {ex_names.get(sell_ex)}) | "
                    f"ทุน ฿{trade_val:,.0f}{boost_msg} | หน่วงส่งคำสั่ง {delay_sec:.2f}s...",
                    "success" if is_boosted else "info"
                )

    def _dispatch_real_order(self, exchange, coin, side, trade_val_thb, coin_amount):
        """Execute real order on specific exchange with appropriate market/instrument routing"""
        k = self.api_keys.get(exchange, {})
        if not k.get("connected") or not k.get("key"):
            return {"success": False, "message": f"{exchange} API key not connected"}
        
        amount_usdt = round(trade_val_thb / 33.54, 2)

        if exchange == "bitkub":
            if side.upper() == "BUY":
                return ExchangeAPIClient.place_bitkub_order(k["key"], k["secret"], coin, "BUY", amount_thb=trade_val_thb)
            else:
                return ExchangeAPIClient.place_bitkub_order(k["key"], k["secret"], coin, "SELL", coin_amount=coin_amount)

        elif exchange == "binance_global":
            return ExchangeAPIClient.place_binance_margin_order(k["key"], k["secret"], coin, side, quantity=coin_amount)

        elif exchange == "bybit":
            # If side is SELL and user doesn't hold spot coin, hedge via linear perpetual short
            is_perp = False
            if side.upper() == "SELL":
                held = self.real_balances.get("bybit", {}).get("coins", {}).get(coin.upper(), 0.0)
                if held < coin_amount:
                    is_perp = True
            return ExchangeAPIClient.place_bybit_order(
                k["key"], k["secret"], coin, side, amount_usdt=amount_usdt, coin_amount=coin_amount, is_perp=is_perp
            )

        elif exchange == "okx":
            is_perp = False
            if side.upper() == "SELL":
                held = self.real_balances.get("okx", {}).get("coins", {}).get(coin.upper(), 0.0)
                if held < coin_amount:
                    is_perp = True
            return ExchangeAPIClient.place_okx_order(
                k["key"], k["secret"], k.get("passphrase", ""), coin, side, amount_usdt=amount_usdt, coin_amount=coin_amount, is_perp=is_perp
            )

        return {"success": False, "message": f"Unsupported exchange: {exchange}"}

    def execute_ultra_realistic_fill(self, p, base_buy_price, base_sell_price, book_depth, raw_net_spread, raw_gross_spread, delay_actual):
        """
        Ultra-Realistic Execution Model:
        1. VWAP Book Depth Slippage on both legs
        2. Decimal lot size truncation & cash dust
        3. Dual-Leg Asymmetric Latency & Legging Risk (Partial Fill / Tick Shift)
        4. Realistic Fee Deduction
        """
        trade_id = p["order_id"]
        trade_val_thb = p["trade_val_thb"]
        buy_ex = p["buy_ex"]
        sell_ex = p["sell_ex"]
        coin = p["coin"]
        ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit", "bybit": "Bybit", "okx": "OKX"}

        # 1. VWAP Slippage calculation
        filled_buy_price, buy_slip_pct = calculate_vwap_slippage(base_buy_price, trade_val_thb, book_depth, is_buy=True)
        filled_sell_price, sell_slip_pct = calculate_vwap_slippage(base_sell_price, trade_val_thb, book_depth, is_buy=False)

        # 2. Dual-Legging Risk simulation (Asymmetric fill)
        leg1_latency_ms = random.randint(35, 85)
        leg2_latency_ms = random.randint(110, 260)
        leg2_roll = random.random()
        execution_note = "100% MATCH"

        if leg2_roll < 0.05:
            # 5% chance: Leg 2 experiences queue delay and fills 1 tick lower (-0.05%)
            filled_sell_price *= 0.9995
            execution_note = "1-TICK SLIP"
        elif leg2_roll < 0.07:
            # 2% chance: Partial fill 85%
            trade_val_thb *= 0.85
            execution_note = "PARTIAL 85%"

        # 3. Truncate coin amount to real exchange decimal lot size
        buy_fee_thb = trade_val_thb * p["fee_rate_buy"]
        net_buy_thb = trade_val_thb - buy_fee_thb
        raw_coin_amount = net_buy_thb / filled_buy_price
        coin_amount = truncate_lot_size(raw_coin_amount, filled_buy_price)

        # 4. Sell Leg execution
        gross_sell_thb = coin_amount * filled_sell_price
        sell_fee_thb = gross_sell_thb * p["fee_rate_sell"]
        net_sell_thb = gross_sell_thb - sell_fee_thb

        total_fees = buy_fee_thb + sell_fee_thb
        net_profit_thb = net_sell_thb - trade_val_thb
        actual_roi_pct = (net_profit_thb / trade_val_thb) * 100.0

        # Slippage monetary impact compared to theoretical top-of-book
        theoretical_profit = (trade_val_thb * (1.0 - p["fee_rate_buy"])) / base_buy_price * base_sell_price * (1.0 - p["fee_rate_sell"]) - trade_val_thb
        slippage_cost_thb = max(0.0, theoretical_profit - net_profit_thb)
        self.total_slippage_thb += slippage_cost_thb

        # 5. Live Execution vs Paper Balance Update
        live_executed = False
        if self.mode == "live":
            # Safety check: Binance borrowable check if selling on Binance
            if sell_ex == "binance_global":
                bn_keys = self.api_keys.get("binance_global", {})
                can_borrow, max_avail, borrow_reason = ExchangeAPIClient.check_binance_borrowable(
                    bn_keys.get("key", ""), bn_keys.get("secret", ""), coin, min_amount=coin_amount
                )
                if not can_borrow:
                    self.log(
                        f"🛡️ [PRE-FLIGHT BLOCKED] ระงับการเทรด {coin}: Binance ปฏิเสธการกู้เหรียญ ({borrow_reason}) ➔ ระบบไม่ส่งคำสั่งเพื่อความปลอดภัย",
                        "warning"
                    )
                    return

            self.log(
                f"⚡ [LIVE TRADE DISPATCH] ส่งคำสั่งซื้อ {ex_names.get(buy_ex, buy_ex)} ➔ ขาย {ex_names.get(sell_ex, sell_ex)} ({coin}) "
                f"ทุน ฿{trade_val_thb:,.0f} (~${trade_val_thb/33.54:.2f} USDT)...",
                "warning"
            )

            # Leg 1: BUY
            res_buy = self._dispatch_real_order(buy_ex, coin, "BUY", trade_val_thb, coin_amount)
            if not res_buy.get("success"):
                self.log(f"❌ [LIVE BUY FAIL] ซื้อ {buy_ex} ไม่สำเร็จ: {res_buy.get('message')} ➔ ยกเลิกขาขายทันที", "error")
                return

            # Leg 2: SELL
            res_sell = self._dispatch_real_order(sell_ex, coin, "SELL", trade_val_thb, coin_amount)
            if res_sell.get("success"):
                live_executed = True
                self.log(
                    f"🎉 [LIVE MATCH SUCCESS] ซื้อ {ex_names.get(buy_ex, buy_ex)} (Order {res_buy.get('order_id')}) + "
                    f"ขาย {ex_names.get(sell_ex, sell_ex)} (Order {res_sell.get('order_id')}) สำเร็จคู่!",
                    "success"
                )
                send_telegram_alert(
                    f"🎉 <b>[LIVE MATCH SUCCESS] จับคู่ทำกำไรสำเร็จ!</b>\n\n"
                    f"🪙 เหรียญ: <b>{coin}</b>\n"
                    f"🟢 ซื้อ {ex_names.get(buy_ex, buy_ex)} สำเร็จ (Order {res_buy.get('order_id')})\n"
                    f"🔴 ขาย {ex_names.get(sell_ex, sell_ex)} สำเร็จ (Order {res_sell.get('order_id')})\n"
                    f"💵 ทุนเทรด: ฿{trade_val_thb:,.0f} THB (~${trade_val_thb/33.54:.2f} USDT)\n"
                    f"💰 สเปรดสุทธิ: +{actual_roi_pct:.2f}% | กำไรสุทธิ +฿{net_profit_thb:.2f} THB 🟢"
                )
                execution_note += " [LIVE 100%]"
            else:
                live_executed = True  # Buy already went through
                self.log(f"⚠️ [LIVE PARTIAL FAIL] ซื้อ {buy_ex} สำเร็จ แต่ขาย {sell_ex} ล้มเหลว: {res_sell.get('message')}", "error")
                send_telegram_alert(
                    f"⚠️ <b>[LIVE PARTIAL ALERT] ขาซื้อสำเร็จ แต่ขาขายขัดข้อง!</b>\n\n"
                    f"🪙 เหรียญ: <b>{coin}</b>\n"
                    f"🟢 ซื้อ {buy_ex}: สำเร็จ (Order {res_buy.get('order_id')})\n"
                    f"❌ ขาย {sell_ex}: {res_sell.get('message')}"
                )
                execution_note += " [LIVE PARTIAL]"

        if self.mode == "live" and not live_executed:
            return

        if live_executed:
            self.refresh_real_balances()

        ex_buy_bal = self.balances.setdefault(buy_ex, {})
        ex_sell_bal = self.balances.setdefault(sell_ex, {})

        ex_buy_bal["THB"] = max(0.0, ex_buy_bal.get("THB", 0.0) - trade_val_thb)
        ex_buy_bal[coin] = ex_buy_bal.get(coin, 0.0) + coin_amount

        current_coin_in_sell = ex_sell_bal.get(coin, 0.0)
        loan_used = False
        if current_coin_in_sell < coin_amount:
            if self.auto_loan_enabled:
                needed_coin = coin_amount - current_coin_in_sell
                borrow_src = buy_ex if "binance" in buy_ex else "binance_global"
                self.borrow_coin(
                    coin=coin,
                    amount=needed_coin,
                    borrow_source=borrow_src,
                    target_exchange=sell_ex,
                    auto=True
                )
                loan_used = True
                execution_note += " + LOAN"

        ex_sell_bal[coin] = max(0.0, ex_sell_bal.get(coin, 0.0) - coin_amount)
        ex_sell_bal["THB"] = ex_sell_bal.get("THB", 0.0) + net_sell_thb

        self.daily_pnl_thb += net_profit_thb
        self.total_volume_thb += trade_val_thb
        self.fees_paid_thb += total_fees
        self.filled_count += 1

        # Consecutive Loss Circuit Breaker check
        if net_profit_thb < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.enabled = False
                self.log(
                    f"🛑 [CIRCUIT BREAKER TRIPPED] บอทหยุดทำงานฉุกเฉินทันที! ตรวจพบขาดทุนติดต่อกันครบ {self.consecutive_losses} ครั้ง "
                    f"(ไม้ล่าสุด {coin} -฿{abs(net_profit_thb):.2f}) ป้องกันเงินทุนไม่ให้ไหลออก เพื่อรอวิเคราะห์หาสาเหตุ",
                    "error"
                )
        else:
            self.consecutive_losses = 0

        # Maximum Cumulative Loss Circuit Breaker (฿500 Stop Loss)
        if self.daily_pnl_thb <= -self.max_daily_loss_thb:
            self.enabled = False
            self.log(
                f"🛑 [MAX LOSS LIMIT REACHED] บอทหยุดทำงานฉุกเฉินทันที! ขาดทุนสะสมรวม (-฿{abs(self.daily_pnl_thb):.2f}) ถึงเกณฑ์จำกัดสูงสุด ฿{self.max_daily_loss_thb:,.2f} บาท ระบบล็อคเงินทุนทันทีเพื่อความปลอดภัยสูงสุด",
                "error"
            )

        trade_record = {
            "trade_id": trade_id,
            "timestamp": time.time(),
            "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
            "coin": coin,
            "buy_ex": buy_ex,
            "sell_ex": sell_ex,
            "buy_price": round(filled_buy_price, 4),
            "sell_price": round(filled_sell_price, 4),
            "amount_coin": coin_amount,
            "trade_value_thb": round(trade_val_thb, 2),
            "gross_spread_pct": round(raw_gross_spread, 3),
            "net_spread_pct": round(actual_roi_pct, 3),
            "initial_spread_pct": p["initial_spread"],
            "delay_sec": delay_actual,
            "leg1_ms": leg1_latency_ms,
            "leg2_ms": leg2_latency_ms,
            "slippage_thb": round(slippage_cost_thb, 2),
            "exec_note": execution_note,
            "gross_profit_thb": round(gross_sell_thb - trade_val_thb, 2),
            "fees_thb": round(total_fees, 2),
            "net_profit_thb": round(net_profit_thb, 2),
            "mode": self.mode.upper(),
            "loan_used": loan_used,
            "pair_type": p.get("pair_type", "DIRECT_THB"),
            "is_direct_thb": p.get("is_direct_thb", True),
            "status": "FILLED"
        }
        self.trades.insert(0, trade_record)
        if len(self.trades) > 500:
            self.trades.pop()

        self.save_state()

        msg = (
            f"✅ [FILLED REALISTIC] {coin} แมตช์สำเร็จ ({delay_actual:.2f}s delay | Leg1: {leg1_latency_ms}ms, Leg2: {leg2_latency_ms}ms) | "
            f"ซื้อ {ex_names.get(buy_ex, buy_ex)} (฿{filled_buy_price:,.4f}) ➔ ขาย {ex_names.get(sell_ex, sell_ex)} (฿{filled_sell_price:,.4f}) | "
            f"ทุน ฿{trade_val_thb:,.0f} | กำไรสุทธิ +฿{net_profit_thb:,.2f} (+{actual_roi_pct:.2f}%) "
            f"[Fee -฿{total_fees:.2f} | Slippage -฿{slippage_cost_thb:.2f} | {execution_note}]"
        )
        self.log(msg, "trade")

        if live_executed:
            send_telegram_alert(
                f"🎉 <b>[LIVE TRADE FILLED] บอททำกำไรสำเร็จ!</b>\n\n"
                f"🪙 เหรียญ: <b>{coin}</b>\n"
                f"🔄 เส้นทาง: {ex_names.get(buy_ex, buy_ex)} ➔ {ex_names.get(sell_ex, sell_ex)}\n"
                f"💵 ทุนเทรด: ฿{trade_val_thb:,.0f} THB\n"
                f"📊 สเปรดสุทธิ: +{actual_roi_pct:.2f}%\n"
                f"💰 <b>กำไรสุทธิ: +฿{net_profit_thb:,.2f} THB</b>\n"
                f"⏱️ เวลา: {trade_record['time_str']}"
            )

    def record_missed_trade(self, p, reason, reason_th, final_spread, delay_actual):
        trade_id = p["order_id"]
        coin = p["coin"]
        buy_ex = p["buy_ex"]
        sell_ex = p["sell_ex"]
        trade_val_thb = p["trade_val_thb"]

        self.missed_count += 1

        trade_record = {
            "trade_id": trade_id,
            "timestamp": time.time(),
            "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
            "coin": coin,
            "buy_ex": buy_ex,
            "sell_ex": sell_ex,
            "buy_price": p["initial_buy_price"],
            "sell_price": p["initial_sell_price"],
            "amount_coin": 0.0,
            "trade_value_thb": round(trade_val_thb, 2),
            "gross_spread_pct": p["initial_gross"],
            "net_spread_pct": 0.0,
            "initial_spread_pct": p["initial_spread"],
            "final_spread_pct": round(final_spread, 3),
            "delay_sec": delay_actual,
            "slippage_thb": 0.0,
            "exec_note": "ABORTED",
            "gross_profit_thb": 0.0,
            "fees_thb": 0.0,
            "net_profit_thb": 0.0,
            "mode": "PAPER",
            "pair_type": p.get("pair_type", "DIRECT_THB"),
            "is_direct_thb": p.get("is_direct_thb", True),
            "status": "MISSED",
            "miss_reason": reason_th
        }
        self.trades.insert(0, trade_record)
        if len(self.trades) > 500:
            self.trades.pop()

        self.save_state()

        ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}
        msg = (
            f"🛡️ [MISSED ORDER] {coin} ยกเลิกออเดอร์อัตโนมัติ (ดีเลย์ {delay_actual:.2f}s)! "
            f"เหตุผล: {reason_th} (สเปรดจาก +{p['initial_spread']:.2f}% ➔ {final_spread:.2f}%) "
            f"| ปกป้องเงินทุน ฿{trade_val_thb:,.0f} ไม่ให้ขาดทุน!"
        )
        self.log(msg, "warning")

    def export_trades_csv(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Trade ID", "Time", "Symbol", "Status", "Buy Exchange", "Sell Exchange",
            "Delay (sec)", "Leg 1 (ms)", "Leg 2 (ms)", "Execution Note",
            "Initial Spread (%)", "Fill Net Spread (%)", "Trade Value (THB)",
            "Net Profit (THB)", "Fees (THB)", "Slippage Cost (THB)",
            "Buy Price", "Sell Price", "Miss Reason"
        ])
        with self.lock:
            for t in self.trades:
                writer.writerow([
                    t.get("trade_id", ""),
                    t.get("time_str", ""),
                    t.get("coin", ""),
                    t.get("status", ""),
                    t.get("buy_ex", ""),
                    t.get("sell_ex", ""),
                    t.get("delay_sec", ""),
                    t.get("leg1_ms", ""),
                    t.get("leg2_ms", ""),
                    t.get("exec_note", ""),
                    t.get("initial_spread_pct", ""),
                    t.get("net_spread_pct", ""),
                    t.get("trade_value_thb", ""),
                    t.get("net_profit_thb", ""),
                    t.get("fees_thb", ""),
                    t.get("slippage_thb", ""),
                    t.get("buy_price", ""),
                    t.get("sell_price", ""),
                    t.get("miss_reason", "")
                ])
        return output.getvalue()

    def get_status(self):
        with self.lock:
            now = time.time()
            uptime_str = "-"
            if self.enabled and self.started_at:
                uptime_sec = int(now - self.started_at)
                hrs = uptime_sec // 3600
                mins = (uptime_sec % 3600) // 60
                secs = uptime_sec % 60
                uptime_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

            filled_trades = [t for t in self.trades if t.get("status") == "FILLED"]
            missed_trades = [t for t in self.trades if t.get("status") == "MISSED"]
            total_evaluated = len(filled_trades) + len(missed_trades)
            fill_rate_pct = (len(filled_trades) / total_evaluated * 100.0) if total_evaluated > 0 else 0.0

            # Delays
            delays = [t.get("delay_sec", 0.0) for t in self.trades if "delay_sec" in t]
            avg_delay = round(sum(delays) / len(delays), 2) if delays else 0.0

            # Best performing coin & pair
            coin_pnl = {}
            pair_pnl = {}
            ex_names = {"bitkub": "Bitkub", "binance_th": "Binance TH", "binance_global": "Binance Global", "orbix": "Orbix", "upbit": "Upbit"}

            for t in filled_trades:
                c = t.get("coin", "-")
                p = t.get("net_profit_thb", 0.0)
                coin_pnl[c] = coin_pnl.get(c, 0.0) + p

                pk = f"{ex_names.get(t.get('buy_ex'), t.get('buy_ex'))} ➔ {ex_names.get(t.get('sell_ex'), t.get('sell_ex'))}"
                pair_pnl[pk] = pair_pnl.get(pk, 0.0) + p

            best_coin = max(coin_pnl.items(), key=lambda x: x[1]) if coin_pnl else ("-", 0.0)
            best_pair = max(pair_pnl.items(), key=lambda x: x[1]) if pair_pnl else ("-", 0.0)

            # Total estimated portfolio value in THB across all exchanges
            total_portfolio_thb = 0.0
            for ex, bal in self.balances.items():
                total_portfolio_thb += bal.get("THB", 0.0)
                total_portfolio_thb += bal.get("USDT", 0.0) * 33.54

            # Masked API keys for security
            masked_keys = {}
            for ex, k in self.api_keys.items():
                masked_keys[ex] = {
                    "has_key": bool(k["key"]),
                    "masked_key": (k["key"][:4] + "..." + k["key"][-4:]) if len(k["key"]) > 8 else ("Set" if k["key"] else "Not Set"),
                    "connected": k["connected"]
                }

            # Prepare active pending queue info
            pending_display = []
            for p in self.pending_orders:
                rem_sec = max(0.0, round(p["execute_time"] - now, 2))
                pending_display.append({
                    "order_id": p["order_id"],
                    "coin": p["coin"],
                    "buy_ex": p["buy_ex"],
                    "sell_ex": p["sell_ex"],
                    "delay_sec": p["delay_sec"],
                    "rem_sec": rem_sec,
                    "initial_spread": p["initial_spread"],
                    "trade_val_thb": p["trade_val_thb"]
                })

            # Calculate active loans & interest
            active_loans = []
            total_accrued_loan_interest = 0.0
            total_active_borrowed_val_thb = 0.0
            total_active_collateral_usdt = 0.0

            for l in self.loans:
                if l["status"] == "ACTIVE":
                    elapsed_h = max(0.05, (now - l["borrow_time"]) / 3600.0)
                    daily_r = l["daily_interest_rate_pct"] / 100.0
                    interest_p = daily_r * (elapsed_h / 24.0)
                    approx_usd = l.get("approx_usd_price", 1.0)
                    val_thb = l["amount"] * approx_usd * 33.54
                    interest_thb = round(val_thb * interest_p, 2)
                    l["accrued_interest_thb"] = interest_thb
                    l["elapsed_hours"] = round(elapsed_h, 2)
                    total_accrued_loan_interest += interest_thb
                    total_active_borrowed_val_thb += val_thb
                    total_active_collateral_usdt += l.get("collateral_amount", 0.0)
                    active_loans.append(l)

            return {
                "enabled": self.enabled,
                "mode": self.mode,
                "uptime": uptime_str,
                "config": {
                    "min_net_spread_pct": self.min_net_spread_pct,
                    "trade_size_thb": self.trade_size_thb,
                    "max_daily_loss_thb": self.max_daily_loss_thb,
                    "max_consecutive_losses": self.max_consecutive_losses,
                    "cooldown_sec": self.cooldown_sec,
                    "sim_delay_min_sec": self.sim_delay_min_sec,
                    "sim_delay_max_sec": self.sim_delay_max_sec,
                    "max_book_cap_pct": self.max_book_cap_pct,
                    "allowed_exchanges": self.allowed_exchanges,
                    "allowed_coins": self.allowed_coins
                },
                "stats": {
                    "daily_pnl_thb": round(self.daily_pnl_thb, 2),
                    "total_trades": len(self.trades),
                    "filled_trades": len(filled_trades),
                    "missed_trades": len(missed_trades),
                    "fill_rate_pct": round(fill_rate_pct, 1),
                    "avg_delay_sec": avg_delay,
                    "consecutive_losses": self.consecutive_losses,
                    "max_consecutive_losses": self.max_consecutive_losses,
                    "total_volume_thb": round(self.total_volume_thb, 2),
                    "fees_paid_thb": round(self.fees_paid_thb, 2),
                    "total_slippage_thb": round(self.total_slippage_thb, 2),
                    "total_portfolio_thb": round(total_portfolio_thb, 2),
                    "best_coin_sym": best_coin[0],
                    "best_coin_pnl": round(best_coin[1], 2),
                    "best_pair_name": best_pair[0],
                    "best_pair_pnl": round(best_pair[1], 2),
                    "total_triggered": self.total_triggered_count,
                    "rebalance_count": self.rebalance_count
                },
                "loans": active_loans[:20],
                "all_loans_count": len(self.loans),
                "auto_loan_enabled": self.auto_loan_enabled,
                "borrowable_assets": self.borrowable_assets,
                "loan_stats": {
                    "active_count": len(active_loans),
                    "total_borrowed_val_thb": round(total_active_borrowed_val_thb, 2),
                    "total_accrued_interest_thb": round(total_accrued_loan_interest, 2),
                    "total_collateral_usdt": round(total_active_collateral_usdt, 2)
                },
                "pending_orders": pending_display,
                "balances": self.balances,
                "real_balances": self.real_balances,
                "api_keys": masked_keys,
                "recent_trades": self.trades[:100],
                "terminal_logs": self.terminal_logs[-35:]
            }

autotrade_engine = AutoTradeEngine()
