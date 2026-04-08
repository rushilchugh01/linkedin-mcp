# LinkedIn Post Engagement Implementation Plan

## Goal

Extend this fork into a cohesive LinkedIn MCP/server project that can:

- Discover company posts via the existing `get_company_posts` flow.
- Navigate directly to individual post URLs.
- Extract post details, comments/commenters, and reactors/likers.
- Expose the same workflows both as MCP tools and direct CLI commands.
- Support durable runs with checkpoints and exports without requiring an agent.

The implementation should stay aligned with the existing repo style: shared Patchright session, serialized tool execution, URL navigation where possible, and isolated DOM-dependent code only where interactions are unavoidable.

## Current Baseline

The fork already has the useful foundation:

- `linkedin_mcp_server/server.py` registers tool modules and applies `SequentialToolExecutionMiddleware`.
- `linkedin_mcp_server/dependencies.py` provides `get_ready_extractor()` for a shared authenticated browser/extractor.
- `linkedin_mcp_server/tools/company.py` has `get_company_posts(company_name)`, which navigates to `/company/{company_name}/posts/` and returns raw section text plus compact references.
- `linkedin_mcp_server/scraping/link_metadata.py` already classifies `/feed/update/...` links as `feed_post` references.
- `linkedin_mcp_server/scraping/extractor.py` owns the browser navigation and extraction logic.

Missing pieces:

- No structured post parser.
- No post detail tool by `post_url`.
- No comments/commenters extraction.
- No reactors/likers extraction.
- No persistent data model for posts, people, comments, and reactions.
- No direct CLI commands for the new workflows.

## Veridis JavaScript Reference Scripts

Use these local scripts as behavior references while porting:

```text
C:\NOS\Projects\veridis\scripts\linkedin-feed-scraper.js
C:\NOS\Projects\veridis\scripts\send-connection-request.js
```

WSL paths:

```text
/mnt/c/NOS/Projects/veridis/scripts/linkedin-feed-scraper.js
/mnt/c/NOS/Projects/veridis/scripts/send-connection-request.js
```

### `linkedin-feed-scraper.js`

This script currently runs in the browser console and:

- Scrolls the LinkedIn feed.
- Expands visible `... more` post text.
- Extracts posts from current feed DOM list items.
- Dedupe-generates post IDs from author URL and post text.
- Attempts to derive activity IDs from `componentkey` attributes.
- Builds feed post URLs from `urn:li:activity:{id}`.
- Parses reaction/comment/repost counts from visible text.
- Extracts visible reaction type icons.
- Filters posts by legal/law keywords and engagement thresholds.
- Opens visible comment sections and extracts commenter details.
- Downloads the final JSON in-browser.

Use this script as a migration reference, not as runtime code. Its feed-DOM approach is useful for extraction heuristics, but the MCP implementation should prefer direct post URLs and shared Patchright browser state.

### `send-connection-request.js`

This script currently runs in the browser console on a LinkedIn profile page and:

- Starts at the top of the profile to avoid sticky/collapsed button states.
- Finds the visible profile heading.
- Scopes interactive element search to the profile's own section.
- Detects `1st` degree and pending connection state.
- Uses direct `Invite ... to connect` buttons for some profiles.
- Falls back to `More -> Connect` for 3rd-degree profiles.
- Searches shadow DOM for `Send without a note`.
- Detects silent success by checking for a pending state after click.

This behavior should inform a rewrite of the Python `connect_with_person` flow. Do not run this JS separately from Python unless it is a short-lived debugging fallback.

## Design Principles

- Use `post_url` / activity URN as the unit of work, not a feed DOM node.
- Keep low-level browser clicks and selectors inside scraper/extractor code, not agent prompts.
- Prefer direct URL navigation over finding posts in an infinite feed.
- Avoid LinkedIn class names unless there is no reasonable alternative.
- Keep single-post MCP tools small enough to fit the existing `TOOL_TIMEOUT_SECONDS` limit; use a dedicated larger timeout for bounded `company_engagement`.
- Return partial results with diagnostics rather than failing an entire enrichment run.
- Make CLI and MCP wrappers call the same core workflow functions.
- Do not reuse the user's normal Chrome/Firefox profile; use the repo's dedicated Patchright profile.
- Port Veridis JavaScript behavior into Python/Patchright methods instead of launching separate JS runtimes.

## Proposed Data Shapes

### Post

```json
{
  "post_url": "/feed/update/urn:li:activity:123/",
  "activity_urn": "urn:li:activity:123",
  "activity_id": "123",
  "author_name": "Example Company",
  "author_url": "/company/example/",
  "text": "...",
  "reaction_count": 10,
  "comment_count": 4,
  "repost_count": 1,
  "reaction_types": ["Like", "Celebrate"],
  "scraped_at": "2026-04-08T00:00:00Z"
}
```

### Person

```json
{
  "profile_url": "/in/person/",
  "name": "Person Name",
  "headline": "General Counsel",
  "degree": "2nd",
  "source": "commenter"
}
```

### Comment

```json
{
  "post_url": "/feed/update/urn:li:activity:123/",
  "commenter_profile_url": "/in/person/",
  "commenter_name": "Person Name",
  "text": "...",
  "relative_timestamp": "2d",
  "observed_at": "2026-04-08T00:00:00Z",
  "approx_timestamp": "2026-04-06T00:00:00Z",
  "like_count": 3,
  "reply_count": 1
}
```

`approx_timestamp` is best-effort and should be treated as approximate because LinkedIn often exposes relative strings like `2d` rather than exact timestamps. Always store `observed_at`/`scraped_at` so relative timestamps remain interpretable later.

### Reaction

```json
{
  "post_url": "/feed/update/urn:li:activity:123/",
  "reactor_profile_url": "/in/person/",
  "reactor_name": "Person Name",
  "reaction_type": "Like",
  "scraped_at": "2026-04-08T00:00:00Z"
}
```

## Module Layout

Add these modules:

```text
linkedin_mcp_server/tools/post.py
linkedin_mcp_server/scraping/post.py
```

Optional later:

```text
linkedin_mcp_server/workflows/__init__.py
linkedin_mcp_server/workflows/company_engagement.py
linkedin_mcp_server/storage/__init__.py
linkedin_mcp_server/storage/jsonl.py
linkedin_mcp_server/storage/sqlite.py
```

Recommended split:

- `tools/post.py`: MCP tool registration only.
- `scraping/post.py`: post-page interactions, comments extraction, reactions extraction, URL normalization.
- `cli_main.py`: direct CLI wiring after scraper methods are stable.
- `storage/jsonl.py`: optional JSONL append/export helpers after the core extraction data shape stabilizes.
- `storage/sqlite.py`: maybe-later query/checkpoint store only if there is a concrete need to query across runs.

For connection requests, update the existing Python path instead of adding a parallel tool:

```text
linkedin_mcp_server/tools/person.py
linkedin_mcp_server/scraping/extractor.py
linkedin_mcp_server/scraping/connection.py
```

If the connection flow grows, move the browser-action code into:

```text
linkedin_mcp_server/scraping/connection_request.py
```

and have `LinkedInExtractor.connect_with_person()` delegate to it.

## MCP Tools

Start with focused post-level tools:

```text
get_post_details(post_url: str)
get_post_comments(post_url: str, limit: int | None = None)
get_post_reactors(post_url: str, limit: int | None = None, reaction_type: str | None = None)
```

Add company engagement orchestration after the single-post tools are stable:

```text
CLI: company-engagement(company_name: str, limit: int = 3, include_comments: bool = true, include_reactors: bool = false)
MCP: company_engagement(company_name: str, limit: int = 3, include_comments: bool = true, include_reactors: bool = false)
```

Do not implement the orchestrator first. It depends on company-post discovery plus single-post details/comments/reactors. Build and test those one-post tools first. For MCP, give `company_engagement` a dedicated larger timeout than the global `TOOL_TIMEOUT_SECONDS`, conservative defaults, and hard internal limits so one call cannot fan out indefinitely. If it still becomes too long-running, add a job-style MCP flow later that returns a job ID and supports polling.

## Connection Request Strategy

The repo already has an MCP tool:

```text
connect_with_person(linkedin_username, note=None)
```

Keep that public tool name unless there is a strong reason to break compatibility. Replace or augment the internals with the stronger behavior from:

```text
/mnt/c/NOS/Projects/veridis/scripts/send-connection-request.js
```

Target behavior:

- Navigate directly to `https://www.linkedin.com/in/{username}/`.
- Scroll to the top before inspecting profile action buttons.
- Identify the profile's main visible heading.
- Scope button search to the profile's top section where possible.
- Detect:
  - already connected
  - already pending
  - direct connect available
  - `More -> Connect` available
  - connect unavailable
- Click direct Connect when available.
- Fall back to More menu Connect when needed.
- For no-note requests, handle the `Send without a note` path, including shadow DOM if LinkedIn still renders it there.
- For note requests, preserve the current Python behavior that opens/fills the note dialog when available.
- Confirm result by checking for pending/connected state after the action rather than assuming success from a click.

Shadow DOM is the most fragile part of the connection-request flow. If the shadow-DOM send button cannot be found, the code should fall back to visible dialog buttons and then return a controlled `send_failed` or `note_not_supported` result with diagnostics. It should not crash or leave the dialog open.

Status mapping should stay compatible with current tool responses:

```text
already_connected
pending
follow_only
connect_unavailable
unavailable
send_failed
note_not_supported
connected
accepted
```

Add any new internal diagnostics under optional fields; avoid changing existing top-level statuses unless necessary.

## Direct CLI Commands

After the MCP post tools work, expose the same workflows directly:

```bash
uv run -m linkedin_mcp_server post-details "https://www.linkedin.com/feed/update/urn:li:activity:123/"
uv run -m linkedin_mcp_server post-comments "https://www.linkedin.com/feed/update/urn:li:activity:123/" --limit 50
uv run -m linkedin_mcp_server post-reactors "https://www.linkedin.com/feed/update/urn:li:activity:123/" --limit 100
uv run -m linkedin_mcp_server company-engagement anthropic --limit 10 --comments --reactors --reactor-limit 50
```

Reactors are opt-in and the implemented default reactor limit is `0`, so any
run that should collect reactors must pass a positive `--reactor-limit`.

If `cli_main.py` becomes too crowded, add a subcommand module rather than stuffing logic into the entry point.

## Storage And Checkpoints

Start with JSON/JSONL output. That is enough for MCP clients, CLI runs, and agent-driven workflows because callers can consume JSON directly.

```text
data/posts.jsonl
data/comments.jsonl
data/reactions.jsonl
```

Keep SQLite as a maybe-later option, not a planned implementation phase. Add it only if there is a concrete need to query across runs, resume large jobs, or dedupe a large local lead database.

Add `.gitignore` entries for local output and session/debug data.

## Throttling And Navigation Pace

Every workflow that performs multiple LinkedIn navigations or modal loads should pace itself deliberately.

Baseline rules:

- Reuse the repo's existing `_NAV_DELAY` for multi-section/page flows where it fits.
- Add jittered pauses between post navigations, comment loads, and reaction-dialog scrolls.
- Keep default limits small.
- Make reactors opt-in because reaction dialogs are expensive.
- Keep `company_engagement` bounded by explicit `limit`, per-post comment limits, per-post reactor limits, and a dedicated timeout.
- Return partial results when throttling or checkpoint/auth barriers appear.

Suggested defaults:

```text
post navigation delay: 2-5s jittered
comment load-more delay: 1.5-3s jittered
reaction dialog scroll delay: 1.5-3s jittered
company engagement default limit: 3 posts
company engagement default comment limit: 20 comments per post
company engagement default reactor limit: 0 unless explicitly requested
```

Do not add stealth/evasion machinery. The goal is to avoid hammering the site and to make failures recoverable, not to bypass platform controls.

## Implementation Phases

### Phase 0: Fork Hygiene

- Rename package/display metadata where appropriate.
- Fix `manifest.json` license metadata to match Apache 2.0.
- Add fork attribution in README.
- Keep the original Apache 2.0 license and copyright.
- Run baseline tests before feature work.

Commands:

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ty check
```

### Phase 1: Post URL Utilities

- Add helpers to normalize post URLs.
- Extract `activity_urn` and numeric activity ID.
- Accept absolute LinkedIn URLs and repo-style relative `/feed/update/...` paths.
- Add unit tests for URL normalization.

### Phase 2: Post Details

- Add `LinkedInExtractor.get_post_details(post_url)` or a delegated helper in `scraping/post.py`.
- Navigate directly to the post URL.
- Use the existing `extract_page` / `innerText` path first.
- Return raw text plus references initially.
- Add structured fields only after observing live page text.

MCP wrapper:

```text
get_post_details(post_url)
```

### Phase 3: Comments

- Add `get_post_comments(post_url, limit=None)`.
- Navigate to post URL.
- Open or reveal the comments section when necessary.
- Load more comments up to `limit`.
- Extract commenter name, profile URL, headline if visible, comment text, relative timestamp, best-effort approximate timestamp, likes, replies, and `observed_at`.
- Return partial results if some comment rows fail.

Keep all selectors in one function/module and document why each is needed.

### Phase 4: Reactors / Likers

- Add `get_post_reactors(post_url, limit=None, reaction_type=None)`.
- Navigate to post URL.
- Click the reaction summary.
- Wait for the reaction dialog.
- Optionally select/filter a reaction type if LinkedIn exposes filter tabs.
- Scroll the dialog to load people.
- Extract reactor name, profile URL, headline if visible, and reaction type when available.
- Close the dialog before returning.

This is likely the most fragile part. Keep it separate from comments.

### Phase 5: Connection Request Port

- Port the logic from `send-connection-request.js` into Python/Patchright.
- Keep `connect_with_person` as the public MCP tool.
- Add helper methods for:
  - locating the profile header section
  - detecting degree/pending state from the visible profile section
  - direct Connect click
  - More-menu Connect click
  - shadow-DOM `Send without a note` search with controlled fallback
  - post-click state verification
- Preserve existing note support where possible.
- Add regression tests against mocked page behavior.

### Phase 6: CLI

- Add CLI subcommands that call the same extraction workflows.
- Support JSON output to stdout and optional `--output` files.
- Keep CLI auth behavior consistent with existing `--login`, `--status`, and `--logout`.

### Phase 7: JSONL Export

- Start with JSONL export.
- Add idempotent append/dedupe behavior for posts and people if exports are written locally.
- Defer SQLite until there is a concrete need for querying across runs or durable job polling.

### Phase 8: Bounded Orchestration Or Jobs

- Add `company-engagement` as a CLI command and `company_engagement` as an MCP tool.
- Use existing `get_company_posts` references as seeds.
- Limit default post count aggressively, for example `limit=3`.
- Make reactors opt-in because they are expensive and fragile.
- Report progress per post.
- Return job summary plus extracted records or storage path.
- Use a dedicated larger timeout for the MCP tool instead of the global default.
- Add hard caps for maximum posts, comments per post, and reactors per post.
- If MCP orchestration is still needed later, use a job pattern:
  - `start_company_engagement_job(...) -> job_id`
  - `get_engagement_job_status(job_id)`
  - `get_engagement_job_results(job_id)`

## Comprehensive Testing Strategy

Testing needs to separate deterministic parser behavior from live LinkedIn behavior. Most tests should run without opening a browser; only a small manual/live suite should touch LinkedIn.

### Test Tiers

Tier 1: Pure unit tests

- Run on every commit.
- No browser, no network, no LinkedIn session.
- Validate URL parsing, activity URN extraction, count parsing, keyword filtering, status mapping, storage upserts, and data normalization.

Tier 2: Mocked browser/extractor tests

- Run on every commit.
- Use mocked Patchright `Page` and existing repo test patterns.
- Validate that tools call the correct extractor methods, progress is reported, errors are mapped to `ToolError`, and timeouts are configured.

Tier 3: Fixture parser tests

- Run on every commit.
- Use small HTML/text fixtures copied from sanitized DOM snapshots or handcrafted examples.
- Validate post/comment/reactor parsing without relying on a live LinkedIn page.
- Keep fixtures minimal and remove personal data.

Tier 4: Local smoke tests

- Run manually before merging scraping changes.
- Uses the dedicated `~/.linkedin-mcp/profile` auth profile.
- Low limits only.
- Confirms the browser opens, auth works, post navigation works, and each new tool returns plausible partial data.

Tier 5: Live exploratory tests

- Run only when selectors or flows are suspected broken.
- Use `--no-headless` and DEBUG logs.
- Capture debug traces/screenshots only when needed, and scrub sensitive data before committing any fixture derived from them.

### Unit Tests

Add tests for:

- `normalize_post_url()`
  - absolute LinkedIn URL
  - relative `/feed/update/.../` path
  - query/hash stripping
  - invalid non-LinkedIn URL rejection
- `extract_activity_urn()`
  - `urn:li:activity:123`
  - `/feed/update/urn:li:activity:123/`
  - missing/invalid values
- Engagement parsing
  - `10 reactions`
  - `1,234 reactions`
  - `and 9 others`
  - `2 comments`
  - `3 reposts`
- Reaction type normalization
  - Like, Celebrate, Love, Insightful, Support, Funny
  - unknown reaction type passthrough or graceful fallback
- Keyword filtering
  - law/legal keyword match in text
  - keyword match in author headline
  - promoted exclusion
  - min reactions/comments threshold behavior
- Person/profile URL normalization
  - `/in/name/`
  - `https://www.linkedin.com/in/name/?miniProfileUrn=...`
- JSONL/export dedupe behavior
  - duplicate people by `profile_url`
  - duplicate reactions by `(post_url, profile_url, reaction_type)`
  - duplicate comments by stable hash

### Tool Wrapper Tests

Add tests in `tests/test_tools.py` for:

- `get_post_details` registration and timeout.
- `get_post_comments` registration and timeout.
- `get_post_reactors` registration and timeout.
- `company_engagement` registration with its dedicated larger timeout and hard caps.
- Tool wrappers call the expected extractor methods.
- Authentication errors go through the same `handle_auth_error` path.
- Generic errors become tool errors through `raise_tool_error`.
- `company_engagement` calls company-post discovery and then post enrichment only for selected feed-post references.
- If a job API is added later, `start_company_engagement_job` returns quickly with a `job_id`, and polling/result tools are covered separately.

Update `TestToolTimeouts` when adding new MCP tools.

### Browser Flow Tests

Use mocked page behavior to test:

- Direct post navigation calls `_navigate_to_page()` with normalized post URL.
- Comment expansion stops at `limit`.
- Comment load-more loop stops when no button exists.
- Reaction dialog close is attempted even when parsing fails.
- Reactor extraction stops at `limit`.
- Partial row parse failures are collected as diagnostics rather than failing the whole result.

### Connection Request Tests

The connection flow must preserve existing behavior and add coverage from `send-connection-request.js`.

Add tests for:

- 1st-degree profile returns `already_connected`.
- Pending profile returns `pending`.
- Direct `Invite ... to connect` button path returns `connected`.
- More-menu `Connect` path returns `connected`.
- Missing More button returns `connect_unavailable` or `send_failed` consistently.
- Menu contains `Pending` after More click and returns `pending`.
- Shadow DOM `Send without a note` button is found and clicked.
- Missing shadow DOM send button falls back to visible dialog actions or a controlled failure status.
- No modal appears but pending state appears afterward, treated as success.
- Note flow still supports add-note/fill/send when a note is provided.
- Note unsupported path returns `note_not_supported`.
- Failed click returns `send_failed`.

Prefer testing helper functions separately:

```text
detect_profile_action_state
find_profile_section
click_direct_connect
click_more_menu_connect
find_shadow_dom_send_without_note
verify_connection_result
```

### Fixture Strategy

Add sanitized fixtures under:

```text
tests/fixtures/linkedin/
```

Suggested fixtures:

```text
company_posts_inner_text.txt
post_page_inner_text.txt
comments_section.html
reactions_dialog.html
profile_topcard_connect_direct.html
profile_topcard_connect_more.html
profile_topcard_pending.html
profile_topcard_1st_degree.html
```

Rules:

- No real private profile data.
- Prefer synthetic names and URLs.
- Keep fixtures small.
- Do not commit raw full-page LinkedIn dumps.
- If a live debug snapshot is used to design a fixture, rewrite it into a synthetic fixture before committing.

### Live Smoke Checklist

Before merging scraping-flow changes:

```bash
uv run -m linkedin_mcp_server --login
uv run -m linkedin_mcp_server --status
uv run -m linkedin_mcp_server --no-headless --transport streamable-http --log-level DEBUG
```

Then test through an MCP client or streamable HTTP:

```text
get_company_posts(company_name="anthropic")
get_post_details(post_url="<one returned feed_post URL>")
get_post_comments(post_url="<same URL>", limit=5)
get_post_reactors(post_url="<same URL>", limit=5)
connect_with_person(linkedin_username="<safe test profile>", note=None)
```

Use a test account/workflow where sending a connection request is intentional. Do not run destructive connection-request smoke tests against arbitrary people.

### CI Expectations

Default CI should run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

CI should not require LinkedIn credentials or a browser session. Live LinkedIn checks should remain manual or separately gated.

### Regression Policy

When LinkedIn DOM changes:

- Add or update a small synthetic fixture reproducing the broken shape.
- Fix the smallest selector/helper module possible.
- Add a regression test before or with the fix.
- Avoid broad changes to generic extraction unless the breakage affects multiple workflows.

## Expected Maintenance

Most stable:

- Company post discovery via URL navigation and `innerText`.
- Feed post URL references.

More fragile:

- Comment reveal/load-more behavior.
- Reaction dialog opening and scrolling.
- Per-person reaction type extraction.

Plan for periodic selector fixes. Keep the DOM-dependent code small and isolated so a LinkedIn markup change does not require touching the whole extractor.

## First Concrete PR

Recommended first PR scope:

- Fork metadata cleanup.
- Add post URL normalization helpers.
- Add `get_post_details(post_url)` MCP tool returning raw `sections` and `references`.
- Add tests for helper parsing and tool registration.
- Update README and manifest tool list.

Do not include comments, reactors, storage, and orchestration in the first PR.

## Second Concrete PR

Recommended second PR scope:

- Port `send-connection-request.js` behavior into the existing Python `connect_with_person` implementation.
- Preserve current response status compatibility.
- Add focused connection-flow unit/mocked-browser tests.
- Keep note support working.

Do not combine connection-request changes with post comments/reactors in the same PR.
