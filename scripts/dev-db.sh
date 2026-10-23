#!/usr/bin/env bash
# Lifecycle for the local dev pgvector Postgres.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/deploy/podman-compose.dev.yml"
MIGRATIONS_DIR="${REPO_ROOT}/db/migrations"

CONTAINER_NAME="hippocampus-postgres-dev"
DB_NAME="${POSTGRES_DB:-hippocampus_dev}"
DB_USER="${POSTGRES_USER:-hippocampus}"
DB_PASS="${POSTGRES_PASSWORD:-hippocampus_dev_password}"
DB_PORT="${HIPPO_DB_PORT:-5442}"

usage() {
    echo "Usage: $0 {up|down|status|logs|psql|migrate|reset}"
    echo "  up       Start pgvector Postgres 16 via Podman"
    echo "  down     Stop the container (data kept)"
    echo "  status   Container status and readiness"
    echo "  logs     Follow container logs"
    echo "  psql     Interactive psql in the container"
    echo "  migrate  Apply pending db/migrations/*.sql in order"
    echo "  reset    Stop and wipe the data volume"
    exit 1
}

run_psql() { podman exec -i "${CONTAINER_NAME}" psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" "$@"; }

case "${1:-}" in
    up|start)
        podman compose -f "${COMPOSE_FILE}" up -d
        for _ in {1..30}; do
            if podman exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
                echo "[+] Ready: Host=127.0.0.1;Port=${DB_PORT};Database=${DB_NAME};Username=${DB_USER}"
                exit 0
            fi
            sleep 1
        done
        echo "[-] Timeout waiting for PostgreSQL." >&2; exit 1 ;;
    down|stop)
        podman compose -f "${COMPOSE_FILE}" down ;;
    status)
        if podman ps --filter "name=${CONTAINER_NAME}" --format "{{.Names}}" | grep -q "${CONTAINER_NAME}"; then
            echo "[+] ${CONTAINER_NAME} is running."
            podman exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" || true
        else
            echo "[-] ${CONTAINER_NAME} is not running."
        fi ;;
    logs)
        podman logs -f "${CONTAINER_NAME}" ;;
    psql)
        podman exec -it "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" ;;
    migrate)
        run_psql -qc "CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"
        for f in "${MIGRATIONS_DIR}"/*.sql; do
            name="$(basename "${f}")"
            if [[ "$(run_psql -tAc "SELECT 1 FROM public.schema_migrations WHERE name='${name}'")" == "1" ]]; then
                echo "[=] ${name} already applied"; continue
            fi
            echo "[*] Applying ${name}"
            { echo "BEGIN;"; cat "${f}"; echo "INSERT INTO public.schema_migrations(name) VALUES ('${name}');"; echo "COMMIT;"; } | run_psql -q
        done ;;
    reset)
        read -r -p "Delete all local Hippocampus database data? [y/N] " confirm
        if [[ "${confirm}" =~ ^[Yy]$ ]]; then
            podman compose -f "${COMPOSE_FILE}" down -v
            echo "[+] Volume wiped. Run '$0 up && $0 migrate'."
        else
            echo "[*] Cancelled."
        fi ;;
    *) usage ;;
esac
