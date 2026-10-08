"""
Continuous Binance Global Connectivity & Ban Monitor
Checks api-gcp.binance.com and fapi.binance.com continuously.
Logs health status and response times.
"""
import urllib.request
import urllib.error
import time
import json
import datetime
import os

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "binance_health_log.json")

def check_binance_health():
    endpoints = [
        {"name": "GCP Edge (api-gcp)", "url": "https://api-gcp.binance.com/api/v3/time"},
        {"name": "Futures (fapi)", "url": "https://fapi.binance.com/fapi/v1/time"},
        {"name": "Public (api.binance.com)", "url": "https://api.binance.com/api/v3/time"}
    ]
    
    results = {}
    for ep in endpoints:
        t0 = time.time()
        try:
            req = urllib.request.Request(ep["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
                elapsed = int((time.time() - t0) * 1000)
                results[ep["name"]] = {
                    "status": "HEALTHY_OK",
                    "code": 200,
                    "latency_ms": elapsed,
                    "server_time": data.get("serverTime")
                }
        except urllib.error.HTTPError as e:
            elapsed = int((time.time() - t0) * 1000)
            results[ep["name"]] = {
                "status": f"HTTP_{e.code}",
                "code": e.code,
                "latency_ms": elapsed,
                "error": e.read().decode("utf-8", errors="ignore")[:100]
            }
        except Exception as e:
            results[ep["name"]] = {
                "status": "ERROR",
                "latency_ms": 0,
                "error": str(e)
            }
    
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": now_str,
        "results": results
    }
    
    # Save log
    try:
        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(entry)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs[-100:], f, indent=2)
    except Exception:
        pass
    
    return entry

if __name__ == "__main__":
    r = check_binance_health()
    print("Binance Health Check:", json.dumps(r, indent=2))
