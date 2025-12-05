"""
Flask + Socket.IO web application for Smart Parking System.

Responsibilities:
    - Dashboard (`/`) showing live slots and history
    - QR payment flow (`/qr`, `/exit/<plate>`, `/api/exit_info/<plate>`, `/api/confirm_exit`)
    - API used by camera app (`/api/plate_detected`, `/api/set_pending_exit`, `/api/get_pending_exit`)
    - Stats + logs JSON APIs
"""

from db_manager import *  # uses: connect_db, DB_NAME, fetch_all_logs, etc.
from flask import Flask, render_template, request, abort, send_file, url_for, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
from dataclasses import is_dataclass, asdict
import re
import shutil
import os
import qrcode
import threading
from datetime import datetime
import sqlite3
import logging
from functools import wraps
from typing import Any, cast
from arduino_handler import ArduinoHandler
import time

# ==== Logging / app setup ====================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='.')  # templates (dashb-*.html, qr.html) live in this folder
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# Initialize database schema (safe to call every startup)
try:
    connect_db()
    logger.info("Database connected successfully")
except Exception as e:
    logger.error(f"Database connection failed: {e}")

# ==== Arduino Integration ====================================================
# NOTE: Arduino is NOT initialized here to avoid COM port conflict!
# Only nesm.py connects to Arduino for IR sensor detection and gate control.
# Flask app communicates with nesm.py via Socket.IO events.
arduino = None  # Disabled - handled by nesm.py

# Simple in-memory pending exit plate (used by QR page)
pending_exit_lock = threading.Lock()
pending_exit_plate = None

# Very light "rate limiting" decorator (placeholder – currently no real limits)
def rate_limit(max_per_minute=60):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Simple rate limiting implementation
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==== HTML routes ============================================================

@app.route('/')
def dashboard():
    """Main dashboard route – shows logs + current slots."""
    try:
        logs = fetch_all_logs()
        slots = fetch_current_slots()
        logger.info(f"Dashboard loaded with {len(logs)} logs and {len(slots)} slots")
        # Use your existing dashboard template file name
        return render_template('dashb-LAPTOP-0RFHURIK.html', logs=logs, slots=slots)
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return render_template('dashb-LAPTOP-0RFHURIK.html', logs=[], slots={}, error=str(e))


# Serve QR code images from IMAGES directory
@app.route('/images/<filename>')
def images(filename):
    return send_from_directory('IMAGES', filename)

# ==== SMS webhook (optional, for auto-payment confirmation) ==================
TRACCAR_TOKEN = os.environ.get('TRACCAR_TOKEN', 'your-token-here')

@app.route('/sms_webhook', methods=['POST'])
@rate_limit(max_per_minute=30)
def sms_webhook():
    """Handle SMS webhook for payment confirmation"""
    try:
        token = request.form.get('token')
        token_preview = (token[:10] + '...') if token else 'no-token'
        if token != TRACCAR_TOKEN:
            logger.warning(f"Unauthorized SMS webhook attempt with token: {token_preview}")
            abort(403)

        sms_text = request.form.get('message', '')
        logger.info(f"SMS received: {sms_text[:50]}...")
        
        # Example SMS: "INR 40.00 received via UPI from abc@okicici. Ref: MH12AB1234"
        match = re.search(r'INR (\d+\.\d{2}) received.*Ref:.*?([A-Z0-9]+)', sms_text)
        if match:
            amount = float(match.group(1))
            plate = match.group(2)
            logger.info(f"Payment detected: ₹{amount} for plate {plate}")
            
            # Confirm payment and exit
            result = confirm_payment_and_exit(plate)
            if result:
                logger.info(f"Payment confirmed for {plate}")
                return "Payment confirmed, exit allowed", 200
            else:
                logger.warning(f"Payment confirmation failed for {plate}")
                return f"Payment received but not matched: {result}", 200
        return "No match", 200
    except Exception as e:
        logger.error(f"SMS webhook error: {e}")
        return "Internal error", 500

@app.route('/check_status/<plate>')
def check_status(plate):
    """Lightweight API to check last known status for given plate."""
    try:
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT status FROM parking_log WHERE plate=? ORDER BY id DESC LIMIT 1", (plate,))
            row = c.fetchone()
        finally:
            if conn:
                conn.close()
        if row:
            return jsonify({'status': row[0], 'plate': plate})
        return jsonify({'status': 'not_found', 'plate': plate})
    except Exception as e:
        logger.error(f"Status check error for {plate}: {e}")
        return jsonify({'status': 'error', 'plate': plate, 'error': str(e)}), 500

@app.route('/exit/<plate>')
def exit_page(plate):
    """Generate exit page with QR code for payment (used by browser)."""
    try:
        exit_info = log_exit(plate)
        # Normalize dataclass ExitResult to dict if necessary
        if is_dataclass(exit_info):
            info = asdict(exit_info)
        elif isinstance(exit_info, dict):
            info = cast(dict, exit_info)
        else:
            info = None

        if info:
            qr_filename = os.path.basename(info['qr_path'])
            qr_url = url_for('images', filename=qr_filename)
            logger.info(f"Exit page generated for {plate}: ₹{info['amount']}")
            return render_template(
                'exit.html',
                plate=info['plate'],
                duration=info['duration_min'],
                amount=info['amount'],
                qr_url=qr_url,
                plate_for_status=info['plate']
            )
        else:
            logger.error(f"Exit page error for {plate}: {exit_info}")
            return f"Error: {exit_info}", 400
    except Exception as e:
        logger.error(f"Exit page exception for {plate}: {e}")
        return f"Internal error: {str(e)}", 500

@app.route('/api/exit_info/<plate>')
def api_exit_info(plate):
    exit_info = log_exit(plate)
    # Normalize dataclass ExitResult to dict if necessary
    if is_dataclass(exit_info):
        info = asdict(exit_info)
    elif isinstance(exit_info, dict):
        info = cast(dict, exit_info)
    else:
        info = None

    if info:
        qr_filename = os.path.basename(info['qr_path'])
        qr_url = url_for('images', filename=qr_filename)
        return jsonify({
            'plate': info['plate'],
            'entry_time': info['entry_time'],
            'exit_time': info['exit_time'],
            'duration_min': info['duration_min'],
            'amount': info['amount'],
            'qr_url': qr_url
        })
    else:
        return jsonify({'error': str(exit_info)}), 400

@app.route('/api/confirm_exit', methods=['POST'])
def api_confirm_exit():
    data = request.get_json()
    plate = data.get('plate')
    if not plate:
        return jsonify({'success': False, 'error': 'No plate provided'}), 400
    result = confirm_payment_and_exit(plate)
    if result:
        # Clear pending exit plate after payment
        global pending_exit_plate
        with pending_exit_lock:
            if pending_exit_plate == plate:
                pending_exit_plate = None
                print(f"[API] Pending exit plate cleared after payment: {plate}")
        
        
        # Request nesm.py to open exit gate via Socket.IO
        # (nesm.py handles Arduino connection to avoid COM port conflict)
        socketio.emit('open_exit_gate', {'plate': plate})
        logger.info(f"🚧 Requested exit gate opening for {plate}")
            
        # Emit payment confirmation event
        socketio.emit('payment_confirmed', {'plate': plate})
        broadcast_logs_update()
        broadcast_slots_update()
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Could not confirm exit'}), 500

@app.route('/api/plate_detected', methods=['POST'])
def plate_detected():
    data = request.get_json()
    plate = data.get('plate')
    status = data.get('status')
    amount = data.get('amount')
    duration = data.get('duration')
    entry_time = data.get('entry_time')
    exit_time = data.get('exit_time')
    
    if not plate or not status:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    # Emit plate detection event with all available information
    event_data = {
        'plate': plate,
        'status': status
    }
    
    # Add optional fields if they exist
    if amount is not None:
        event_data['amount'] = amount
    if duration is not None:
        event_data['duration'] = duration
    if entry_time is not None:
        event_data['entry_time'] = entry_time
    if exit_time is not None:
        event_data['exit_time'] = exit_time
        
    socketio.emit('plate_detected', event_data)
    if status == 'entry_logged':
        broadcast_logs_update()
        broadcast_slots_update()
    return jsonify({'success': True})

@app.route('/api/set_pending_exit/<plate>', methods=['POST'])
def set_pending_exit(plate):
    global pending_exit_plate
    with pending_exit_lock:
        pending_exit_plate = plate
        print(f"[API] Pending exit plate set: {plate}")
    # Emit WebSocket event for real-time update
    socketio.emit('pending_exit', {'plate': plate})
    return jsonify({'success': True, 'pending_exit': plate})

@app.route('/api/get_pending_exit')
def get_pending_exit():
    global pending_exit_plate
    with pending_exit_lock:
        plate = pending_exit_plate
        print(f"[API] Pending exit plate retrieved (not cleared): {plate}")
    return jsonify({'pending_exit': plate})

@app.route('/qr')
def qr_page():
    """Standalone QR code payment page – shows current pending exit, if any."""
    return render_template('qr.html')

# New API endpoints for better functionality
@app.route('/api/stats')
def api_stats():
    """Return summary statistics used by dashboard cards."""
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # Total vehicles today
        c.execute("SELECT COUNT(*) FROM parking_log WHERE DATE(entry_time) = DATE('now')")
        today_entries = c.fetchone()[0]
        
        # Currently parked vehicles
        c.execute("SELECT COUNT(*) FROM parking_log WHERE status='IN'")
        currently_parked = c.fetchone()[0]
        
        # Total revenue today
        c.execute("SELECT SUM(amount) FROM parking_log WHERE DATE(exit_time) = DATE('now') AND status='OUT'")
        today_revenue = c.fetchone()[0] or 0
        
        # Available slots
        occupied_slots = get_occupied_slots()
        available_slots = TOTAL_SLOTS - len(occupied_slots)
        
        conn.close()
        
        return jsonify({
            'today_entries': today_entries,
            'currently_parked': currently_parked,
            'today_revenue': today_revenue,
            'available_slots': available_slots,
            'total_slots': TOTAL_SLOTS
        })
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
def api_logs():
    """Return raw logs list for dashboard / debugging."""
    try:
        logs = fetch_all_logs()
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f"Logs API error: {e}")
        return jsonify({'error': str(e)}), 500

# ==== WebSocket event handlers (real-time updates to dashboard) =============
@socketio.on('connect')
def handle_connect():
    logger.info('[SOCKET] Client connected')
    # Send current logs to the newly connected client
    try:
        logs = fetch_all_logs()
        emit('logs_update', {'logs': logs})
        # Send current slots map as well
        emit('slots_update', {'slots': fetch_current_slots()})
        logger.info(f'[SOCKET] Sent {len(logs)} logs to new client')
    except Exception as e:
        logger.error(f'[SOCKET] Error sending initial data: {e}')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('[SOCKET] Client disconnected')

# Function to broadcast parking logs to all connected clients
def broadcast_logs_update():
    logs = fetch_all_logs()
    socketio.emit('logs_update', {'logs': logs})

def broadcast_slots_update():
    try:
        slots = fetch_current_slots()
        socketio.emit('slots_update', {'slots': slots})
    except Exception:
        pass

# Helper functions to emit custom events (entry/exit) if you want to hook them
def emit_entry_event(plate, status):
    socketio.emit('entry_event', {
        'plate': plate,
        'status': status,
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    # Also update the logs
    broadcast_logs_update()

def emit_exit_event(plate: str, exit_info: Any):
    # Normalize dataclass ExitResult to dict if necessary
    if is_dataclass(exit_info):
        # `asdict` expects a dataclass instance; at runtime we only call this
        # when that is true, so we can safely ignore the static type warning.
        info = asdict(exit_info)  # type: ignore[arg-type]
    elif isinstance(exit_info, dict):
        info = exit_info
    else:
        info = None

    if info:
        socketio.emit('exit_event', {
            'plate': plate,
            'status': 'success',
            'amount': info['amount'],
            'duration': info['duration_min'],
            'entry_time': info['entry_time'],
            'exit_time': info['exit_time']
        })
    else:
        socketio.emit('exit_event', {
            'plate': plate,
            'status': 'error',
            'message': str(exit_info)
        })
    # Also update the logs
    broadcast_logs_update()

if __name__ == '__main__':
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)

# The following test code can be run separately if needed:
# plate = "MH12AB1234"
# entry_status = log_entry(plate)
# print(f"🚗 Entry Status: {entry_status}")
# exit_info = log_exit(plate)
# print(f"🏁 Exit Info: {exit_info}")
