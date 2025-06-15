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

# Corrected symbol configuration based on the trading strategy
SYMBOLS = {
    "CRASH": {
        "code": "1HZ100V",
        "name": "Crash 1000 Index",
        "signal_type": "SELL",  # Only SELL for crash
        "pip_size": 0.01,  # Crash pip size is typically 0.01
        "tp_pips_min": 70,
        "tp_pips_max": 100,
        "sl_pips_min": 10,
        "sl_pips_max": 15,
        "ohlc_request": {
            "ticks_history": "1HZ100V",
            "end": "latest",
            "count": 50,  # Get more historical data
            "granularity": 1800,  # 30 minutes
            "style": "candles",
            "subscribe": 1
        }
    },
    "BOOM": {
        "code": "1HZ150V",
        "name": "Boom 1000 Index", 
        "signal_type": "BUY",  # Only BUY for boom
        "pip_size": 0.01,  # Boom pip size is typically 0.01
        "tp_pips_min": 80,
        "tp_pips_max": 150,
        "sl_pips_min": 10,
        "sl_pips_max": 20,
        "ohlc_request": {
            "ticks_history": "1HZ150V",
            "end": "latest",
            "count": 50,  # Get more historical data
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
trading_data = {
    "CRASH": {
        "previous_day_trend": None,  # "BEARISH" or "BULLISH"
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "historical_candles": [],
        "no_trade_alert_sent": False  # Track if we already sent no-trade alert today
    },
    "BOOM": {
        "previous_day_trend": None,  # "BEARISH" or "BULLISH"
        "first_candle_close": None,
        "daily_trade_count": 0,
        "last_trade_date": None,
        "historical_candles": [],
        "no_trade_alert_sent": False  # Track if we already sent no-trade alert today
    }
}

def is_new_day():
    """Check if we're in a new trading day (UTC)"""
    now = datetime.utcnow()
    current_date = now.date()
    
    for symbol in trading_data:
        if trading_data[symbol]["last_trade_date"] != current_date:
            return True
    return False

def reset_daily_data():
    """Reset daily trading data for new day"""
    current_date = datetime.utcnow().date()
    
    for symbol in trading_data:
        # Reset daily counters but preserve historical data
        trading_data[symbol]["daily_trade_count"] = 0
        trading_data[symbol]["first_candle_close"] = None
        trading_data[symbol]["last_trade_date"] = current_date
        trading_data[symbol]["no_trade_alert_sent"] = False  # Reset alert flag
    
    logger.info(f"Daily data reset for {current_date}")

def analyze_previous_day_trend(symbol, candles):
    """
    Analyze previous day trend based on the strategy:
    - Look at all 30min candles from previous day
    - Determine if market was buying (bullish) or selling (bearish)
    """
    if len(candles) < 48:  # Need at least 2 days of 30min candles
        return None
    
    # Get previous day candles (last 48 candles = yesterday's data)
    previous_day_candles = candles[-96:-48]  # 48 candles ago to 48 candles ago
    
    if not previous_day_candles:
        return None
    
    # Calculate trend based on overall price movement
    day_open = float(previous_day_candles[0]["open"])
    day_close = float(previous_day_candles[-1]["close"])
    
    # Count bullish vs bearish candles
    bullish_count = 0
    bearish_count = 0
    
    for candle in previous_day_candles:
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        
        if close_price > open_price:
            bullish_count += 1
        elif close_price < open_price:
            bearish_count += 1
    
    # Determine overall trend
    if day_close > day_open and bullish_count > bearish_count:
        return "BULLISH"
    elif day_close < day_open and bearish_count > bullish_count:
        return "BEARISH"
    else:
        # If unclear, use candle count as tie-breaker
        return "BULLISH" if bullish_count > bearish_count else "BEARISH"

def is_first_candle_of_day(candle_epoch):
    """Check if this is the first 30min candle of the current day"""
    candle_time = datetime.utcfromtimestamp(candle_epoch)
    
    # First candle is at 00:00 or 00:30 UTC
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
        # SELL signal for crash
        sl = round(entry_price + (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price - (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "SELL"
        direction = "below"
        emoji = "🔻"
        condition = f"Bearish candle closed below first candle close ({first_candle_close})"
    else:  # BOOM
        # BUY signal for boom
        sl = round(entry_price - (symbol_config["sl_pips_max"] * symbol_config["pip_size"]), 2)
        tp = round(entry_price + (symbol_config["tp_pips_min"] * symbol_config["pip_size"]), 2)
        signal_type = "BUY"
        direction = "above"
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

def process_candle_data(symbol, candle):
    """Process candle according to the correct trading strategy"""
    try:
        health_monitor.message_count += 1
        
        if is_new_day():
            reset_daily_data()
        
        symbol_config = SYMBOLS[symbol]
        data = trading_data[symbol]
        
        # Add candle to historical data
        candle_data = {
            "open": float(candle.get("open", 0)),
            "close": float(candle.get("close", 0)),
            "high": float(candle.get("high", 0)),
            "low": float(candle.get("low", 0)),
            "epoch": int(candle.get("epoch", 0))
        }
        
        data["historical_candles"].append(candle_data)
        
        # Keep only last 200 candles (about 4 days)
        if len(data["historical_candles"]) > 200:
            data["historical_candles"] = data["historical_candles"][-200:]
        
        candle_epoch = candle_data["epoch"]
        candle_open = candle_data["open"]
        candle_close = candle_data["close"]
        
        # Check if this is the first candle of the day
        if is_first_candle_of_day(candle_epoch) and data["first_candle_close"] is None:
            data["first_candle_close"] = candle_close
            logger.info(f"First candle of day stored for {symbol}: {candle_close}")
            
            # Analyze previous day trend
            if len(data["historical_candles"]) >= 48:
                data["previous_day_trend"] = analyze_previous_day_trend(symbol, data["historical_candles"])
                logger.info(f"Previous day trend for {symbol}: {data['previous_day_trend']}")
                
                # Send NO TRADE alert if market conditions don't match our rules
                if symbol == "CRASH" and data["previous_day_trend"] != "BEARISH":
                    if not data["no_trade_alert_sent"]:  # Only send once per day
                        no_trade_alert = format_no_trade_alert(symbol, symbol_config, 
                                                             data["previous_day_trend"], "wrong_trend")
                        asyncio.create_task(send_telegram_alert(no_trade_alert))
                        data["no_trade_alert_sent"] = True
                        logger.info(f"NO TRADE alert sent for {symbol} - Previous day was {data['previous_day_trend']}")
                
                elif symbol == "BOOM" and data["previous_day_trend"] != "BULLISH":
                    if not data["no_trade_alert_sent"]:  # Only send once per day
                        no_trade_alert = format_no_trade_alert(symbol, symbol_config,
                                                             data["previous_day_trend"], "wrong_trend")
                        asyncio.create_task(send_telegram_alert(no_trade_alert))
                        data["no_trade_alert_sent"] = True
                        logger.info(f"NO TRADE alert sent for {symbol} - Previous day was {data['previous_day_trend']}")
            
            return
        
        # Skip if we don't have required data
        if (data["first_candle_close"] is None or 
            data["previous_day_trend"] is None or 
            data["daily_trade_count"] >= 2):
            return
        
        # Check trading conditions based on strategy
        if symbol == "CRASH":
            # For CRASH: Only trade if previous day was BEARISH
            if (data["previous_day_trend"] == "BEARISH" and
                candle_close < candle_open and  # Bearish candle
                candle_close < data["first_candle_close"]):  # Closed below first candle
                
                alert = format_trade_alert(symbol, symbol_config, candle_close, 
                                         data["first_candle_close"], data["previous_day_trend"])
                
                logger.info(f"CRASH SELL signal generated @ {candle_close}")
                asyncio.create_task(send_telegram_alert(alert))
                data["daily_trade_count"] += 1
                
        elif symbol == "BOOM":
            # For BOOM: Only trade if previous day was BULLISH
            if (data["previous_day_trend"] == "BULLISH" and
                candle_close > candle_open and  # Bullish candle
                candle_close > data["first_candle_close"]):  # Closed above first candle
                
                alert = format_trade_alert(symbol, symbol_config, candle_close,
                                         data["first_candle_close"], data["previous_day_trend"])
                
                logger.info(f"BOOM BUY signal generated @ {candle_close}")
                asyncio.create_task(send_telegram_alert(alert))
                data["daily_trade_count"] += 1
                
    except Exception as e:
        logger.error(f"Error processing candle for {symbol}: {e}")

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
                
                # Send startup notification
                startup_msg = (
                    f"🟢 <b>Crash/Boom Strategy Bot Started</b>\n\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"💻 {platform.node()}\n"
                    f"🐍 Python {platform.python_version()}\n\n"
                    f"📊 <b>Strategy Rules:</b>\n"
                    f"🔻 CRASH: SELL only when prev day bearish\n"
                    f"🔺 BOOM: BUY only when prev day bullish\n"
                    f"📈 Max 2 trades per day per symbol\n\n"
                    f"⚠️ <b>Important:</b>\n"
                    f"• Bot will start analyzing from tomorrow\n"
                    f"• Today is for data collection only\n"
                    f"• Signals will begin after first full day analysis"
                )
                await send_telegram_alert(startup_msg, True)
                
                # Subscribe to market data
                for symbol in SYMBOLS:
                    await ws.send(json.dumps(SYMBOLS[symbol]["ohlc_request"]))
                    logger.info(f"Subscribed to {symbol} OHLC data")
                
                # Main message processing loop
                async for message in ws:
                    try:
                        data = json.loads(message)
                        
                        if "error" in data:
                            logger.error(f"API error: {data['error']['message']}")
                            continue
                            
                        if data.get("msg_type") == "candles":
                            # Handle historical candles response
                            candles = data.get("candles", [])
                            symbol_code = data.get("echo_req", {}).get("ticks_history", "")
                            
                            if "1HZ100V" in symbol_code:
                                symbol = "CRASH"
                            elif "1HZ150V" in symbol_code:
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
                                symbol = "CRASH" if "1HZ100V" in ohlc.get("symbol", "") else "BOOM"
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
        status_report = report + "\n\n<b>Trading Status:</b>\n"
        for symbol in trading_data:
            data = trading_data[symbol]
            status_report += (f"{symbol}: {data['daily_trade_count']}/2 trades, "
                            f"Trend: {data['previous_day_trend'] or 'Unknown'}\n")
        
        await send_telegram_alert(status_report, True)
        logger.info("Sent hourly health report")

async def main():
    """Main application entry point"""
    logger.info("Starting Deriv Trading Signal Bot with Crash/Boom Strategy")
    
    # Start health monitoring
    health_task = asyncio.create_task(health_monitoring())
    
    try:
        await deriv_websocket_connection()
    except asyncio.CancelledError:
        logger.info("Bot shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        health_task.cancel()
        shutdown_msg = (
            "🔴 <b>Deriv Bot Stopped</b>\n\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🕒 Uptime: {str(health_monitor.get_uptime()).split('.')[0]}"
        )
        await send_telegram_alert(shutdown_msg, True)

if __name__ == "__main__":
    # Auto-restart loop with crash protection
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"Bot crashed: {e}. Restarting in 10 seconds...")
            time.sleep(10)
