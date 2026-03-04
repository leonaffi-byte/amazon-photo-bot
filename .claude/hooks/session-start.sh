#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python dependencies
pip install -r "$CLAUDE_PROJECT_DIR/requirements.txt" --quiet

# Install test dependencies
pip install pytest pytest-asyncio --quiet

# Install Playwright Chromium for scraping tests
playwright install --with-deps chromium 2>/dev/null || true
