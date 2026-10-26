#!/usr/bin/env bash
# Superseded by the numbered scripts in deploy/azure/ (your values now live in deploy/azure/config.env).
# Kept so old instructions still work: this deploys only the web app.
set -euo pipefail
exec bash "$(dirname "$0")/azure/05_webapp.sh"
