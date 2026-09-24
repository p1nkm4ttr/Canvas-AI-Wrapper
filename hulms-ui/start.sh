#!/bin/bash
# Production launcher: builds once (or when asked), then serves. Much
# snappier than dev.sh -- no HMR, no on-demand compilation, no React
# dev-mode double rendering. Use dev.sh only when changing UI code.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
if [ "$1" = "--build" ] || [ ! -f ".next/BUILD_ID" ]; then
  echo "Building production bundle..."
  if ! npm run build; then
    echo "Build failed."
    read -r -p "Press Enter to close. "
    exit 1
  fi
fi
( sleep 4; open http://localhost:3117 ) &
npm start
