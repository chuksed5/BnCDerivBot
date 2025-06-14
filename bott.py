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
DERIV_APP_ID = os.getenv('DERIV_APP_ID', '1089')  # Default Deriv app ID
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

# Production-optimized symbol configuration
SYMBOLS = {
    "CRASH": {
        "code": "1HZ100V",
        "name": "Crash 1000 Index",
        "signal_type": "SELL",
        "pip_size": 0.1,
        "tp_pips": 85,
        "sl_pips": 15,
        "ohlc_request": {
            "ticks_history": "1HZ100V",
            "end": "latest",
            "count": 1,
            "granularity": 1800,  # 30 minutes
            "style": "candles",
            "subscribe": 1
        }
    },
    "BOOM": {
        "code": "1HZ150V",
        "name": "Boom 1000 Index",
        "signal_type": "BUY",
        "pip_size": 0.1,
        "tp_pips": 100,
        "sl_pips": 15,
        "ohlc_request": {
            "ticks_history": "1HZ150V",
            "end": "latest",
            "count": 1,
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

# Global state
current_day = None
health_monitor = BotHealthMonitor()
first_candle_data = {
    "CRASH": {"price": None, "alert_count": 0, "last_alert_time": None},
    "BOOM": {"price": None, "alert_count": 0, "last_alert_time": None}
}

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

def format_alert(symbol_config, entry_price, first_candle_close):
    """Optimized alert formatting"""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    
    if symbol_config["signal_type"] == "SELL":
        sl = round(first_candle_close + symbol_config["sl_pips"] * symbol_config["pip_size"], 1)
        tp = round(entry_price - symbol_config["tp_pips"] * symbol_config["pip_size"], 1)
        direction = "below"
        emoji = "🔻"
    else:  # BUY
        sl = round(first_candle_close - symbol_config["sl_pips"] * symbol_config["pip_size"], 1)
        tp = round(entry_price + symbol_config["tp_pips"] * symbol_config["pip_size"], 1)
        direction = "above"
        emoji = "🔺"
    
    return (
        f"🚨 <b>{symbol_config['name']} {symbol_config['signal_type']} Signal</b>\n\n"
        f"{emoji} Breakout {direction} first 30m candle close\n"
        f"🎯 Entry: {entry_price}\n💰 TP: {tp}\n❌ SL: {sl}\n\n"
        f"📆 Date: {current_time.split()[0]}\n⏰ Time: {current_time.split()[1]}"
    )

def is_new_trading_day():
    """Check if UTC date has changed"""
    global current_day
    now = datetime.utcnow()
    today = now.date()
    
    if current_day != today:
        logger.info(f"New trading day detected: {today}")
        current_day = today
        return True
    return False

def reset_daily_data():
    """Reset daily tracking data"""
    for symbol in first_candle_data:
        first_candle_data[symbol] = {"price": None, "alert_count": 0, "last_alert_time": None}
    logger.info("Daily data reset complete")

def process_candle(symbol, candle):
    """Production-optimized candle processing"""
    try:
        health_monitor.message_count += 1
        
        if is_new_trading_day():
            reset_daily_data()
        
        symbol_config = SYMBOLS[symbol]
        data = first_candle_data[symbol]
        
        candle_close = float(candle.get("close", 0))
        candle_open = float(candle.get("open", 0))
        candle_epoch = int(candle.get("epoch", 0))
        
        # Get current time in UTC
        now = datetime.utcnow()
        current_minute = now.minute
        current_hour = now.hour
        
        # Only consider candles at :00 or :30 minutes (30-minute intervals)
        if current_minute not in [0, 30]:
            logger.debug(f"Ignoring candle at non-30min interval: {now}")
            return
            
        # Store first candle of the day (only at market open)
        if data["price"] is None:
            # Only accept first candle at market open time (adjust to your timezone)
            if current_hour == 0 and current_minute == 0:  # Midnight UTC
                data["price"] = candle_close
                data["first_candle_time"] = now
                logger.info(f"First candle for {symbol} at {now}: {candle_close}")
                if DEVELOPMENT_MODE:
                    test_msg = f"🔹 First candle stored for {symbol_config['name']} @ {candle_close}"
                    asyncio.create_task(send_telegram_alert(test_msg, True))
                return
            else:
                logger.debug(f"Ignoring potential first candle at wrong time: {now}")
                return
        
        # Don't process signals until we have a first candle reference
        if data["price"] is None:
            logger.debug("No first candle reference yet")
            return
            
        # Check signal conditions (only after first candle is set)
        if symbol == "CRASH":
            if (candle_close < candle_open and 
                candle_close < data["price"] and 
                data["alert_count"] < 2):
                alert = format_alert(symbol_config, candle_close, data["price"])
                logger.info(f"CRASH SELL signal @ {candle_close}")
                asyncio.create_task(send_telegram_alert(alert))
                data["alert_count"] += 1
                data["last_alert_time"] = datetime.utcnow()
        
        elif symbol == "BOOM":
            if (candle_close > candle_open and 
                candle_close > data["price"] and 
                data["alert_count"] < 2):
                alert = format_alert(symbol_config, candle_close, data["price"])
                logger.info(f"BOOM BUY signal @ {candle_close}")
                asyncio.create_task(send_telegram_alert(alert))
                data["alert_count"] += 1
                data["last_alert_time"] = datetime.utcnow()
                
    except Exception as e:
        logger.error(f"Error processing candle: {e}")

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
                    f"🟢 <b>Deriv Signal Bot Started</b>\n\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"💻 {platform.node()}\n"
                    f"🐍 Python {platform.python_version()}"
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
                            
                        if data.get("msg_type") == "ohlc":
                            ohlc = data.get("ohlc", {})
                            if ohlc:
                                symbol = "CRASH" if "1HZ100V" in ohlc.get("symbol", "") else "BOOM"
                                process_candle(symbol, {
                                    "open": ohlc["open"],
                                    "close": ohlc["close"],
                                    "symbol": ohlc["symbol"]
                                })
                                
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
        await send_telegram_alert(report, True)
        logger.info("Sent hourly health report")

async def main():
    """Main application entry point"""
    logger.info("Starting Deriv Trading Signal Bot in production mode")
    
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