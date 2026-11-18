#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 server.py --port 8766
