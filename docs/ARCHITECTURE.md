# Vehicle Offer Extraction — Architecture & Code Flow

This document explains how the offer-generation pipeline works end to end, from
the HTTP request to the per-dealer Excel zip written on disk.

> **Multi-type note:** the pipeline now supports multiple offer types (default
> `sales_specials`). Type resolution, routing, and per-type prompts/schemas/output
> folders are documented in the top-level [README](../README.md).
> The stages below describe the shared mechanics, which are identical for every
> type; only the Excel filter label, prompt/schema, and output subfolder change
> per type. Outputs are written under `storage/offers/<type>/{zip,errors}/`.

---

## 1. High-level pipeline (A → B → C)

The system is a 3-stage, in-process pub/sub pipeline. Each stage runs on its own
background worker thread(s) so the API returns immediately.

```mermaid
flowchart LR
    A["**A — API**<br/>GET /api/v1/offers/generate"]
    B["**B — scrape_broker**<br/>1 worker"]
    C["**C — extract_broker**<br/>5 workers"]
    Z["storage/offers/<br/>dealer .zip + error .txt"]

    A -->|publish { excel_path }| B
    B -->|scrape ALL Sales Specials URLs in parallel| B
    B -->|publish 1 message per dealer| C
    C -->|LLM sequential per dealer,<br/>dealers parallel ×5| C
    C --> Z
```

| Stage | Broker | Workers | Responsibility |
|-------|--------|---------|----------------|
| A | — | request thread | Publish the excel path, return `202`-style "processing" |
| B | `scrape_broker` | 1 | Read workbook, scrape every Sales Specials URL **in parallel**, fan out per-dealer data |
| C | `extract_broker` | 5 | Per dealer: LLM extract **sequentially**; **different dealers run in parallel** |

---

## 2. Where each piece lives

```mermaid
flowchart TD
    subgraph API
        OFF["app/api/offers.py<br/>generate_offers()"]
    end
    subgraph Events
        BR["app/events/broker.py<br/>InMemoryBroker<br/>scrape_broker / extract_broker"]
        SUB["app/events/subscriber.py<br/>handle_scrape_event / handle_extract_event"]
    end
    subgraph Service
        SVC["app/service/offer_generation_service.py<br/>scrape_dealers() / build_dealer()"]
        SCR["app/service/scraper.py<br/>Playwright + BeautifulSoup"]
        LLM["app/service/llm_extractor.py<br/>LLMOfferExtractor (key pool)"]
        XL["app/service/excel_service.py<br/>build_workbook_bytes()"]
    end
    subgraph Workflow
        WF["app/workflows/offer_generation_graph.py<br/>scrape() / build_from_body()"]
    end

    OFF -->|publish| BR
    BR --> SUB
    SUB --> SVC
    SVC --> WF
    WF --> SCR
    WF --> LLM
    WF --> XL
```

---

## 3. Stage B — scraping (fan-out)

`handle_scrape_event` → `OfferGenerationService.scrape_dealers()`

```mermaid
flowchart TD
    START["event { excel_path }"] --> READ["pd.read_excel + validate columns"]
    READ --> FILTER["keep rows where type == 'Sales Specials'"]
    FILTER --> POOL["ThreadPoolExecutor(SCRAPER_MAX_WORKERS)"]
    POOL --> S1["_scrape_one(url #1)"]
    POOL --> S2["_scrape_one(url #2)"]
    POOL --> S3["_scrape_one(url #N)"]
    S1 --> GROUP["group scraped bodies by dealer<br/>(preserve original order)"]
    S2 --> GROUP
    S3 --> GROUP
    GROUP --> PUB["for each dealer: extract_broker.publish(payload)"]
```

Each `_scrape_one` calls `workflow.scrape(url)` (Playwright render → visible body
text). On failure it records `scrape_error` instead of a body, so a bad URL never
stops the batch.

**B → C message shape (one per dealer):**

```jsonc
{
  "dealer_id": "HWA00001GMC",
  "dealer_name": "Heyward Allen GMC",
  "date_token": "20260825",
  "urls": [
    { "oem": "GMC",      "type": "Sales Specials", "url": "https://…", "body": "…", "scrape_error": null },
    { "oem": "Cadillac", "type": "Sales Specials", "url": "https://…", "body": null, "scrape_error": "…" }
  ]
}
```

---

## 4. Stage C — extraction (fan-in per dealer)

`handle_extract_event` → `OfferGenerationService.build_dealer()`

One dealer per extract-broker worker. Within a dealer the URLs are processed
**sequentially**; across dealers up to **5 run in parallel** (one per worker).

```mermaid
flowchart TD
    MSG["dealer payload"] --> LOOP{"for each URL entry<br/>(sequential)"}
    LOOP -->|scrape_error present| ERR1["record error section"]
    LOOP -->|body present| BUILD["workflow.build_from_body()"]
    BUILD --> EX["LLM extract → dedupe → Excel bytes"]
    EX -->|count > 0| WB["add workbook (oem.xlsx)"]
    EX -->|count == 0| ERR2["record 'No offers extracted'"]
    WB --> DONE
    ERR1 --> DONE
    ERR2 --> DONE
    DONE["_assemble_dealer()"] --> ZIP["write {dealer}_{date}.zip<br/>(all workbooks)"]
    DONE --> TXT["write error_{dealer}_{date}.txt<br/>(all errors, one file)"]
```

`build_from_body` runs the extraction half of the workflow only (no scraping):

```
_extract_with_llm  →  _normalize_offers (dedupe)  →  _create_excel
```

---

## 5. LLM key pool (parallelism without rate-limit blowups)

`LLMOfferExtractor` holds **one client per API key** in a `queue.Queue`. Every
`extract()` checks a client out, runs the call, and returns it — so at most
`len(keys)` LLM calls run at once, each on a **distinct key**.

```mermaid
flowchart LR
    subgraph Pool["client pool (queue)"]
        K1["client key1"]
        K2["client key2"]
        K3["client key3"]
        K4["client key4"]
        K5["client key5"]
    end
    D1["dealer worker 1"] -->|get| Pool
    D2["dealer worker 2"] -->|get| Pool
    D3["dealer worker 3"] -->|get| Pool
    Pool -->|put back after call| Pool
```

- **Scraper concurrency** (`SCRAPER_MAX_WORKERS`) and **LLM concurrency**
  (number of keys) are decoupled: you can scrape 8-wide while the pool caps LLM
  calls at 5.
- If a 6th extraction starts before a key frees up, it blocks on `queue.get()`
  until one returns — natural backpressure, no 429s.

---

## 6. End-to-end sequence

```mermaid
sequenceDiagram
    participant Client
    participant API as A (API)
    participant SB as B (scrape_broker)
    participant EB as C (extract_broker ×5)
    participant Disk as storage/offers

    Client->>API: GET /api/v1/offers/generate?excel_path=…
    API->>SB: publish { excel_path }
    API-->>Client: { status: "processing" }
    SB->>SB: read workbook, filter Sales Specials
    par scrape all URLs in parallel
        SB->>SB: _scrape_one(url) × N
    end
    loop one message per dealer
        SB->>EB: publish dealer payload (bodies)
    end
    par dealers in parallel (≤5)
        EB->>EB: build_dealer() — LLM sequential per dealer
        EB->>Disk: {dealer}_{date}.zip
        EB->>Disk: error_{dealer}_{date}.txt
    end
```

---

## 7. Configuration (`.env`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `GEMINI_API_KEYS` | — | Comma-separated keys; each concurrent LLM call uses a distinct one (falls back to `GEMINI_API_KEY`) |
| `SCRAPER_MAX_WORKERS` | `5` | URLs scraped in parallel in stage B |
| `DEALER_EXTRACT_WORKERS` | `5` | Dealers extracted in parallel in stage C (keep near key count) |
| `MAX_BODY_CHARS` | `350000` | Max scraped text sent to the LLM |
| `LOCAL_STORAGE_DIR` | `./storage/offers` | Output folder for zips + error files |

---

## 8. Outputs

For each dealer, in `storage/offers/`:

- `{dealer_id}_{dealer_name}_{date}.zip` — one `.xlsx` per OEM that produced offers.
- `error_{dealer_id}_{dealer_name}_{date}.txt` — **single** file listing every
  OEM URL that failed to scrape or yielded no offers (sections separated by a
  divider). No error zip.

> The CLI entry point ([main.py](../main.py)) runs the same two stages
> synchronously (scrape → extract) for local runs without the API/brokers.
