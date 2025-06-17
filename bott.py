import os
import websockets
import asyncio
import json
import requests
from datetime import datetime, timedelta
import time
import logging
from logging.handlers import RotatingFileHandler
import platform
import psutil
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# Load environment variables from .env file
load_dotenv()

# Flask web server for keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Deriv Trading Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# Start the keep-alive server
keep_alive()

# Configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DERIV_APP_ID = os.getenv('DERIV_APP_ID', '1089')
LOG_FILE = os.getenv('LOG_FILE', 'deriv_bot.log')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# Production settings
DEVELOPMENT_MODE = os.getenv('DEVELOPMENT_MODE', 'false').lower() == 'true'
MAX_LOG_BYTES = int(os.getenv('MAX_LOG_BYTES', '5242880'))  # 5MB
LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', '3'))

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# CORRECTED symbol configuration with proper Deriv API symbol codes
SYMBOLS = {
    "CRASH": {
        "code": "CRASH1000",  # Correct symbol code for Crash 1000
        "name": "Crash 1000 Index",
        "signal_type": "SELL",  # Only SELL for crash
        "pip_size": 0.01,
        "tp_pips_min": 70,
        "tp_pips_max": 100,
        "sl_pips_min": 10,
        "sl_pips_max": 15,
        "ohlc_request": {
            "ticks_history": "CRASH1000",  # Updated symbol code
            "end": "latest",
            "count": 50,
            "granularity": 1800,  # 30 minutes
            "style": "candles",
            "subscribe": 1
        }
    },
    "BOOM": {
        "code": "BOOM1000",  # Correct symbol code for Boom 1000
        "name": "Boom 1000 Index", 
        "signal_type": "BUY",  # Only BUY for boom
        "pip_size": 0.01,
        "tp_pips_min": 80,
        "tp_pips_max": 150,
        "sl_pips_min": 10,
        "sl_pips_max": 20,
        "ohlc_request": {
            "ticks_history": "BOOM1000",  # Updated symbol code
            "end": "latest",
            "count": 50,
            "granularity": 1800,
            "style": "candles",
            "subscribe": 1
        }
    }
}

class BotHealthMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.message_count = 0
        self.alert_count = 0
        self.connection_count = 0
        self.last_health_check = None
    
    def get_uptime(self):
        return datetime.now() - self.start_time
    
    def get_system_stats(self):
        return {
            "cpu": psutil.cpu_percent(),
            "memory": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent
        }
    
    def generate_health_report(self):
        stats = self.get_system_stats()
        return (
            f"⚙️ <b>Bot Health Report</b>\n\n"
            f"🕒 Uptime: {str(self.get_uptime()).split('.')[0]}\n"
            f"📨 Messages: {self.message_count}\n"
            f"🚨 Alerts: {self.alert_count}\n"
            f"🔌 Connections: {self.connection_count}\n\n"
            f"<b>System:</b>\n"
            f"🖥️ CPU: {stats['cpu']}%\n"
            f"🧠 Memory: {stats['memory']}%\n"
            f"💾 Disk: {stats['disk']}%"
        )

# Global state tracking
health_monitor = BotHealthMonitor()

# CORRECTED trading_data structure with proper reversal tracking
trading_data = {
    "CRASH": {
        "previous_day_trend": None,
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "historical_candles": [],
        "no_trade_alert_sent": False,
        "first_signal_price": None,
        "first_signal_sent": False,
        "price_went_against_us": False,
        "reversal_threshold_hit": False,  # NEW: Track if we hit minimum reversal distance
        "highest_price_after_first": None,  # NEW: Track highest price after first signal (for CRASH)
        "lowest_price_after_first": None   # NEW: Track lowest price after first signal (for BOOM)
    },
    "BOOM": {
        "previous_day_trend": None,
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "historical_candles": [],
        "no_trade_alert_sent": False,
        "first_signal_price": None,
        "first_signal_sent": False,
        "price_went_against_us": False,
        "reversal_threshold_hit": False,  # NEW: Track if we hit minimum reversal distance
        "highest_price_after_first": None,  # NEW: Track highest price after first signal (for BOOM)
        "lowest_price_after_first": None   # NEW: Track lowest price after first signal (for CRASH)
    }
}

def check_if_price_went_against_us(symbol, current_price):
    """Check if current price moved against our first signal with minimum threshold"""
    data = trading_data[symbol]
    
    if not data["first_signal_sent"] or data["first_signal_price"] is None:
        return False
    
    first_entry = data["first_signal_price"]
    symbol_config = SYMBOLS[symbol]
    
    # Define minimum reversal threshold (e.g., 5 pips against us)
    min_reversal_pips = 5
    min_reversal_distance = min_reversal_pips * symbol_config["pip_size"]
    
    if symbol == "CRASH":
        # CRASH is SELL signal, so price going UP means it went against us
        # Track highest price after first signal
        if data["highest_price_after_first"] is None or current_price > data["highest_price_after_first"]:
            data["highest_price_after_first"] = current_price
        
        # Check if price went significantly against us
        if current_price > first_entry + min_reversal_distance:
            data["reversal_threshold_hit"] = True
            return True
            
    elif symbol == "BOOM":  
        # BOOM is BUY signal, so price going DOWN means it went against us
        # Track lowest price after first signal
        if data["lowest_price_after_first"] is None or current_price < data["lowest_price_after_first"]:
            data["lowest_price_after_first"] = current_price
        
        # Check if price went significantly against us
        if current_price < first_entry - min_reversal_distance:
            data["reversal_threshold_hit"] = True
            return True
    
    return False

def check_reversal_conditions(symbol, current_price):
    """Check if we have a valid reversal setup for second signal"""
    data = trading_data[symbol]
    
    if not data["reversal_threshold_hit"] or not data["first_signal_sent"]:
        return False
    
    first_entry = data["first_signal_price"]
    
    if symbol == "CRASH":
        # For CRASH reversal: Price should come back down significantly from the high
        if data["highest_price_after_first"] is None:
            return False
        
        # Price should have retraced at least 50% from the high back towards entry
        high_point = data["highest_price_after_first"]
        retracement_distance = (high_point - first_entry) * 0.5
        target_reversal_price = high_point - retracement_distance
        
        return current_price <= target_reversal_price
        
    elif symbol == "BOOM":
        # For BOOM reversal: Price should come back up significantly from the low
        if data["lowest_price_after_first"] is None:
            return False
        
        # Price should have retraced at least 50% from the low back towards entry
        low_point = data["lowest_price_after_first"]
        retracement_distance = (first_entry - low_point) * 0.5
        target_reversal_price = low_point + retracement_distance
        
        return current_price >= target_reversal_price
    
    return False

def format_reversal_trade_alert(symbol, symbol_config, entry_price, first_candle_close, trend, first_signal_price):
    """Format the reversal (second) signal alert"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    data = trading_data[symbol]
    
    if symbol == "CRASH":
        sl = round(entry_price + (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price - (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "SELL"
        emoji = "🔻"
        highest_against = data["highest_price_after_first"]
        reversal_reason = f"Price went UP to {highest_against} from first signal ({first_signal_price}), now reversing back down"
    else:  # BOOM
        sl = round(entry_price - (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price + (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "BUY"
        emoji = "🔺"
        lowest_against = data["lowest_price_after_first"]
        reversal_reason = f"Price went DOWN to {lowest_against} from first signal ({first_signal_price}), now reversing back up"
    
    return (
        f"🔄 <b>REVERSAL SIGNAL #{data['daily_trade_count'] + 1} - {symbol_config['name']}</b>\n\n"
        f"📊 Previous Day Trend: {trend}\n"
        f"⚠️ First Signal Entry: {first_signal_price}\n"
        f"🔄 Reversal Reason: {reversal_reason}\n"
        f"✅ New Confirmation: Price back above/below first candle close\n\n"
        f"{emoji} <b>REVERSAL Trade Details:</b>\n"
        f"💰 Entry: {entry_price}\n"
        f"🎯 TP: {tp}\n"
        f"❌ SL: {sl}\n\n"
        f"📅 Date: {current_time.split()[0]}\n"
        f"⏰ Time: {current_time.split()[1]}\n\n"
        f"📈 Signal: {data['daily_trade_count'] + 1}/2 (REVERSAL ENTRY)"
    )

def process_candle_data(symbol, candle):
    """Process candle with CORRECTED reversal-based second signal logic"""
    try:
        health_monitor.message_count += 1
        
        if is_new_day():
            reset_daily_data()
        
        symbol_config = SYMBOLS[symbol]
        data = trading_data[symbol]
        
        candle_data = {
            "open": float(candle.get("open", 0)),
            "close": float(candle.get("close", 0)),
            "high": float(candle.get("high", 0)),
            "low": float(candle.get("low", 0)),
            "epoch": int(candle.get("epoch", 0))
        }
        
        data["historical_candles"].append(candle_data)
        
        if len(data["historical_candles"]) > 200:
            data["historical_candles"] = data["historical_candles"][-200:]
        
        candle_epoch = candle_data["epoch"]
        candle_open = candle_data["open"]
        candle_close = candle_data["close"]
        
        # Log real-time price for debugging
        logger.info(f"Real-time {symbol} price: Open={candle_open}, Close={candle_close}, Time={datetime.utcfromtimestamp(candle_epoch)}")
        
        # Handle first candle of the day
        if is_first_candle_of_day(candle_epoch) and data["first_candle_close"] is None:
            data["first_candle_close"] = candle_close
            logger.info(f"First candle of day stored for {symbol}: {candle_close}")
            
            if len(data["historical_candles"]) >= 48:
                data["previous_day_trend"] = analyze_previous_day_trend(symbol, data["historical_candles"])
                logger.info(f"Previous day trend for {symbol}: {data['previous_day_trend']}")
                
                # Send no-trade alerts if trend doesn't match
                if symbol == "CRASH" and data["previous_day_trend"] != "BEARISH":
                    if not data["no_trade_alert_sent"]:
                        no_trade_alert = format_no_trade_alert(symbol, symbol_config, 
                                                             data["previous_day_trend"], "wrong_trend")
                        asyncio.create_task(send_telegram_alert(no_trade_alert))
                        data["no_trade_alert_sent"] = True
                        logger.info(f"NO TRADE alert sent for {symbol} - Previous day was {data['previous_day_trend']}")
                
                elif symbol == "BOOM" and data["previous_day_trend"] != "BULLISH":
                    if not data["no_trade_alert_sent"]:
                        no_trade_alert = format_no_trade_alert(symbol, symbol_config,
                                                             data["previous_day_trend"], "wrong_trend")
                        asyncio.create_task(send_telegram_alert(no_trade_alert))
                        data["no_trade_alert_sent"] = True
                        logger.info(f"NO TRADE alert sent for {symbol} - Previous day was {data['previous_day_trend']}")
            
            return
        
        # Skip if we don't have required data or already sent 2 signals
        if (data["first_candle_close"] is None or 
            data["previous_day_trend"] is None or 
            data["daily_trade_count"] >= 2):
            return
        
        # CONTINUOUS MONITORING: Check if price went against our first signal
        if data["first_signal_sent"]:
            price_went_against = check_if_price_went_against_us(symbol, candle_close)
            if price_went_against and not data["price_went_against_us"]:
                data["price_went_against_us"] = True
                logger.info(f"{symbol} price went against first signal. Reversal tracking activated.")
        
        # FIRST SIGNAL LOGIC
        if data["daily_trade_count"] == 0:
            signal_generated = False
            
            if symbol == "CRASH":
                if (data["previous_day_trend"] == "BEARISH" and
                    candle_close < candle_open and
                    candle_close < data["first_candle_close"]):
                    
                    alert = format_trade_alert(symbol, symbol_config, candle_close, 
                                             data["first_candle_close"], data["previous_day_trend"])
                    
                    logger.info(f"FIRST SIGNAL - CRASH SELL @ {candle_close}")
                    asyncio.create_task(send_telegram_alert(alert))
                    signal_generated = True
                    
            elif symbol == "BOOM":
                if (data["previous_day_trend"] == "BULLISH" and
                    candle_close > candle_open and
                    candle_close > data["first_candle_close"]):
                    
                    alert = format_trade_alert(symbol, symbol_config, candle_close,
                                             data["first_candle_close"], data["previous_day_trend"])
                    
                    logger.info(f"FIRST SIGNAL - BOOM BUY @ {candle_close}")
                    asyncio.create_task(send_telegram_alert(alert))
                    signal_generated = True
            
            if signal_generated:
                data["daily_trade_count"] = 1
                data["first_signal_sent"] = True
                data["first_signal_price"] = candle_close
                logger.info(f"First signal sent for {symbol} at {candle_close}")
        
        # SECOND SIGNAL LOGIC (REVERSAL) - CORRECTED
        elif (data["daily_trade_count"] == 1 and 
              data["price_went_against_us"] and 
              check_reversal_conditions(symbol, candle_close)):
            
            signal_generated = False
            
            if symbol == "CRASH":
                # Second CRASH signal: Same entry conditions as first + reversal confirmation
                if (candle_close < candle_open and
                    candle_close < data["first_candle_close"]):
                    
                    alert = format_reversal_trade_alert(symbol, symbol_config, candle_close,
                                                      data["first_candle_close"], data["previous_day_trend"],
                                                      data["first_signal_price"])
                    
                    logger.info(f"SECOND SIGNAL (REVERSAL) - CRASH SELL @ {candle_close}")
                    asyncio.create_task(send_telegram_alert(alert))
                    signal_generated = True
                    
            elif symbol == "BOOM":
                # Second BOOM signal: Same entry conditions as first + reversal confirmation
                if (candle_close > candle_open and
                    candle_close > data["first_candle_close"]):
                    
                    alert = format_reversal_trade_alert(symbol, symbol_config, candle_close,
                                                      data["first_candle_close"], data["previous_day_trend"],
                                                      data["first_signal_price"])
                    
                    logger.info(f"SECOND SIGNAL (REVERSAL) - BOOM BUY @ {candle_close}")
                    asyncio.create_task(send_telegram_alert(alert))
                    signal_generated = True
            
            if signal_generated:
                data["daily_trade_count"] = 2
                logger.info(f"Second (reversal) signal sent for {symbol} at {candle_close}")
                
    except Exception as e:
        logger.error(f"Error processing candle for {symbol}: {e}")

def reset_daily_data():
    """Reset daily trading data for new day"""
    current_date = datetime.utcnow().date()
    
    for symbol in trading_data:
        trading_data[symbol]["daily_trade_count"] = 0
        trading_data[symbol]["first_candle_close"] = None
        trading_data[symbol]["last_trade_date"] = current_date
        trading_data[symbol]["no_trade_alert_sent"] = False
        trading_data[symbol]["first_signal_price"] = None
        trading_data[symbol]["first_signal_sent"] = False
        trading_data[symbol]["price_went_against_us"] = False
        trading_data[symbol]["reversal_threshold_hit"] = False  # Reset reversal tracking
        trading_data[symbol]["highest_price_after_first"] = None
        trading_data[symbol]["lowest_price_after_first"] = None
    
    logger.info(f"Daily data reset for {current_date}")

def is_new_day():
    """Check if we're in a new trading day (UTC)"""
    now = datetime.utcnow()
    current_date = now.date()
    
    for symbol in trading_data:
        if trading_data[symbol]["last_trade_date"] != current_date:
            return True
    return False

def analyze_previous_day_trend(symbol, candles):
    """Analyze previous day trend based on the strategy"""
    if len(candles) < 48:
        return None
    
    previous_day_candles = candles[-96:-48]
    
    if not previous_day_candles:
        return None
    
    day_open = float(previous_day_candles[0]["open"])
    day_close = float(previous_day_candles[-1]["close"])
    
    bullish_count = 0
    bearish_count = 0
    
    for candle in previous_day_candles:
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        
        if close_price > open_price:
            bullish_count += 1
        elif close_price < open_price:
            bearish_count += 1
    
    if day_close > day_open and bullish_count > bearish_count:
        return "BULLISH"
    elif day_close < day_open and bearish_count > bullish_count:
        return "BEARISH"
    else:
        return "BULLISH" if bullish_count > bearish_count else "BEARISH"

def is_first_candle_of_day(candle_epoch):
    """Check if this is the first 30min candle of the current day"""
    candle_time = datetime.utcfromtimestamp(candle_epoch)
    return candle_time.hour == 0 and candle_time.minute in [0, 30]

async def send_telegram_alert(message, disable_notification=False):
    """Production-ready Telegram alert with retry and timeout"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": disable_notification
    }
    
    for attempt in range(3):
        try:
            with requests.Session() as session:
                response = session.post(url, json=payload, timeout=15)
                response.raise_for_status()
                health_monitor.alert_count += 1
                logger.info("Telegram alert sent successfully")
                return True
        except Exception as e:
            logger.warning(f"Telegram attempt {attempt + 1} failed: {str(e)}")
            if attempt < 2:
                await asyncio.sleep(5)
    
    logger.error("Failed to send Telegram alert after 3 attempts")
    return False

def format_trade_alert(symbol, symbol_config, entry_price, first_candle_close, trend):
    """Format trading alert according to strategy"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    
    if symbol == "CRASH":
        sl = round(entry_price + (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price - (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "SELL"
        emoji = "🔻"
        condition = f"Bearish candle closed below first candle close ({first_candle_close})"
    else:  # BOOM
        sl = round(entry_price - (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price + (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "BUY"
        emoji = "🔺"
        condition = f"Bullish candle closed above first candle close ({first_candle_close})"
    
    return (
        f"🚨 <b>{symbol_config['name']} {signal_type} Signal</b>\n\n"
        f"📊 Previous Day Trend: {trend}\n"
        f"🎯 Condition: {condition}\n\n"
        f"{emoji} <b>Trade Details:</b>\n"
        f"💰 Entry: {entry_price}\n"
        f"🎯 TP: {tp}\n"
        f"❌ SL: {sl}\n\n"
        f"📅 Date: {current_time.split()[0]}\n"
        f"⏰ Time: {current_time.split()[1]}\n\n"
        f"📈 Daily Trade Count: {trading_data[symbol]['daily_trade_count'] + 1}/2"
    )

def format_no_trade_alert(symbol, symbol_config, trend, reason):
    """Format alert when we're not trading due to wrong market conditions"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    
    if symbol == "CRASH":
        required_trend = "BEARISH"
        our_rule = "CRASH = SELL ONLY"
        wrong_condition = "Previous day was BULLISH (we need BEARISH)"
    else:  # BOOM
        required_trend = "BULLISH" 
        our_rule = "BOOM = BUY ONLY"
        wrong_condition = "Previous day was BEARISH (we need BULLISH)"
    
    return (
        f"⚠️ <b>NO TRADE ALERT - {symbol_config['name']}</b>\n\n"
        f"❌ <b>Market conditions don't match our strategy</b>\n\n"
        f"📊 Previous Day Trend: {trend}\n"
        f"📋 Our Rule: {our_rule}\n"
        f"🚫 Issue: {wrong_condition}\n\n"
        f"💡 <b>Action:</b> No trading for {symbol} today\n"
        f"⏳ Will check again tomorrow\n\n"
        f"📅 Date: {current_time.split()[0]}\n"
        f"⏰ Time: {current_time.split()[1]}"
    )

async def get_active_symbols():
    """Get active symbols from Deriv API to verify correct codes"""
    try:
        async with websockets.connect(
            f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}",
            ping_interval=30,
            ping_timeout=30
        ) as ws:
            # Request active symbols
            await ws.send(json.dumps({"active_symbols": "brief", "product_type": "basic"}))
            
            response = await ws.recv()
            data = json.loads(response)
            
            if "active_symbols" in data:
                symbols = data["active_symbols"]
                crash_boom_symbols = [s for s in symbols if "CRASH" in s["symbol"] or "BOOM" in s["symbol"]]
                
                logger.info("Available Crash/Boom symbols:")
                for symbol in crash_boom_symbols:
                    logger.info(f"  {symbol['symbol']}: {symbol['display_name']}")
                
                return crash_boom_symbols
    except Exception as e:
        logger.error(f"Error getting active symbols: {e}")
        return []

async def deriv_websocket_connection():
    """Production-ready WebSocket connection with enhanced stability"""
    while True:
        try:
            async with websockets.connect(
                f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}",
                ping_interval=30,
                ping_timeout=30,
                close_timeout=15,
                max_queue=100
            ) as ws:
                health_monitor.connection_count += 1
                logger.info("Successfully connected to Deriv WebSocket")
                
                # First, get and log available symbols
                await ws.send(json.dumps({"active_symbols": "brief", "product_type": "basic"}))
                response = await ws.recv()
                symbols_data = json.loads(response)
                
                if "active_symbols" in symbols_data:
                    crash_boom_symbols = [s for s in symbols_data["active_symbols"] 
                                        if "CRASH" in s["symbol"] or "BOOM" in s["symbol"]]
                    logger.info("Available Crash/Boom symbols:")
                    for symbol in crash_boom_symbols:
                        logger.info(f"  {symbol['symbol']}: {symbol['display_name']}")
                
                # Send startup notification with symbol verification
                startup_msg = (
                    f"🟢 <b>Crash/Boom Strategy Bot Started</b>\n\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"💻 {platform.node()}\n"
                    f"🐍 Python {platform.python_version()}\n\n"
                    f"📊 <b>Monitoring Symbols:</b>\n"
                    f"🔻 {SYMBOLS['CRASH']['code']} - {SYMBOLS['CRASH']['name']}\n"
                    f"🔺 {SYMBOLS['BOOM']['code']} - {SYMBOLS['BOOM']['name']}\n\n"
                    f"📈 <b>Strategy Rules:</b>\n"
                    f"🔻 CRASH: SELL only when prev day bearish\n"
                    f"🔺 BOOM: BUY only when prev day bullish\n"
                    f"📈 Max 2 trades per day per symbol (with reversal logic)\n\n"
                    f"⚡ <b>Real-time price monitoring active!</b>"
                )
                await send_telegram_alert(startup_msg, True)
                
                # Subscribe to market data
                for symbol in SYMBOLS:
                    subscription_request = SYMBOLS[symbol]["ohlc_request"]
                    await ws.send(json.dumps(subscription_request))
                    logger.info(f"Subscribed to {symbol} ({SYMBOLS[symbol]['code']}) OHLC data")
                
                # Main message processing loop
                async for message in ws:
                    try:
                        data = json.loads(message)
                        
                        if "error" in data:
                            logger.error(f"API error: {data['error']['message']}")
                            # If symbol not found, send alert
                            if "symbol" in data["error"]["message"].lower():
                                error_alert = (
                                    f"❌ <b>Symbol Error Detected</b>\n\n"
                                    f"Error: {data['error']['message']}\n\n"
                                    f"Please check symbol codes:\n"
                                    f"🔻 CRASH: {SYMBOLS['CRASH']['code']}\n"
                                    f"🔺 BOOM: {SYMBOLS['BOOM']['code']}\n\n"
                                    f"Bot will continue trying to reconnect..."
                                )
                                await send_telegram_alert(error_alert)
                            continue
                            
                        if data.get("msg_type") == "candles":
                            # Handle historical candles response
                            candles = data.get("candles", [])
                            symbol_code = data.get("echo_req", {}).get("ticks_history", "")
                            
                            if "CRASH1000" in symbol_code or "CRASH" in symbol_code:
                                symbol = "CRASH"
                            elif "BOOM1000" in symbol_code or "BOOM" in symbol_code:
                                symbol = "BOOM"
                            else:
                                continue
                            
                            # Process historical candles
                            for candle_data in candles:
                                candle = {
                                    "open": candle_data["open"],
                                    "close": candle_data["close"],
                                    "high": candle_data["high"],
                                    "low": candle_data["low"],
                                    "epoch": candle_data["epoch"]
                                }
                                trading_data[symbol]["historical_candles"].append(candle)
                            
                            logger.info(f"Loaded {len(candles)} historical candles for {symbol}")
                            
                        elif data.get("msg_type") == "ohlc":
                            # Handle real-time candle updates
                            ohlc = data.get("ohlc", {})
                            if ohlc:
                                symbol_code = ohlc.get("symbol", "")
                                if "CRASH" in symbol_code:
                                    symbol = "CRASH"
                                elif "BOOM" in symbol_code:
                                    symbol = "BOOM"
                                else:
                                    continue
                                    
                                process_candle_data(symbol, ohlc)
                                
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        
        except (websockets.exceptions.ConnectionClosed, ConnectionError) as e:
            logger.error(f"Connection error: {e}. Reconnecting in 15 seconds...")
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Unexpected error: {e}. Restarting in 30 seconds...")
            await asyncio.sleep(30)

async def health_monitoring():
    """Periodic health checks and reporting"""
    while True:
        await asyncio.sleep(3600)  # Every hour
        report = health_monitor.generate_health_report()
        
        # Add trading status
        status_report = report + "\n\n📊 <b>Trading Status:</b>\n"
        for symbol in trading_data:
            data = trading_data[symbol]
            status_report += (
                f"{symbol}: {data['daily_trade_count']}/2 trades, "
                f"Trend: {data['previous_day_trend'] or 'Unknown'}\n"
            )
        
        await send_telegram_alert(status_report, True)

async def main():
    """Main application entry point"""
    logger.info("Starting Crash/Boom Trading Bot...")
    
    # Verify symbol codes before starting
    logger.info("Verifying symbol codes...")
    await get_active_symbols()
    
    tasks = [
        deriv_websocket_connection(),
        health_monitoring()
    ]
    
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        error_msg = f"🔴 <b>Bot Crashed</b>\n\nError: {str(e)}\nTime: {datetime.now()}"
        asyncio.run(send_telegram_alert(error_msg))
