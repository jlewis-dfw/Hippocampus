---
description: Add a database migration the project's way - next numbered file, store and MemStore methods, expected-schema list, docs and a test
argument-hint: <short_snake_case_name> [what it changes]
---

# Role

You are a careful backend developer on Hippocampus. A migration is forward-only and runs on every user's database, so you keep it additive,
keep SQL inside `store.py`, and prove it applies from zero.

# Task

1. **Understand the change.** Take the name from `$ARGUMENTS` (lowercase words joined by `_`) and ask what the migration must store if it is unclear. Read
   `docs/Database/migrations.md` and the newest file in `db/migrations/` for the style. Check whether the work can be done without a schema change.
2. **Create the file:** `.venv/bin/python scripts/new_migration.py <name>`. It numbers the file after the highest existing one. Write the SQL: qualify tables as
   `hippocampus.<name>`, give new columns defaults, add constraints with names, add indexes only for queries you wrote, and never drop or rewrite data.
3. **Wire the code.** Add the `store.py` method(s) (the only code that runs SQL), the same method(s) on `MemStore` in `tests/unit/test_api.py`, the route or
   schema if the API changes (every route needs a tag and a one-line docstring), and the new columns or tables to `EXPECTED` in `scripts/migrate_check.py`.
4. **Test.** Add a unit test (fakes) and an integration test (`tests/integration/`, real database). Run
   `.venv/bin/python -m pytest tests/unit tests/integration -q` (the integration tier applies every migration from zero in a throwaway database).
5. **Document.** Update `docs/Database/` (schema) and any user-facing page the change affects. Run `scripts/docs_check.py` on the pages you touched.
6. **Report** the migration file name, what it adds, the tests run and their result, and remind the user to take `/dbbackup` before running `/upgrade`.

# Context

- The runner (`src/hippocampus/storage/migrate.py`) requires numbers `0001..N` without gaps and applies each file in a transaction, recorded in `public.schema_migrations`.
- There are no down-migrations. To undo, restore a backup or write a new forward migration. Details: `docs/Database/migrations.md`.
- Layering: `service` and `cli` call `ingest` and `storage`, which call `core`; imports point downwards only.

# Constraints

- Never edit or renumber an existing migration that has been committed. Never put destructive SQL (`DROP`, `TRUNCATE`, rewriting data) in a migration without the user's explicit say-so and a back-out plan.
- Never run migrations against the user's real database; tests use their own throwaway container. Do not touch `hippocampus.env` or the live stack.
- Match the surrounding code's naming and comment density; ruff line length 120.
- Do not commit or change git state.
