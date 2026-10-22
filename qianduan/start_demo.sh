#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 demo/simulate_device.py --scenario both --seconds 120 --records 6
