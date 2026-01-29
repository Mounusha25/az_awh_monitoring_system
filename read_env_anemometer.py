import serial

def read_env_anemometer(serial_port='/dev/ttyUSB2', baud_rate=9600):
    ser = serial.Serial(serial_port, baud_rate, timeout=1)
    buffer = b''  # Buffer to store incomplete data

    def decode_value(byte1, byte2, factor, offset=0):
        value = (byte1 << 8) + byte2
        return (value / factor) + offset

    def decode_velocity(bytes_9_to_14):
        value1 = (bytes_9_to_14[0] << 8) + bytes_9_to_14[1]
        value2 = (bytes_9_to_14[2] << 8) + bytes_9_to_14[3]
        value3 = (bytes_9_to_14[4] << 8) + bytes_9_to_14[5]
        average_value = (value1 + value2 + value3) / 3
        scale_factor = 1.77 / ((0x80 * 3) / 3)
        velocity = average_value * scale_factor
        adjusted_velocity = velocity - 0.01
        return round(adjusted_velocity, 2)

    def determine_unit_and_conversion(byte3, byte4):
        if byte3 == 0x10:  # Velocity mode
            if byte4 == 0x00:
                return "m/s", 1
            elif byte4 == 0x04:
                return "km/h", 3.6
            elif byte4 == 0x08:
                return "MPH", 2.23694
            elif byte4 == 0x0C:
                return "ft/m", 196.850394
            elif byte4 == 0x10:
                return "ft/s", 3.28084
        return "Unknown Unit", 1

    def decode_temperature(bytes_7_to_8):
        raw_value = (bytes_7_to_8[0] << 8) + bytes_7_to_8[1]
        temperature = (raw_value - 0x0238) * (27.3 - 26.8) / (0x023d - 0x0238) + 26.8
        return round(temperature, 1)

    def decode_humidity(bytes_5_to_6):
        raw_value = (bytes_5_to_6[0] << 8) + bytes_5_to_6[1]
        humidity = (raw_value - 0x0141) * (32.3 - 32.1) / (0x0143 - 0x0141) + 32.1
        return round(humidity, 1)

    def process_packet(packet):
        byte3 = packet[2]
        byte4 = packet[3]
        unit, conversion_factor = determine_unit_and_conversion(byte3, byte4)
        humidity = decode_humidity(packet[4:6])
        temperature = decode_temperature(packet[6:8])
        velocity = decode_velocity(packet[8:14])
        print(f"Decoded Data -> Humidity: {humidity}%, Temperature: {temperature}°C, Velocity: {velocity} {unit}")
        return humidity, temperature, velocity, unit

    def find_start_of_packet(buffer):
        start_sequence = b'\xeb\xa0'
        start_index = buffer.find(start_sequence)
        if start_index != -1 and len(buffer[start_index:]) >= 16:
            return start_index
        return -1

    try:
        while True:
            incoming_data = ser.read(ser.in_waiting or 1)
            buffer += incoming_data

            start_index = find_start_of_packet(buffer)
            if start_index != -1:
                if len(buffer[start_index:]) >= 16:
                    packet = buffer[start_index:start_index + 16]
                    humidity, temperature, velocity, unit = process_packet(packet)
                    buffer = buffer[start_index + 16:]
                    return humidity, temperature, velocity, unit
                else:
                    print("Incomplete packet, waiting for more data.")
    except KeyboardInterrupt:
        print("Exiting...")

    finally:
        ser.close()

if __name__ == "__main__":
    # Test the function with a connected meter
    try:
        humidity, temperature, velocity, unit = read_env_anemometer()
        print(f"Test Result -> Humidity: {humidity}%, Temperature: {temperature}°C, Velocity: {velocity} {unit}")
    except Exception as e:
        print(f"An error occurred: {e}")