# ReEngage Ops Dashboard V2 — Outline Document

## 1. What is ReEngage?

ReEngage is Loop's review response automation system for DoorDash and UberEats. When a customer leaves a review on a merchant portal, ReEngage generates an AI-powered response and enables operators to post it. The Ops Dashboard is the control plane — it manages operators, assigns reviews, generates responses, and tracks everything.

---

## 2. Old Dashboard vs New Dashboard

### Old Dashboard (V1)
- **Location**: `/backend/cloud_endpoints/services/streamlit/re_engage_config/`
- **Deployment**: Separate Cloud Run service (`re-engage-streamlit`)
- **Auth**: Streamlit-native, service account credentials (`backend-master.json`)
- **Database**: PostgreSQL (direct) + BigQuery (read-only)
- **Response tracking**: Manual — operator clicks "mark as sent" in dashboard
- **Extension**: Basic copy/paste, operator self-reports via "Mark as Responded" button
- **Assignment**: None — operators manually pick reviews from a shared list
- **User management**: None — anyone with access can use it
- **Pages**: Response Automation, App Responses Sent, Metrics, Review Monitoring, Automation Metrics (BETA), Evaluation Playground (BETA)

### New Dashboard (V2)
- **Location**: `/reengage-ops-dashboard/`
- **Deployment**: Cloud Run with nginx (Streamlit + FastAPI on same container)
- **Auth**: Google OAuth with role-based access control (lead/tenured/new)
- **Database**: BigQuery V2 tables (assignments, ops_log, users) + existing read-only tables
- **Response tracking**: Automatic — extension intercepts platform API to verify reply was posted
- **Extension**: Google OAuth identity, auto-detection of replies, no operator self-reporting
- **Assignment**: Daily engine (8 AM cron) with chain-platform affinity optimization
- **User management**: Full CRUD with roles, approval workflow, reduction % for new operators
- **Pages**: My Queue, All Reviews, Chain Health, Response Config, Ops Log, Search Review, Operator Assignments, Manage Users

### Key Differences Summary

| Feature | V1 (Old) | V2 (New) |
|---------|----------|----------|
| **Operator trust model** | Self-reported ("I responded") | Platform-verified (intercept API confirmation) |
| **Assignment** | Manual / shared queue | Automated daily distribution with affinity optimization |
| **Auth** | Service account | Google OAuth per operator |
| **Roles** | None | Lead / Tenured / New Operator |
| **Extension auth** | Manual email input (spoofable) | Google OAuth (verified identity) |
| **Paste into textbox** | Yes (bot detection risk) | Removed — copy only |
| **Mark as Responded** | Manual button (sabotage risk) | Auto-detected from platform API |
| **Anti-parroting** | None | GPT instructed to rephrase complaints |
| **Audit trail** | Limited | Full ops_log with every action |
| **Queue ordering** | Unordered | Chain-first, DD before UE (minimize platform switches) |
| **Infrastructure** | Streamlit only | Streamlit + FastAPI + nginx reverse proxy |
| **Session persistence** | Streamlit default (frequent logouts) | Auto-redirect OAuth on session expiry |

---

## 3. Architecture

### Data Flow
```
DoorDash / UberEats
    |
    v
Backend (Loop Core) → PostgreSQL → CDC → BigQuery
    |                                        |
    v                                        v
review_response_config              automation_reviews
review_responses                    slug_am_mapping
    |                                        |
    +-------------- Dashboard ---------------+
                        |
            +-----------+-----------+
            |                       |
        Streamlit               FastAPI
        (Dashboard UI)          (Extension API)
            |                       |
        port 8501               port 8001
            |                       |
            +--- nginx (8080) ------+
                    |
                Cloud Run
                    |
            Chrome Extension
            (Operator browser)
```

### Cloud Run Container
```
Port 8080 (nginx)
  /api/extension/*  → FastAPI (8001)
  /*                → Streamlit (8501)
```

Startup: `entrypoint.sh` starts FastAPI + Streamlit in background, waits for both health checks, then starts nginx as the main process.

### BigQuery Tables

**V2 Tables (owned by dashboard)**
| Table | Purpose |
|-------|---------|
| `reengage_dashboard_v2_users` | Operator accounts: email, name, role, approved, reduction_pct |
| `reengage_dashboard_v2_assignments` | Daily review assignments: review_uid, operator_email, status |
| `reengage_dashboard_v2_ops_log` | Audit trail: every action logged with operator, timestamp, remarks |

**Read-Only Tables (existing pipeline)**
| Table | Purpose |
|-------|---------|
| `elt_data.automation_reviews` | All reviews from DD/UE with customer data, ratings, is_replied |
| `pg_cdc_public.review_responses` | AI-generated responses with response_text, coupon_value |
| `pg_cdc_public.review_response_config` | Automation rules: which chains/ratings get AI responses |
| `restaurant_aggregate_metrics.slug_am_mapping` | Maps slug → chain name, brand |

---

## 4. Dashboard Tabs

### Operator Tabs (All Users)

**My Queue** — Personal assignment inbox
- Reviews assigned by the daily engine, sorted: chain → DoorDash first → UberEats → urgency
- Filters: Chain, Platform, Priority, Status, AI Response availability
- "Generate Responses" button for reviews missing AI responses
- Inline mark responded with portal links
- Metrics: Assigned count, Pending, Critical, Missing AI response

**All Reviews** — Global review browser (last 14 days)
- Filters: Chain, Platform, Status, Priority
- Shows up to 200 reviews with full context
- CSV export

**Chain Health** — Per-chain aggregate metrics
- Total reviews, Response rate %, AI coverage %, Average rating
- Breakdown by status (Critical/Pending/Responded/Expired)

**Response Config** — Read-only view of automation configs
- Shows targeting rules: platforms, ratings, customer types, sentiment
- Response settings: AI/Template, tonality, coupon config per platform

**Ops Log** — Audit trail (last 500 entries)
- Tracks: mark_responded, generate_responses, assignment, redistribute

**Scoreboard** — Operator leaderboard

### Lead-Only Tabs

**Search Review** — Detailed review lookup
- Search by ID (review_uid, order_id, review_id, customer_id) or by customer name + chain
- Shows full context: review details, customer info, assignment status, ops log history

**Operator Assignments** — Team overview
- Per-operator stats: pending count, chains covered, platforms, urgency
- Drill into any operator's queue

**Manage Users** — Admin panel
- Add/remove operators with role selection
- Set reduction % for new operators
- Approve/revoke access
- Manual assignment engine trigger

---

## 5. Assignment Engine

### Trigger
- **Automatic**: Cloud Scheduler at 8 AM daily
- **Manual**: Lead clicks "Run assignment now" in Manage Users

### Logic
1. Load eligible reviews (last 14 days, PENDING, has matching response config)
2. Skip already-assigned reviews
3. Sort by urgency (lowest days_left first)
   - DoorDash: 7-day response window
   - UberEats: 14-day response window
4. Calculate operator weights:
   - Tenured: weight = 1.0
   - New: weight = max(1.0 - reduction_pct/100, 0.1)
   - Leads: excluded from assignments
5. Group reviews by chain x platform
6. Assign groups to operators, preferring those who already handle that chain x platform (minimize portal login switches)
7. Write to `reengage_dashboard_v2_assignments` with status = 'pending'

### Redistribution
When an operator is deleted, their pending assignments are redistributed:
- Prefers operators already handling that chain x platform
- Balances by assignment count
- Single batch BigQuery UPDATE (fast)

---

## 6. AI Response Generation

### Pipeline
```
Review + Config → GPT-4.1-nano → response_text → review_responses table
```

### Prompt Design
- **Tone**: Adjusts based on config + review sentiment (apologetic for negative, enthusiastic for positive)
- **Anti-parroting**: Explicit instruction to rephrase customer complaints
  - "hair in food" → "quality concern you experienced"
  - "small ingredients caught in teeth" → "texture of the food"
- **Requirements**: Must include customer name, no placeholders, no follow-up questions
- **Character limits**: DoorDash 300 chars, UberEats 500 chars
- **Coupon integration**: Natural language mention if coupon_value > 0

### Config Matching
- Finds best matching config based on: chain, platform, rating, customer type, feedback presence
- Returns most recently created match if multiple

---

## 7. Chrome Extension

### Purpose
Operators use the extension on DoorDash/UberEats merchant portals to:
1. See the AI-generated response for a review
2. Copy it to clipboard
3. Paste and submit on the platform
4. Response is automatically verified and logged

### Components

| File | World | Purpose |
|------|-------|---------|
| `background.js` | Service Worker | UUID capture (Segment events + URL parsing), API proxy, interceptor injection |
| `content.js` | Isolated | UI injection, auto-mark listener, toast notifications |
| `interceptor.js` | MAIN | Intercepts platform fetch/XHR to detect reply submissions |
| `popup.html/js` | Extension | Google OAuth sign-in, settings |

### Authentication
- Google OAuth via `chrome.identity.launchWebAuthFlow()`
- Operator signs in with Google → verified email stored
- No manual email input (prevents spoofing)
- Backend validates email against approved users table

### UUID Capture
- **DoorDash**: Intercepts Segment analytics POST → extracts `delivery_uuid` from event properties
- **UberEats**: Parses `/feedback/reviews/{workflowUUID}` from URL on navigation

### Response Lookup
- Calls `GET /api/extension/lookup?order_id=<uuid>` on Cloud Run
- Backend queries BigQuery: review_responses JOIN automation_reviews
- Returns: response text, coupon value, customer name, rating, review text, is_replied status

### UI Injection
When operator opens a review with a ReEngage response:
- Shows customer context: name, stars, review text
- Shows full AI response text
- Coupon instruction (if applicable)
- "Already responded" warning (if is_replied or response_sent)
- Copy Response button (clipboard only, no auto-paste)

### Auto-Detection (Reply Verification)
Instead of trusting the operator to click "Mark as Responded", the extension intercepts the platform's own API:

**DoorDash:**
- XHR intercept: `POST /consumer_feedback/send_response` → success = reply posted
- XHR intercept: reviews list shows `merchantResponded: true` for the review

**UberEats:**
- XHR intercept: GraphQL `submitEaterReviewReply` mutation → success = reply posted
- XHR intercept: `EaterReviews` query shows `reply.comment` for the review

On detection → auto-calls `POST /api/extension/mark-responded` → BigQuery updated → dashboard reflects RESPONDED.

### Trust Model
The operator **cannot** fake a response:
- Email is Google OAuth verified (no spoofing)
- Response logging only fires when the platform's own API confirms the reply was posted
- No manual "Mark as Responded" button exists

---

## 8. FastAPI Extension Backend

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/extension/health` | Health check |
| GET | `/api/extension/lookup` | Look up AI response for an order_id |
| POST | `/api/extension/mark-responded` | Log response + upsert assignment as completed |

### Auth
- `X-Operator-Email` header on every request
- Validated against `reengage_dashboard_v2_users` (must be approved)
- Any email domain allowed (not restricted to loopai.com)

### Mark Responded Logic
1. Validate operator email
2. Look up real `review_uid` from `automation_reviews` (COALESCE of review_id, order_id)
3. Write to `reengage_dashboard_v2_ops_log` (audit trail)
4. Upsert `reengage_dashboard_v2_assignments`:
   - If assignment exists → update status to 'completed'
   - If no assignment → create new row with status = 'completed'

---

## 9. Dashboard Status Logic

### Review Status
```
RESPONDED: is_replied = TRUE OR response_sent IS NOT NULL OR assignment.status = 'completed'
EXPIRED:   days_left <= 0 AND not RESPONDED
PENDING:   everything else
```

### Priority
```
CRITICAL: days_left <= 1
URGENT:   days_left = 2
NORMAL:   days_left > 2
```

### Days Left
- DoorDash: review_date + 7 days - today
- UberEats: review_date + 14 days - today

---

## 10. Auth & Session Management

### Dashboard Login
- Google OAuth 2.0 flow via Streamlit
- First user becomes bootstrap LEAD
- Subsequent users require LEAD approval
- Name auto-backfilled from Google on first login

### Session Expiry Handling
- On session expiry: auto-redirects to Google OAuth (no button click needed)
- Google silently re-authenticates if already logged in
- Falls back to manual sign-in button if auto-redirect fails

### Roles
| Role | Dashboard Access | Gets Assignments | Weight |
|------|-----------------|------------------|--------|
| Lead | Full (all tabs + admin) | No | N/A |
| Tenured Operator | Standard tabs | Yes | 1.0 |
| New Operator | Standard tabs | Yes | 1.0 - reduction_pct/100 |

---

## 11. Deployment

### Build & Deploy
```bash
COMMIT_SHA=$(git rev-parse --short HEAD) && \
gcloud builds submit . --config cloudbuild.yaml \
  --project=arboreal-vision-339901 \
  --substitutions=COMMIT_SHA=$COMMIT_SHA

gcloud run deploy reengage-ops-dashboard-v2 \
  --image us-central1-docker.pkg.dev/arboreal-vision-339901/docker-images-repo/reengage-ops-dashboard-v2:$COMMIT_SHA \
  --region us-central1 \
  --project arboreal-vision-339901
```

### URLs
- Dashboard: `https://reengage-ops-dashboard-v2-ul5ne76yva-uc.a.run.app`
- Extension API: `https://reengage-ops-dashboard-v2-ul5ne76yva-uc.a.run.app/api/extension/`

---

## 12. Known Issues & Future Work

### Open Issues
- **Chip City / Momos attribution**: Dashboard counts all replies as Loop's. Need to use `replied_user_uuid` (UE) / `replied_user_email` (DD) to distinguish Loop vs third-party replies
- **"Auto-posted by Loop" metric**: Currently uses `is_replied.sum()` — should only count reviews where Loop actually posted (cross-reference with `review_responses.response_sent` or `replied_user_uuid`)
- **Session persistence**: Auto-redirect works but causes a brief flash. Cookie-based session could be smoother (extra-streamlit-components crashed on Cloud Run)

### Future Improvements
- Source verification in reporting (Loop vs Momos vs staff)
- UberEats auto-post integration (backend already has `send_reply_to_review`)
- Operator performance analytics (response time, quality scoring)
- Slack notifications for critical reviews
