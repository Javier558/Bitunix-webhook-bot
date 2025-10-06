# app.py
from flask import Flask, request, jsonify
import os
import requests
import time
import hmac
import hashlib
import json

app = Flask(__name__)

# ---------------------------
# Configuration (from env)
# ---------------------------
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")
# Base URL, e.g. "https://api.bitunix.com" or testnet "https://testnet-api.bitunix.com"
BITUNIX_BASE_URL = os.getenv("BITUNIX_BASE_URL", "wss://openapi.bitunix.com:443/ws-api/v1")
# Endpoint path for placing futures orders (set to the correct path your exchange uses)
# Example: "/futures/order" or "/order"
BITUNIX_ORDER_ENDPOINT = os.getenv("BITUNIX_ORDER_ENDPOINT", "/order")

# Fixed leverage as requested
FIXED_LEVERAGE = int(os.getenv("FIXED_LEVERAGE", "50"))

# Default quantity if TradingView doesn't send one (in contract/asset units)
DEFAULT_QUANTITY = float(os.getenv("DEFAULT_QUANTITY", "0.01"))

# If SIMULATE == "true", we won't call the real API (useful if you have no funds)
SIMULATE = os.getenv("SIMULATE", "true").lower() == "true"

# Some APIs require signing the payload; set USE_SIGNATURE to "true" to add HMAC signature header
USE_SIGNATURE = os.getenv("USE_SIGNATURE", "false").lower() == "true"
SIGNATURE_HEADER = os.getenv("SIGNATURE_HEADER", "X-SIGNATURE")  # custom header name for signature if required

# Optional: additional headers you always want to send
EXTRA_HEADERS_JSON = os.getenv("EXTRA_HEADERS_JSON", "{}")
try:
    EXTRA_HEADERS = json.loads(EXTRA_HEADERS_JSON) if EXTRA_HEADERS_JSON else {}
except Exception:
    EXTRA_HEADERS = {}

# ---------------------------
# Helpers
# ---------------------------
def sign_payload(payload: str) -> str:
    """
    HMAC-SHA256 signature. Many exchanges use similar signing.
    If Bitunix requires a different signature method, change this function.
    """
    secret = (BITUNIX_API_SECRET or "").encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()

def build_headers(payload_str: str):
    headers = {
        "Content-Type": "application/json",
    }
    if BITUNIX_API_KEY:
        headers["X-API-KEY"] = BITUNIX_API_KEY
    # include any extra headers from env
    headers.update(EXTRA_HEADERS)
    if USE_SIGNATURE and BITUNIX_API_SECRET:
        signature = sign_payload(payload_str)
        headers[SIGNATURE_HEADER] = signature
    return headers

def place_futures_order(symbol, side, quantity, sl=None, tp=None, leverage=FIXED_LEVERAGE):
    """
    Sends an order to Bitunix futures API.
    - symbol: e.g. "BTCUSD" or the exact symbol that Bitunix expects
    - side: "buy" or "sell"
    - quantity: number in asset contracts (or size as Bitunix expects)
    - sl, tp: absolute price levels (or None)
    - leverage: fixed 50x (default)
    """
    endpoint = BITUNIX_BASE_URL.rstrip("/") + BITUNIX_ORDER_ENDPOINT
    # Build payload according to a common futures API pattern.
    # If Bitunix expects different fields, map them here (e.g., "stopPrice", "takeProfit", "positionSide", etc.)
    payload = {
        "symbol": symbol,
        "side": side,            # "buy" or "sell"
        "type": "market",        # market order. change to "limit" and add price if you want limit orders
        "quantity": quantity,
        "leverage": leverage,
    }
    # Attach SL/TP fields if provided (these keys may need to change per Bitunix spec)
    if sl is not None:
        payload["stop_loss"] = sl
    if tp is not None:
        payload["take_profit"] = tp

    payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = build_headers(payload_str)

    if SIMULATE:
        # Simulate a successful response shape
        simulated = {
            "simulated": True,
            "endpoint": endpoint,
            "payload": payload,
            "headers": {k: ("<hidden>" if "KEY" in k or "SECRET" in k or "SIGN" in k.upper() else v) for k, v in headers.items()},
            "timestamp": int(time.time() * 1000)
        }
        print("SIMULATED ORDER:", json.dumps(simulated, indent=2))
        return simulated

    # Real API call
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=15)
        # raise for HTTP errors
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        # include server response body for debugging
        text = getattr(e.response, "text", "")
        print("HTTPError:", e, "response:", text)
        return {"error": "http_error", "message": str(e), "response_text": text}
    except Exception as e:
        print("Error sending order:", e)
        return {"error": "exception", "message": str(e)}

# ---------------------------
# Flask route
# ---------------------------
@app.route("/", methods=["POST"])
def webhook():
    data = None
    try:
        data = request.get_json(force=True)
    except Exception as e:
        print("Failed to parse JSON:", e)
        return jsonify({"error": "invalid_json", "message": str(e)}), 400

    print("Received alert payload:", json.dumps(data, indent=2))

    # Required fields from your Pine alert: side and symbol
    side = data.get("side") or data.get("action")  # accept either "side" or "action"
    symbol = data.get("symbol") or data.get("ticker") or data.get("instrument")
    sl = data.get("sl") or data.get("stop_loss") or data.get("stop")
    tp = data.get("tp") or data.get("take_profit") or data.get("limit")
    quantity = data.get("quantity")
    leverage = data.get("leverage", FIXED_LEVERAGE)

    # Validate required fields
    if not side or not symbol:
        return jsonify({"error": "missing_fields", "message": "Require 'side' and 'symbol' in webhook JSON"}), 400

    # Normalize side
    side_norm = side.lower()
    if side_norm not in ("buy", "sell", "long", "short"):
        return jsonify({"error": "invalid_side", "message": f"Invalid side: {side}"}), 400
    if side_norm == "long":
        side_norm = "buy"
    if side_norm == "short":
        side_norm = "sell"

    # Determine quantity
    try:
        if quantity is None:
            quantity = float(DEFAULT_QUANTITY)
        else:
            quantity = float(quantity)
    except Exception:
        return jsonify({"error": "invalid_quantity", "message": "Quantity must be numeric"}), 400

    # Optionally coerce sl/tp to float or None
    try:
        sl = None if sl is None else float(sl)
    except Exception:
        sl = None
    try:
        tp = None if tp is None else float(tp)
    except Exception:
        tp = None

    # Place order
    order_result = place_futures_order(symbol=symbol, side=side_norm, quantity=quantity, sl=sl, tp=tp, leverage=int(leverage))

    # Log and return
    print("Order result:", json.dumps(order_result, indent=2) if isinstance(order_result, dict) else str(order_result))
    return jsonify({"status": "ok", "order_result": order_result}), 200

# ---------------------------
# Health check endpoint (useful for Render)
# ---------------------------
@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
