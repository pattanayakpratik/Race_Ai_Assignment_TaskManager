# Task Management System

A small multi-user task manager built with Django and MySQL: users create
projects, add tasks to them, assign tasks to teammates, comment on them, and see
a dashboard of "my tasks".

## Stack

| Component | Version |
| --------- | ------- |
| Python    | 3.14    |
| Django    | 6.1     |
| MySQL     | 8.4 (LTS, via Docker) |
| DB driver | mysqlclient 2.2.8 |

Django 6.1 requires **MySQL 8.4 or newer**, which is why the Compose file pins
the 8.4 LTS image rather than 8.0.

## Setup

### 1. Clone and create a virtualenv

```bash
git clone <repo-url>
cd Race_Ai_Assignment_TaskManager

python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

The defaults in `.env.example` match `docker-compose.yml`, so the file works
as-is for local development. It is the single source of truth for both the
container and Django:

| Variable | Default | Used by |
| -------- | ------- | ------- |
| `DJANGO_SECRET_KEY` | insecure dev key | Django |
| `DJANGO_DEBUG` | `True` | Django |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Django |
| `DB_NAME` | `taskmanager` | Django + MySQL container |
| `DB_USER` | `taskmanager` | Django + MySQL container |
| `DB_PASSWORD` | `taskmanager` | Django + MySQL container |
| `DB_HOST` | `127.0.0.1` | Django |
| `DB_PORT` | `3307` | Django + published container port |
| `MYSQL_ROOT_PASSWORD` | `rootpassword` | MySQL container only |

`.env` is gitignored; `.env.example` is committed as the template.

### 3. Start MySQL

```bash
docker compose up -d --wait
```

`--wait` blocks until the container's healthcheck passes, so the next step will
not race the server's startup. Verify with:

```bash
docker compose ps          # STATUS should read "healthy"
```

The database is published on host port **3307** (not 3306) to avoid colliding
with a MySQL that may already be installed on the host. The `taskmanager`
database, user, and password are created automatically on first boot; data
persists in the `mysql_data` named volume.

Useful commands:

```bash
docker compose logs -f db  # follow server logs
docker compose down        # stop, keep the data
docker compose down -v     # stop and wipe the data
```

**Prefer a MySQL you already run?** Skip Compose and point `DB_HOST` / `DB_PORT`
/ credentials in `.env` at it. It must be MySQL 8.4+, and the database must
already exist (`CREATE DATABASE taskmanager CHARACTER SET utf8mb4;`).

### 4. Migrate, create a superuser, run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The app is then at http://127.0.0.1:8000/ and the admin at
http://127.0.0.1:8000/admin/.

### 5. Run the tests (optional)

```bash
python manage.py test
```

Django builds a throwaway `test_taskmanager` database for this. The Compose
setup grants the app user rights over `test_*` databases via
[`docker/mysql-init/`](docker/mysql-init/), which MySQL runs on first
initialisation. If you are pointing at your own MySQL instead, grant it
yourself:

```sql
GRANT ALL PRIVILEGES ON `test\_%`.* TO 'taskmanager'@'%';
```

## Routes

| URL | Name | Purpose |
| --- | --- | --- |
| `/` | `dashboard` | Logged-in landing page (login required) |
| `/accounts/register/` | `register` | Create an account |
| `/accounts/login/` | `login` | `django.contrib.auth.views.LoginView` |
| `/accounts/logout/` | `logout` | `django.contrib.auth.views.LogoutView` (POST) |
| `/admin/` | — | Django admin |

## The one deliberate index

Beyond the primary keys and the indexes Django creates automatically for foreign
keys, the schema adds exactly one index, on `Task`:

```python
models.Index(fields=['status', 'due_date'], name='task_status_due_date_idx')
```

It exists to serve the **overdue-tasks query** — open tasks whose `due_date` has
passed — which is the app's hottest non-trivial read (it backs the dashboard's
Overdue filter) and the only required query that filters on something other than
a foreign key. `(status, due_date)` is the right order because `status` is
matched against discrete values and `due_date` is a range: MySQL can only use a
range column as a key after every preceding column is pinned to constants.

**Measured on 50,000 tasks** (~2.9% overdue-and-open, the realistic shape — most
tasks end up Done and most open work is not yet due):

| Query form | Index chosen | Rows examined | Extra |
| --- | --- | --- | --- |
| `.exclude(status=DONE)` | none | 49,989 | `Using where` |
| `.filter(status__in=[TODO, IN_PROGRESS])` | `task_status_due_date_idx` | **1,457** | `Using index condition` |
| same, `COUNT(*)` only | `task_status_due_date_idx` | 1,457 | `Using index` (covering) |
| same, with `IGNORE INDEX` | none | 49,989 | `Using where` |

Two things that surfaced while measuring, both worth stating plainly:

1. **Phrasing decides whether the index is used at all.** `!= 'done'` is an
   inequality on the leading column, so MySQL cannot reduce it to discrete
   values and `due_date` becomes unusable as a second key — it falls back to a
   full scan. Written as `status__in=[TODO, IN_PROGRESS]` it examines 1,457 rows
   instead of 49,989, a 34x reduction. The reusable query helper therefore uses
   `status__in`, not `exclude`.
2. **Selectivity decides whether the index is worth it.** On an earlier,
   uniformly-random seed where 33% of rows matched, MySQL correctly ignored the
   index — at that hit rate a scan beats index lookups plus row fetches. The
   index pays off precisely because overdue-and-open is a small minority in real
   data, which is the case worth optimising for.

Runner-up considered and rejected: `(assigned_to, status, due_date)`, which
would also serve the dashboard's "my tasks". It was rejected because
`assigned_to` already carries an automatic FK index that handles the dashboard's
equality filter, and because the overdue query is defined project-wide rather
than per-user, so a leading `assigned_to` column would not apply to it.

## Assumptions

Decisions made where the brief left room; kept here as they accumulate.

- Host port **3307** for MySQL, to avoid clashing with a local MySQL install.
- `sql_mode=STRICT_TRANS_TABLES` is set on connect, so out-of-range or truncated
  values raise instead of being silently coerced.
- **`due_date` is required.** The brief marks only `assigned_to` as nullable, so
  every task carries a due date. This also keeps the overdue query free of
  `NULL` handling.
- **Losing a user does not lose the work.** `Task.assigned_to` is `SET_NULL`, so
  deleting a user leaves their tasks in place, unassigned. `Project.owner`,
  `Comment.author`, and the two containment FKs (`Task.project`,
  `Comment.task`) cascade.
- **Choices are stored as readable strings** (`todo`, `in_progress`, `done`)
  via `TextChoices`, so raw SQL output stays legible. Reordering or inserting a
  choice later cannot silently reinterpret existing rows the way integer codes
  would.
- **`description` on both `Project` and `Task`** is `blank=True`, an addition to
  the brief's field list; a task list is hard to use without somewhere to put
  detail.
- Both models carry `created_at` / `updated_at`; comments carry `created_at`
  only, since they are append-only.
- **Auth is Django's, not hand-rolled.** `LoginView` and `LogoutView` are used
  directly. Django ships no registration view, so `accounts.views.RegisterView`
  is a thin `CreateView` over the stock `UserCreationForm` — password
  validation and hashing stay with `django.contrib.auth`.
- **Only login / logout / register are wired.** Including
  `django.contrib.auth.urls` would also expose the password change and reset
  flows, which are out of scope and would raise `TemplateDoesNotExist` if
  followed, so the three required routes are registered explicitly instead.
- **Registration signs the new user straight in** rather than bouncing them to
  the login form.
- **Logging out is a POST.** `LogoutView` has rejected GET since Django 5.0, so
  the header uses a small form rather than a link.

## Troubleshooting

**`(1130, "Host '...' is not allowed to connect to this MySQL server")` or
`Access denied for user 'root'@'localhost'`** — the `mysql_data` volume was left
half-initialized (e.g. the first `docker compose up` was interrupted while MySQL
was still creating the data directory, so the app user was never created). MySQL
only runs its init scripts on an empty data directory, so restarting the
container does not fix it. Wipe the volume and let it initialize cleanly:

```bash
docker compose down -v
docker compose up -d --wait
```

Confirm the init actually ran:

```bash
docker exec taskmanager-mysql mysql -uroot -prootpassword \
  -e "SELECT user, host FROM mysql.user WHERE user='taskmanager'"
```

**Port 3307 already in use** — change `DB_PORT` in `.env`; both Compose and
Django read it, so nothing else needs editing.
