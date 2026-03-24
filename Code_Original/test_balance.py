# test_balance.py
import time
from read_balance import BalanceSerialReader, parse_balance_line

def handle_balance_line(line: str):
    print(f"Raw: {line}")
    parsed = parse_balance_line(line)
    if parsed:
        ST, GS, check, weight, unit = parsed
        print(f"Parsed: ST={ST}, GS={GS}, check={check}, weight={weight}, unit={unit}")

def main():
    print("Starting balance reader... Press Ctrl+C to stop.")
    reader = BalanceSerialReader(callback=handle_balance_line, interval=1)
    reader.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        reader.stop()
        time.sleep(0.5)

if __name__ == "__main__":
    main()
