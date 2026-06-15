     1|#!/bin/bash
     2|# /vol1/1000/HD1/APP/hermes/awork/fnmon/start.sh
     3|# Start the CPU Monitor service
     4|cd "$(dirname "$0")"
     5|exec python3 app.py
     6|