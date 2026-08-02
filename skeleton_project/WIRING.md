WIRING (PCA9685 + Pi + Servo + LEDs)

- PCA9685 VCC -> Pi 3.3V
- PCA9685 GND -> Pi GND
- PCA9685 SCL/SDA -> Pi SCL/SDA (I2C-1)
- PCA9685 V+ -> 5–6V external (servo/LED power)
- Jaw Servo (CH0): Signal -> CH0 pin, + -> V+, - -> GND
- Eyes LEDs (CH4): Use a transistor/MOSFET and series resistor; never drive LED directly from PCA output.
- PIR: OUT -> GPIO17, VCC -> 5V (module dependent), GND -> GND

Use the Home Assistant guided calibration only after wiring is complete. It can
briefly energize the jaw, eyes, and speaker while Maintenance Mode is active.
Disconnect servo and LED power before changing wiring or linkage geometry, and
increase the jaw maximum gradually so the mechanism never binds.
- COMMON GROUND: External 5V, PCA9685, and Pi must share ground.
- MAINTENANCE MODE: Use the software lockout for powered observation and tuning;
  disconnect servo/LED power before hands-on linkage or wiring work. The Home
  Assistant switch is not an electrical disconnect.
