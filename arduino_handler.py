"""
Arduino Communication Handler for Smart Parking System

This module handles serial communication between Python and Arduino Uno R3.
- Listens for IR sensor events (ENTRY_DETECTED, EXIT_DETECTED)
- Sends gate control commands (OPEN_ENTRY, CLOSE_ENTRY, OPEN_EXIT, CLOSE_EXIT)
- Manages servo motor operations via Arduino
"""

import serial
import serial.tools.list_ports
import time
import threading
import logging

logger = logging.getLogger(__name__)


class ArduinoHandler:
    """Handler for Arduino serial communication and gate control"""
    
    def __init__(self, port='COM6', baudrate=9600, auto_detect=False):
        """
        Initialize Arduino connection
        
        Args:
            port: Serial port (e.g., 'COM6', 'COM3' for Windows; '/dev/ttyUSB0' for Linux)
            baudrate: Communication speed (must match Arduino sketch - default 9600)
            auto_detect: If True, automatically find Arduino port
        """
        if auto_detect:
            detected_port = self.find_arduino_port()
            self.port = detected_port if detected_port else port
        else:
            self.port = port
            
        self.baudrate = baudrate
        self.serial_conn = None
        self.running = False
        self.callback = None
        self.connected = False
        
    def find_arduino_port(self):
        """
        Auto-detect Arduino COM port
        
        Returns:
            str: Detected port name or None if not found
        """
        ports = serial.tools.list_ports.comports()
        for port in ports:
            # Look for Arduino, CH340, or common USB-Serial chips
            if any(keyword in port.description.upper() for keyword in ['ARDUINO', 'CH340', 'USB-SERIAL']):
                logger.info(f"Auto-detected Arduino on {port.device}")
                return port.device
        logger.warning("Could not auto-detect Arduino port")
        return None
    
    def connect(self):
        """
        Establish serial connection with Arduino
        
        Returns:
            bool: True if connected successfully, False otherwise
        """
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1,
                write_timeout=1
            )
            time.sleep(2)  # Wait for Arduino to reset after connection
            self.connected = True
            logger.info(f"✅ Connected to Arduino on {self.port}")
            
            # Read initial message from Arduino
            time.sleep(0.5)
            if self.serial_conn.in_waiting > 0:
                initial_msg = self.serial_conn.readline().decode('utf-8').strip()
                logger.info(f"Arduino says: {initial_msg}")
            
            return True
            
        except serial.SerialException as e:
            logger.error(f"❌ Failed to connect to Arduino on {self.port}: {e}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error connecting to Arduino: {e}")
            self.connected = False
            return False
    
    def start_listening(self, callback):
        """
        Start background thread to listen for Arduino events
        
        Args:
            callback: Function to call when Arduino sends data (e.g., IR sensor events)
        """
        if not self.connected:
            logger.warning("Cannot start listening - Arduino not connected")
            return False
            
        self.callback = callback
        self.running = True
        listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        listener_thread.start()
        logger.info("🎧 Arduino event listener started")
        return True
    
    def _listen_loop(self):
        """Background thread loop to continuously listen for Arduino messages"""
        while self.running:
            try:
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8').strip()
                    if line:
                        logger.info(f"📡 Arduino → {line}")
                        
                        # Call the registered callback with the event
                        if self.callback:
                            self.callback(line)
                            
            except UnicodeDecodeError:
                logger.warning("Could not decode Arduino message")
            except Exception as e:
                logger.error(f"Error reading from Arduino: {e}")
                
            time.sleep(0.05)  # Small delay to prevent CPU overuse
    
    def send_command(self, command):
        """
        Send command to Arduino
        
        Args:
            command: Command string (e.g., 'OPEN_ENTRY', 'CLOSE_EXIT')
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        if not self.connected or not self.serial_conn:
            logger.warning(f"Cannot send command '{command}' - Arduino not connected")
            return False
            
        try:
            self.serial_conn.write(f"{command}\n".encode('utf-8'))
            self.serial_conn.flush()
            logger.info(f"📤 Python → Arduino: {command}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending command '{command}' to Arduino: {e}")
            return False
    
    # ===== Gate Control Commands =====
    
    def open_entry_gate(self):
        """Open entry gate (servo motor at entry)"""
        return self.send_command("OPEN_ENTRY")
    
    def close_entry_gate(self):
        """Close entry gate"""
        return self.send_command("CLOSE_ENTRY")
    
    def open_exit_gate(self):
        """Open exit gate (servo motor at exit)"""
        return self.send_command("OPEN_EXIT")
    
    def close_exit_gate(self):
        """Close exit gate"""
        return self.send_command("CLOSE_EXIT")
    
    # ===== Connection Management =====
    
    def is_connected(self):
        """Check if Arduino is connected"""
        return self.connected and self.serial_conn is not None
    
    def disconnect(self):
        """Close Arduino connection and stop listener"""
        self.running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
                self.connected = False
                logger.info("Disconnected from Arduino")
            except Exception as e:
                logger.error(f"Error disconnecting from Arduino: {e}")
    
    def __del__(self):
        """Cleanup on object destruction"""
        self.disconnect()


# ===== Standalone test code =====
if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 50)
    print("Arduino Handler Test")
    print("=" * 50)
    
    # Test callback function
    def test_callback(event):
        print(f"🔔 Event received: {event}")
        if event == "ENTRY_DETECTED":
            print("   → Vehicle at ENTRY gate!")
        elif event == "EXIT_DETECTED":
            print("   → Vehicle at EXIT gate!")
    
    # Initialize and connect
    arduino = ArduinoHandler(port='COM6', auto_detect=False)
    
    if arduino.connect():
        print("\n✅ Arduino connected successfully!")
        arduino.start_listening(test_callback)
        
        print("\nTesting gate controls...")
        print("Opening entry gate...")
        arduino.open_entry_gate()
        time.sleep(3)
        
        print("Closing entry gate...")
        arduino.close_entry_gate()
        time.sleep(1)
        
        print("\nListening for IR sensor events... (Press Ctrl+C to stop)")
        print("Trigger your IR sensors to test!")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nStopping test...")
            arduino.disconnect()
            print("✅ Test complete!")
    else:
        print("\n❌ Could not connect to Arduino on COM6")
        print("Please check:")
        print("  1. Arduino is plugged in")
        print("  2. Arduino code is uploaded")
        print("  3. COM6 is the correct port")
