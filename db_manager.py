import sqlite3
from datetime import datetime
import os
import time
import qrcode
import math
import logging

# Single canonical DB module, using the same logic you had before.

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_NAME = "parking.db"
RATE_PER_MIN = 1  # ₹1 per minute
TOTAL_SLOTS = 15
SLOT_LABELS = [f"A{i}" for i in range(1, TOTAL_SLOTS + 1)]


def connect_db():
    """Create the parking_log table if it doesn't exist."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS parking_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                entry_time TEXT,
                exit_time TEXT,
                duration_min INTEGER,
                amount REAL,
                status TEXT,
                slot TEXT
            )
        """
        )
        # Ensure slot column exists for older DBs
        c.execute("PRAGMA table_info(parking_log)")
        columns = [row[1] for row in c.fetchall()]
        if "slot" not in columns:
            try:
                c.execute("ALTER TABLE parking_log ADD COLUMN slot TEXT")
                logger.info("Added slot column to parking_log table")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        logger.info(f"[DB] Using DB file at: {os.path.abspath(DB_NAME)}")
    except Exception as e:
        logger.error(f"[DB] Database connection error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def wait_for_db_unlock(max_retries=5, wait_time=0.5):
    """Wait for the database to be unlocked, retrying if necessary."""
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_NAME)
            conn.execute("SELECT 1")
            conn.close()
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.warning(f"Database locked, attempt {attempt + 1}/{max_retries}")
                time.sleep(wait_time)
            else:
                logger.error(f"Database error: {e}")
                raise
    logger.error("Database unlock timeout")
    return False


def log_entry(plate):
    """Log a vehicle entry. Returns status string."""
    if not wait_for_db_unlock():
        logger.error("[DB ERROR - Entry] Database is locked.")
        return "db_locked"

    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT * FROM parking_log WHERE plate=? AND status='IN'", (plate,))
        if c.fetchone():
            logger.warning(f"Vehicle {plate} already parked")
            return "already_in"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        slot = find_next_available_slot()
        if slot is None:
            logger.warning(f"Parking full, cannot accommodate {plate}")
            return "full"

        c.execute(
            "INSERT INTO parking_log (plate, entry_time, status, slot) VALUES (?, ?, 'IN', ?)",
            (plate, now, slot),
        )
        conn.commit()
        logger.info(f"[DB] Entry logged: plate={plate}, slot={slot}, entry_time={now}")
        return "entry_logged"
    except Exception as e:
        logger.error(f"[DB ERROR - Entry] {e}")
        import traceback

        traceback.print_exc()
        return "db_error"
    finally:
        if conn:
            conn.close()


def generate_upi_qr(amount, plate, upi_id="dubeysagar744@oksbi", payee_name="ParkingLot"):
    """
    Generates a UPI QR code for the specified amount and plate number.
    The QR code is saved in the IMAGES/ directory and the file path is returned.
    """
    upi_url = f"upi://pay?pa={upi_id}&pn={payee_name}&am={amount}&cu=INR&tn={plate}"
    os.makedirs("IMAGES", exist_ok=True)
    filename = f"IMAGES/upi_qr_{plate}.png"
    qr = qrcode.make(upi_url)
    with open(filename, "wb") as f:
        qr.save(f)
    return filename


def log_exit(plate):
    """
    Log a vehicle exit, calculate amount, and generate UPI QR.
    Returns dict with details or error string.
    """
    print(f"[DEBUG] Starting exit process for plate: {plate}")
    if not wait_for_db_unlock():
        print("[DB ERROR - Exit] Database is locked.")
        return "db_locked"
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT id, entry_time, exit_time, status, slot FROM parking_log WHERE plate=? ORDER BY id DESC",
            (plate,),
        )
        all_entries = c.fetchall()
        print(f"[DEBUG] All entries for {plate}: {all_entries}")
        c.execute(
            "SELECT id, entry_time, slot FROM parking_log WHERE plate=? AND status='IN' ORDER BY id DESC LIMIT 1",
            (plate,),
        )
        row = c.fetchone()
        if not row:
            print(f"[DEBUG] No IN entry found for plate: {plate}")
            return "not_found"
        entry_id, entry_time_str, slot = row
        print(f"[DEBUG] Found IN entry: ID={entry_id}, Entry={entry_time_str}, Slot={slot}")
        # Try to parse entry_time, auto-correct if needed
        try:
            entry_time = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            print(f"[ERROR] Invalid entry time format: {entry_time_str} - {e}")
            # Try to auto-correct common formats
            try:
                entry_time = datetime.fromisoformat(entry_time_str)
                print(f"[FIX] Auto-corrected entry_time: {entry_time}")
            except Exception as e2:
                print(f"[ERROR] Could not auto-correct entry_time: {entry_time_str} - {e2}")
                return "db_error"
        exit_time = datetime.now()
        duration_seconds = (exit_time - entry_time).total_seconds()
        duration = max(1, math.ceil(duration_seconds / 60))
        amount = duration * RATE_PER_MIN
        # Use plain ASCII for console print to avoid Windows encoding errors with '₹'
        print(
            f"[DEBUG] Plate: {plate} | Entry: {entry_time_str} | "
            f"Exit: {exit_time.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"Duration: {duration} min | Amount: Rs {amount}"
        )
        try:
            qr_path = generate_upi_qr(amount, plate)
            print(f"[DEBUG] QR code generated: {qr_path}")
        except Exception as e:
            print(f"[ERROR] Failed to generate QR code: {e}")
            return "qr_error"
        # Do NOT update status to OUT yet, wait for payment confirmation
        result = {
            "plate": plate,
            "entry_time": entry_time_str,
            "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_min": duration,
            "amount": amount,
            "qr_path": qr_path,
            "slot": slot if slot else "Unknown",
        }
        print(f"[DEBUG] Exit result prepared: {result}")
        return result
    except Exception as e:
        print(f"[DB ERROR - Exit] {e}")
        import traceback

        traceback.print_exc()
        return "db_error"
    finally:
        if conn:
            conn.close()


def fetch_all_logs():
    """Fetch all parking logs, newest first."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT plate, entry_time, exit_time, duration_min, amount, status, slot "
            "FROM parking_log ORDER BY id DESC"
        )
        return c.fetchall()
    finally:
        if conn:
            conn.close()


def confirm_payment_and_exit(plate):
    """
    Marks payment as done for the given plate, updates status to OUT, sets exit_time, duration, and amount if not
    already set. Returns True if successful, False otherwise.
    """
    if not wait_for_db_unlock():
        print("[DB ERROR - Payment] Database is locked.")
        return False
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT id, entry_time, exit_time FROM parking_log WHERE plate=? AND status='IN' ORDER BY id DESC LIMIT 1",
            (plate,),
        )
        row = c.fetchone()
        if not row:
            return False
        entry_id, entry_time_str, exit_time_str = row
        if not exit_time_str:
            entry_time = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            exit_time = datetime.now()
            duration_seconds = (exit_time - entry_time).total_seconds()
            duration = max(1, math.ceil(duration_seconds / 60))
            amount = duration * RATE_PER_MIN
            c.execute(
                """
                UPDATE parking_log SET exit_time=?, duration_min=?, amount=?, status='OUT' WHERE id=?
            """,
                (exit_time.strftime("%Y-%m-%d %H:%M:%S"), duration, amount, entry_id),
            )
        else:
            c.execute("UPDATE parking_log SET status='OUT' WHERE id=?", (entry_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[DB ERROR - Payment] {e}")
        return False
    finally:
        if conn:
            conn.close()


def get_occupied_slots():
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT slot FROM parking_log WHERE status='IN'")
        return {row[0] for row in c.fetchall() if row[0]}
    finally:
        if conn:
            conn.close()


def find_next_available_slot():
    occupied = get_occupied_slots()
    for label in SLOT_LABELS:
        if label not in occupied:
            return label
    return None


def fetch_current_slots():
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT slot, plate FROM parking_log WHERE status='IN'")
        rows = c.fetchall()
        slots = {label: None for label in SLOT_LABELS}
        for slot, plate in rows:
            if slot in slots:
                slots[slot] = plate
        return slots
    finally:
        if conn:
            conn.close()

