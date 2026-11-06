#!/usr/bin/env bash
set -e

echo "============================================================"
echo "AegisPay APIMatic SDK Generation - CodeGen v3"
echo "============================================================"

# Check Python environment
if [ -d ".venv" ]; then
    .venv/bin/python apimatic/export_sdks.py
else
    python3 apimatic/export_sdks.py
fi

# Optional direct APIMatic CLI execution if API key is present
if [ -n "$APIMATIC_API_KEY" ]; then
    echo "Running live APIMatic validation..."
    npx --yes @apimatic/cli@2.0.0 auth login --auth-key="$APIMATIC_API_KEY"
    npx --yes @apimatic/cli@2.0.0 api validate --file backend/apimatic_validation/aegispay_api.openapi.yaml
fi
