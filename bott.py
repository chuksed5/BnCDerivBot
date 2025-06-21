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
        "code": "CRASH1000",
        "name": "Crash 1000 Index",
        "signal_type": "SELL",
        "pip_size": 0.01,
        "tp_pips_min": 70,
        "tp_pips_max": 100,
        "sl_pips_min": 10,
        "sl_pips_max": 15,
        # Multi-timeframe subscriptions
        "timeframes": {
            "5m": {
                "granularity": 300,  # 5 minutes
                "ohlc_request": {
                    "ticks_history": "CRASH1000",
                    "end": "latest",
                    "count": 100,
                    "granularity": 300,
                    "style": "candles",
                    "subscribe": 1
                }
            },
            "30m": {
                "granularity": 1800,  # 30 minutes (your main timeframe)
                "ohlc_request": {
                    "ticks_history": "CRASH1000",
                    "end": "latest",
                    "count": 50,
                    "granularity": 1800,
                    "style": "candles",
                    "subscribe": 1
                }
            },
            "1h": {
                "granularity": 3600,  # 1 hour
                "ohlc_request": {
                    "ticks_history": "CRASH1000",
                    "end": "latest",
                    "count": 24,
                    "granularity": 3600,
                    "style": "candles",
                    "subscribe": 1
                }
            }
        }
    },
    "BOOM": {
        "code": "BOOM1000",
        "name": "Boom 1000 Index", 
        "signal_type": "BUY",
        "pip_size": 0.01,
        "tp_pips_min": 80,
        "tp_pips_max": 150,
        "sl_pips_min": 10,
        "sl_pips_max": 20,
        # Multi-timeframe subscriptions
        "timeframes": {
            "5m": {
                "granularity": 300,
                "ohlc_request": {
                    "ticks_history": "BOOM1000",
                    "end": "latest",
                    "count": 100,
                    "granularity": 300,
                    "style": "candles",
                    "subscribe": 1
                }
            },
            "30m": {
                "granularity": 1800,
                "ohlc_request": {
                    "ticks_history": "BOOM1000",
                    "end": "latest",
                    "count": 50,
                    "granularity": 1800,
                    "style": "candles",
                    "subscribe": 1
                }
            },
            "1h": {
                "granularity": 3600,
                "ohlc_request": {
                    "ticks_history": "BOOM1000",
                    "end": "latest",
                    "count": 24,
                    "granularity": 3600,
                    "style": "candles",
                    "subscribe": 1
                }
            }
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

# FIXED trading_data structure with proper flag management
trading_data = {
    "CRASH": {
        "previous_day_trend": None,
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "no_trade_alert_sent": False,
        "first_signal_price": None,
        "first_signal_sent": False,
        "price_went_against_us": False,
        "reversal_threshold_hit": False,
        "highest_price_after_first": None,
        "lowest_price_after_first": None,
        # FIXED: Proper daily reset flags
        "daily_reset_done": False,
        "conditions_checked_today": False,
        "last_signal_check_time": None,
        # Multi-timeframe data storage
        "timeframes": {
            "5m": {"candles": [], "trend": None, "momentum": None, "analysis": {}},
            "30m": {"candles": [], "trend": None, "momentum": None, "analysis": {}},
            "1h": {"candles": [], "trend": None, "momentum": None, "analysis": {}}
        },
        "mtf_alignment": {"score": 0, "signals": [], "signal_strength": 0},
        "last_mtf_analysis": None
    },
    "BOOM": {
        "previous_day_trend": None,
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "no_trade_alert_sent": False,
        "first_signal_price": None,
        "first_signal_sent": False,
        "price_went_against_us": False,
        "reversal_threshold_hit": False,
        "highest_price_after_first": None,
        "lowest_price_after_first": None,
        # FIXED: Proper daily reset flags
        "daily_reset_done": False,
        "conditions_checked_today": False,
        "last_signal_check_time": None,
        # Multi-timeframe data storage
        "timeframes": {
            "5m": {"candles": [], "trend": None, "momentum": None, "analysis": {}},
            "30m": {"candles": [], "trend": None, "momentum": None, "analysis": {}},
            "1h": {"candles": [], "trend": None, "momentum": None, "analysis": {}}
        },
        "mtf_alignment": {"score": 0, "signals": [], "signal_strength": 0},
        "last_mtf_analysis": None
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
            data["price_went_against_us"] = True
            return True
            
    elif symbol == "BOOM":  
        # BOOM is BUY signal, so price going DOWN means it went against us
        # Track lowest price after first signal
        if data["lowest_price_after_first"] is None or current_price < data["lowest_price_after_first"]:
            data["lowest_price_after_first"] = current_price
        
        # Check if price went significantly against us
        if current_price < first_entry - min_reversal_distance:
            data["reversal_threshold_hit"] = True
            data["price_went_against_us"] = True
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

def process_candle_data(symbol, candle, timeframe="30m"):
    """FIXED candle processing with proper logic flow"""
    try:
        health_monitor.message_count += 1
        
        # Handle new day reset
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
        
        # Store candle in appropriate timeframe
        if timeframe in data["timeframes"]:
            data["timeframes"][timeframe]["candles"].append(candle_data)
            
            # Keep only recent candles
            max_candles = {"5m": 200, "30m": 100, "1h": 50}
            if len(data["timeframes"][timeframe]["candles"]) > max_candles[timeframe]:
                data["timeframes"][timeframe]["candles"] = data["timeframes"][timeframe]["candles"][-max_candles[timeframe]:]
        
        # Process only on 30m timeframe for main logic
        if timeframe != "30m":
            return
            
        candle_epoch = candle_data["epoch"]
        candle_open = candle_data["open"]
        candle_close = candle_data["close"]
        
        # Handle first candle of the day
        if is_first_candle_of_day(candle_epoch) and data["first_candle_close"] is None:
            data["first_candle_close"] = candle_close
            logger.info(f"First candle of day stored for {symbol}: {candle_close}")
            
            if len(data["timeframes"]["30m"]["candles"]) >= 48:
                data["previous_day_trend"] = analyze_previous_day_trend(symbol, data["timeframes"]["30m"]["candles"])
                logger.info(f"Previous day trend for {symbol}: {data['previous_day_trend']}")
        
        # Skip if we don't have required data
        if (data["first_candle_close"] is None or 
            data["previous_day_trend"] is None or 
            data["daily_trade_count"] >= 2):
            return
            
        # Check if price went against us (for reversal setup)
        if data["first_signal_sent"]:
            check_if_price_went_against_us(symbol, candle_close)
        
        # FIXED: Prevent multiple signal checks per candle
        candle_time_key = f"{candle_epoch}_{symbol}"
        if data.get("last_signal_check_time") == candle_time_key:
            return
        data["last_signal_check_time"] = candle_time_key
        
        # FIRST SIGNAL LOGIC - SIMPLIFIED AND FIXED
        if data["daily_trade_count"] == 0:
            basic_conditions_met = False
            
            if symbol == "CRASH":
                if (data["previous_day_trend"] == "BEARISH" and
                    candle_close < candle_open and
                    candle_close < data["first_candle_close"]):
                    basic_conditions_met = True
                    
            elif symbol == "BOOM":
                if (data["previous_day_trend"] == "BULLISH" and
                    candle_close > candle_open and
                    candle_close > data["first_candle_close"]):
                    basic_conditions_met = True
            
            if basic_conditions_met:
                # FIXED: Simplified MTF check with lower threshold
                if not data["conditions_checked_today"]:
                    data["conditions_checked_today"] = True
                    
                    # LOWERED MTF threshold and simplified logic
                    can_trade, mtf_reason = should_take_trade_mtf_simplified(symbol)
                    
                    if can_trade:
                        # Send trade signal
                        alert = format_simple_trade_alert(symbol, symbol_config, candle_close, 
                                                        data["first_candle_close"], data["previous_day_trend"], 1)
                        
                        logger.info(f"FIRST SIGNAL - {symbol} {symbol_config['signal_type']} @ {candle_close}")
                        asyncio.create_task(send_telegram_alert(alert))
                        
                        data["daily_trade_count"] = 1
                        data["first_signal_sent"] = True
                        data["first_signal_price"] = candle_close
                        
                    else:
                        # Send rejection alert ONLY ONCE
                        rejection_alert = format_simple_rejection_alert(symbol, symbol_config, mtf_reason)
                        asyncio.create_task(send_telegram_alert(rejection_alert))
                        logger.info(f"{symbol} signal rejected: {mtf_reason}")
                        
        # SECOND SIGNAL LOGIC (REVERSAL) - SIMPLIFIED
        elif (data["daily_trade_count"] == 1 and 
              data["price_went_against_us"] and 
              check_reversal_conditions(symbol, candle_close)):
            
            signal_generated = False
            
            if symbol == "CRASH":
                if (candle_close < candle_open and
                    candle_close < data["first_candle_close"]):
                    
                    alert = format_reversal_trade_alert(symbol, symbol_config, candle_close,
                                                      data["first_candle_close"], data["previous_day_trend"],
                                                      data["first_signal_price"])
                    
                    logger.info(f"SECOND SIGNAL (REVERSAL) - CRASH SELL @ {candle_close}")
                    asyncio.create_task(send_telegram_alert(alert))
                    signal_generated = True
                    
            elif symbol == "BOOM":
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
        logger.error(f"Error processing candle for {symbol}-{timeframe}: {e}")


    # FIXED: More robust duplicate signal check
    current_time = datetime.utcnow()
    candle_time_key = f"{symbol}_{current_time.hour}_{current_time.minute // 30}"  # 30-min blocks
    
    if data.get("last_signal_check_time") == candle_time_key:
        return
    data["last_signal_check_time"] = candle_time_key
    
    # Add a cooldown period after rejection alert
    if data.get("last_rejection_time") and (current_time - data["last_rejection_time"]).total_seconds() < 3600:
        return
    
def should_take_trade_mtf_simplified(symbol):
    """Simplified MTF check with lower threshold"""
    score = calculate_mtf_alignment(symbol)
    
    # Lower threshold for testing
    if score >= 40:  # Reduced from 60
        return True, f"Basic MTF alignment: {score}%"
    return False, f"MTF alignment too weak: {score}%"

def format_simple_trade_alert(symbol, symbol_config, entry_price, first_candle_close, trend, signal_number):
    """Simplified trading alert"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    
    if symbol == "CRASH":
        sl = round(entry_price + (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price - (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "SELL"
        emoji = "🔻"
    else:  # BOOM
        sl = round(entry_price - (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price + (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "BUY"
        emoji = "🔺"
    
    return (
        f"🚨 <b>{symbol_config['name']} {signal_type} Signal #{signal_number}</b>\n\n"
        f"📊 Previous Day: {trend}\n"
        f"✅ Entry Condition: Met\n"
        f"📈 First Candle: {first_candle_close}\n\n"
        f"{emoji} <b>Trade Details:</b>\n"
        f"💰 Entry: {entry_price}\n"
        f"🎯 TP: {tp}\n"
        f"❌ SL: {sl}\n\n"
        f"📅 {current_time.split()[0]} ⏰ {current_time.split()[1]}\n"
        f"📈 Daily Count: {signal_number}/2"
    )

def format_simple_rejection_alert(symbol, symbol_config, reason):
    """Simplified rejection alert"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    
    return (
        f"⚠️ <b>SIGNAL FILTERED - {symbol_config['name']}</b>\n\n"
        f"❌ Reason: {reason}\n"
        f"⏳ Waiting for better setup...\n\n"
        f"📅 {current_time}"
    )

def reset_daily_data():
    """Reset daily trading data for new day with more robust checks"""
    current_date = datetime.utcnow().date()
    
    for symbol in trading_data:
        data = trading_data[symbol]
        last_reset_date = data.get("last_reset_date")
        
        if last_reset_date != current_date:
            # Reset all daily flags and counters
            data.update({
                "daily_trade_count": 0,
                "first_candle_close": None,
                "last_trade_date": current_date,
                "no_trade_alert_sent": False,
                "first_signal_price": None,
                "first_signal_sent": False,
                "price_went_against_us": False,
                "reversal_threshold_hit": False,
                "highest_price_after_first": None,
                "lowest_price_after_first": None,
                "conditions_checked_today": False,
                "last_signal_check_time": None,
                "last_rejection_time": None,
                "daily_reset_done": True,
                "last_reset_date": current_date
            })
            logger.info(f"Full daily reset for {symbol} on {current_date}")

def is_new_day():
    """Check if we're in a new trading day (UTC)"""
    now = datetime.utcnow()
    current_date = now.date()
    
    for symbol in trading_data:
        if trading_data[symbol]["last_trade_date"] != current_date:
            # Reset the daily_reset_done flag for new day
            trading_data[symbol]["daily_reset_done"] = False
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
    """Check if this is the first valid candle of the current day"""
    candle_time = datetime.utcfromtimestamp(candle_epoch)
    # Check between 00:00 and 00:45 UTC as first candle window
    return candle_time.hour == 0 and 0 <= candle_time.minute <= 45

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

def calculate_ema(prices, period):
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = prices[0]  # Start with the first price
    
    for price in prices[1:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    
    return ema

def calculate_rsi(prices, period=14):
    """Calculate Relative Strength Index"""
    if len(prices) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    if len(gains) < period:
        return None
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

def analyze_timeframe_trend(candles, timeframe):
    """Analyze trend and momentum for a specific timeframe"""
    if len(candles) < 20:
        return {"trend": "UNKNOWN", "momentum": "NEUTRAL", "strength": 0}
    
    closes = [float(c["close"]) for c in candles[-20:]]
    
    # Calculate EMAs
    ema_9 = calculate_ema(closes, 9)
    ema_21 = calculate_ema(closes, 21)
    
    # Calculate RSI
    rsi = calculate_rsi(closes, 14)
    
    # Determine trend
    current_price = closes[-1]
    trend = "UNKNOWN"
    strength = 0
    
    if ema_9 and ema_21:
        if ema_9 > ema_21 and current_price > ema_9:
            trend = "BULLISH"
            strength = min(((ema_9 - ema_21) / ema_21) * 100, 100)
        elif ema_9 < ema_21 and current_price < ema_9:
            trend = "BEARISH" 
            strength = min(((ema_21 - ema_9) / ema_21) * 100, 100)
        else:
            trend = "SIDEWAYS"
            strength = 0
    
    # Determine momentum
    momentum = "NEUTRAL"
    if rsi:
        if rsi > 70:
            momentum = "OVERBOUGHT"
        elif rsi < 30:
            momentum = "OVERSOLD"
        elif rsi > 50:
            momentum = "BULLISH"
        else:
            momentum = "BEARISH"
    
    return {
        "trend": trend,
        "momentum": momentum,
        "strength": strength,
        "rsi": rsi,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "current_price": current_price
    }

def calculate_mtf_alignment(symbol):
    """Calculate multi-timeframe alignment score"""
    data = trading_data[symbol]
    symbol_config = SYMBOLS[symbol]
    
    alignment_score = 0
    signal_strength = 0
    signals = []
    
    # Analyze each timeframe
    for tf_name, tf_data in data["timeframes"].items():
        if len(tf_data["candles"]) >= 20:
            analysis = analyze_timeframe_trend(tf_data["candles"], tf_name)
            tf_data["analysis"] = analysis
            
            # Weight timeframes (higher timeframes have more weight)
            weight = {"5m": 1, "30m": 3, "1h": 5}[tf_name]
            
            if symbol == "CRASH":
                # For CRASH (SELL signals), we want bearish alignment
                if analysis["trend"] == "BEARISH":
                    alignment_score += weight * 2
                    signal_strength += analysis["strength"] * weight
                elif analysis["trend"] == "SIDEWAYS" and analysis["momentum"] == "BEARISH":
                    alignment_score += weight * 1
                    signal_strength += (analysis["strength"] / 2) * weight
                
                # RSI confirmation for CRASH
                if analysis["rsi"] and analysis["rsi"] > 60:
                    alignment_score += weight * 0.5
                    
            elif symbol == "BOOM":
                # For BOOM (BUY signals), we want bullish alignment
                if analysis["trend"] == "BULLISH":
                    alignment_score += weight * 2
                    signal_strength += analysis["strength"] * weight
                elif analysis["trend"] == "SIDEWAYS" and analysis["momentum"] == "BULLISH":
                    alignment_score += weight * 1
                    signal_strength += (analysis["strength"] / 2) * weight
                
                # RSI confirmation for BOOM
                if analysis["rsi"] and analysis["rsi"] < 40:
                    alignment_score += weight * 0.5
            
            signals.append({
                "timeframe": tf_name,
                "trend": analysis["trend"],
                "momentum": analysis["momentum"],
                "strength": analysis["strength"],
                "weight": weight
            })
    
    # Normalize scores
    max_possible_score = (1 + 3 + 5) * 2.5  # Max weight * max multiplier
    normalized_score = min((alignment_score / max_possible_score) * 100, 100)
    
    data["mtf_alignment"] = {
        "score": round(normalized_score, 2),
        "signal_strength": round(signal_strength / 9, 2),  # Normalize by total weight
        "signals": signals,
        "timestamp": datetime.utcnow()
    }
    
    return normalized_score

def get_mtf_signal_quality(symbol):
    """Get signal quality based on multi-timeframe analysis"""
    score = calculate_mtf_alignment(symbol)
    
    if score >= 80:
        return "EXCELLENT", "🟢"
    elif score >= 65:
        return "GOOD", "🟡" 
    elif score >= 50:
        return "FAIR", "🟠"
    else:
        return "POOR", "🔴"

def should_take_trade_mtf(symbol):
    """Enhanced trade decision with multi-timeframe confirmation"""
    data = trading_data[symbol]
    
    # Calculate current alignment
    alignment_score = calculate_mtf_alignment(symbol)
    
    # Minimum score threshold for taking trades
    MIN_ALIGNMENT_SCORE = 60  # You can adjust this
    
    if alignment_score < MIN_ALIGNMENT_SCORE:
        logger.info(f"MTF alignment too weak for {symbol}: {alignment_score}% (need {MIN_ALIGNMENT_SCORE}%)")
        return False, f"Multi-timeframe alignment only {alignment_score}% (need {MIN_ALIGNMENT_SCORE}%+)"
    
    # Additional confluence checks
    confluence_count = 0
    
    # Check if 1H and 30M are aligned
    h1_analysis = data["timeframes"]["1h"].get("analysis", {})
    m30_analysis = data["timeframes"]["30m"].get("analysis", {})
    
    if symbol == "CRASH":
        if h1_analysis.get("trend") == "BEARISH":
            confluence_count += 2
        if m30_analysis.get("momentum") in ["BEARISH", "OVERBOUGHT"]:
            confluence_count += 1
    elif symbol == "BOOM":
        if h1_analysis.get("trend") == "BULLISH":
            confluence_count += 2
        if m30_analysis.get("momentum") in ["BULLISH", "OVERSOLD"]:
            confluence_count += 1
    
    if confluence_count >= 2:
        return True, f"Strong MTF alignment: {alignment_score}% with {confluence_count} confluence factors"
    else:
        return False, f"Insufficient confluence: {confluence_count}/3 factors aligned"

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

async def send_status_update():
    status = "🔄 <b>Bot Status</b>\n\n"
    for symbol in trading_data:
        data = trading_data[symbol]
        status += (
            f"<b>{symbol}</b>\n"
            f"Trades today: {data['daily_trade_count']}/2\n"
            f"First candle: {data['first_candle_close'] or 'Not set'}\n"
            f"Prev trend: {data['previous_day_trend'] or 'Unknown'}\n"
            f"MTF score: {data['mtf_alignment'].get('score', 0)}%\n\n"
        )
    await send_telegram_alert(status)

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
