# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

AI-powered mortgage eligibility and advisory chatbot for NewPoint Mortgage. Users fill a 5-step loan scenario wizard (or use a conversational chat intake), and the backend matches them against lender programs via two parallel tracks:

1. **Structured (MySQL):** deterministic gate-based eligibility matching
2. **Semantic (Qdrant + OpenAI):** RAG over lender guidelines for follow-up Q&A

Three supported lenders: Denali/NQM, Everest/Deephaven, Summit/Verus.

## Development Commands

### Backend (Python 3.12)

```bash
# First-time setup
python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run API server (port 8080)  — or: npm run dev:api
python -m uvicorn backend.api:app --reload --host 0.0.0.0 --port 8080

# Run LoanPASS pricing service (port 8090) — SEPARATE process so it can be
# restarted independently of the main API.  — or: npm run dev:pricing
python -m uvicorn backend.pricing_app:app --reload --host 0.0.0.0 --port 8090

# Quick import-check after editing Python files
python -c "import backend.api; import backend.pricing_app; import backend.loanpass_routes; import backend.chat.routes; import backend.chat.portfolio; import backend.metrics; print('OK')"

# Delete eligibility trace logs (logs/*.txt). Trace files are OFF by default
# (set ELIGIBILITY_TRACE=1 to enable); this clears any that accumulated.
npm run clean:logs                 # files older than 7 days
python -m backend.tools.clean_logs --all   # everything
```

### Frontend (Node 22+)

Run from **repo root** (`package.json` stays at root; app lives in `frontend/`):

```bash
npm ci          # install
npm run dev     # Vite dev server on port 5173 (proxies /api → localhost:8080)
npm run build   # production build
npm run lint    # ESLint
npm run format  # Prettier — always run after editing .tsx files (CI enforces it)
npm run dev:macos  # clean AppleDouble (._*) files first, then dev (use on this macOS/SSD setup)
```

### Docker (single container, 4 supervised processes)

One image runs the main API (8080), the LoanPASS pricing service (8090), the
Vite frontend (5173), and a **pricing-watchdog** under **supervisord**
(`supervisord.conf`). Each program has `autorestart=true`, so a pricing crash
restarts ONLY pricing — the API + frontend keep running. The watchdog
(`backend/tools/pricing_watchdog.py`) polls `/api/loanpass/health` and runs
`supervisorctl restart pricing` after N consecutive failures, since supervisord's
`autorestart` catches a process *exit* but not a *hang* (LoanPASS rate-limit /
token churn). Grep container logs for `PRICING WATCHDOG`.

```bash
docker build -t newpoint-assistant .
docker run --rm -p 5173:5173 -p 8080:8080 -p 8090:8090 --env-file .env newpoint-assistant

# Restart ONLY the pricing service inside a running container (no API/frontend bounce):
docker exec <container> supervisorctl restart pricing
docker exec <container> supervisorctl status   # see all four programs
```

### Deployment / CI

**Pushing to `main` auto-deploys to production.** `.github/workflows/deploy-main.yml` triggers on every push to `main`: it SSHes to the prod host, does `git reset --hard origin/main`, then `docker compose up -d --build form-chat-assistant`, and health-checks `http://127.0.0.1:8082/api/health` (host 8082 → container 8080). Treat a merge to `main` as a production release. Required CI secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_PASSWORD`, `DEPLOY_COMPOSE_DIR` (the old `docs/DEPLOY.md` walkthrough is in git history if needed). There is no test/lint CI gate — only the deploy job — so run `npm run lint` / `npm run format` locally before pushing.

### Database

```bash
# Initial schema + migrations (run in order, 001–020)
mysql -u root -p newpoint_mortgage < ingest/schema.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/009_intake_tables.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/010_form_history_scenario.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/011_form_history_client_broker.sql
# 012–013 second-lien details, 014 search_sessions, 015 archive tags, 016 form_history
# status, 017 program_name_loanpass, 018 form_history description/origin,
# 019 geo structured conditions (effect/conditions columns on map_geographic_restrictions),
# 020 dscr_eligible_states (broader DSCR licensing allowlist — see Eligibility Engine § geo)

# Reload the geo allowlist/overlay table from a CSV export (dry-run by default; --apply
# truncates + reloads). Source of truth for which states each program can lend in.
python -m ingest.tools.load_map_geographic_restrictions path/to/map_geographic_restrictions.csv --apply

# Re-ingest a lender's matrix PDF into MySQL + Qdrant
python ingest/lenders/denali_flow_to_mysql_qdrant.py --apply
python ingest/lenders/everest_deephaven_flow_to_mysql_qdrant.py --apply
python ingest/lenders/summit_verus_flow_to_mysql_qdrant.py --apply

# Guideline PDFs → Qdrant (run after matrix or when guidelines update)
python ingest/lenders/denali_nqm_guidelines_qdrant.py --apply
python ingest/lenders/everest_deephaven_guidelines_qdrant.py --apply
python ingest/lenders/summit_verus_guidelines_qdrant.py --apply
```

## Environment

Copy `.env.example` → `.env`. Key vars:

```
QDRANT_URL=http://187.77.186.41:6333
OPENAI_API_KEY=sk-...
OPENAI_CHAT_MODEL=gpt-4o-mini
MYSQL_HOST=72.60.96.212
MYSQL_PORT=3306
MYSQL_DATABASE=newpoint_mortgage_dev
```

All config constants (Qdrant collection names, lender ID mappings) live in `backend/config.py`.

## Architecture

### Repository layout

```
/                          # repo root
├── CLAUDE.md
├── Dockerfile
├── package.json           # npm scripts; node_modules at root
├── requirements.txt
├── frontend/              # TanStack Start + React 19 UI
├── backend/               # FastAPI + retrieval engine + config.py
├── ingest/                # offline ingest — see ingest/README.md
├── input/                 # lender PDFs (Matrices/, Guidelines/)
├── docs/                  # plain-language docs: running, form, chat, eligibility, pricing
└── chatbot_retrieval/     # ⚠ STALE legacy snapshot — do NOT edit (see note below)
```

> **`chatbot_retrieval/` is dead code.** It holds an older, smaller copy of `eligibility.py` / `rag.py` / `eligibility_tolerance.py` from before the `backend/` reorg. Nothing imports it at runtime — the live engine is `backend/`. Edits sometimes leak into it from search-and-replace; ignore it and never make it a source of truth. (`loan_details_field_labels_spec.md` at the repo root is likewise a one-off design spec, not loaded by any code — it documents the contextual Loan Details field labels per scenario.)

### Backend layout

Organized by **capability** (vertical slices), not technical layer. Each module owns one thing the engine does.

```
backend/
├── api.py                # thin FastAPI entry (uvicorn backend.api:app) — CORS, startup, mounts routers, /api/chat + small endpoints
├── pricing_app.py        # standalone FastAPI app (uvicorn backend.pricing_app:app, port 8090) — serves ONLY /api/loanpass/* so LoanPASS restarts independently
├── loanpass_routes.py    # APIRouter with the LoanPASS pricing endpoints + pydantic models (mounted by pricing_app; inline in api.py only if MOUNT_PRICING_INLINE=1)
├── loanpass_client.py    # REST client for the LoanPASS public API (login → execute-summary/execute-product → price); not the iframe
├── loanpass_fields.py    # maps a wizard/eligibility form dict → LoanPASS creditApplicationFields
├── loanpass_config.py    # LoanPASS iframe embed config (secrets from env)
├── form_import.py        # parse uploaded 1003 PDFs + Fannie MISMO 3.4 XML/HTML → wizard field dicts (POST /api/parse-loan-form)
├── config.py             # env + lender constants (.env, Qdrant, MySQL); LOG_LEVEL + ELIGIBILITY_TRACE gate + MOUNT_PRICING_INLINE
├── metrics.py            # (0) MASTER LIST — SLOT_DEFS catalog, normalize/validate, geo field defs, portfolio→EligibilityRequest contract
├── eligibility.py        # (2) ONE module — models + trace + engine (10-layer matcher) + service + routes + geo evaluation + /api/geo
├── eligibility_tolerance.py # marginal tolerance for numeric gates (LTV/CLTV/DTI 0.5%, loan $2, FICO 2pts, DSCR 0.05); imported by eligibility.py
├── chat/                 # (3,4) conversational intake
│   ├── routes.py         #     /api/intake/* router + turn orchestration (was intake.py)
│   ├── extract.py        #     (3) raw text → metrics (LLM extractor)
│   ├── next_question.py  #     (4) deterministic planner gate + override + LLM asker (was planner.py + asker.py)
│   ├── portfolio.py      #     shared working memory: slot state, planning, ordering (was slot_engine.py)
│   └── session.py        #     IntakeSession persistence (was session_store.py)
├── rag.py                # (5) Know More — Qdrant RAG + /api/chat advisor (general_chat folded in)
├── pdf.py                # (6) POST /api/scenario/pdf
├── scenario.py           # (7) /api/form-history/* saved profiles
├── connections/          # (1) all outbound clients + logging in one place
│   ├── db.py · openai.py · qdrant.py · embeddings.py · logging.py
├── utilities/            # cross-capability pure-logic helpers
│   ├── guard.py          #     greeting/junk rejection + deflection cleanup (was chat_guard)
│   ├── notes.py          #     consideration-bullet filter/format (was summarize_notes)
│   └── scenario_notes_extract.py  # LO free-text → scenario_notes_delta (shared by /form chat + /api/intake)
└── tools/                # dev maintenance (not imported at runtime)
    ├── clean_logs.py     #     prune/delete logs/*.txt  (npm run clean:logs)
    ├── pricing_watchdog.py  #  polls /api/loanpass/health; restarts ONLY pricing on a hang (4th supervisor program)
    ├── _dump_pricing_payloads.py  # run 3 sample scenarios through eligibility + LoanPASS; dumps exact request/response payloads to logs/
    └── test_second_lien_scenarios.py   # second-lien regression check
```

> `chat/routes.py` (the `/api/intake` router) and `chat/portfolio.py` (the slot working-memory shared by all chat modules) are deliberate — a feature needs a router, and the portfolio helpers are imported by extract/next_question/session/routes, so they can't dissolve without import cycles. Geo split per concern: field defs → `metrics.py`, restriction evaluation + `/api/geo` → `eligibility.py`.

**Capability map (the engine's mental model):** (0) master metrics list · (1) config + connections · (2) eligibility engine · (3) chat metric-extraction · (4) next-question logic · (5) Know More RAG · (6) PDF export · (7) save scenario.

**The metric contract:** chat stores a snake_case "portfolio"; `metrics.py:portfolio_to_eligibility_request()` translates it to the camelCase `EligibilityRequest` the engine consumes. `metrics.py` is the single source of truth both sides validate against.

### Ingest layout

See `ingest/README.md`. Summary:

```
ingest/
├── schema.sql
├── migrations/             # SQL 001–018
├── lenders/                # per-lender matrix + guidelines → MySQL / Qdrant
└── tools/                  # one-off DB maintenance
```

### API (`backend/api.py`)

FastAPI app on port 8080. Key endpoints:

- `POST /api/eligibility` — full MySQL + Qdrant eligibility; returns matched programs + geo/overlay exclusion lists + RAG notes
- `POST /api/eligibility/quick` — SQL-only scan (no Qdrant); used for real-time sidebar counts while the form is being filled
- `POST /api/chat` — RAG retrieval + LLM advisor; supports `mode="results_general"` for cross-collection Q&A
- `POST /api/form-history/save` / `GET /api/form-history` / `GET /api/form-history/{id}` — saved wizard profiles
- `POST /api/scenario/pdf` — server-side PDF (PyMuPDF)
- `POST /api/parse-loan-form` — upload a 1003 PDF or Fannie MISMO 3.4 XML/HTML → wizard field dict (`backend/form_import.py`; client side: `lib/parseLoanFormApi.ts` → `loanFormToWizardPatch.ts`). Sample inputs live in `forms/`.
- `POST /api/parse-scenario` / `POST /api/extract-scenario` / `POST /api/scenario-notes/extract` — free-text → structured scenario / field deltas / session notes
- **LoanPASS pricing (port 8090, `backend/loanpass_routes.py`):** `GET /api/loanpass/program/{id}`, `POST /api/loanpass/products`, `POST /api/loanpass/price` — REST client in `loanpass_client.py`, form→field mapping in `loanpass_fields.py`

**Server-side chat intake (`/api/intake/*`)** — mounted from `backend/chat/routes.py`. **Note:** the frontend now drives `/chat` client-side and calls only `/api/intake/extract`; the planner endpoints below (`start`/`message`/`preview`/`submit`/`chip_answer`/…) still exist and work but are no longer called by the UI.

- `POST /api/intake/extract` — **stateless** free-text + portfolio → validated delta + ambiguities + scenario notes (reuses `extractor_llm` → `validate_extracted_values` → `merge_extracted`; the client holds the portfolio).
- `POST /api/intake/frame` — **stateless** question framing for the `/chat` dispatcher (Phase-2): phrases ONE combo/summary ask from `{kind, questions, whys, lead, recent}`. The client calls it only for combo (④) and summary (③) formats, max 3×/session, 3.5s timeout, hard-coded template fallback; disable with `VITE_CHAT_FRAMING_LLM=0`. These two are the only intake endpoints the UI uses.
- `POST /api/intake/start` — create session, optional bulk-extract from initial text
- `POST /api/intake/message` — main turn handler (see intake pipeline below)
- `POST /api/intake/preview` — mid-intake eligibility run
- `POST /api/intake/submit` — final eligibility; 400 if required slots missing
- `POST /api/intake/bulk_fill` — merge checklist values (no LLM)
- `POST /api/intake/edit_slot` — sidebar single-slot edit (no LLM)
- `POST /api/intake/chip_answer` — chip selection (skips Extractor LLM)
- `POST /api/intake/next_question` — after a sidebar slot edit, returns the next unanswered question (no extraction)

All `/api/intake/*` responses share: `session_id`, `bot_text`, `input_type`, `chips`, `action`, `target_slots`, `portfolio_delta`, `scenario_notes_delta`, `question_count`, `can_submit`.

### Frontend (`frontend/`)

TanStack Start (SSR) + React 19 + Tailwind 4 + shadcn/ui.

```
frontend/
├── src/
│   ├── routes/            # /form, /chat, /
│   ├── components/        # LoanWizard, ui/, wizard/
│   └── lib/               # API clients, form helpers, intake utils
├── vite.config.ts
├── tsconfig.json
└── eslint.config.js
```

**Routes → mode wrappers → shared shell.** `/` renders an `AccessGate` (it does NOT blind-redirect, so the URL doesn't flip to `/form` before sign-in); once a role is granted (or remembered) it navigates to `/form`. The client-side access gate (`lib/access.ts`, not real security) maps role → `formMode`: Underwriter/Admin → `"underwriter"` (asks optional slots), Loan Officer → `"lo"` (mandatory only). "Remember me" persists the role in localStorage; otherwise it lives in an in-memory module var that survives `/form`↔`/chat` nav but not a reload. Each route then renders a thin mode wrapper that sets `intakeMode` and forwards `WizardShellProps` (vault open/reset/history/new-scenario):

```
routes/form.tsx → wizard/form/FormWizard  (intakeMode="form")
routes/chat.tsx → wizard/chat/ChatWizard  (intakeMode="chat")
                        ↓
        wizard/shell/WizardShell  ──(today re-exports)──>  components/LoanWizard
                        ↓
   FormChatFlow (form intake + results)  |  ChatConversationFlow (chat intake)
   wizard/shared/* (vault, sidebar, results model, Know More)
```

> **Incremental split in progress.** The wizard was carved out of the original ~11k-line `LoanWizard.tsx` into `wizard/{shell,form,chat,shared}/` modules; `LoanWizard.tsx` is now ~4.7k lines and shrinking. `WizardShell` (`wizard/shell/WizardShell.tsx`) currently still re-exports `LoanWizard` while the monolith is decomposed; the module headers (e.g. `wizard/index.ts`, `shell/WizardShell.tsx`) describe the target layout. `LoanWizard.tsx` remains the source of truth for `form` state, eligibility, vault, and session until the extraction finishes. Pure-logic helpers already extracted alongside it: `wizard/loanWizardForm.ts` (`makeEmptyForm()` default state + form helpers), `wizard/loanWizardEligibility.ts` (`EligibleProgram` type + eligibility shaping), `wizard/loanWizardProfileSections.ts` (sidebar profile-section builders).

**Two intake UIs, both client-side, one shared `form` state:**

Both `/form` and `/chat` are now client-side flows that patch the SAME `form`/`setForm` the wizard owns (so the lien/purpose/LTV cascade `useEffect`s and the post-results edit → dirty → Resubmit loop keep working untouched), and both drive their questions from `lib/formChatFlow.ts` (`FORM_CHAT_QUESTIONS`). Mode is **URL-driven, no in-UI toggle**: a plain route = `"lo"` (loan-officer flow, mandatory questions only) and `?mode=underwriter` = `"underwriter"` (also asks optional slots). The difference between the two routes is only the **turn loop and presentation**:

- **`/form` — guided chat (`wizard/FormChatFlow.tsx`).** A Claude-style guided chat (Mortgage Profile sidebar + centered chat column) that walks `FORM_CHAT_QUESTIONS` in order, rendering option cards / form cards per question. The 5-step wizard internals below still describe the underlying `form` state model and its completion gates; the step UI is reachable via edit mode.
- **`/chat` — prose-first conversation (`wizard/ChatConversationFlow.tsx`).** Rebuilt as a client-side conversational intake (the server `/api/intake/*` **planner loop is retired for chat**). Each user turn extracts fields via the stateless `/api/intake/extract` endpoint (reuses the intake Extractor), merges the snake_case delta into `form` via `portfolioToFormPatch`, and `lib/chatConversation.ts` (`advanceChatModeNext`) decides the next ask's presentation (prose / direct / combined / form card). Inferred or ambiguous values are queued and confirmed one at a time. The turn loop lives in `wizard/hooks/useChatConversation.ts`. See `docs/CHAT.md`.

> The legacy server-driven chat (`useChatIntake` hook + `ChatIntakeMessage.tsx` + the `/api/intake/message` planner) has been removed from the UI. The planner endpoints still exist in `backend/chat/routes.py` but are no longer called by the frontend — only `/api/intake/extract` is.

**Key components (`frontend/src/components/`):**

- `LoanWizard.tsx` — primary component (still large); owns `form` state + 5-step wizard + unified sidebar + eligibility/vault/session; accepts `intakeMode: "form" | "chat"` and `formMode: "lo" | "underwriter"`. Aliased as `WizardShell`.
- `wizard/form/FormWizard.tsx`, `wizard/chat/ChatWizard.tsx` — thin per-mode entry wrappers (set `intakeMode`, forward `WizardShellProps`). `wizard/shell/types.ts` holds the shared `WizardShellProps`/`WizardIntakeMode`/`WizardPhase` contracts.
- `wizard/FormChatFlow.tsx` — `/form` guided-chat UI; driven by `lib/formChatFlow.ts`, patches the wizard's `form` state
- `wizard/ChatConversationFlow.tsx` — `/chat` prose-first intake column; mounts `useChatConversation`, renders in the same visual language as FormChatFlow
- `wizard/hooks/useChatConversation.ts` — the `/chat` client turn loop (extract → merge → reinforce → advance); calls only `/api/intake/extract`
- `wizard/hooks/useWizardSession.ts` — extracted sessionStorage session hook
- `wizard/shared/` — UI shared by both modes: `ScenarioVaultOverlay`, `mortgageProfileSidebar/` (`FormProfileSidebar`/`ChatProfileSidebar` + `formProfileSections.ts` + `EligibleProgramsPreview`), `results/` (`formChatResultsModel.ts` data model + `formChatResultsUi.tsx`), and re-exports of `SaveProfileDialog`/`ScenarioHistoryView`/`ProgramKnowMoreDetail`/`EligibilityExclusionDetails`/`PostEssentialsOptionalPicker`
- `PostEssentialsOptionalPicker.tsx` — renders the end-of-intake optional slot batch card
- `ProgramKnowMoreDetail.tsx` — `PROGRAM_DETAIL:` card (key metrics + summarized notes)
- `SaveProfileDialog.tsx` / `ScenarioHistoryView.tsx` — save and browse wizard profiles
- `wizard/results/` — the matched-programs results screen, extracted from `LoanWizard.tsx`. `ResultsScreen.tsx` is the container; children include `ResultsToolbar`, `ProgramResultCards`, `NearMissesSection`, `ProgramFocusBanner`, `NoProgramsEmptyState`, `ResultsPagination`, `ResultsFooter`, `ResultsChatDock` (results-mode Q&A thread), `ResultsModifyPanel`, `SuggestionCards`. The dock owns its own message thread, separate from the wizard's `messages`.
- `EligibilityExclusionDetails.tsx` — renders geo/overlay exclusion lists for a scenario
- `wizard/SearchablePicker.tsx` — searchable combobox: inline dropdown on desktop, **full-screen sheet on mobile** (≤767px) so option lists aren't clipped by chat scroll containers or lost behind the keyboard. `CountySearchControl.tsx` (county autocomplete) wraps it; use it for any new search-pick field.
- `VoiceWave.tsx` + `lib/useSpeechToText.ts` — voice input. Speech-to-text drives the follow-up, start, and intake text fields (three independent `useSpeechToText()` instances in `LoanWizard.tsx`); `VoiceWave` is the listening animation.
- `AppHeader.tsx` — top nav; `ScenarioHistoryDialog.tsx` — history browser modal

**Key lib modules (`frontend/src/lib/`):**

- `nqmIntegratedForm.ts` — canonical option lists, type definitions, and visibility helpers (`isDscrPathScenario`, `shouldShowSecondLienFields`, etc.) shared across wizard and eligibility payload builders
- `stateGeoFollowUp.ts` — `STATE_FOLLOWUP_OPTIONS` and geo helpers
- `quickEligibilityPayload.ts` — builds the `EligibilityRequest` from wizard state for `/api/eligibility/quick`
- `formChatFlow.ts` — `FORM_CHAT_QUESTIONS`: question definitions, option lists, and `showIf`/ordering logic shared by BOTH `/form` (`FormChatFlow.tsx`) and `/chat` (`ChatConversationFlow.tsx`); each question maps to a `form` field. Keep in sync with `SLOT_DEFS`/`SLOT_ORDER` and the wizard steps. Also exports `mandatoryComplete()` (the chat/form submit gate), per-question `safeDefault` (the 2-strike assume-on-skip value, mostly "No" for yes/no fields), and `buildCascadePatchForFormEdit()` — the shared upstream-edit cascade (see LoanWizard internals below).
- `chatConversation.ts` — the `/chat` conversational dispatcher (pure logic, injectable `rng`): `advanceChatModeNext` runs the five-format cadence — scripted opening (bulk extract + `chatStockTakeLine`), Q1–Q3 alternate ① why-question / ② options-question, Q4 is ④ combo (curated `COMBO_PAIRS` two-field pairs, theme groups only as fallback) or ③ summary (`CHAT_SUMMARY_ASK`), Q5+ constrained roll with anti-repetition (`usedTemplates` variant pools — wording never repeats in a session) + periodic ③. Holds `WHY_BY_FIELD` (engine-grounded "why it matters" per field) and the 2-strike skip guard (`attempts` counter → `safeDefault` or defer). Does NOT own a second field schema — reads `FORM_CHAT_QUESTIONS`.
- `chatIntakeApi.ts` — fetch wrappers for `/api/intake/*`. Only `intakeExtract` (`/api/intake/extract`) and `intakeFrame` (`/api/intake/frame`, Phase-2 framing LLM) are live in the UI; the planner wrappers (`start`/`message`/`chip_answer`/…) are retained but unused.
- `portfolioToFormPatch.ts` — converts an extractor snake_case portfolio/delta into a camelCase `form` patch (the client-side mirror of the backend's `portfolio_to_eligibility_request`).
- `chatThreadView.tsx` — renders the chat message thread and the `CHAT_*` control-payload cards (see message prefixes below); `scenarioNotesExtract.ts` — LO free-text → Session Notes (shared by `/form` and `/chat`).
- `formChatLayout.ts` — shared class-string constants for the guided-chat column (column max width — also used by the Compare pricing modal — horizontal insets, message stack, extraction cards, the Claude-style composer card) plus `syncComposerTextareaHeight()`. `/form` (`FormChatFlow`) and `/chat` (`ChatConversationFlow`/`LoanWizard`) must stay visually in sync THROUGH these constants — don't fork the classes inline.
- `welcomeIntro.ts` — shared welcome copy for `/form` and `/chat`; mobile viewports get a shorter guide paragraph (`welcomeGuideParagraph(isMobile)` drops the desktop tail sentence).
- `wizardSessionPersist.ts` — sessionStorage persistence (`nqm_wizard_session_v1`)
- `loanpass/` — **client-side LoanPASS pricing** (the results-screen Compare/pricing flow lives in the browser, calling the `/api/loanpass/*` pricing service for raw data only): `types.ts` (LoanPASS iframe-API field-value shapes), `mapWizardToLoanpassFields.ts` (wizard `form` → LoanPASS credit fields, the client mirror of `backend/loanpass_fields.py`), `programProducts.ts` (match an eligibility product label → a LoanPASS DB product row), `fetchLoanpassPrice.ts` (orchestrates products → price), `pricingTable.ts` (pricing-grid model + rate/price formatting + jsPDF/autoTable PDF export). Rendered by `wizard/LoanpassPricingCard.tsx` (rate×lock-period grid, LO-vs-underwriter display via `getAccessRole`, Download PDF).

> **Pricing has both a server and a client mapper.** `backend/loanpass_fields.py` maps a form dict → LoanPASS fields for the server `/api/loanpass/price` path; `lib/loanpass/mapWizardToLoanpassFields.ts` does the same client-side for the results Compare flow. Keep enum/occupancy/field-id mappings in sync across the two.

**Mobile UX conventions** (mobile pass, 2026-06):

- Breakpoint: JS checks use `window.matchMedia("(max-width: 767px)")` (aligned with Tailwind's `md:`); CSS uses `md:`/`sm:` variants. `SearchablePicker` and `welcomeIntro` both follow this.
- Horizontal overflow: `html`/`body` use `overflow-x: clip` (`styles.css`), and the route → wizard → flow flex chain carries `min-w-0` at every level. **New flex columns inside the wizard need `min-w-0`** or long content (pills, tables) pushes the page wider than the viewport on phones.
- Safe areas: `.pb-safe` / `.pb-safe-sm` utilities (`styles.css`) pad composer/scroll bottoms past the home indicator; `AccessGate` uses `h-dvh` + `env(safe-area-inset-*)` padding.
- Pickers: never render a dropdown list inside the chat scroll container on mobile — use `SearchablePicker` (full-screen sheet).
- Scenario Vault on mobile renders labeled rows (`VaultMobileLabeledRow` in `ScenarioHistoryView.tsx`) instead of the desktop table; compare wizard cards/footers are uniform width via `FORM_CHAT_MAX_WIDTH`.

### LoanWizard — key internals

**5-step wizard** (`activeStep: 1–5`, "5 C's"):

- Step 1 Basics: Citizenship → visa/OFAC gates (FN) → Occupancy → **Loan Purpose** (comes before Lien Position) → Lien Position (filtered by purpose) → Property Type → Value/Loan/LTV triangle → FICO → FTHB/FTI → Income Path
- Step 2 Capacity: Doc Type + Doc Timeframe (`documentationTimeframe`, "12"/"24" — income-doc types only, hidden on DSCR path) + DTI (income path) OR DSCR + Rental Type (DSCR path) + Prepay + Stepdown
- Step 3 Credit: Payment history + `hasCreditEvent` gate + multi-select credit events with per-event dates/buckets
- Step 4 Collateral: State + geo sub-questions + Rural/Acreage + Vacant/Rehab (DSCR refi only) + optional Property Condition / Declining Market
- Step 5 Conditions: Listing seasoning + POA + Non-arm's length + Departing residence (optional) + Product preferences (always expanded)

**Form completion gates:**

- `block1Complete()` — Step 1 fields; includes `lienPosition`, `propertyType`, credit score, FTHB/FTI
- `block2Complete()` — block1 + Step 2 financial fields
- `collateralStepComplete` — geo location + rural + vacant/rehab
- `conditionsStepComplete` — POA + nonArmsLength + listingSeasoning (when required) + departingRent (when departing = renting). **Departing residence itself is optional.**
- `isFormComplete = block2Complete() && collateralStepComplete && conditionsStepComplete` — gates the Resubmit button in edit mode AND the sidebar Resubmit button in **both** intake modes (`canResubmit={isFormComplete}`; chat mode is no longer always-resubmittable)

**Lien position cascade** — `primaryLoanPurpose` is selected first; it filters which `lienPosition` options appear. A `useEffect` on `lienPosition` + `primaryLoanPurpose` syncs `isSecondLien` ("yes"/"no") and `loanPurpose` used by the eligibility payload.

**Upstream-edit cascade + red profile gaps** — editing an upstream field from the sidebar (post-results or mid-chat) routes through `buildCascadePatchForFormEdit(form, fieldKey, patch)` in `lib/formChatFlow.ts`: it clears now-invalid downstream dependents (e.g. `investmentIncomePath` change wipes doc type/timeframe/DTI/DSCR/rental type; `state` change clears geo follow-ups; `stateCounty` re-infers them). A Submit/Resubmit attempt while the form is incomplete sets `profileGapsForced` — missing required fields render highlighted (red) in the Mortgage Profile sidebar (`highlightProfileGaps` prop) and a toast points at them; the flag auto-clears once `isFormComplete`. In chat mode the blocked resubmit also re-prompts the conversation via `chatRepromptRef` (registered by `ChatConversationFlow` through `registerReprompt`).

**`FField` component** — renders form fields with label + optional red `*` marker. Pass `required` or `conditional` for the `*`; pass `optional` for the "(optional)" text label. `required`/`conditional` fields block step completion; `optional` ones never do.

**Edit mode — Back to Results** — when entering edit mode (`handleProfileEdit`), the current messages + eligiblePrograms + eligibilityTableMsgId are snapshotted into `savedResultsSnapshotRef`. Clicking "Back to Results" restores this snapshot directly (no API call). The snapshot is cleared on full Reset and on Resubmit (new results supersede it).

**Unified sidebar** — both `intakeMode="form"` and `intakeMode="chat"` now render identical `profileSections`-based sidebar (section cards → clickable rows → ✓/● icon + label + value + priority marker). Chat-only extras (Session Notes amber card, Run Eligibility button) are injected into the same block. The old `ChatModeSidebar` component and its inline-edit state were removed.

**Message prefixes triggering custom renderers:**

- `ELIGIBILITY_TABLE:{JSON}` — matched-programs table
- `PROGRAM_DETAIL:{JSON}` — key metrics + summarized considerations card
- `LOADING:{label}` — animated loading indicator
- `INTAKE_PREVIEW:{JSON}` — mid-intake preview card
- `INTAKE_CHECKLIST:{JSON}` — inline checklist at 10-question cap
- `INTAKE_OPTIONAL_PICKER:{}` — end-of-intake optional batch card
- `CHAT_*:{JSON}` — `/chat` control-payload cards emitted by `useChatConversation` and rendered in `lib/chatThreadView.tsx` / `ChatConversationFlow.tsx`: `CHAT_BULK_SUMMARY` (opening-turn extraction summary + stock-take line), `CHAT_CAPTURED` (per-turn captured-field echo), `CHAT_OPTIONS` / `CHAT_PRODUCT_PREF` (clickable option cards), `CHAT_CLARIFY` (ambiguity candidate cards), `CHAT_CREDIT_EVENTS` (credit-events fallback: multi-select event card → per-event timing card with seasoning buckets + MM/YYYY; the first ask is prose — "list all events with when each happened" — and the extractor's single-event deltas are union-merged via `adjustCreditEventPatch`), `CHAT_SUMMARY_ASK` (③ stock-take question: captured + numbered remaining, free-text multi-slot reply), `CHAT_RECAP` (closing recap: all captured values + notes/change-anything invite), `CHAT_OPTIONAL_BATCH` (end-of-intake optional batch, both modes, incl. inline product-pref cards), `CHAT_PRE_SUBMIT` / `CHAT_FINAL_CTA` (submit CTAs). Plain prose turns render as normal assistant bubbles. The `/chat` cadence (5 ask formats, COMBO_PAIRS, 2-strike skip guard) is documented in `docs/CHAT.md`.

**Chips carry letter prefixes** (`A ·`, `B ·`, `C ·`) rendered with `String.fromCharCode(65 + index)`. The backend's `resolve_answer()` accepts single-letter inputs (A/B/C) and routes them directly to `chip_answer` without calling the Extractor LLM.

### Server-Side Chat Intake Engine

> **Largely dormant.** Since the `/chat` rebuild this planner/asker loop is no longer wired to the UI (the client `useChatConversation` loop replaced it; see `docs/CHAT.md`). It's documented here because the code still exists, the slot catalog it defines (`SLOT_DEFS`/`SLOT_ORDER`) remains the shared source of truth for all three intakes, and `/api/intake/extract` reuses the Extractor + validation layer described below.

**Turn flow** (`POST /api/intake/message`):

1. **Fast path — letter resolution** (`resolve_answer()`) — if user typed/said a single letter matching the last question's chip options, set the slot directly and skip the Extractor.
2. **Call 1 — Extractor** (`chat_extractor.py:extractor_llm`) — maps user text to slot codes; two modes: `turn` (last_target_slots weighted) and `bulk` (initial multi-field dump).
3. **Validation layer** (`validate_extracted_values()`) — before merging, routes each extracted enum value through strict validation: state inputs use Levenshtein (`validate_state_input()`); other enums check against `SLOT_DEFS` options. Near-misses → `ASK_CLARIFY` ambiguity; garbage → discarded silently.
4. **`merge_extracted()`** — merges only the validated values into portfolio; will not downgrade high-confidence filled slots.
5. **Stage A — `planner_gate`** — fires milestones in priority order: ambiguities → `ASK_CLARIFY`; ≥3 user answers OR ≥80% quick-payload fill → `OFFER_PREVIEW`; all triggered essentials filled + optionals remain → `OFFER_OPTIONAL_BATCH`; fully complete → `OFFER_SUBMIT`; 10+ questions → `OFFER_CHECKLIST`; else `next_slot_strict()` returns `ASK_SLOT_DEFINITIVE`.
6. **Call 2 — Asker** (`chat_asker.py:asker_llm`) — only runs when planner_gate returns `None`; proposes the next question.
7. **Stage B — `planner_override`** — enforces 2:1 single:combined ratio via `single_streak`/`combined_streak` counters.

**Slot engine** — the catalog (`SLOT_DEFS`) now lives in `backend/metrics.py`; portfolio state, ordering (`SLOT_ORDER`) and planning live in `backend/chat/portfolio.py`. ~57 slots total:

- `SLOT_DEFS` — ~57 slot definitions with `id`, `section` (1–4), `priority` (`essential`/`conditional`/`optional`), `kind` (`enum`/`currency`/`number`/`text`), `options`, and a `trigger` lambda `(portfolio: dict) -> bool`. New slots added in v6: `visa_category`, `second_lien_product`, `prepay_stepdown`, `vacant_property`, `recently_rehabbed`, `hi_lava_zone`. Newest slot: `doc_timeframe` ("12"/"24", triggered for income-doc types via `_doc_timeframe_applies`, never on DSCR path).
- `SLOT_ORDER` — deterministic tuple of all 50+ slot IDs in the order the chat should ask them (Block 1 → Block 2 → Block 3 geo → Block 4 credit). `next_slot_strict()` walks this list, skipping optional-priority slots (those go only to the end-of-intake batch).
- `validate_extracted_values(extracted, portfolio)` — validates Extractor output before merge; returns `(valid, ambiguities, discarded)`. State values are validated with Levenshtein (`validate_state_input()`); enum values are checked against `SLOT_DEFS` options.
- `portfolio_to_eligibility_request()` — translates snake_case portfolio to the camelCase `EligibilityRequest` dict. Maps new fields: `hiLavaZone`, `vacantProperty`, `recentlyRehabbed`, `prepayStepdown`, `visaCategory`, `secondLienProduct`, `documentationTimeframe`.
- `uses_cltv_leverage()` — returns `True` for `second_lien` (standalone) and `second_lien_piggyback`; triggers a separate CLTV field.
- Portfolio convention: each slot `foo` has companion keys `foo_status` (`pending`/`filled`/`inferred`), `foo_source`, `foo_confidence`.

**Lien position in slot_engine** — three codes: `first_lien_only`, `second_lien` (standalone HELOC/HELOAN; triggers `second_lien_product`), `second_lien_piggyback`. Both second-lien types trigger `existing_first_lien` and `uses_cltv_leverage`.

**Planner milestones** — `OFFER_PREVIEW` fires at 3 user answers OR ≥80% `quick_eligibility_fill_ratio()`; `OFFER_OPTIONAL_BATCH` fires when all triggered essentials are filled (optionals bundled into one end-of-intake card with Skip all); `OFFER_CHECKLIST` fires as a fallback at question_count ≥ 10.

**Action kinds** — `GREETING`, `ASK_SLOT_DEFINITIVE`, `ASK_SLOT_COMBINED`, `ASK_CLARIFY`, `OFFER_PREVIEW`, `OFFER_SUBMIT`, `OFFER_CHECKLIST`, `OFFER_OPTIONAL_BATCH`, `OFFER_FREE_TEXT`.

**Session store** (`backend/chat/session.py`) — `IntakeSession` persisted to MySQL (`intake_sessions`, migration 009) with in-memory fallback.

### Eligibility Engine (`backend/eligibility.py`)

Pure MySQL queries: filters `ltv_matrix` by LTV/CLTV, loan amount, FICO, DTI/DSCR, occupancy, purpose, doc type → applies `map_geographic_restrictions` and `credit_event_seasoning` → returns matched programs with `rag_notes` and `special_overlay`. Accepts `quick=True` to skip Qdrant note fetching.

**Geo licensing footprint (DSCR-broader)** — the state allowlist is doc-type-dependent. `_eligible_state_set(conn, is_dscr)` reads `map_geographic_restrictions`: a **standard** (income-doc) scenario uses `restriction_detail = 'Eligible lending state'` (16 states); a **DSCR** scenario uses `'Eligible lending state (DSCR)'` (39 states, seeded by migration 020). The DSCR list is NOT a strict superset — AZ/NC/NV are standard-only — so it is stored as its own marker, not derived. Per-program overlay rows (overlays, conditions) also live in `map_geographic_restrictions`; reload the whole table from CSV via `ingest/tools/load_map_geographic_restrictions.py`.

**Near-miss ("Just Missed") discovery** — alongside matches, the full (non-quick) run returns up to 2 `NearMissProgram`s the borrower can realistically reach. Two tracks: (1) LTV/loan-amount misses found in-pool (`_evaluate_near_miss`, with `suggested_ltv`/`suggested_loan`); (2) human-fixable FICO/DTI misses (`_find_fico_dti_near_misses`) — Layer 1 is re-run with a relaxed envelope (`NEAR_MISS_FICO_RANGE` = 40 pts, `NEAR_MISS_DTI_RANGE` = 10 pts), then each candidate is classified against the REAL scenario and must miss on exactly ONE of FICO or DTI. DTI misses carry a `near_miss_suggestion` (e.g. add a co-borrower) rendered as an amber lightbulb chip in the results UI. The frontend persists `nearMissPrograms` in the wizard session (`wizardSessionPersist.ts`).

### Key Data Model

- `ltv_matrix` — eligibility gates keyed by lender + occupancy + purpose + doc type
- `map_geographic_restrictions` — per-program state allowlist + geo overlays/conditions (effect/conditions cols added in 019; DSCR allowlist in 020); `credit_event_seasoning`, `program_requirements` — overlay rules
- `intake_sessions` — chat intake portfolio + planner state (migration 009)
- `form_history_scenario` — saved wizard profiles with form_fields JSON + accepted/rejected programs (migrations 010, 011); archive tags added in 015, status column in 016

### Reference / design docs

> `docs/` now holds five PLAIN-LANGUAGE product docs (running/form/chat/eligibility/pricing — written for laypeople, describing what's built). The old engineering design docs (CHAT_MODE_REBUILD, CHAT_CONVERSATIONAL_UPGRADE_PLAN, CHAT_BACKEND_FLOW, CHAT_UI_FLOW, CHAT_INTERFACE_LOGIC, API_GUIDE, CODE_WALKTHROUGH, DEPLOY, FLOWS, GEO_FLOW, diagrams/…) were removed 2026-06 and are recoverable from git history. For `/chat` internals, this CLAUDE.md (LoanWizard/chat sections) is now the primary engineering reference.

- `ChatIntakeFlow_v6.md` (the v6 server-side planner design doc) and the legacy `ChatIntakeExperience.jsx` prototype were also removed 2026-06 — recover from git history if needed.

## Critical Sync Rules

- **`SLOT_DEFS` (in `backend/metrics.py`) / `SLOT_ORDER` (in `backend/chat/portfolio.py`) must stay in sync with `LoanWizard.tsx` step fields AND `frontend/src/lib/formChatFlow.ts` (`FORM_CHAT_QUESTIONS`).** `formChatFlow.ts` is the single field schema for BOTH client intakes (`/form` guided chat and `/chat` prose conversation), so a new field needs: a slot definition, an entry in `SLOT_ORDER` at the correct block position, and a question in `formChatFlow.ts`. If it affects eligibility, also map it in `portfolio_to_eligibility_request()` and in the client `portfolioToFormPatch.ts`. Worked example: `doc_timeframe` (added 2026-06) touches all five places, plus an extraction rule in `backend/chat/extract.py` since the Extractor needed normalization guidance ("1 year" → "12").
- **`portfolio_to_eligibility_request()` must map every slot** that the eligibility engine needs. When adding a slot that affects eligibility, add its mapping to this function.
- **Always run `npm run format`** after editing `.tsx` files — Prettier is enforced on CI.
- **Python 3.13 not supported**; use 3.12.
- **Three servers run locally** — Vite proxies `/api/loanpass/*` → `localhost:8090` (pricing service, `backend.pricing_app`) and all other `/api/*` → `localhost:8080` (main API). Run `npm run dev:api`, `npm run dev:pricing`, and `npm run dev`. The proxy lists `/api/loanpass` BEFORE `/api` so the specific prefix wins. Set `MOUNT_PRICING_INLINE=1` to fold pricing back into the main API for single-process dev; prod can instead point `VITE_PRICING_BASE_URL` at a separate pricing host.
- macOS `._*` sidecar files are filtered in `backend/api.py` startup to prevent venv import errors.

## Reference Docs (plain-language, layman-facing)

- `docs/RUNNING.md` — how to run (env, three services, Docker, deploy)
- `docs/FORM.md` — Form Mode guided intake, as built
- `docs/CHAT.md` — Chat Mode conversational intake, as built
- `docs/ELIGIBILITY.md` — how matching works (quick vs full, gates, near-misses)
- `docs/PRICING.md` — LoanPASS pricing engine + standalone service
