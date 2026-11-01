# APIMatic validation (`backend/apimatic_validation/`)

Targets the hackathon's "Best Use of APIMatic" prize (dossier section 5.2).

| File | What it is |
|------|------------|
| `paypal_subset.openapi.yaml` | OpenAPI 3.0 spec of exactly the PayPal endpoints and fields `paypal_escrow.py` uses (OAuth token, Orders v2 create with `payment_source.paypal.attributes.vault`, authorization capture/void, Payouts v1 create, verify-webhook-signature, payout-item webhook events). Sources are cited in the file header. Unconfirmed fields carry `x-aegispay-unverified: true`. |
| `aegispay_api.openapi.yaml` | AegisPay's own API, exported from FastAPI (`app.openapi()`, mock mode). |
| `export_openapi.py` | Regenerates the file above: `python -m backend.apimatic_validation.export_openapi` (add `--check` for CI staleness check). |
| `../../tests/test_apimatic_contract.py` | Drift tests: request bodies built by `PayPalEscrowEngine` (captured via `httpx.MockTransport`) and `MockPayPalEscrowEngine` responses are validated against the subset spec, including an "unknown field" check that catches renamed or invented fields. |

## Run the drift tests (no account needed)

```bash
uv run --no-project --with-requirements requirements-dev.txt python -m pytest -q tests/test_apimatic_contract.py
```

Known drift is recorded as `xfail(strict=True)` tests with the reason in the decorator; fixing the code makes
the xfail flip to a failure so the marker gets removed.

## APIMatic CLI status

The APIMatic CLI (`npx @apimatic/cli`, v2.0.0) installs and runs without an account, but **every
`api validate` / `sdk generate` call requires authentication**. Run without credentials on 2026-11-01:

```
$ npx @apimatic/cli api validate --file backend/apimatic_validation/paypal_subset.openapi.yaml
■  API validation failed
■  Authorization has been denied for this request. Please run apimatic auth login to log in via browser,
   or apimatic auth login --auth-key={api-key} to log in with an auth key.
```

No account was created and no credentials were used. Once someone has an APIMatic account / API key:

```bash
npm i -g @apimatic/cli            # or prefix every command below with: npx @apimatic/cli
apimatic auth login --auth-key=$APIMATIC_API_KEY     # or `apimatic auth login` (browser)

# Validate both specs (syntax + semantics)
apimatic api validate --file backend/apimatic_validation/paypal_subset.openapi.yaml
apimatic api validate --file backend/apimatic_validation/aegispay_api.openapi.yaml

# Generate a typed Python SDK (also: typescript, java, csharp, php, ruby, go)
apimatic sdk generate -l python --input backend/apimatic_validation   # needs the CLI's src/spec layout, see `apimatic quickstart`
```

For the prize write-up, also run `apimatic quickstart` (portal + SDK + Context Plugin) on the PayPal subset and
record the output here. CI does the validate step automatically (`.github/workflows/apimatic-validate.yml`)
when the `APIMATIC_API_KEY` repository secret exists; otherwise that step is skipped and the drift tests still run.
