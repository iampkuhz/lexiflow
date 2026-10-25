#!/bin/bash
set -Eeuo pipefail

# PID 1 for the single-container release. The fixed paths are part of the image
# contract; caller supplied paths, database URLs and published identities are not.
readonly ROOT=/var/lib/lexiflow
readonly PGDATA=/var/lib/lexiflow/postgres
readonly RUN=/run/lexiflow
readonly IDENTITY=/app/release-identity.env
readonly IDENTITY_JSON=/app/release-identity.json
readonly DATASET=/app/dataset.zip
readonly JAR=/app/lexiflow-api.jar
readonly PGPORT=5432
readonly STOP_SECONDS=35

log() { printf 'lexiflow-runtime: %s\n' "$1" >&2; }
fail() { log "$1"; exit 1; }
hex64() { [[ "$1" =~ ^[0-9a-f]{64}$ ]]; }
bounded_id() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]]; }

read_identity() {
  [[ -f "$IDENTITY" && ! -L "$IDENTITY" ]] || fail identity-unavailable
  local count=0 line key value
  local schema_version='' software_version='' api_contract='' sql_version=''
  local release_id='' preparation_id='' rule_id='' dataset_sha256=''
  local seen_schema=0 seen_software=0 seen_contract=0 seen_sql=0
  local seen_release=0 seen_preparation=0 seen_rule=0 seen_digest=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((count+=1))
    [[ "$count" -le 8 && "$line" == *=* ]] || fail identity-invalid
    key=${line%%=*}; value=${line#*=}
    [[ "$key" =~ ^(schemaVersion|softwareVersion|apiContract|sqlVersion|releaseId|preparationId|ruleId|datasetSha256)$ ]] || fail identity-invalid
    [[ -n "$value" && "$value" != *[!A-Za-z0-9._+-]* ]] || fail identity-invalid
    case "$key" in
      schemaVersion) ((seen_schema == 0)) || fail identity-invalid; seen_schema=1; schema_version=$value ;;
      softwareVersion) ((seen_software == 0)) || fail identity-invalid; seen_software=1; software_version=$value ;;
      apiContract) ((seen_contract == 0)) || fail identity-invalid; seen_contract=1; api_contract=$value ;;
      sqlVersion) ((seen_sql == 0)) || fail identity-invalid; seen_sql=1; sql_version=$value ;;
      releaseId) ((seen_release == 0)) || fail identity-invalid; seen_release=1; release_id=$value ;;
      preparationId) ((seen_preparation == 0)) || fail identity-invalid; seen_preparation=1; preparation_id=$value ;;
      ruleId) ((seen_rule == 0)) || fail identity-invalid; seen_rule=1; rule_id=$value ;;
      datasetSha256) ((seen_digest == 0)) || fail identity-invalid; seen_digest=1; dataset_sha256=$value ;;
    esac
  done < "$IDENTITY"
  [[ "$count" -eq 8 && "$seen_schema$seen_software$seen_contract$seen_sql$seen_release$seen_preparation$seen_rule$seen_digest" == 11111111 ]] || fail identity-invalid
  [[ "$schema_version" == 1 && "$api_contract" == caption-hints.v2 ]] || fail identity-invalid
  bounded_id "$software_version" && bounded_id "$sql_version" \
    && bounded_id "$release_id" && bounded_id "$preparation_id" \
    && bounded_id "$rule_id" && hex64 "$dataset_sha256" || fail identity-invalid
  SOFTWARE_VERSION=$software_version
  API_CONTRACT=$api_contract
  SQL_VERSION=$sql_version
  RELEASE_ID=$release_id
  PREPARATION_ID=$preparation_id
  RULE_ID=$rule_id
  DATASET_SHA256=$dataset_sha256
}

identity_line() {
  printf 'schemaVersion=1\nsoftwareVersion=%s\napiContract=%s\nsqlVersion=%s\nreleaseId=%s\npreparationId=%s\nruleId=%s\ndatasetSha256=%s\n' \
    "$SOFTWARE_VERSION" "$API_CONTRACT" "$SQL_VERSION" "$RELEASE_ID" \
    "$PREPARATION_ID" "$RULE_ID" "$DATASET_SHA256"
}

# Persistent data compatibility excludes the per-boot application version.
# SQL/API/dataset drift still refuses the volume without modifying its data.
volume_identity_line() {
  printf 'schemaVersion=2\napiContract=%s\nsqlVersion=%s\nreleaseId=%s\npreparationId=%s\nruleId=%s\ndatasetSha256=%s\n' \
    "$API_CONTRACT" "$SQL_VERSION" "$RELEASE_ID" \
    "$PREPARATION_ID" "$RULE_ID" "$DATASET_SHA256"
}

safe_regular() { [[ -f "$1" && ! -L "$1" ]]; }
assert_file_value() {
  local file=$1 wanted=$2 actual
  safe_regular "$file" || fail volume-marker-invalid
  actual=$(cat -- "$file") || fail volume-marker-invalid
  [[ "$actual" == "$wanted" ]] || fail volume-identity-mismatch
}

atomic_write() {
  local target=$1 content=$2 temporary="${1}.pending" old
  if [[ -e "$target" || -L "$target" ]]; then
    safe_regular "$target" || fail marker-path-invalid
    old=$(cat -- "$target") || fail marker-read-failed
    [[ "$old" == "$content" ]] || fail marker-conflict
    if [[ -e "$temporary" || -L "$temporary" ]]; then
      safe_regular "$temporary" || fail marker-pending-invalid
      old=$(cat -- "$temporary") || fail marker-pending-invalid
      [[ "$old" == "$content" ]] || fail marker-pending-conflict
      rm -- "$temporary" || fail marker-pending-cleanup-failed
    fi
    return
  fi
  if [[ -e "$temporary" || -L "$temporary" ]]; then
    safe_regular "$temporary" || fail marker-pending-invalid
    old=$(cat -- "$temporary") || fail marker-pending-invalid
    [[ "$old" == "$content" ]] || fail marker-pending-conflict
  else
    (umask 077; set -o noclobber; printf '%s\n' "$content" > "$temporary") || fail marker-write-failed
  fi
  mv -- "$temporary" "$target" || fail marker-write-failed
}

make_secret() {
  local target=$1 bytes
  bytes=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n') || fail secret-generation-failed
  hex64 "$bytes" || fail secret-generation-failed
  local temporary="${target}.pending" old
  if [[ -e "$target" || -L "$target" ]]; then
    safe_regular "$target" || fail volume-secret-invalid
    old=$(cat -- "$target") || fail volume-secret-invalid
    hex64 "$old" || fail volume-secret-invalid
    [[ ! -e "$temporary" && ! -L "$temporary" ]] || fail volume-secret-conflict
    return
  fi
  if [[ -e "$temporary" || -L "$temporary" ]]; then
    safe_regular "$temporary" || fail volume-secret-invalid
    old=$(cat -- "$temporary") || fail volume-secret-invalid
    hex64 "$old" || fail volume-secret-invalid
    mv -- "$temporary" "$target" || fail secret-write-failed
  else
    (umask 077; set -o noclobber; printf '%s\n' "$bytes" > "$temporary") || fail secret-write-failed
    mv -- "$temporary" "$target" || fail secret-write-failed
  fi
}

validate_volume() {
  [[ -d "$ROOT" && ! -L "$ROOT" ]] || fail volume-path-invalid
  local entries=() entry name
  shopt -s nullglob dotglob
  entries=("$ROOT"/*)
  shopt -u nullglob dotglob
  if ((${#entries[@]} == 0)); then
    atomic_write "$ROOT/.lexiflow-owner" 'lexiflow-runtime'
    atomic_write "$ROOT/.lexiflow-format" '2'
    atomic_write "$ROOT/.lexiflow-initializing" "$(volume_identity_line)"
    make_secret "$ROOT/.postgres-password"
    make_secret "$ROOT/.app-password"
    mkdir -m 0700 "$PGDATA" || fail volume-create-failed
    return
  fi

  # Inspect the complete existing volume before any writes or recovery actions.
  # A pending owner file alone is not ownership proof and is never claimed.
  if ((${#entries[@]} > 0)); then
    for entry in "${entries[@]}"; do
      name=${entry##*/}
      case "$name" in
        .lexiflow-owner|.lexiflow-owner.pending|.lexiflow-format|.lexiflow-format.pending|.lexiflow-initializing|.lexiflow-initializing.pending|.lexiflow-initdb-complete|.lexiflow-initdb-complete.pending|.lexiflow-ready|.lexiflow-ready.pending|.postgres-password|.postgres-password.pending|.app-password|.app-password.pending|postgres) ;;
        *) fail volume-unknown-content;;
      esac
      [[ ! -L "$entry" ]] || fail volume-path-invalid
    done
  fi
  [[ -e "$ROOT/.lexiflow-owner" ]] || fail volume-owner-missing
  # Validate every recognized item, including pending marker files, before
  # repairing or completing any marker. Thus an unknown/corrupt volume is
  # rejected without mutating its contents.
  local marker target expected value
  for marker in owner format initializing initdb-complete ready; do
    case "$marker" in
      owner) target="$ROOT/.lexiflow-owner"; expected='lexiflow-runtime' ;;
      format) target="$ROOT/.lexiflow-format"; expected='2' ;;
      initializing) target="$ROOT/.lexiflow-initializing"; expected="$(volume_identity_line)" ;;
      initdb-complete) target="$ROOT/.lexiflow-initdb-complete"; expected="$(volume_identity_line)" ;;
      ready) target="$ROOT/.lexiflow-ready"; expected="$(volume_identity_line)" ;;
    esac
    if [[ -e "$target" ]]; then assert_file_value "$target" "$expected"; fi
    if [[ -e "$target.pending" ]]; then
      safe_regular "$target.pending" || fail marker-pending-invalid
      value=$(cat -- "$target.pending") || fail marker-pending-invalid
      [[ "$value" == "$expected" ]] || fail marker-pending-conflict
    fi
  done
  for target in "$ROOT/.postgres-password" "$ROOT/.app-password"; do
    [[ ! -e "$target" || ! -e "$target.pending" ]] || fail volume-secret-conflict
    if [[ -e "$target" ]]; then
      safe_regular "$target" || fail volume-secret-invalid
      value=$(cat -- "$target") || fail volume-secret-invalid
      hex64 "$value" || fail volume-secret-invalid
    fi
    if [[ -e "$target.pending" ]]; then
      safe_regular "$target.pending" || fail volume-secret-invalid
      value=$(cat -- "$target.pending") || fail volume-secret-invalid
      hex64 "$value" || fail volume-secret-invalid
    fi
  done
  if [[ -e "$PGDATA" ]]; then [[ -d "$PGDATA" && ! -L "$PGDATA" ]] || fail volume-path-invalid; fi
  if [[ -e "$PGDATA/PG_VERSION" ]]; then
    [[ -s "$PGDATA/PG_VERSION" && ! -L "$PGDATA/PG_VERSION" ]] || fail postgres-data-invalid
  fi
  [[ -f "$ROOT/.lexiflow-format" && ! -L "$ROOT/.lexiflow-format" ]] || fail volume-format-missing
  [[ "$(cat -- "$ROOT/.lexiflow-format")" == 2 ]] || fail volume-format-unsupported
  local complete_present=0
  [[ ! -e "$ROOT/.lexiflow-initdb-complete" && ! -e "$ROOT/.lexiflow-initdb-complete.pending" ]] || complete_present=1
  if [[ -e "$ROOT/.lexiflow-ready" || -e "$ROOT/.lexiflow-ready.pending" ]]; then
    ((complete_present == 1)) || fail ready-without-initdb-proof
    [[ -d "$PGDATA" && ! -L "$PGDATA" && -s "$PGDATA/PG_VERSION" && ! -L "$PGDATA/PG_VERSION" ]] || fail ready-without-postgres-data
  fi
  if ((complete_present)); then
    [[ -d "$PGDATA" && ! -L "$PGDATA" && -s "$PGDATA/PG_VERSION" && ! -L "$PGDATA/PG_VERSION" ]] || fail initdb-proof-without-postgres-data
    [[ -e "$ROOT/.lexiflow-initializing" || -e "$ROOT/.lexiflow-ready" || -e "$ROOT/.lexiflow-ready.pending" ]] || fail initdb-proof-state-invalid
  fi

  atomic_write "$ROOT/.lexiflow-owner" 'lexiflow-runtime'
  [[ -e "$ROOT/.lexiflow-format" ]] || fail volume-format-missing
  atomic_write "$ROOT/.lexiflow-format" '2'
  if [[ -e "$ROOT/.lexiflow-initializing" ]]; then
    atomic_write "$ROOT/.lexiflow-initializing" "$(volume_identity_line)"
  elif [[ ! -e "$ROOT/.lexiflow-ready" && ! -e "$ROOT/.lexiflow-ready.pending" ]]; then
    fail volume-initializing-missing
  fi
  if [[ ! -e "$PGDATA" ]]; then
    [[ -e "$ROOT/.lexiflow-initializing" && ! -e "$ROOT/.lexiflow-ready" && ! -e "$ROOT/.lexiflow-ready.pending" ]] || fail volume-path-invalid
    mkdir -m 0700 "$PGDATA" || fail volume-create-failed
  fi
  [[ -d "$PGDATA" && ! -L "$PGDATA" ]] || fail volume-path-invalid
  if [[ ! -e "$ROOT/.postgres-password" ]]; then
    [[ -e "$ROOT/.lexiflow-initializing" && ! -e "$ROOT/.lexiflow-ready" && ! -e "$PGDATA/PG_VERSION" ]] || fail volume-secret-invalid
    make_secret "$ROOT/.postgres-password"
  fi
  if [[ ! -e "$ROOT/.app-password" ]]; then
    [[ -e "$ROOT/.lexiflow-initializing" && ! -e "$ROOT/.lexiflow-ready" && ! -e "$PGDATA/PG_VERSION" ]] || fail volume-secret-invalid
    make_secret "$ROOT/.app-password"
  fi
  safe_regular "$ROOT/.postgres-password" && safe_regular "$ROOT/.app-password" || fail volume-secret-invalid
  local pgpass apppass
  pgpass=$(cat "$ROOT/.postgres-password") || fail volume-secret-invalid
  apppass=$(cat "$ROOT/.app-password") || fail volume-secret-invalid
  hex64 "$pgpass" && hex64 "$apppass" || fail volume-secret-invalid
  if safe_regular "$ROOT/.lexiflow-ready"; then
    atomic_write "$ROOT/.lexiflow-ready" "$(volume_identity_line)"
    if [[ -e "$ROOT/.lexiflow-initializing" ]]; then
      atomic_write "$ROOT/.lexiflow-initializing" "$(volume_identity_line)"
      rm -f -- "$ROOT/.lexiflow-initializing"
    fi
  else
    [[ -e "$ROOT/.lexiflow-initializing" ]] || fail volume-unrecognized-state
    atomic_write "$ROOT/.lexiflow-initializing" "$(volume_identity_line)"
  fi
}

is_empty_or_partial_pgdata() {
  [[ ! -e "$PGDATA/PG_VERSION" && ! -L "$PGDATA/PG_VERSION" ]]
}

prepare_pgdata() {
  [[ -d "$PGDATA" && ! -L "$PGDATA" ]] || fail volume-path-invalid
  local init_marker="$ROOT/.lexiflow-initdb-complete"
  if [[ -e "$init_marker" || -e "$init_marker.pending" ]]; then
    [[ ! -e "$init_marker" || ! -L "$init_marker" ]] || fail marker-path-invalid
    [[ ! -e "$init_marker.pending" || ! -L "$init_marker.pending" ]] || fail marker-path-invalid
    [[ ! -e "$init_marker" ]] || assert_file_value "$init_marker" "$(volume_identity_line)"
    [[ ! -e "$init_marker.pending" ]] || assert_file_value "$init_marker.pending" "$(volume_identity_line)"
    atomic_write "$init_marker" "$(volume_identity_line)"
  else
    [[ -f "$ROOT/.lexiflow-format" && ! -L "$ROOT/.lexiflow-format" ]] || fail volume-format-unsupported
    [[ "$(cat -- "$ROOT/.lexiflow-format")" == 2 ]] || fail volume-format-unsupported
    [[ -f "$ROOT/.lexiflow-initializing" && ! -L "$ROOT/.lexiflow-initializing" ]] || fail volume-unrecognized-state
    assert_file_value "$ROOT/.lexiflow-initializing" "$(volume_identity_line)"
    [[ ! -e "$ROOT/.lexiflow-ready" && ! -e "$ROOT/.lexiflow-ready.pending" ]] || fail volume-unrecognized-state
    # No completion marker means initdb never completed; PG_VERSION may already
    # exist, so only this exact format-2 initializing state authorizes cleanup.
    find "$PGDATA" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + || fail partial-initdb-cleanup-failed
    timeout --kill-after=5s 120s initdb -D "$PGDATA" --username=postgres --pwfile="$ROOT/.postgres-password" \
      --auth-local=trust --auth-host=scram-sha-256 >/dev/null &
    INIT_PID=$!
    wait_active "$INIT_PID" || fail postgres-init-failed
    atomic_write "$init_marker" "$(volume_identity_line)"
  fi
  [[ -s "$PGDATA/PG_VERSION" && ! -L "$PGDATA/PG_VERSION" ]] || fail postgres-data-invalid
}

write_proof() {
  local version=$1
  [[ "$version" =~ ^[1-9][0-9]{0,18}$ ]] || fail dataset-version-invalid
  printf '{"schemaVersion":1,"cliResult":"PASS","datasetVersion":%s,"releaseId":"%s","preparationId":"%s","ruleId":"%s","sqlVersion":"%s","sha256":"%s"}\n' \
    "$version" "$RELEASE_ID" "$PREPARATION_ID" "$RULE_ID" "$SQL_VERSION" "$DATASET_SHA256" \
    > "$RUN/verified.pending" || fail proof-write-failed
  chmod 0600 "$RUN/verified.pending" || fail proof-write-failed
  mv -f "$RUN/verified.pending" "$RUN/verified.json" || fail proof-write-failed
}

HTTP_BODY=''
DATASET_VERSION_RE='"datasetVersion":([1-9][0-9]{0,18})'
http_status_body() {
  local endpoint=${1:-/api/v1/runtime-status}
  local host=${2:-127.0.0.1} port=${3:-8080}
  local wire line status='' body='' headers_done=0 length='' transfer='' name value
  wire=$(timeout --kill-after=1s 3s bash --noprofile --norc -c '
    set -eu
    exec 3<>/dev/tcp/"$2"/"$3"
    printf "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" "$1" "$2" >&3
    exec 4<&3
    exec 3>&-
    response=""
    read_status=0
    IFS= read -r -d "" -n 8193 response <&4 || read_status=$?
    printf "%s" "$response"
    if [ "$read_status" -eq 0 ]; then printf "\\002"; else printf "\\001"; fi
  ' runtime-http "$endpoint" "$host" "$port" 2>/dev/null) || return 1
  [[ "$wire" == *$'\001' ]] || return 1
  wire=${wire%$'\001'}
  ((${#wire} <= 8192)) || return 1
  [[ "$wire" == *$'\r\n\r\n'* ]] || return 1
  local headers=${wire%%$'\r\n\r\n'*} raw_body=${wire#*$'\r\n\r\n'}
  status=${headers%%$'\r\n'*}
  # Spring/Tomcat commonly omits the deprecated reason phrase: `HTTP/1.1 200 `.
  # Accept only HTTP/1.0 or 1.1 status 200 and a bounded printable optional phrase.
  [[ "$status" =~ ^HTTP/1\.[01][[:blank:]]200([[:blank:]][[:print:]]{0,64})?$ ]] || return 1
  local prior_nocasematch=0
  local seen_headers=' '
  shopt -q nocasematch && prior_nocasematch=1
  shopt -s nocasematch
  while IFS= read -r line; do
    line=${line%$'\r'}
    [[ -n "$line" ]] || continue
    [[ "$line" == *:* ]] || { ((prior_nocasematch)) || shopt -u nocasematch; return 1; }
    name=${line%%:*}; value=${line#*:}
    name=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
    [[ "$seen_headers" != *" $name "* ]] || { ((prior_nocasematch)) || shopt -u nocasematch; return 1; }
    seen_headers+="$name "
    value=${value#"${value%%[![:space:]]*}"}
    case "$name" in
      content-length)
        [[ -z "$length" && "$value" =~ ^(0|[1-9][0-9]{0,3})$ ]] || { ((prior_nocasematch)) || shopt -u nocasematch; return 1; }
        length=$value
        ;;
      transfer-encoding)
        [[ -z "$transfer" && "$value" == chunked ]] || { ((prior_nocasematch)) || shopt -u nocasematch; return 1; }
        transfer=chunked
        ;;
      connection|content-type|cache-control|date|server|vary|x-content-type-options) ;;
      *) ((prior_nocasematch)) || shopt -u nocasematch; return 1 ;;
    esac
  done <<< "${headers#*$'\r\n'}"
  ((prior_nocasematch)) || shopt -u nocasematch
  [[ -z "$length" || -z "$transfer" ]] || return 1
  if [[ -n "$length" ]]; then
    ((${#raw_body} == length)) || return 1
    body=$raw_body
  elif [[ "$transfer" == chunked ]]; then
    local remaining=$raw_body chunk_line chunk_size chunk_count=0
    body=''
    while :; do
      [[ "$remaining" == *$'\r\n'* ]] || return 1
      chunk_line=${remaining%%$'\r\n'*}
      remaining=${remaining#*$'\r\n'}
      [[ "$chunk_line" =~ ^([0-9a-fA-F]{1,4})$ ]] || return 1
      chunk_size=$((16#${BASH_REMATCH[1]}))
      if ((chunk_size == 0)); then [[ "$remaining" == $'\r\n' ]] || return 1; break; fi
      ((chunk_count+=1)); ((chunk_count <= 64 && ${#body} + chunk_size <= 4096)) || return 1
      ((${#remaining} >= chunk_size + 2)) || return 1
      body+="${remaining:0:chunk_size}"
      remaining=${remaining:chunk_size}
      [[ "$remaining" == $'\r\n'* ]] || return 1
      remaining=${remaining:2}
    done
  else
    return 1
  fi
  ((${#body} <= 4096)) || return 1
  HTTP_BODY=$body
}

wait_active() {
  local child=$1 result=0
  while kill -0 "$child" 2>/dev/null; do
    [[ -z "${PG_PID:-}" || "$PG_PID" == "$child" ]] || kill -0 "$PG_PID" 2>/dev/null || fail postgres-exited
    [[ -z "${API_PID:-}" || "$API_PID" == "$child" ]] || kill -0 "$API_PID" 2>/dev/null || fail api-exited
    sleep 1
  done
  if wait "$child"; then result=0; else result=$?; fi
  [[ "${INIT_PID:-}" != "$child" ]] || INIT_PID=''
  return "$result"
}

runtime_health() {
  http_status_body /internal/container-health || return 1
  local health_re='^\{"softwareVersion":"[A-Za-z0-9._+-]+","apiContract":"caption-hints\.v2","mode":"formal","ready":true,"reason":"(OK|PREWARM_DEGRADED)","datasetVersion":[1-9][0-9]*,"publishedDataset":\{"releaseId":"[A-Za-z0-9._+-]+","preparationId":"[A-Za-z0-9._+-]+","ruleId":"[A-Za-z0-9._+-]+","sqlVersion":"[A-Za-z0-9._+-]+","sha256":"[0-9a-f]{64}"\}\}$'
  [[ "$HTTP_BODY" =~ $health_re ]]
}

prepare_runtime_state() {
  [[ -d "$RUN" && ! -L "$RUN" && -O "$RUN" ]] || fail run-private-directory-invalid
  local mode entry name entries=()
  mode=$(stat -c '%a' "$RUN") || fail run-private-directory-invalid
  [[ "$mode" == 700 ]] || fail run-private-directory-permissions
  shopt -s nullglob dotglob
  entries=("$RUN"/*)
  shopt -u nullglob dotglob
  if ((${#entries[@]} > 0)); then
    for entry in "${entries[@]}"; do
      name=${entry##*/}
      case "$name" in initializing|verified.json|verified.pending) ;;
        *) fail run-private-state-unknown;;
      esac
      [[ -f "$entry" && ! -L "$entry" && -O "$entry" ]] || fail run-private-state-invalid
    done
  fi
  # These fixed files are volatile runtime-owned state. Inspect the entire
  # private directory first, then discard only those recognized files so an
  # old per-boot proof can never authorize a later container restart.
  for name in initializing verified.json verified.pending; do
    if [[ -e "$RUN/$name" ]]; then rm -- "$RUN/$name" || fail run-private-state-cleanup-failed; fi
  done
}

start_postgres() {
  postgres -D "$PGDATA" -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp \
    -c password_encryption=scram-sha-256 &
  PG_PID=$!
  local attempt=0
  while ((attempt < 120)); do
    kill -0 "$PG_PID" 2>/dev/null || fail postgres-exited
    if pg_isready -h 127.0.0.1 -p "$PGPORT" -U postgres -d postgres >/dev/null 2>&1; then return; fi
    sleep 1
    ((attempt+=1))
  done
  fail postgres-readiness-timeout
}

bootstrap_database() {
  local app_password
  app_password=$(cat "$ROOT/.app-password") || fail volume-secret-invalid
  timeout --kill-after=2s 60s psql -h /tmp -U postgres -d postgres --set=ON_ERROR_STOP=1 --set=app_password="$app_password" <<'SQL' >/dev/null &
SELECT format('CREATE ROLE lexiflow LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :'app_password') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lexiflow') \gexec
ALTER ROLE lexiflow LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'app_password';
SELECT 'CREATE DATABASE lexiflow OWNER lexiflow' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='lexiflow') \gexec
SQL
  INIT_PID=$!
  wait_active "$INIT_PID" || fail postgres-bootstrap-failed
  timeout --kill-after=2s 60s psql -h /tmp -U postgres -d lexiflow --set=ON_ERROR_STOP=1 <<'SQL' >/dev/null &
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname='lexiflow' AND r.rolname <> 'lexiflow') THEN
    RAISE EXCEPTION 'database owner mismatch';
  END IF;
END $$;
SELECT 'CREATE SCHEMA lexiflow_release AUTHORIZATION lexiflow' WHERE NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='lexiflow_release') \gexec
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='lexiflow_release' AND r.rolname <> 'lexiflow') THEN
    RAISE EXCEPTION 'schema owner mismatch';
  END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE lexiflow TO lexiflow;
GRANT USAGE, CREATE ON SCHEMA lexiflow_release TO lexiflow;
SQL
  INIT_PID=$!
  wait_active "$INIT_PID" || fail postgres-bootstrap-failed
}

run_initializer() {
  local app_password
  app_password=$(cat "$ROOT/.app-password") || fail volume-secret-invalid
  export LEXIFLOW_RELEASE_JDBC_URL="jdbc:postgresql://127.0.0.1:${PGPORT}/lexiflow?currentSchema=lexiflow_release&user=lexiflow&password=${app_password}"
  timeout --kill-after=5s 20m java -Xms256m -Xmx768m -Djava.io.tmpdir=/tmp -jar "$JAR" \
    --release-dataset initialize --package "$DATASET" --expected-sha256 "$DATASET_SHA256" \
    --expected-database lexiflow --expected-schema lexiflow_release &
  INIT_PID=$!
  wait_active "$INIT_PID" || fail dataset-initialize-failed
}

start_api() {
  local app_password
  app_password=$(cat "$ROOT/.app-password") || fail volume-secret-invalid
  export SPRING_DATASOURCE_URL="jdbc:postgresql://127.0.0.1:${PGPORT}/lexiflow?currentSchema=lexiflow_release"
  export SPRING_DATASOURCE_USERNAME=lexiflow SPRING_DATASOURCE_PASSWORD=$app_password
  unset app_password JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS || true
  java -Xms64m -Xmx768m -Dlexiflow.runtime.mode=formal \
    -Dlexiflow.runtime.distribution=true \
    -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar "$JAR" &
  API_PID=$!
}

stop_children() {
  local status=0 deadline=$((SECONDS + STOP_SECONDS)) pid
  trap - TERM INT HUP
  for pid in "${API_PID:-}" "${PG_PID:-}" "${INIT_PID:-}"; do
    [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
  done
  for pid in "${API_PID:-}" "${PG_PID:-}" "${INIT_PID:-}"; do
    [[ -n "$pid" ]] || continue
    while kill -0 "$pid" 2>/dev/null && ((SECONDS < deadline)); do sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" 2>/dev/null || true; status=1; fi
    wait "$pid" 2>/dev/null || true
  done
  API_PID=''; PG_PID=''; INIT_PID=''
  return "$status"
}

supervise() {
  local status=0
  trap 'if stop_children; then exit 0; else exit 1; fi' TERM INT HUP
  while :; do
    if ! kill -0 "$PG_PID" 2>/dev/null; then log postgres-exited; status=1; break; fi
    if ! kill -0 "$API_PID" 2>/dev/null; then log api-exited; status=1; break; fi
    sleep 1
  done
  stop_children || status=1
  exit "$status"
}

runtime_start() {
  [[ "$(id -un)" == postgres ]] || fail runtime-user-invalid
  [[ -f "$JAR" && ! -L "$JAR" && -f "$DATASET" && ! -L "$DATASET" ]] || fail runtime-input-invalid
  PG_PID=''; API_PID=''; INIT_PID=''
  trap 'stop_children || exit 1; exit 0' TERM INT HUP
  trap 'rc=$?; trap - EXIT; stop_children || rc=1; exit "$rc"' EXIT
  read_identity
  prepare_runtime_state
  atomic_write "$RUN/initializing" "$(identity_line)"
  validate_volume
  prepare_pgdata

  start_postgres
  bootstrap_database
  start_api
  local attempt=0 version
  while ((attempt < 90)); do
    kill -0 "$PG_PID" 2>/dev/null || fail postgres-exited
    kill -0 "$API_PID" 2>/dev/null || fail api-exited
    if http_status_body /api/v1/runtime-status && [[ "$HTTP_BODY" == \{*\} ]]; then break; fi
    sleep 1
    ((attempt+=1))
  done
  ((attempt < 90)) || fail api-readiness-timeout

  run_initializer
  attempt=0
  version=''
  while ((attempt < 60)); do
    kill -0 "$PG_PID" 2>/dev/null || fail postgres-exited
    kill -0 "$API_PID" 2>/dev/null || fail api-exited
    if http_status_body /api/v1/runtime-status && [[ "$HTTP_BODY" =~ $DATASET_VERSION_RE ]]; then
      version=${BASH_REMATCH[1]}
      break
    fi
    sleep 1
    ((attempt+=1))
  done
  [[ -n "$version" ]] || fail api-data-readiness-timeout
  write_proof "$version"
  rm -f "$RUN/initializing" || fail marker-remove-failed
  atomic_write "$ROOT/.lexiflow-ready" "$(volume_identity_line)"
  rm -f "$ROOT/.lexiflow-initializing"

  local health_attempt=0
  while ((health_attempt < 30)); do
    kill -0 "$PG_PID" 2>/dev/null || fail postgres-exited
    kill -0 "$API_PID" 2>/dev/null || fail api-exited
    runtime_health && break
    sleep 1
    ((health_attempt+=1))
  done
  ((health_attempt < 30)) || fail runtime-health-failed
  supervise
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    health)
      [[ "$#" -eq 1 ]] || exit 64
      read_identity
      runtime_health
      ;;
    run)
      [[ "$#" -eq 1 ]] || exit 64
      runtime_start
      ;;
    '') runtime_start ;;
    *) exit 64 ;;
  esac
fi
