#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="${PROJECT_DIR}/PromptFit/PromptFit.xcodeproj"

if [[ ! -d "/Applications/Xcode.app" ]]; then
  osascript -e 'display dialog "PromptFit needs the full Xcode app before it can be installed on your iPhone. The Mac App Store will open next." buttons {"Open App Store"} default button "Open App Store" with title "Install Xcode first"'
  open "macappstore://itunes.apple.com/app/id497799835"
  exit 0
fi

open -a Xcode "$PROJECT_PATH"
osascript -e 'display dialog "In Xcode: connect and select your iPhone, choose your Personal Team under Signing & Capabilities, then press the triangular Run button." buttons {"OK"} default button "OK" with title "Install PromptFit"'
