#!/bin/bash
# One command: start the HULMS UI and open the browser.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
( sleep 8; open http://localhost:3117 ) &
npm run dev
