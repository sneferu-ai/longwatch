# LongWatch examples

Five runnable examples, in the order you would use them. Every one was executed
against a live LongWatch server (Postgres 16 + ffmpeg 7) before this document
shipped.

## Prerequisites

- A running LongWatch server. If you do not have one, follow the quick start in
  the [README](../../README.md) first.
- `curl` and `python3` (both ship in the Docker image).
- The examples default to `BASE_URL=http://localhost:8000`. Override it when
  your server is elsewhere.
- If the deployment sets `LONGWATCH_AUTH_TOKEN`, export it so the scripts attach
  the bearer header:

  ```sh
  export LONGWATCH_AUTH_TOKEN='the-token'
  ```

## Run them

```sh
chmod +x ./*.sh
BASE_URL=http://localhost:8000 ./01_health_check.sh
BASE_URL=http://localhost:8000 ./02_ingest_demo_and_watch.sh
BASE_URL=http://localhost:8000 ./03_triage_and_decide.sh
BASE_URL=http://localhost:8000 ./04_export_and_verify.sh
BASE_URL=http://localhost:8000 python3 05_verify_ledger.py
```

| # | Script | What it demonstrates | What success looks like |
|---|---|---|---|
| 1 | `01_health_check.sh` | `/healthz` and the operator status surface | `"ok": true` and a printed model/worker/guard summary |
| 2 | `02_ingest_demo_and_watch.sh` | The explicit demo seed + ingest job polling | the job reaches `succeeded` and the source state is `ready` |
| 3 | `03_triage_and_decide.sh` | The ranked queue + recording a decision | `{"state": "accepted", "ledger_seq": N, …}` |
| 4 | `04_export_and_verify.sh` | Export → download → offline verification | `verify_bundle.py` exit 0, `Signature Verified Successfully` |
| 5 | `05_verify_ledger.py` | Ledger + health monitoring from Python stdlib | `ok: True` and exit code 0 |

Examples 2–4 need ingested footage. Example 2 seeds the labeled synthetic demo
fixture; the seed is idempotent — when an intact demo source already exists, the
API returns it instead of re-seeding. The demo source is marked
`demo · synthetic` everywhere it appears.

## Notes

- Every script is idempotent enough to re-run: the demo seed returns the
  existing source, and each export creates a new bundle.
- The vocabulary guard is demonstrated in example 3: run it with
  `DECIDE=flag NOTE='this looks like cruelty'` and the API refuses with
  `422 vocabulary_breach`, naming the term.
- `04_export_and_verify.sh` extracts the ZIP with `python3 -m zipfile` so it
  needs no `unzip` binary; it uses `openssl` for the independent signature leg
  only when OpenSSL ≥ 3.0 is present.
