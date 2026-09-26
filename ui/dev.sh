#!/bin/bash
# One command: start the Canvas Coach UI and open the browser.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
trap 'kill 0' EXIT
node feed-proxy.mjs &
( sleep 8; open http://localhost:3117 ) &
npm run dev
