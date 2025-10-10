from flask import Flask, request, jsonify
import requests
import os
import time
import hmac
import hashlib
import json
import uuid # Needed for nonce

app = Flask(__name__)

# API keys from environment
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Bitunix endpoints
BITUNIX_ORDER_URL = "https://fapi.bitunix.com/api/v1/futures/trade/place_order"
BITUNIX_TICKER_URL = "https://fapi.bitunix.com/api/v1/futures/market/ticker" # Still need to verify exact Ticker endpoint/response for Futures
BITUNIX_POSITION_URL = "https://fapi.bitunix.com/api/v1/futures/trade/position_info"

LEVERAGE = 50
RETRY_DELAY = 0.5  # seconds
MAX_RETRIES = 5

# --- SHA256 Helper Function ---
def sha256_hex(input_string):
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()

# --- Bitunix API Signing Function ---
# Needs method (GET/POST), params (dict for query string), body (dict for JSON body)
def get_bitunix_headers(method, endpoint, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex # Generate a unique nonce for each request

    # 1. Build queryParams string (sorted by key, concatenated values)
    query_params_string = ""
    if params:
        sorted_params = sorted(params.items())
        for k, v in sorted_params:
            query_params_string += f"{k}{v}" # As per example "id1uid200", no quotes, no separators

    # 2. Build body string (compressed JSON, remove spaces)
    body_string = ""
    if body:
        # According to doc: "remove all spaces". json.dumps with separators=(',', ':') does this.
        # "request body format must be identical to the signature string" -> could imply sorted keys
        body_string = json.dumps(body, separators=(',', ':'), sort_keys=True) 

    # 3. Construct digest_input string
    digest_input = nonce + timestamp + BITUNIX_API_KEY + query_params_string + body_string

    # 4. Generate digest (first SHA256)
    digest = sha256_hex(digest_input)

    # 5. Construct sign_input string
    sign_input = digest + BITUNIX_API_SECRET

    # 6. Generate sign (second SHA256)
    sign = sha256_hex(sign_input)
    
    return {
        "api-key": BITUNIX_API_KEY,
        "sign": sign,
        "nonce": nonce,
        "timestamp": timestamp,
        "Content-Type": "application/json"
    }

@app.route("/", methods=["POST"])
def webhook():
    # 1. Handle incoming JSON payload and errors
    try:
        data = request.json
        if not data:
            raise ValueError("No JSON payload received.")
        
        symbol = data["symbol"].upper()  # Ensure symbol is uppercase
        side = data["side"].upper()      # Ensure side is uppercase
        quantity = data["quantity"]
        stop_loss = data.get("sl")
        take_profit = data.get("tp")
        
    except Exception as e:
        print(f"Error parsing incoming JSON: {e}")
        return jsonify({"status": "error", "message": f"Invalid JSON payload: {e}"}), 400

    print(f"Received webhook for {symbol} ({side})")

    # 2. Check for open positions and close them if necessary
    try:
        # For position info, need symbol and marginCoin params for GET request
        position_params = {"symbol": symbol, "marginCoin": "USDT"} # Assuming USDT as margin coin
        position_headers = get_bitunix_headers("GET", BITUNIX_POSITION_URL, params=position_params)
        position_resp = requests.get(BITUNIX_POSITION_URL, params=position_params, headers=position_headers, timeout=10)
        print(f"DEBUG: Position API Status: {position_resp.status_code}, Body: {position_resp.text}")
        position_data = position_resp.json()
        
        if position_data.get("data"):
            # The 'data' might be a single dict or a list, so handle both
            current_positions = position_data["data"] if isinstance(position_data["data"], list) else [position_data["data"]]
            
            for pos in current_positions:
                if float(pos.get("size", 0)) > 0 and pos.get("symbol") == symbol:
                    print(f"Closing existing position for {symbol}...")
                    close_payload = {
                        "symbol": symbol,
                        "side": "SELL" if pos["side"].upper() == "BUY" else "BUY", # Opposite side
                        "type": "MARKET", # Use MARKET order to ensure close
                        "quantity": pos["size"] # Close full size
                    }
                    # Need to sign the close order payload
                    close_headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=close_payload)
                    close_resp = requests.post(BITUNIX_ORDER_URL, json=close_payload, headers=close_headers, timeout=10)
                    print(f"Close position response: {close_resp.json()}")
                    break # Assuming only one position per symbol for this logic

    except Exception as e:
        print(f"Error checking or closing existing position: {e}")
        # Continue with the new order even if closing fails

    # 3. Place new order with retry logic
    result = {"message": "Order failed after max retries."} # default result
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Get last price
            # Assuming 'futures/market/ticker' is still the endpoint,
            # but now passing params and generating signed headers for a GET request.
            # If this is a public endpoint that doesn't need signing, the get_bitunix_headers logic
            # would need a slight modification (e.g., return minimal headers for public endpoints)
            ticker_params = {"symbol": symbol}
            ticker_headers = get_bitunix_headers("GET", BITUNIX_TICKER_URL, params=ticker_params)
            ticker_resp = requests.get(BITUNIX_TICKER_URL, params=ticker_params, headers=ticker_headers, timeout=10)
            print(f"DEBUG: Ticker API Status: {ticker_resp.status_code}, Body: {ticker_resp.text}")
            ticker_data = ticker_resp.json()
            
            last_price = Nonce
            if ticker_data.get("code") == 0 and isinstance(ticker_data.get("data"), list):
                for ticker_item in ticker_data["data"]:
                    if ticker_item.get("symbol") == symbol:
                        # Ensure 'lastPrice' exists and is not None before converting
                        if ticker_item.get("lastPrice") is not Nonce:
                            last_price = float(ticker_item.get("lastPrice"))
                            break
            
            if last_price is Nonce:
                # Log the full response for better debugging if lastPrice is missing
                raise ValueError(f"'lastPrice' not found for {symbol} in ticker response: {ticker_data}")

            print(f"Attempt {attempt}: Last price for {symbol} is {last_price}")

            # Build and send order
            order_payload = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT", # Using LIMIT as in original script, but MARKET might be preferred for immediate fill.
                "price": str(last_price), # Ensure price is a string as per docs/examples
                "quantity": str(quantity), # Ensure quantity is a string
                "leverage": LEVERAGE,
                "stop_loss": str(stop_loss) if stop_loss else None,
                "take_profit": str(take_profit) if take_profit else None,
                "guaranteed_stop_loss": True # Bitunix expects boolean True/False
            }
            # Remove keys with None values before sending and signing
            order_payload = {k: v for k, v in order_payload.items() if v is not None}

            # Sign the order placement payload
            order_headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=order_payload)
            response = requests.post(BITUNIX_ORDER_URL, json=order_payload, headers=order_headers, timeout=10)
            result = response.json()
            print(f"Order attempt {attempt}: {result}")

            if result.get("code") == 0:
                print("Order successfully placed ✅")
                return jsonify({"status": "success", "response": result})

            time.sleep(RETRY_DELAY)

        except Exception as e:
            print(f"Error on attempt {attempt} for {symbol}: {e}")
            time.sleep(RETRY_DELAY)

    print(f"Order for {symbol} failed after {MAX_RETRIES} retries ❌")
    return jsonify({"status": "failed", "response": result}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
