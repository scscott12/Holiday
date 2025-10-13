WIRING (PCA9685 + Pi + Servo + LEDs)

- PCA9685 VCC -> Pi 3.3V
- PCA9685 GND -> Pi GND
- PCA9685 SCL/SDA -> Pi SCL/SDA (I2C-1)
- PCA9685 V+ -> 5–6V external (servo/LED power)
- Jaw Servo (CH0): Signal -> CH0 pin, + -> V+, - -> GND
- Eyes LEDs (CH4): Use a transistor/MOSFET and series resistor; never drive LED directly from PCA output.
- PIR: OUT -> GPIO17, VCC -> 5V (module dependent), GND -> GND
- COMMON GROUND: External 5V, PCA9685, and Pi must share ground.
