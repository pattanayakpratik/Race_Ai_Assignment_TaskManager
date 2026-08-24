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

| URL | Name | Who may reach it |
| --- | --- | --- |
| `/` | `dashboard` | Any logged-in user; shows only their own assignments |
| `/accounts/register/` | `register` | Anonymous |
| `/accounts/login/` | `login` | Anonymous |
| `/accounts/logout/` | `logout` | Logged-in (POST only) |
| `/projects/` | `project-list` | Logged-in; lists only your projects |
| `/projects/new/` | `project-create` | Any logged-in user |
| `/projects/<pk>/` | `project-detail` | Project members |
| `/projects/<pk>/edit/` | `project-update` | Project owner |
| `/projects/<pk>/delete/` | `project-delete` | Project owner |
| `/projects/<pk>/tasks/new/` | `task-create` | Project owner |
| `/tasks/<pk>/` | `task-detail` | Project members |
| `/tasks/<pk>/edit/` | `task-update` | Project owner |
| `/tasks/<pk>/delete/` | `task-delete` | Project owner |
| `/tasks/<pk>/comments/new/` | `comment-create` | Project members (POST) |
| `/admin/` | — | Staff |

## Dashboard

`/` is the logged-in landing page: every task where the user is `assigned_to`,
in three columns — **To Do / In Progress / Done**. Each card links to the task
and to its project, shows the due date and priority, and is colour-coded by
priority down its left edge. Empty columns still render, so the board keeps its
shape when a user has nothing in progress.

Two details worth noting:

- **One query, grouped in Python.** The view fetches the user's tasks once and
  buckets the already-loaded rows by status. Three status-filtered querysets
  would be three round trips for the same set of rows. (This is not the
  "counting in Python" the brief warns against — that concerns aggregates,
  which the per-project status counts do in SQL with `annotate`/`Count`.)
- **The columns come from `Task.Status.choices`**, not a hardcoded list, so the
  board tracks the model. Adding a status adds a column without editing the
  view or the template. A test asserts the columns match `Task.Status.values`.

`select_related('project')` is what holds the page at a fixed **3 queries** —
session, user, tasks — no matter how many cards are on the board; without it,
each card's project name would cost a query. `test_dashboard_is_a_fixed_number_of_queries`
pins this with `assertNumQueries`, re-checking after adding 20 tasks across 20
projects.

## Permissions

Two roles, both defined once in [`tasks/models.py`](tasks/models.py) and
enforced once in [`tasks/permissions.py`](tasks/permissions.py):

- **Owner** — the user in `Project.owner`. The only user who may edit or delete
  the project, or create, edit, and delete tasks inside it.
- **Member** — the owner, *or* any user assigned at least one task in the
  project (`Project.objects.visible_to()`). Members may view the project and
  **all** of its tasks, not only the ones assigned to them.

| Action | Owner | Member | Other user | Anonymous |
| --- | :-: | :-: | :-: | :-: |
| View project / its tasks | yes | yes | 403 | → login |
| Create / edit / delete task | yes | 403 | 403 | → login |
| Comment on a task | yes | yes | 403 | → login |
| Edit / delete project | yes | 403 | 403 | → login |

Creating a project is open to any logged-in user, who becomes its owner.

**Enforcement happens in `dispatch()`**, before the view body or the form runs,
so it applies identically to GET and POST. A direct POST from a non-owner is
rejected whatever the templates chose to render; the `{% if can_edit %}` guards
in the templates only hide buttons and are never the control. An authenticated
user who fails a check gets **403** — not a redirect, and not a 404: they
exist, they are simply not allowed.

Two fields are deliberately kept out of their forms so a crafted POST cannot
reach them:

- `Project.owner` is set from `request.user`, so you cannot create a project
  owned by someone else.
- `Task.project` comes from the URL, so you cannot move a task into a project
  you do not own — which would otherwise be a way to write into it.
- `Comment.author` is set from `request.user` and `Comment.task` from the URL,
  so a comment cannot be posted under someone else's name or onto a task you
  cannot see.

### Commenting follows the *view* rule

`CommentCreateView` is guarded by `TaskViewableRequiredMixin`, not the edit
mixin — the brief says any authenticated user who can view a task can comment
on it. So a member can comment on a task assigned to somebody else, or on one
they have no right to edit, while a non-member is refused. There is a test that
pins exactly this: the same user, on the same task, is allowed to comment and
refused an edit.

Membership is **derived, not stored**: it is computed from the assignment rows
each time. Assigning someone a task grants them access to the project, and
clearing that assignment takes it away again — both directions are tested.

Comments are **append-only**, and that holds on every path into the model:

- Only a create route exists. `comment-update` / `comment-delete` are absent by
  design, and a test asserts they do not resolve.
- `CommentForm` exposes `body` alone, so a comment cannot be retargeted or
  reattributed by a crafted POST.
- `CommentAdmin` sets `has_change_permission` and `has_delete_permission` to
  `False`, which also strips the "delete selected" bulk action. Otherwise the
  admin would be the one way around the rule. Both are tested by POSTing to the
  admin endpoints as a superuser and asserting a 403 with the row intact.

The one remaining way a comment disappears is cascade: deleting its task or
project takes its comments with it. That is intended — the alternative is
orphaned rows.

Both have tests. `python manage.py test` runs the full suite: every mutating
endpoint is POSTed as owner, member, outsider, and anonymous, and each test
asserts the database is *unchanged*, not merely that a 403 came back.

## Required queries

Both live as queryset methods on [`tasks/models.py`](tasks/models.py), so there
is one definition each and views compose against it.

### Overdue tasks

```python
Task.objects.overdue()                        # every overdue task
Task.objects.assigned_to_user(user).overdue() # mine, as the dashboard uses it
```

```sql
SELECT ... FROM `tasks_task`
WHERE (`due_date` < '2026-08-25' AND `status` IN ('todo', 'in_progress'))
ORDER BY `due_date` ASC, `id` ASC
```

```
EXPLAIN: type=range  key=task_status_due_date_idx  Using index condition
```

Surfaced as an **Overdue filter on the dashboard** (`/?filter=overdue`), with a
count in the toggle and a red badge on late cards. The badge uses an
`is_overdue` property that re-states the same rule in Python on an
already-loaded row, so it costs no queries; a test asserts the property and the
queryset never disagree.

Written as `status__in=[...]` rather than `.exclude(status=DONE)` — that is
what makes the index usable, and it is measured in the index section below. The
status list comes from `Task.open_statuses()`, derived from the choices, so
adding a status keeps the query correct.

### Per-project status counts

For one project (used on the project detail page):

```python
Task.objects.filter(project=project).values('status').annotate(count=Count('id'))
```

```sql
SELECT `status`, COUNT(`id`) AS `count` FROM `tasks_task`
WHERE `project_id` = 46 GROUP BY 1 ORDER BY 1 ASC
```

Three rows come back for six tasks — the database groups and counts; Python
only fills in a zero for any status with no tasks, and orders by the choices.

For a list of projects (used on the project list page), conditional
aggregation puts every count in the same query:

```python
Project.objects.with_status_counts()   # todo_count, in_progress_count, done_count
```

```sql
SELECT `tasks_project`.*,
       COUNT(CASE WHEN `tasks_task`.`status` = 'todo' THEN `tasks_task`.`id` END) AS `todo_count`,
       COUNT(CASE WHEN `tasks_task`.`status` = 'in_progress' THEN ... END) AS `in_progress_count`,
       COUNT(CASE WHEN `tasks_task`.`status` = 'done' THEN ... END) AS `done_count`
FROM `tasks_project` LEFT OUTER JOIN `tasks_task` ON (...)
GROUP BY `tasks_project`.`id`
```

The whole project list is **3 queries** regardless of how many projects it
holds, pinned by `assertNumQueries`.

#### The trap this hides

`visible_to()` joins `tasks` to work out membership, and the count annotation
aggregates over `tasks` too. Combining them directly gives wrong numbers **in
both directions**. Measured against a project holding 4 To Do / 1 In Progress /
1 Done, viewed by a user assigned 3 of those tasks:

| Expression | todo / in_progress / done | |
| --- | --- | --- |
| `with_status_counts()` alone | 4 / 1 / 1 | correct |
| `visible_to(u).with_status_counts()` | 3 / 0 / 0 | **undercount** — the annotation reuses the membership join, so it only counts rows that satisfied the assignment filter |
| `with_status_counts().visible_to(u)` | 12 / 3 / 3 | **overcount** — the join fans out to one row per assignment, tripling every count |
| `visible_to_with_counts(u)` | 4 / 1 / 1 | correct |

`visible_to_with_counts()` resolves membership to a set of ids first, which
leaves the annotation a single clean join. Both wrong forms are pinned by a test
(`test_naive_chaining_is_what_we_avoided`) so a future refactor back to them
fails loudly rather than silently reporting bad numbers.

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
- **Membership is "owner, or assigned a task in the project".** The brief left
  the definition open. This one needs no extra model or join table: assigning a
  task is already the act that brings someone into a project.
- **Creating a task is owner-only.** The brief specifies owner-only edit and
  delete but is silent on create. Letting a member add a task they could not
  then change or remove would be the odd case, so create follows the same rule.
- **Membership grants project-wide read.** Someone assigned one task can see
  every task in that project, not just their own — otherwise they could not
  see the work their task depends on.
- **Failed permission checks return 403, not 404.** Hiding a resource's
  existence was not asked for, and 403 makes the enforcement legible in tests.
- **"Overdue" is judged against `timezone.localdate()`**, which with
  `TIME_ZONE = 'UTC'` means the UTC date. Due dates are plain dates with no
  per-user timezone, so one consistent clock is the honest reading.

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
