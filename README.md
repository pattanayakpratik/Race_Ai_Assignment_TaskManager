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
