import serial
import time

# Configure the serial port
ser = serial.Serial(
    port='/dev/ttyAMA0',  # Serial port
    baudrate=9600,        # Baud rate
    parity=serial.PARITY_NONE,  # Parity
    stopbits=serial.STOPBITS_ONE,  # Stop bits
    bytesize=serial.EIGHTBITS,  # Data bits
    timeout=1            # Read timeout
)

# Check if the serial port is open
if ser.is_open:
    print("Serial port is open.")
else:
    print("Failed to open serial port.")

try:
    # Send data
    ser.write(b'Hello, serial port!\n')
    print("Data sent.")

    # Wait for a response
    time.sleep(1)

    # Read response
    response = ser.readline()
    if response:
        print("Received data:", response.decode().strip())
    else:
        print("No data received.")
finally:
    # Close the serial port
    ser.close()
    print("Serial port closed.")
