@echo off
echo ============================================================
echo AegisPay APIMatic SDK Generation - CodeGen v3
echo ============================================================

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe apimatic\export_sdks.py
) else (
    python apimatic\export_sdks.py
)

if not "%APIMATIC_API_KEY%"=="" (
    echo Running live APIMatic validation...
    call npx --yes @apimatic/cli@2.0.0 auth login --auth-key="%APIMATIC_API_KEY%"
    call npx --yes @apimatic/cli@2.0.0 api validate --file backend\apimatic_validation\aegispay_api.openapi.yaml
)
