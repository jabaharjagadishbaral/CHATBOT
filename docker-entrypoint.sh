#!/bin/sh
set -e
trap 'kill 0' EXIT

python server.py &
SERVER_PID=$!

exec python bridge.py
