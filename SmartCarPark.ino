/*
 * Smart Parking System - Arduino Sketch
 * 
 * Hardware Configuration:
 * - Entry IR Sensor: Pin 2
 * - Exit IR Sensor: Pin 3
 * - Entry Servo Motor: Pin 9
 * - Exit Servo Motor: Pin 10
 * 
 * Communication: Serial @ 9600 baud
 * 
 * Events sent to Python:
 * - ENTRY_DETECTED
 * - EXIT_DETECTED
 * - ENTRY_GATE_OPENED / ENTRY_GATE_CLOSED
 * - EXIT_GATE_OPENED / EXIT_GATE_CLOSED
 * 
 * Commands received from Python:
 * - OPEN_ENTRY
 * - CLOSE_ENTRY
 * - OPEN_EXIT
 * - CLOSE_EXIT
 */

#include <Servo.h>

// ===== Pin Configuration =====
const int ENTRY_IR_PIN = 2;
const int EXIT_IR_PIN = 3;
const int ENTRY_SERVO_PIN = 9;
const int EXIT_SERVO_PIN = 10;

// ===== Servo Objects =====
Servo entryServo;
Servo exitServo;

// ===== Gate Positions =====
const int GATE_CLOSED = 0;   // 0 degrees = closed
const int GATE_OPEN = 90;    // 90 degrees = open

// ===== IR Sensor States =====
int entryIRState = HIGH;
int lastEntryIRState = HIGH;
int exitIRState = HIGH;
int lastExitIRState = HIGH;

// ===== Debouncing =====
unsigned long lastEntryDebounceTime = 0;
unsigned long lastExitDebounceTime = 0;
const unsigned long debounceDelay = 500;  // 500ms debounce

// ===== Gate State Tracking =====
bool entryGateOpen = false;
bool exitGateOpen = false;

void setup() {
  // Initialize serial communication
  Serial.begin(9600);
  
  // Initialize IR sensor pins as INPUT with internal pull-up
  pinMode(ENTRY_IR_PIN, INPUT_PULLUP);
  pinMode(EXIT_IR_PIN, INPUT_PULLUP);
  
  // Attach servos
  entryServo.attach(ENTRY_SERVO_PIN);
  exitServo.attach(EXIT_SERVO_PIN);
  
  // Initialize gates to closed position
  entryServo.write(GATE_CLOSED);
  exitServo.write(GATE_CLOSED);
  
  delay(500);
  
  Serial.println("Arduino Parking System Ready");
  Serial.println("IR Sensors: Entry=Pin2, Exit=Pin3");
  Serial.println("Servos: Entry=Pin9, Exit=Pin10");
}

void loop() {
  // Read IR sensor states
  int currentEntryIR = digitalRead(ENTRY_IR_PIN);
  int currentExitIR = digitalRead(EXIT_IR_PIN);
  
  // ===== Entry IR Sensor Detection =====
  // IR sensors are active LOW (LOW = object detected)
  if (currentEntryIR != lastEntryIRState) {
    lastEntryDebounceTime = millis();
  }
  
  if ((millis() - lastEntryDebounceTime) > debounceDelay) {
    if (currentEntryIR != entryIRState) {
      entryIRState = currentEntryIR;
      
      // When IR sensor goes LOW, object is detected
      if (entryIRState == LOW) {
        Serial.println("ENTRY_DETECTED");
      }
    }
  }
  lastEntryIRState = currentEntryIR;
  
  // ===== Exit IR Sensor Detection =====
  if (currentExitIR != lastExitIRState) {
    lastExitDebounceTime = millis();
  }
  
  if ((millis() - lastExitDebounceTime) > debounceDelay) {
    if (currentExitIR != exitIRState) {
      exitIRState = currentExitIR;
      
      // When IR sensor goes LOW, object is detected
      if (exitIRState == LOW) {
        Serial.println("EXIT_DETECTED");
      }
    }
  }
  lastExitIRState = currentExitIR;
  
  // ===== Process Serial Commands from Python =====
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    
    if (command == "OPEN_ENTRY") {
      openEntryGate();
    } 
    else if (command == "CLOSE_ENTRY") {
      closeEntryGate();
    } 
    else if (command == "OPEN_EXIT") {
      openExitGate();
    } 
    else if (command == "CLOSE_EXIT") {
      closeExitGate();
    }
  }
  
  delay(50);  // Small delay to prevent excessive CPU usage
}

// ===== Gate Control Functions =====

void openEntryGate() {
  if (!entryGateOpen) {
    entryServo.write(GATE_OPEN);
    entryGateOpen = true;
    Serial.println("ENTRY_GATE_OPENED");
  }
}

void closeEntryGate() {
  if (entryGateOpen) {
    entryServo.write(GATE_CLOSED);
    entryGateOpen = false;
    Serial.println("ENTRY_GATE_CLOSED");
  }
}

void openExitGate() {
  if (!exitGateOpen) {
    exitServo.write(GATE_OPEN);
    exitGateOpen = true;
    Serial.println("EXIT_GATE_OPENED");
  }
}

void closeExitGate() {
  if (exitGateOpen) {
    exitServo.write(GATE_CLOSED);
    exitGateOpen = false;
    Serial.println("EXIT_GATE_CLOSED");
  }
}
