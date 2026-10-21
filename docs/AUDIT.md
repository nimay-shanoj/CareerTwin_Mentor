# Code Audit — CareerTwin (`frontend/`)

Audit performed **before** any change, against the code as found on disk.
The master prompt's "verified findings" were treated as hypotheses and re-checked
against the source. Where the prompt and the code disagree, the code wins — the
disagreements are listed in [§3](#3-where-the-prompt-and-the-code-disagree).

Scope: every file under `frontend/` (backend + templates + static assets).

---

## 1. Repository shape

```
Career-Twin-main/
├── README.md
└── frontend/                      ← the whole application (backend + frontend)
    ├── app.py                     app factory + page routes
    ├── app.txt                    0 bytes, empty, unused
    ├── auth.py                    login / register / logout blueprint
    ├── config.py                  configuration
    ├── extensions.py              db + login_manager only
    ├── models.py                  User, Profile, QuizResult, UserProgress, Resume
    ├── overview_routes.py         /api/overview
    ├── profile_routes.py          /profile
    ├── progress_routes.py         /api/progress*
    ├── quiz_routes.py             /api/quiz/*
    ├── resume_routes.py           /api/resume/*
    ├── career_twin_all.zip        stale snapshot of an older build
    ├── requirements.txt
    ├── runtime.txt                python-3.11
    ├── vercel.json
    ├── uploads/jake-ryan.pdf      a real-looking resume, committed
    ├── static/                    10 CSS files + 3 JS files, no vendor dir
    └── templates/                 11 templates, no base template
```

There is **no `network/`, `docs/`, `tests/`, `scripts/`, `tools/`** directory yet.

---

## 2. Findings

### 2.1 Security / configuration

| # | Finding | Evidence |
|---|---|---|
| S1 | **Hard-coded MySQL credentials in source.** `mysql+pymysql://Aditya:Bigcola69@localhost/careertwin` | `config.py:13` |
| S2 | **Hard-coded fallback `SECRET_KEY`** (`career-twin-secret-key-12345`) used whenever `SECRET_KEY` is unset | `config.py:4` |
| S3 | **No CSRF protection anywhere** — no `Flask-WTF`, no `CSRFProtect`, no hidden inputs, no `X-CSRFToken` | whole app |
| S4 | **No session-cookie hardening** — `HttpOnly`/`SameSite`/`Secure` are all Flask defaults (none set) | `config.py` |
| S5 | **No `next`-redirect validation.** No `next` support exists yet, so there is no open-redirect surface *today* — but any future `?next=` must be validated | `auth.py` |
| S6 | **No password policy.** `register` accepts any password including empty-ish strings | `auth.py:18-25` |
| S7 | **Committed PII-looking artefact**: `uploads/jake-ryan.pdf` (128 KB, a real-looking resume) | `frontend/uploads/` |
| S8 | **Committed archive with IDE cruft**: `career_twin_all.zip` contains a `.idea/` folder and an outdated copy of the templates | `frontend/career_twin_all.zip` |
| S9 | **No `.gitignore`** — no protection for `uploads/`, `.env`, `*.db`, `__pycache__/` | repo root |
| S10 | **No `.env` support** — `python-dotenv` is not a dependency | `requirements.txt` |

### 2.2 Front-end structure

| # | Finding | Evidence |
|---|---|---|
| F1 | **No base template.** All 11 templates repeat a full `<!DOCTYPE html>` / `<head>` / navbar block. | all templates |
| F2 | **`:root` variable blocks duplicated in exactly 10 CSS files** | `dashboard/overview/profile/progress/projects/quiz/resume/roadmap/skillgap/style.css` |
| F3 | **Web fonts never loaded.** `DM Sans` and `Poppins` are referenced in CSS but no `<link>` or `@font-face` exists → everyone sees the browser fallback. | `style.css`, all page CSS |
| F4 | **Landing nav anchors broken**: `href="Features"`, `href="Workflow"`, `href="Login"` (missing `#`, resolved as relative paths) | `landing.html:16-18` |
| F5 | **Landing nav class mismatch**: markup uses `class="navlinks"`, CSS targets `.nav-links` | `landing.html:15` |
| F6 | **Landing CTAs both point to `login.html`** — "Get Started" should go to `/register` | `landing.html:21-22` |
| F7 | **Landing file ends with duplicated `</body></html>` and a `<script>` *after* the closing tags** | `landing.html:138-142` |
| F8 | **Landing copy says "Seven modules"** but the features grid renders 7 cards while the product will have 8 (mentorship) | `landing.html:43` |
| F9 | **Inline `style="..."` for error text and logout buttons** on 6 templates | `login`, `register`, `profile`, `dashboard`, `resume`, `overview` |
| F10 | **`alert()` used for validation** in `resume.js`, `skillgap.html`, `projects.html`, `roadmap.js`, `progress.html` | 5 files |
| F11 | **Emoji used as icons** across landing, dashboard, overview, skillgap, projects, quiz, progress | 7 templates |
| F12 | **Career data duplicated 5×** — the four career names plus their skills/projects/roadmaps appear in `roadmap.js`, `skillgap.html`, `projects.html`, `progress_routes.py` and `profile.html` (and a 6th copy of the progress tasks inline in `progress.html`) | 6 files |
| F13 | **`/dashboard` and `/overview` overlap** — both are tile/stats grids listing the same modules | `dashboard.html`, `overview.html` |
| F14 | **Most templates lack `<meta charset>`, `<meta viewport>` and `lang="en"`** — only `landing.html` has a viewport tag | 10 of 11 templates |
| F15 | **`skillgap`, `roadmap`, `projects`, `quiz`, `progress` have no Logout control** | 5 templates |
| F16 | **No `static/vendor/`** — the Socket.IO client is not vendored, so an offline LAN demo cannot work | `static/` |

### 2.3 Correctness / robustness

| # | Finding | Evidence |
|---|---|---|
| C1 | **`/api/quiz/submit` can 500 on bad input.** `int(score)` / `int(total_questions)` / `int(percentage)` run *outside* the `try` block — a non-numeric value raises `ValueError` and returns an unhandled 500 instead of a 400. | `quiz_routes.py:32-34` vs `try:` at 39 |
| C2 | **`create_app()` swallows database errors.** `db.create_all()` failure only prints to stdout; the app boots with no tables and every DB page then 500s. | `app.py:100-105` |
| C3 | **`app = create_app()` runs at import time**, so importing the module has DB side effects and creates tables — hostile to tests and to WSGI servers that import twice. | `app.py:109` |
| C4 | **`app.py` has no `run.py`**; dev server is started by `app.run(debug=True)` inside the module. | `app.py:111-112` |
| C5 | **`extensions.py` holds only `db` and `login_manager`** — no CSRF object, no Socket.IO object. | `extensions.py` |
| C6 | **`profile_routes.py` imports `flash` and never uses it.** | `profile_routes.py:1` |
| C7 | **Resume `filepath` is stored as an absolute local path** — meaningless on Vercel's ephemeral `/tmp` and unreadable after a redeploy. | `resume_routes.py:116` |
| C8 | **Uploaded resumes are never cleaned up** — every upload leaves a file in `uploads/` forever. | `resume_routes.py:96` |
| C9 | **`overview_routes.py` imports `PROGRESS_TASKS` from `progress_routes`** — tight coupling between two unrelated features. | `overview_routes.py:5` |
| C10 | **`resume.js` escapes before `innerHTML`** but `quiz.html` injects `item.compatible_career` from the DB via `innerHTML` without escaping. Currently only static role names can reach it, but it is a stored-XSS shape. | `quiz.html:343-353` |
| C11 | **`progress.html` and `skillgap.html` render server data via `innerHTML`** (currently static strings). | `progress.html:129`, `skillgap.html:77-78` |
| C12 | **Flat module layout with absolute imports** (`from config import Config`) — the app only imports correctly when the CWD is `frontend/`. Tests must fix `sys.path` explicitly. | all backend modules |
| C13 | **`app.txt` is a 0-byte stray file.** | `frontend/app.txt` |
| C14 | **No `README` accuracy** — it still says "Backend (In Progress)" and lists no chat/mentorship features. | `README.md:27` |

### 2.4 What is genuinely fine

- `vercel.json` + `runtime.txt` are consistent: `vercel.json` sits in `frontend/`, so Vercel's project root is `frontend/` and `src: app.py` resolves. **Leave untouched.**
- The Vercel `/tmp` SQLite + `/tmp/uploads` branch in `config.py` is correct and must survive the refactor.
- `resume_routes.py` skill extraction uses `(?<!\w)…(?!\w)` instead of `\b` so `c++`, `c#`, `.net` match — deliberate and correct. **Keep it.**
- The quiz's weighted scoring model (`maxScores`, `userScores`, per-question `weights`) is coherent and is the contract `/api/quiz/submit` stores. **Preserve it exactly.**
- `PROGRESS_TASKS` ids 1–17 are stable and referenced by stored `user_progress` rows. **Never renumber them.**
- `MAX_CONTENT_LENGTH = 16 MB` plus the 413 handler in `app.py` is correct.

---

## 3. Where the prompt and the code disagree

| Prompt claim | Reality | Resolution |
|---|---|---|
| "working in the repo `Career-Twin-main/`" | **There is no git repository.** No `.git` anywhere, and `git` is not installed on this machine. | The project was initialised as a fresh git repo on the target branch (`feat/revamp-mentor-chat`) as part of Phase 1. |
| "Create branch `feat/revamp-mentor-chat`" | Impossible without git. | Installed git, `git init`, created the branch. |
| "Do not modify existing tables … so `db.create_all()` keeps working" | True, and no migration tooling exists. | New tables only. Confirmed by `db.create_all()` running clean before any change. |
| "The whole Flask app lives in `frontend/`" | True. | All new Python modules go under `frontend/` to preserve the absolute-import layout and the Vercel build root. |
| "MySQL (local)" | **MySQL is not installed on this machine** and the only credentials that ever existed are the ones hard-coded in `config.py`. | `DATABASE_URL` now wins; MySQL is still the default engine but its credentials come from `MYSQL_*` env vars; when neither is present the app falls back to a local SQLite file so a fresh clone runs. Engine for existing deployments is unchanged. **Flagged to the user in the Phase 1 report.** |
| "`/api/overview`" returns stats | True. | Kept byte-compatible. |
| Landing page has "Seven modules" | True — but the grid renders 7 cards for 7 features; the sentence refers to features, not the sidebar module list. | Copy becomes "eight" once Mentorship is added. |

---

## 4. Target structure

```
frontend/
├── config.py                 env-driven, no secrets
├── extensions.py             db, login_manager, csrf, socketio
├── app.py                    factory (route layout unchanged)
├── run.py                    dev/prod entrypoint
├── careers.py                SINGLE SOURCE OF TRUTH for the 4 careers
├── chat_service.py           persist / authorize / rate-limit / allocate seq
├── hub.py                    transport-agnostic connection registry + Sink iface
├── chat_routes.py            /api/conversations*, /api/chat/token, /api/network/stats
├── mentorship_models.py      MentorProfile, MentorshipRequest, Conversation, Message
├── mentorship_routes.py      /api/mentors*, /api/mentorship*, /api/notifications/summary
├── socket_events.py          Flask-SocketIO namespace /chat
├── network/
│   ├── protocol.py           frame codec (magic/version/length/payload)
│   ├── tcp_server.py         raw asyncio TCP chat server
│   └── cli_client.py         terminal client
├── scripts/seed_mentors.py
├── tools/loadtest.py
├── static/
│   ├── css/tokens.css base.css layout.css components.css pages/*.css
│   ├── js/app.js chat.js network.js
│   ├── icons/sprite.svg
│   └── vendor/socket.io.min.js
├── templates/
│   ├── base_public.html base_app.html
│   ├── partials/_sidebar.html _topbar.html _flash.html
│   └── macros/ui.html
└── tests/
```

---

## 5. Rules for the refactor

1. **URLs stay.** `/dashboard`, `/dashboard.html`, `/login.html`, … all keep working.
2. **Endpoint names stay.** `dashboard_page`, `resume_page`, `auth.login`, `auth.register`, `auth.logout`, `overview_page`, … are referenced by `url_for` and must not be renamed.
3. **Form field names stay.** `name`, `email`, `password`, `confirm_password`, `phone`, `college_name`, `degree`, `skills`, `career_goal`.
4. **JSON contracts stay.** `/api/quiz/submit`, `/api/quiz/history`, `/api/progress`, `/api/progress/toggle`, `/api/progress/reset`, `/api/overview`, `/api/resume/analyze`, `/api/resume/latest`.
5. **Existing tables are never altered.** Mentorship adds new tables only.
6. **`PROGRESS_TASKS` ids 1–17 never change.**
7. **Quiz scoring logic and its `/api/quiz/submit` payload are preserved exactly.**
