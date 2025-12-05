"""
Camera / plate-detection application for Smart Parking System.

Responsibilities:
    - Open camera, read frames
    - Detect license plates using EasyOCR
    - Decide whether detection is an ENTRY or EXIT based on DB status
    - For ENTRY: call `log_entry` and notify server
    - For EXIT: call `log_exit`, show amount, notify server and QR page
"""

import cv2
import easyocr
import re
import time
import datetime
import imutils
import os
import sqlite3
from db_manager import *  # uses: DB_NAME, log_entry, log_exit
import requests
import webbrowser
import socketio as sio_client
import logging
import threading
from queue import Queue
from arduino_handler import ArduinoHandler
from typing import cast

# ==== Logging setup ==========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==== One-time setup: DB + OCR + SocketIO ===================================
try:
    connect_db()
    logger.info("Database connected successfully")
except Exception as e:
    logger.error(f"Database connection failed: {e}")

reader = easyocr.Reader(['en'], gpu=False)  # OCR engine
plate_pattern = r"[A-Z]{2}[0-9]{1,2}[A-Z]{0,2}[0-9]{3,4}"  # rough Indian plate regex

# SocketIO client is used to push events to the Flask dashboard in real-time.
sio = None
try:
    sio = sio_client.Client()
    sio.connect('http://localhost:5000', wait_timeout=1)
    logger.info("[SOCKET] Connected to server")
except Exception as e:
    logger.warning(f"[SOCKET] Could not connect to server: {e}")
    logger.info("[SOCKET] Running in offline mode")

def _on_sio_connect():
    logger.info("[SOCKET] Connected to server")


def _on_sio_disconnect():
    logger.info("[SOCKET] Disconnected from server")


def _on_sio_connect_error(data):
    logger.error(f"[SOCKET] Connection error: {data}")


if sio:
    # Register handlers only if `on` is available and callable. Some SocketIO client
    # implementations may not expose a callable `on` attribute, so we guard calls.
    try:
        on_callable = getattr(sio, 'on', None)
        if callable(on_callable):
            try:
                # Preferred signature: on(event, handler)
                on_callable('connect', _on_sio_connect)
                on_callable('disconnect', _on_sio_disconnect)
                on_callable('connect_error', _on_sio_connect_error)
            except TypeError:
                # Some clients may use a different registration API; fall back
                # to decorator-based registration or skip registering.
                try:
                    @sio.event
                    def connect():
                        _on_sio_connect()

                    @sio.event
                    def disconnect():
                        _on_sio_disconnect()

                    @sio.event
                    def connect_error(data):
                        _on_sio_connect_error(data)
                except Exception:
                    logger.debug("SocketIO registration fallback failed; continuing offline")
        else:
            # Fall back to decorator-based registration if supported
            try:
                @sio.event
                def connect():
                    _on_sio_connect()

                @sio.event
                def disconnect():
                    _on_sio_disconnect()

                @sio.event
                def connect_error(data):
                    _on_sio_connect_error(data)
            except Exception:
                logger.debug("SocketIO event registration skipped (offline mode)")
    except Exception:
        logger.debug("SocketIO event registration failed; continuing in offline mode")

# ==== Socket.IO Event Handler for Exit Gate Control =========================
def _on_open_exit_gate(data):
    """Handle exit gate open request from Flask app (after payment confirmation)"""
    plate = data.get('plate', 'Unknown')
    logger.info(f"🚧 [SOCKETIO] Received exit gate open request for {plate}")
    
    if arduino and arduino.is_connected():
        logger.info(f"🚧 Opening exit gate for {plate}...")
        arduino.open_exit_gate()
        time.sleep(3)  # Keep gate open for 3 seconds
        arduino.close_exit_gate()
        logger.info("✅ Exit gate closed")
    else:
        logger.warning("⚠️ Cannot open exit gate - Arduino not connected")

# Register exit gate control event handler
if sio:
    try:
        on_callable = getattr(sio, 'on', None)
        if callable(on_callable):
            try:
                on_callable('open_exit_gate', _on_open_exit_gate)
                logger.info("📡 Registered 'open_exit_gate' event handler")
            except:
                @sio.event
                def open_exit_gate(data):
                    _on_open_exit_gate(data)
    except Exception as e:
        logger.debug(f"Could not register exit gate handler: {e}")

# ==== Arduino Integration ====================================================
arduino = None
try:
    arduino = ArduinoHandler(port='COM6', auto_detect=False)
    if arduino.connect():
        logger.info("✅ Arduino connected on COM6")
    else:
        logger.warning("⚠️ Arduino not connected - running without gate control")
        arduino = None
except Exception as e:
    logger.warning(f"⚠️ Could not initialize Arduino: {e}")
    arduino = None

def clean_text(text: str) -> str | None:
    """Normalize raw OCR text and extract something that looks like a plate."""
    text = text.strip().upper().replace(" ", "")
    match = re.findall(plate_pattern, text)
    return match[0] if match else None

def detect_plate_easyocr(frame, save_path: str = "detections"):
    """
    Run EasyOCR on a single frame and return (plate_text, bounding_box).

    - bounding_box is (x, y, w, h) or (None, None) if nothing usable found.
    - Saves cropped plate images into `save_path/` for debugging.
    """
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        filtered = cv2.bilateralFilter(gray, 11, 17, 17)
        results = reader.readtext(filtered)

        for (bbox, text, prob) in results:
            if float(prob) < 0.6:
                continue
            plate = clean_text(text)
            if plate and len(bbox) == 4:
                try:
                    x_coords = [pt[0] for pt in bbox]
                    y_coords = [pt[1] for pt in bbox]
                    x = int(min(x_coords))
                    y = int(min(y_coords))
                    w = int(max(x_coords)) - x
                    h = int(max(y_coords)) - y
                    os.makedirs(save_path, exist_ok=True)
                    cropped = frame[y:y+h, x:x+w]
                    filename = os.path.join(save_path, f"{plate}_{int(time.time())}.jpg")
                    cv2.imwrite(filename, cropped)
                    logger.info(f"Plate detected: {plate} (confidence: {prob:.2f})")
                    return plate, (x, y, w, h)
                except Exception as e:
                    logger.warning(f"Error processing plate detection: {e}")
                    continue
        return None, None
    except Exception as e:
        logger.error(f"Plate detection error: {e}")
        return None, None

# ==== Runtime state ==========================================================
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    logger.error("Failed to open camera")
    exit(1)

logger.info("[INFO] Smart Parking Plate Detector Ready")

last_detected_plate = None
last_detection_time = 0
cooldown = 5
detection_mode = None  # Will be set by Arduino IR sensors: "entry" or "exit"
status_message = "🚗 Waiting for vehicle detection..."

def send_api_request(endpoint: str, data: dict) -> bool:
    """
    Helper to POST JSON data to the Flask server (dashboard / QR page).

    Returns True on HTTP 200, False otherwise (with warnings logged).
    """
    try:
        response = requests.post(f"http://localhost:5000{endpoint}", json=data, timeout=5)
        if response.status_code == 200:
            logger.info(f"API request successful: {endpoint}")
            return True
        else:
            logger.warning(f"API request failed: {endpoint} - Status: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"API request error: {endpoint} - {e}")
        return False

# ==== Arduino Event Callback =================================================
def handle_arduino_event(event: str):
    """
    Callback function triggered when Arduino detects IR sensor events.
    Sets detection_mode to trigger OCR processing.
    """
    global detection_mode, status_message
    
    if event == "ENTRY_DETECTED":
        logger.info("🚗 [ENTRY IR] Vehicle at entry gate - starting detection...")
        detection_mode = "entry"
        status_message = "🚗 Entry vehicle detected - scanning plate..."
        
    elif event == "EXIT_DETECTED":
        logger.info("🏁 [EXIT IR] Vehicle at exit gate - starting detection...")
        detection_mode = "exit"
        status_message = "🏁 Exit vehicle detected - scanning plate..."
        
    elif "GATE_OPENED" in event:
        logger.info(f"✅ {event}")
    elif "GATE_CLOSED" in event:
        logger.info(f"✅ {event}")

# Start Arduino event listener
if arduino:
    arduino.start_listening(handle_arduino_event)
    logger.info("🎧 Arduino event listener active")
else:
    logger.warning("⚠️ Running without Arduino - use 'c' key for manual detection")

# ==== Main camera loop =======================================================

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = imutils.resize(frame, width=640)
    key = cv2.waitKey(1) & 0xFF
    now = time.time()

    # Manual trigger (fallback if Arduino not connected)
    if key == ord('c') and not arduino:
        detection_mode = "entry"  # Default to entry for manual mode
        print("\n[MODE] Manual plate detection activated (ENTRY mode).")

    # When detection_mode is set (by pressing 'c'), run ORC and handle logic.
    if detection_mode:
        plate_text, box = detect_plate_easyocr(frame)
        if plate_text and box and (plate_text != last_detected_plate or now - last_detection_time > cooldown):
            last_detected_plate = plate_text
            last_detection_time = now
            x, y, w, h = box
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            logger.info(f"[DETECTED] Plate: {plate_text} | Mode: {detection_mode.upper()} | Time: {datetime.now().strftime('%H:%M:%S')}")

            # Process based on detection mode (entry, exit, or manual detect)
            if detection_mode in ["entry", "exit", "detect"]:
                # Check if plate is already in the database
                try:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("SELECT * FROM parking_log WHERE plate=? AND status='IN'", (plate_text,))
                    existing_entry = c.fetchone()
                    conn.close()
                    
                    if existing_entry:
                        # ---------------- EXIT branch ----------------
                        # This plate is currently IN the parking_log -> process as exit
                        status_message = f"\u26a0\ufe0f {plate_text} already parked. Processing exit."
                        logger.info(f"Already inside: {plate_text}")
                        
                        # Get exit information
                        result = log_exit(plate_text)
                        logger.info(f"Exit result: {result}")
                        
                        # Accept either a dict or the ExitResult dataclass from db_manager
                        info = None
                        if isinstance(result, dict):
                            info = cast(dict, result)
                        elif hasattr(result, 'amount'):
                            # Convert dataclass-like object to dict for downstream usage
                            info = {
                                'plate': getattr(result, 'plate', plate_text),
                                'amount': getattr(result, 'amount', None),
                                'duration_min': getattr(result, 'duration_min', None),
                                'entry_time': getattr(result, 'entry_time', None),
                                'exit_time': getattr(result, 'exit_time', None),
                            }

                        if info:
                            # Happy path: we have valid exit info
                            status_message = f"💵 ₹{info['amount']} for {info['duration_min']} min"
                            logger.info(
                                f"Exit info: {plate_text} | ₹{info['amount']} | "
                                f"{info['duration_min']} min"
                            )
                            
                            # Notify dashboard to show QR code
                            send_api_request(f"/api/set_pending_exit/{plate_text}", {})
                            
                            # Send real-time update to server
                            send_api_request("/api/plate_detected", {
                                'plate': plate_text,
                                'status': 'exit_pending',
                                'amount': info['amount'],
                                'duration': info['duration_min'],
                                'entry_time': info['entry_time'],
                                'exit_time': info['exit_time']
                            })
                            
                            # If exit was triggered by IR sensor, keep gate closed until payment
                            if detection_mode_was_exit:
                                logger.info("💳 Exit gate will open after payment confirmation")
                        elif result == "db_locked":
                            status_message = "🔒 DB Locked. Try again."
                            logger.error(f"[EXIT] DB LOCKED during exit for {plate_text}")
                        elif result == "not_found":
                            status_message = f"❌ Entry not found: {plate_text}"
                            logger.error(f"[EXIT] Entry not found for plate: {plate_text}")
                        elif result == "db_error":
                            status_message = f"❌ DB Error: {plate_text}"
                            logger.error(f"[EXIT] Database error for plate: {plate_text}")
                        elif result == "qr_error":
                            status_message = f"❌ QR Error: {plate_text}"
                            logger.error(f"[EXIT] QR code generation error for plate: {plate_text}")
                        else:
                            # Keep the on-screen message short; log full details
                            status_message = f"❌ Exit Error: {plate_text}"
                            logger.warning(
                                f"[EXIT] Unknown error processing exit for {plate_text}: {result}"
                            )
                    else:
                        # ---------------- ENTRY branch ---------------
                        # No active IN row -> treat as new entry
                        result = log_entry(plate_text)
                        if result == "already_in":
                            status_message = f"\u26a0\ufe0f {plate_text} already parked but not found in initial check."
                            logger.info(f"Already inside (unexpected): {plate_text}")
                        elif result == "db_locked":
                            status_message = f"🔒 DB Locked. Try again."
                            logger.error(f"DB LOCKED while entry: {plate_text}")
                        elif result == "full":
                            status_message = f"🚫 Parking full for {plate_text}"
                            logger.warning(f"Parking full: {plate_text}")
                        else:
                            status_message = f"✅ Entry logged: {plate_text}"
                            logger.info(f"Entry added: {plate_text}")
                            
                            # Open entry gate if Arduino is connected
                            if arduino and arduino.is_connected():
                                logger.info("🚧 Opening entry gate...")
                                arduino.open_entry_gate()
                                time.sleep(3)  # Keep gate open for 3 seconds
                                arduino.close_entry_gate()
                                logger.info("✅ Entry gate closed")
                            
                            # Send real-time update to server
                            send_api_request("/api/plate_detected", {
                                'plate': plate_text,
                                'status': 'entry_logged',
                                'entry_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                except Exception as e:
                    status_message = f"❌ Database error for {plate_text}: {str(e)}"
                    logger.error(f"Database error: {e}")
                    import traceback
                    traceback.print_exc()

            detection_mode = None
            print("-" * 60)

    cv2.putText(frame, status_message, (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    cv2.imshow("Smart Parking Plate Detector", frame)

    if key == ord('q'):
        break

# Cleanup
if arduino:
    arduino.disconnect()
    logger.info("Arduino disconnected")

cap.release()
cv2.destroyAllWindows()
