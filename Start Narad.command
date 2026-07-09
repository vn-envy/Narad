#!/bin/bash
# Double-clickable launcher — starts the Narad backend (:8000) and frontend (:5173).
cd "$(dirname "$0")"
exec ./dev.sh
