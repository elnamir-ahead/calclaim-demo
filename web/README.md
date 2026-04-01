# CalcClaim demo web portal

Single-page UI served by the API at **`/demo/ui/`** (redirect from **`/demo`**).

## Run locally

From the repo root:

```bash
pip install -r requirements.txt
uvicorn lambda.handler:app --reload --host 0.0.0.0 --port 8000
```

Open **http://127.0.0.1:8000/demo** — the page calls **`POST /claims/adjudicate`** on the same origin.

The UI includes a **calcClaim2 & PDF test alignment** section (static pipeline + pytest mapping) and, after each adjudication, a **live pipeline** card showing which calcClaim2 stages ran and the `calc_claim2` JSON from the API.

## Deployed API elsewhere

If the UI is opened from another host (or static S3), set **API base URL** in the portal settings (stored in `localStorage`) to your API Gateway base, e.g. `https://xxxx.execute-api.us-east-1.amazonaws.com/prod` (no trailing slash).

## Industry context

- **Web / mobile** portals are the usual human-facing channel; **EDI / NCPDP batch** and **call-center** tools often share the same adjudication APIs behind the scenes.
- This demo focuses on **HTTPS + JSON** (OpenAPI contract) and a **printable** adjudication summary for stakeholders.
