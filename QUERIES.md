# Query write-up

The four MySQL/ORM requirements from the brief: the ORM call, the SQL it
generates, and why it is written that way.

All SQL below is the real output of `str(queryset.query)` and `EXPLAIN`, run
against MySQL 8.4 through the app's own settings. Measurements were taken with
seed data generated in a transaction and rolled back afterwards; the scripts are
described inline so the numbers can be reproduced.

**Contents**

1. [MySQL, not SQLite](#1-mysql-not-sqlite)
2. [Overdue-tasks query](#2-overdue-tasks-query)
3. [Per-project status counts](#3-per-project-status-counts)
4. [N+1 avoidance](#4-n1-avoidance)
5. [The one deliberate index](#5-the-one-deliberate-index)

---

## 1. MySQL, not SQLite

`config/settings.py` uses the `mysqlclient` backend, with every value read from
`.env` so nothing is hardcoded:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', 'taskmanager'),
        'USER': os.environ.get('DB_USER', 'taskmanager'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'taskmanager'),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '3307'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        'TEST': {'CHARSET': 'utf8mb4', 'COLLATION': 'utf8mb4_0900_ai_ci'},
    }
}
```

Verified live against the running container:

```
vendor         : mysql            server version : (8, 4, 11)
database       : taskmanager      connected as   : taskmanager@%
sql_mode       : STRICT_TRANS_TABLES     charset : utf8mb4
```

**Why 8.4 specifically.** Django 6.1 sets a minimum supported MySQL of 8.4
(`django/db/backends/mysql/features.py`), so the Compose file pins the 8.4 LTS
image. An 8.0 image is rejected at connect time.

**Why `STRICT_TRANS_TABLES`.** Without it MySQL silently truncates overlong
strings and coerces out-of-range values. Strict mode turns those into errors, so
bad data fails loudly instead of being written half-formed.

Setup is `docker compose up -d --wait`; full instructions are in the
[README](README.md#3-start-mysql).

---

## 2. Overdue-tasks query

**Where it lives:** `TaskQuerySet.overdue()` in `tasks/models.py` — one
definition, reused by the dashboard filter, the overdue badge count, and the
tests.

### The ORM call

```python
class TaskQuerySet(models.QuerySet):
    def overdue(self, today=None):
        return self.filter(
            due_date__lt=today or timezone.localdate(),
            status__in=Task.open_statuses(),
        )
```

```python
Task.objects.overdue()                          # every overdue task
Task.objects.assigned_to_user(user).overdue()   # mine — what the dashboard runs
```

### The SQL

```sql
SELECT `tasks_task`.`id`, `tasks_task`.`title`, ...
FROM `tasks_task`
WHERE (`tasks_task`.`due_date` < '2026-08-25'
       AND `tasks_task`.`status` IN ('todo', 'in_progress'))
ORDER BY `tasks_task`.`due_date` ASC, `tasks_task`.`id` ASC
```

```
EXPLAIN: type=range  key=task_status_due_date_idx  Using index condition
```

### Why it is written this way

**`status__in=[...]` rather than `.exclude(status=DONE)`.** These are logically
equivalent and perform completely differently. `!= 'done'` is an inequality on
the leading column of the `(status, due_date)` index, so MySQL cannot pin that
column to discrete values and `due_date` becomes unusable as a second key — the
plan collapses to a full scan. Pinning status to a list keeps both columns
usable. Measured on 50,000 rows, this is the difference between examining 1,457
rows and 49,989. Full numbers in [section 5](#5-the-one-deliberate-index).

**The status list is derived, not hardcoded.** `Task.open_statuses()` returns
every value from `Status.choices` except `DONE`, so adding a fourth status keeps
the query correct instead of quietly excluding it.

**`today` is injectable.** Tests pass a fixed date rather than depending on the
clock. The default is `timezone.localdate()`.

### Where it surfaces

The dashboard's **Overdue filter** (`/?filter=overdue`), with a count in the
toggle and a red badge on late cards. The badge reads `Task.is_overdue`, a
property that restates the same rule in Python on an already-loaded row so it
costs no queries. `test_is_overdue_property_matches_the_queryset` asserts the
property and the queryset never disagree.

The count in the toggle is its own aggregate query rather than being derived
from the loaded rows, because the board may already be filtered and the count
must reflect the whole board:

```sql
SELECT COUNT(*) FROM tasks_task
WHERE assigned_to_id = 17 AND due_date < '2026-08-25'
      AND status IN ('todo','in_progress')
```

```
EXPLAIN: key=task_status_due_date_idx  Using where; Using index
```

`Using index` means it is answered entirely from the index — the table is never
touched.

### A correctness note on time

"Overdue" is judged against `timezone.localdate()`, which with `TIME_ZONE='UTC'`
is the UTC date. Due dates are plain `DateField`s with no per-user timezone, so
one consistent clock is the honest reading. This is not a detail to gloss over:
five tests initially failed because they used `datetime.date.today()` (the
system-local date) while the app used `timezone.localdate()`. The two differed
by a day, so "yesterday" was not actually overdue. The app was consistent; the
tests were not.

---

## 3. Per-project status counts

Counting happens in the database in both forms below. Python's only role is to
fill in a zero for statuses that returned no row, and to order by the choices.

### 3a. One project — `GROUP BY`

**Where it lives:** `TaskQuerySet.status_counts()`, consumed by
`Project.status_counts()`. Used on the project detail page.

```python
def status_counts(self):
    return self.values('status').annotate(count=Count('id')).order_by('status')
```

```sql
SELECT `tasks_task`.`status` AS `status`,
       COUNT(`tasks_task`.`id`) AS `count`
FROM `tasks_task`
WHERE `tasks_task`.`project_id` = 46
GROUP BY 1
ORDER BY 1 ASC
```

```python
[{'status': 'done', 'count': 2},
 {'status': 'in_progress', 'count': 2},
 {'status': 'todo', 'count': 2}]
```

`.values('status').annotate(...)` is what makes this a `GROUP BY`: it returns
one row per distinct status, never the task rows. Six tasks come back as three
rows. `test_counting_happens_in_sql_not_python` asserts the SQL contains
`GROUP BY` and that the result length is 3, not 6.

### 3b. A list of projects — conditional aggregation

**Where it lives:** `ProjectQuerySet.with_status_counts()`. Used on the project
list, where one aggregate query per project would be an N+1 of its own.

```python
def with_status_counts(self):
    return self.annotate(**{
        f'{status}_count': Count('tasks', filter=Q(tasks__status=status))
        for status in Task.Status.values
    })
```

```sql
SELECT `tasks_project`.*,
       COUNT(CASE WHEN `tasks_task`.`status` = 'todo'
                  THEN `tasks_task`.`id` ELSE NULL END) AS `todo_count`,
       COUNT(CASE WHEN `tasks_task`.`status` = 'in_progress'
                  THEN `tasks_task`.`id` ELSE NULL END) AS `in_progress_count`,
       COUNT(CASE WHEN `tasks_task`.`status` = 'done'
                  THEN `tasks_task`.`id` ELSE NULL END) AS `done_count`
FROM `tasks_project`
LEFT OUTER JOIN `tasks_task` ON (`tasks_project`.`id` = `tasks_task`.`project_id`)
GROUP BY `tasks_project`.`id`
```

The annotation names are generated from `Status.values`, so a new status adds a
column without editing this method.

### The trap: combining membership filtering with aggregation

This is the part worth reading. `visible_to()` joins `tasks` to work out project
membership, and the count annotation aggregates over `tasks` as well. Chaining
them gives **wrong numbers in both directions**, silently.

(`visible_to()` also joins `members`, a second to-many relation, which is why
its `distinct()` is load-bearing: a user who is both an explicit member and an
assignee would otherwise match several times over and repeat in the project
list. `test_project_appears_once_for_a_user_who_is_both_member_and_assignee`
covers exactly that case.)

Measured against a project holding **4 To Do / 1 In Progress / 1 Done**, viewed
by a user who is assigned 3 of those tasks:

| Expression | todo / in_progress / done | |
| --- | --- | --- |
| `with_status_counts()` alone | 4 / 1 / 1 | correct |
| `visible_to(u).with_status_counts()` | **3 / 0 / 0** | undercount |
| `with_status_counts().visible_to(u)` | **12 / 3 / 3** | overcount |
| `visible_to_with_counts(u)` | 4 / 1 / 1 | correct |

- The **undercount** happens because the annotation reuses the join the
  membership filter already created. That join is constrained to rows where
  `assigned_to = u`, so the counts only see the 3 assigned tasks — and every
  status the user is not assigned reads as zero.
- The **overcount** happens because the join fans out to one row per matching
  assignment. With 3 assignments, every task is counted 3 times: 4 → 12.

Note that both wrong answers look entirely plausible on a small dataset. Nothing
raises, and a status breakdown of `3 / 0 / 0` is not obviously absurd.

The fix is to resolve membership to a set of ids first, so the annotation gets a
clean single join:

```python
def visible_to_with_counts(self, user):
    return self.filter(
        pk__in=self.visible_to(user).values('pk')
    ).with_status_counts()
```

MySQL inlines the subquery, so the project list is still **one** query:

```sql
SELECT `tasks_project`.*, COUNT(CASE WHEN ... END) AS `todo_count`, ...
FROM `tasks_project`
LEFT OUTER JOIN `tasks_task` ON (...)
INNER JOIN `auth_user` ON (`tasks_project`.`owner_id` = `auth_user`.`id`)
WHERE `tasks_project`.`id` IN (
    SELECT DISTINCT `U0`.`id` FROM `tasks_project` `U0`
    LEFT OUTER JOIN `tasks_task` `U2` ON (`U0`.`id` = `U2`.`project_id`)
    WHERE (`U0`.`owner_id` = 31 OR `U2`.`assigned_to_id` = 31)
)
GROUP BY `tasks_project`.`id`, `auth_user`.`id`
```

`test_naive_chaining_is_what_we_avoided` pins **both** wrong forms, so a future
refactor back to them fails loudly rather than quietly reporting bad numbers.

---

## 4. N+1 avoidance

`select_related` for forward FKs (a JOIN in the same query), `prefetch_related`
for reverse/to-many relations (a second query, matched up in Python). A reverse
FK cannot be `select_related` — it is one-to-many, so there is no single row to
join onto.

### What each page does

| Page | Renders per row | Optimisation |
| --- | --- | --- |
| Project list | owner, per-status counts | `select_related('owner')` + annotated counts |
| Project detail | each task's assignee, each member | `prefetch_related(Prefetch('tasks', queryset=Task.objects.select_related('assigned_to')), 'members')` |
| Task detail | each comment's author | `prefetch_related(Prefetch('comments', queryset=Comment.objects.select_related('author')))` |
| Dashboard | each card's project | `select_related('project')` |

The nesting in the `Prefetch` calls is the important part: `prefetch_related`
alone would load the tasks in one query and then issue one query per task for
its assignee. The inner `select_related` folds that into the prefetch query.

### Measured

Query counts per page, then with every collection tripled (10 → 30 projects,
20 → 60 tasks, 20 → 60 comments):

| Page | Queries | After tripling | |
| --- | --- | --- | --- |
| Dashboard | 4 | 4 | constant |
| Dashboard (overdue filter) | 4 | 4 | constant |
| Project list | 3 | 3 | constant |
| Project detail | 6 | 6 | constant |
| Task detail | 4 | 4 | constant |

Two of the four on most pages are the session and user lookups from
`AuthenticationMiddleware`.

`NPlusOneTests` pins this permanently. Each test measures a page, grows the
collection it renders, and measures again — an absolute count says nothing about
how a page scales, so the assertion is that the count does not *change*.

### A regression this actually caught

While refactoring the comment loading into a `Prefetch`, the override landed on
`TaskPageContextMixin` — but `TaskAccessMixin` defines the same
`get_task_queryset()` method, and it came first in the MRO. The override was
dead code, and because the refactor had also removed the old
`select_related('author')` call, rendering comment authors silently became an
N+1.

Reordering the bases fixed it, and the fix is verified by reintroducing the bug:

```
AssertionError: /tasks/82/ went from 19 to 34 queries when the list doubled
                -- that is an N+1.
```

The docstring on `TaskPageContextMixin` records why base order matters there.

---

## 5. The one deliberate index

Beyond the primary keys and the indexes Django creates automatically for foreign
keys, the schema adds exactly one:

```python
class Meta:
    indexes = [
        models.Index(fields=['status', 'due_date'], name='task_status_due_date_idx'),
    ]
```

```sql
SHOW INDEX FROM tasks_task;
PRIMARY                                              id
tasks_task_assigned_to_id_...  (automatic, FK)       assigned_to_id
tasks_task_project_id_...      (automatic, FK)       project_id
task_status_due_date_idx                             status, due_date   <-- the one
```

### Why this index

It serves the **overdue-tasks query**, which is the app's hottest non-trivial
read — it backs the dashboard's Overdue filter and its count badge — and the
only required query that filters on something other than a foreign key. The
other required queries are already served: membership and status counts filter
on `project_id` and `assigned_to_id`, which Django indexes automatically.

### Why this column order

`status` is matched against discrete values and `due_date` is a range. MySQL can
only use a range column as a key once every preceding column is pinned to
constants, so the equality-style column has to come first. Reversed, as
`(due_date, status)`, the range on `due_date` would consume the index and
`status` would be a filter applied afterwards.

### Measured

50,000 tasks with a realistic distribution — ~2.9% overdue-and-open, because
most tasks end up Done and most open work is not yet due:

| Query form | Index chosen | Rows examined | Extra |
| --- | --- | --- | --- |
| `.exclude(status=DONE)` | none | 49,989 | `Using where` |
| `.filter(status__in=[TODO, IN_PROGRESS])` | `task_status_due_date_idx` | **1,457** | `Using index condition` |
| same, `COUNT(*)` only | `task_status_due_date_idx` | 1,457 | `Using index` (covering) |
| same, with `IGNORE INDEX` | none | 49,989 | `Using where` |

A 34x reduction in rows examined, and the `COUNT(*)` form is answered entirely
from the index without touching the table.

### Two findings from measuring it

1. **Phrasing decides whether the index is used at all.** The first row of that
   table is the natural way to write "not done" and it does not use the index.
   This is why `overdue()` uses `status__in`, and why that choice has a comment
   on it in the model rather than being left to look arbitrary.

2. **Selectivity decides whether the index is worth having.** An earlier
   measurement used a uniformly-random seed where 33% of rows matched, and MySQL
   correctly ignored the index — at that hit rate a scan beats index lookups
   plus row fetches. The index pays off precisely because overdue-and-open is a
   small minority in realistic data. It would have been easy to publish the
   first set of numbers and conclude the index was useless; the distribution was
   the problem, not the index.

### Runner-up, and why not

`(assigned_to, status, due_date)` would also serve the dashboard's "my tasks".
Rejected because `assigned_to` already carries an automatic FK index that
handles the dashboard's equality filter, and because `overdue()` is defined
project-wide rather than per-user — a leading `assigned_to` column would not
apply to `Task.objects.overdue()` at all.
