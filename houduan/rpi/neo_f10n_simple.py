#!/usr/bin/env python3
"""NEO-F10N GPS reader for Raspberry Pi GPIO UART. Prints WGS84 positions."""

import argparse
import re
import sys
import time


def _degrees(nmea_value, hemisphere, is_latitude):
    digits = 2 if is_latitude else 3
    if hemisphere not in (("N", "S") if is_latitude else ("E", "W")):
        return None
    if len(nmea_value) < digits + 2:
        return None
    degrees_text = nmea_value[:digits]
    minutes_text = nmea_value[digits:]
    if not degrees_text.isdigit() or not re.fullmatch(r"\d{2}(?:\.\d+)?", minutes_text):
        return None
    degrees, minutes = int(degrees_text), float(minutes_text)
    limit = 90 if is_latitude else 180
    if degrees > limit or minutes >= 60 or (degrees == limit and minutes != 0):
        return None
    result = degrees + minutes / 60
    return -result if hemisphere in ("S", "W") else result


def parse_rmc(line):
    """Return (latitude, longitude, UTC time) or None for invalid NMEA RMC."""
    try:
        line = line.decode("ascii").strip() if isinstance(line, bytes) else line.strip()
    except UnicodeDecodeError:
        return None
    if not line.startswith("$") or line.count("*") != 1 or len(line) > 256:
        return None
    payload, check = line[1:].split("*", 1)
    if not re.fullmatch(r"[0-9A-Fa-f]{2}", check):
        return None
    checksum = 0
    for character in payload:
        checksum ^= ord(character)
    if checksum != int(check, 16):
        return None

    fields = payload.split(",")
    if len(fields) < 7 or not re.fullmatch(r"[A-Z]{2}RMC", fields[0]):
        return None
    if fields[2] != "A":  # A = valid; V = no fix
        return None
    if len(fields) > 12 and fields[12] and fields[12] not in ("A", "D"):
        return None  # Reject N/E/M/S: no fix, estimated, manual, simulated

    latitude = _degrees(fields[3], fields[4], True)
    longitude = _degrees(fields[5], fields[6], False)
    if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
        return None
    return latitude, longitude, fields[1]


def main():
    arguments = argparse.ArgumentParser(description="NEO-F10N GPIO UART GPS location monitor")
    arguments.add_argument("--port", default="/dev/serial0", help="Pi UART device")
    arguments.add_argument("--baud", type=int, default=38400, help="NEO-F10N UART baud rate")
    args = arguments.parse_args()

    try:
        import serial
    except ImportError:
        print("缺少 pyserial。请先执行：python3 -m pip install pyserial", file=sys.stderr)
        return 2

    print(f"NEO-F10N GPS 监听：{args.port}，{args.baud} 波特率，8N1", flush=True)
    print("等待卫星定位……按 Ctrl+C 退出。", flush=True)
    try:
        while True:
            try:
                with serial.Serial(args.port, args.baud, timeout=1,
                                   bytesize=8, parity="N", stopbits=1) as receiver:
                    print("串口已连接（仍需等待 GPS 有效定位）", flush=True)
                    last_notice = time.monotonic()
                    while True:
                        position = parse_rmc(receiver.readline())
                        if position is not None:
                            latitude, longitude, utc_time = position
                            print(f"纬度 latitude={latitude:.7f}  "
                                  f"经度 longitude={longitude:.7f}  "
                                  f"GPS_UTC={utc_time}  坐标系=WGS84", flush=True)
                            last_notice = time.monotonic()
                        elif time.monotonic() - last_notice >= 10:
                            print("等待有效定位：请检查天线、卫星信号和接线……", flush=True)
                            last_notice = time.monotonic()
            except (serial.SerialException, OSError) as error:
                print(f"串口异常：{error}；2 秒后重新连接……", file=sys.stderr, flush=True)
                time.sleep(2)
    except KeyboardInterrupt:
        print("\nGPS 监听已结束。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
