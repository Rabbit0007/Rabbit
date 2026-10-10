# Implementation Plan: Rabbit Product UI

## Overview

This plan transforms Rabbit into a product-grade penetration testing platform through additive changes only. No existing core files (dispatcher, scheduler, workers, existing routers) are modified. New routers, database tables, middleware, and frontend views are layered on top of the existing FastAPI + SQLite + Alpine.js architecture.

Implementation order: authentication system first (all other features depend on it), then vulnerability reports, worker dashboard, templates, attack timeline, and finally navigation/layout integration.

## Tasks

- [x] 1. Authentication foundation — database, models, and auth router
  - [x] 1.1 Add bcrypt dependency and create auth database schema
    - Add `bcrypt` to `cairn/pyproject.toml` dependencies
    - Create `cairn/src/cairn/server/auth_db.py` with schema for `users`, `sessions`, and `login_attempts` tables
    - Add a `configure_auth_db()` function that runs the new schema DDL on the existing SQLite connection
    - Call `configure_auth_db()` from the FastAPI lifespan after `db.configure()`
    - _Requirements: 1.1, 1.3, 2.1, 3.1_

  - [x] 1.2 Create Pydantic models for auth requests and responses
    - Create `cairn/src/cairn/server/models/auth.py` with `RegisterRequest`, `LoginRequest`, `UserResponse`, `PasswordChangeRequest`
    - Implement validation: username 3-32 chars `[a-zA-Z0-9_-]`, password 8-72 chars for registration, 8-128 chars with complexity for password change
    - _Requirements: 1.4, 1.6, 1.7, 4.1, 4.4_

  - [x] 1.3 Implement the auth router — registration endpoint
    - Create `cairn/src/cairn/server/routers/auth.py`
    - Implement `POST /api/auth/register`: validate input, check username uniqueness (case-insensitive), hash password with bcrypt cost 12, create user record, create session token (128+ bits via `secrets.token_hex(32)`), set HTTP-only cookie, return user info
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 1.4 Implement the auth router — login endpoint with rate limiting
    - Implement `POST /api/auth/login`: validate credentials, check rate limit (5 failed attempts per username in 15-min window), create session with 24h expiry, set HTTP-only Secure SameSite=Strict cookie
    - Return generic error for invalid username OR password (no distinction)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 1.5 Implement the auth router — logout, me, and password change endpoints
    - Implement `POST /api/auth/logout`: invalidate session within 1 second, clear cookie
    - Implement `GET /api/auth/me`: return current user info from session
    - Implement `PUT /api/auth/password`: verify current password, validate new password policy, update hash, invalidate all other sessions for user
    - _Requirements: 3.3, 4.1, 4.2, 4.3, 4.4_

  - [x]* 1.6 Write unit tests for auth router
    - Test registration validation (username format, password length, duplicate username)
    - Test login with valid/invalid credentials and rate limiting
    - Test session creation, expiry, and logout invalidation
    - Test password change with correct/incorrect current password
    - _Requirements: 1.1–1.7, 2.1–2.5, 3.1–3.6, 4.1–4.4_

- [x] 2. Auth middleware and route protection
  - [x] 2.1 Create auth middleware dependency
    - Create `cairn/src/cairn/server/middleware/auth.py`
    - Implement a FastAPI dependency that extracts `session_token` from cookie, validates against `sessions` table, extends expiration (sliding window), injects user into request state
    - Return 401 for invalid/expired/missing tokens; return 302 redirect to login for browser navigation requests (Accept: text/html)
    - Exempt paths: `/api/auth/register`, `/api/auth/login`, `/static/*`, `/`
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 5.1, 5.2, 5.3, 5.4_

  - [x] 2.2 Wire auth middleware into the FastAPI app
    - Register the auth router in `app.py`
    - Apply the auth middleware as a dependency on all existing routers (projects, hints, intents, export, settings) without modifying those router files — use `app.include_router(..., dependencies=[Depends(require_auth)])`
    - _Requirements: 5.1, 5.2_

  - [x]* 2.3 Write unit tests for auth middleware
    - Test unauthenticated access returns 401
    - Test expired session returns 401 and clears cookie
    - Test valid session extends expiration
    - Test exempt paths bypass auth
    - Test browser requests get 302 redirect
    - _Requirements: 5.1–5.4_

- [x] 3. Checkpoint — Auth system complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Vulnerability Report Engine
  - [x] 4.1 Create vulnerability extraction service
    - Create `cairn/src/cairn/server/services/vulnerability_extraction.py`
    - Implement severity pattern matching against fact descriptions using regex patterns for critical/high/medium/low
    - Implement `scan_project_facts(project_id)` that extracts vulnerabilities and upserts into `vulnerabilities` table
    - Implement `scan_all_projects()` for full re-scan
    - _Requirements: 6.1, 6.2, 6.4, 6.5_

  - [x] 4.2 Create vulnerabilities database schema and models
    - Add `vulnerabilities` table schema to `auth_db.py` (or a new `product_db.py`)
    - Create `cairn/src/cairn/server/models/vulnerabilities.py` with `Vulnerability`, `VulnerabilitySummary`, `VulnerabilityExportRequest` Pydantic models
    - _Requirements: 6.2, 6.3_

  - [x] 4.3 Implement vulnerabilities router — list and summary endpoints
    - Create `cairn/src/cairn/server/routers/vulnerabilities.py`
    - Implement `GET /api/vulnerabilities`: list with optional severity and project_id query filters (AND logic), return vulnerabilities with title, severity, project name
    - Implement `GET /api/vulnerabilities/summary`: return counts grouped by severity level
    - _Requirements: 6.3, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 4.4 Implement vulnerabilities router — export and refresh endpoints
    - Implement `GET /api/vulnerabilities/export?format=json|csv`: generate export file respecting active filters, include summary section
    - Implement `POST /api/vulnerabilities/refresh`: trigger re-scan of all project facts
    - Handle edge cases: unsupported format returns error, zero results generates valid file with summary only
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 4.5 Wire vulnerabilities router into the app
    - Register the vulnerabilities router in `app.py` with auth dependency
    - Ensure project deletion cascades to vulnerabilities (handled by FK ON DELETE CASCADE)
    - _Requirements: 6.5_

  - [x]* 4.6 Write unit tests for vulnerability extraction and router
    - Test pattern matching extracts correct severity levels
    - Test filter combinations (severity, project, both, none)
    - Test JSON and CSV export format correctness
    - Test empty results produce valid export with zero counts
    - _Requirements: 6.1–6.7, 7.1–7.6, 8.1–8.6_

- [x] 5. Worker Dashboard
  - [x] 5.1 Create dispatcher internal state endpoint
    - Create `cairn/src/cairn/dispatcher/internal_api.py` with a minimal FastAPI app on a separate internal port
    - Expose `GET /internal/status` returning worker configs, running tasks, task history, heartbeat data
    - Start the internal API server alongside the dispatcher loop
    - _Requirements: 9.1, 9.4, 10.1, 10.2, 10.4, 10.5_

  - [x] 5.2 Create worker task history schema and models
    - Add `worker_task_history` table schema
    - Create `cairn/src/cairn/server/models/workers.py` with `WorkerStatus`, `WorkerTaskHistoryEntry` Pydantic models
    - _Requirements: 10.1, 10.2, 11.2, 11.3_

  - [x] 5.3 Implement workers router
    - Create `cairn/src/cairn/server/routers/workers.py`
    - Implement `GET /api/workers`: proxy to dispatcher internal status endpoint, format response with name, type, status, current task (truncated 120 chars), tasks completed, avg duration, last heartbeat
    - Implement `GET /api/workers/{name}/history`: return 20 most recent tasks for the worker with project name, task type, description, start time, duration, outcome
    - Handle dispatcher unreachable: return connectivity warning
    - _Requirements: 9.1–9.5, 10.1–10.6, 11.1–11.3_

  - [x] 5.4 Wire workers router into the app
    - Register the workers router in `app.py` with auth dependency
    - _Requirements: 9.1_

  - [x]* 5.5 Write unit tests for workers router
    - Test worker status formatting and truncation
    - Test history endpoint returns correct number of entries
    - Test handling when dispatcher is unreachable
    - _Requirements: 9.1–9.5, 10.1–10.6, 11.1–11.3_

- [x] 6. Checkpoint — Backend APIs complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Project Templates
  - [x] 7.1 Create templates database schema and built-in template data
    - Add `templates` table schema
    - Create `cairn/src/cairn/server/services/templates.py` with `BUILTIN_TEMPLATES` list containing Web Application Assessment, Internal Network Pentest, External Network Pentest, and CTF Challenge
    - _Requirements: 12.1, 12.2_

  - [x] 7.2 Create templates Pydantic models
    - Create `cairn/src/cairn/server/models/templates.py` with `TemplateResponse`, `CreateTemplateRequest` models
    - Validate: title/origin/goal 1-200 chars, hints 0-10 items
    - _Requirements: 13.1, 13.6_

  - [x] 7.3 Implement templates router
    - Create `cairn/src/cairn/server/routers/templates.py`
    - Implement `GET /api/templates`: return built-in templates + user's custom templates, label each as builtin or user-created
    - Implement `POST /api/templates`: create custom template, enforce 50-template limit per user
    - Implement `DELETE /api/templates/{id}`: delete only if user owns the template, reject with error otherwise
    - _Requirements: 12.1, 12.2, 13.1–13.7_

  - [x] 7.4 Wire templates router into the app
    - Register the templates router in `app.py` with auth dependency
    - _Requirements: 12.1_

  - [x]* 7.5 Write unit tests for templates router
    - Test built-in templates are always returned
    - Test custom template CRUD operations
    - Test 50-template limit enforcement
    - Test ownership check on delete
    - _Requirements: 12.1–12.5, 13.1–13.7_

- [x] 8. Attack Timeline
  - [x] 8.1 Create timeline Pydantic models
    - Create `cairn/src/cairn/server/models/timeline.py` with `TimelineEvent` model
    - Event types: fact_discovery, intent_declaration, intent_conclusion, project_completion
    - _Requirements: 14.1, 14.2, 14.3_

  - [x] 8.2 Implement timeline router
    - Create `cairn/src/cairn/server/routers/timeline.py`
    - Implement `GET /api/projects/{id}/timeline`: query facts and intents for the project, merge into chronological order (by created_at, then declaration order as tiebreaker), return as TimelineEvent list with event type, description, timestamp, actor, and node_id for graph linking
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

  - [x] 8.3 Wire timeline router into the app
    - Register the timeline router in `app.py` with auth dependency
    - _Requirements: 14.1_

  - [x]* 8.4 Write unit tests for timeline router
    - Test chronological ordering with tiebreaker
    - Test event type classification
    - Test empty project returns empty-state
    - _Requirements: 14.1–14.6_

- [x] 9. Checkpoint — All backend complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Frontend — Login and navigation shell
  - [x] 10.1 Add Alpine.js routing and navigation sidebar to index.html
    - Extend the existing `index.html` with a `currentView` state variable for client-side routing
    - Add navigation sidebar component with links to Projects, Vulnerability Reports, Worker Dashboard, Templates
    - Display authenticated username (truncated to 20 chars) in nav header
    - Add logout button that calls `/api/auth/logout` and redirects to login
    - Implement responsive hamburger menu for viewports < 768px
    - Highlight active navigation link
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

  - [x] 10.2 Implement login and registration views
    - Add login form view with username/password fields, submit to `/api/auth/login`
    - Add registration form with username/password fields, submit to `/api/auth/register`
    - Show validation errors inline (username format, password length)
    - On successful auth, transition to projects view and show navigation
    - On page load, call `/api/auth/me` to check existing session
    - _Requirements: 1.1, 2.1, 3.1, 3.4, 5.3_

- [x] 11. Frontend — Vulnerability reports view
  - [x] 11.1 Implement vulnerability report page
    - Add vulnerability report view with severity summary cards (Critical, High, Medium, Low counts)
    - Display vulnerability list table with title, severity badge, project name
    - Add filter controls: severity dropdown, project dropdown
    - Implement client-side filter application calling `GET /api/vulnerabilities?severity=X&project_id=Y`
    - Add "No vulnerabilities match" empty state message
    - _Requirements: 6.3, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 11.2 Implement vulnerability export controls
    - Add export buttons for JSON and CSV formats
    - Trigger download via `GET /api/vulnerabilities/export?format=json|csv` with current filter params
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 12. Frontend — Worker dashboard view
  - [x] 12.1 Implement worker dashboard page
    - Add worker dashboard view showing worker cards with name, type, status indicator (idle/busy/offline)
    - Display current task description (truncated 120 chars) for busy workers
    - Show metrics: tasks completed, average duration, last heartbeat
    - Implement 5-second polling interval for status updates
    - Highlight status transitions for 3 seconds
    - Show connectivity warning if 3 consecutive polls fail
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 12.2 Implement worker task history panel
    - Add expandable history panel per worker showing 20 most recent tasks
    - Display project name, task type, description, start time, duration, outcome status
    - Color-code outcome: success (green), failed (red), rejected (orange), released (gray)
    - _Requirements: 11.1, 11.2, 11.3_

- [x] 13. Frontend — Templates view
  - [x] 13.1 Implement template browser and project creation from template
    - Add templates view showing built-in and custom templates in a grid/list
    - Visually label custom templates as "User Created"
    - On template select, pre-populate project creation form with title, origin, goal, hints
    - Allow editing all pre-populated fields before submission
    - Validate required fields (title, origin, goal) before submit
    - Submit via existing project creation API
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 13.2, 13.3_

  - [x] 13.2 Implement custom template creation and management
    - Add "Save as Template" form with title, origin, goal, hints fields
    - Validate field lengths (1-200 chars for title/origin/goal, 0-10 hints)
    - Show error when 50-template limit reached
    - Add delete button on user-owned templates with confirmation
    - _Requirements: 13.1, 13.4, 13.5, 13.6, 13.7_

- [x] 14. Frontend — Attack timeline view
  - [x] 14.1 Implement attack timeline panel in project view
    - Add timeline panel to the existing project graph view
    - Render events chronologically with distinct dot colors and labeled badges per event type
    - Display description, formatted timestamp, and actor for each event
    - Implement vertical scrolling for overflow
    - Show empty-state message when no events exist
    - Poll for new events and append without losing scroll position
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x] 14.2 Implement timeline-to-graph navigation
    - On timeline event click, highlight and center the corresponding node in the graph view
    - Visually distinguish selected timeline entry with background/border style
    - Handle events with no associated graph node (select entry only, don't alter graph)
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

- [x] 15. Final checkpoint — Full integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- All changes are additive — no existing core files are modified
- Existing routers get auth protection via `dependencies=[Depends(require_auth)]` in `app.include_router()` calls
- The dispatcher internal API is a separate minimal server; it does not modify dispatcher logic
- Frontend changes extend the existing `index.html` with new Alpine.js views and routing
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3"] },
    { "id": 2, "tasks": ["1.4", "1.5"] },
    { "id": 3, "tasks": ["1.6", "2.1"] },
    { "id": 4, "tasks": ["2.2"] },
    { "id": 5, "tasks": ["2.3", "4.1", "4.2", "5.1", "5.2", "7.1", "7.2", "8.1"] },
    { "id": 6, "tasks": ["4.3", "4.4", "5.3", "7.3", "8.2"] },
    { "id": 7, "tasks": ["4.5", "5.4", "7.4", "8.3"] },
    { "id": 8, "tasks": ["4.6", "5.5", "7.5", "8.4"] },
    { "id": 9, "tasks": ["10.1", "10.2"] },
    { "id": 10, "tasks": ["11.1", "11.2", "12.1", "12.2", "13.1", "13.2", "14.1"] },
    { "id": 11, "tasks": ["14.2"] }
  ]
}
```
