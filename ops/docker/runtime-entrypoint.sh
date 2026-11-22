#!/bin/bash
set -Eeuo pipefail
readonly ROOT=/var/lib/lexiflow
readonly DB=/var/lib/lexiflow/lexicon.db
readonly PREFERENCES_DIR=/var/lib/lexiflow/preferences
readonly PREFERENCES_DB=/var/lib/lexiflow/preferences/preferences.db
readonly PREFERENCES_MOUNT_INFO=/proc/self/mountinfo
readonly RUN=/run/lexiflow
readonly IDENTITY=/app/release-identity.env
readonly IDENTITY_JSON=/app/release-identity.json
readonly DATASET=/app/dataset.zip
readonly JAR=/app/lexiflow-api.jar
readonly STOP_SECONDS=35
API_PID=''
INIT_PID=''
log() { printf 'lexiflow-runtime: %s\n' "$1" >&2; }
fail() { log "$1"; exit 1; }
hex64() { [[ "$1" =~ ^[0-9a-f]{64}$ ]]; }
bounded_id() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]]; }
read_identity() {
  [[ -f "$IDENTITY" && ! -L "$IDENTITY" && -f "$IDENTITY_JSON" && ! -L "$IDENTITY_JSON" ]] || fail identity-unavailable
  local count=0 line key value; local schema_version='' software_version='' api_contract='' sql_version=''
  local release_id='' preparation_id='' rule_id='' dataset_sha256=''; local seen=''
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((count+=1)); [[ "$count" -le 8 && "$line" == *=* ]] || fail identity-invalid
    key=${line%%=*}; value=${line#*=}; [[ -n "$value" && "$value" != *[!A-Za-z0-9._+-]* ]] || fail identity-invalid
    case "$key" in
      schemaVersion) [[ -z ${seen_schema:-} ]] || fail identity-invalid; seen_schema=1; schema_version=$value;;
      softwareVersion) [[ -z ${seen_software:-} ]] || fail identity-invalid; seen_software=1; software_version=$value;;
      apiContract) [[ -z ${seen_contract:-} ]] || fail identity-invalid; seen_contract=1; api_contract=$value;;
      sqlVersion) [[ -z ${seen_sql:-} ]] || fail identity-invalid; seen_sql=1; sql_version=$value;;
      releaseId) [[ -z ${seen_release:-} ]] || fail identity-invalid; seen_release=1; release_id=$value;;
      preparationId) [[ -z ${seen_preparation:-} ]] || fail identity-invalid; seen_preparation=1; preparation_id=$value;;
      ruleId) [[ -z ${seen_rule:-} ]] || fail identity-invalid; seen_rule=1; rule_id=$value;;
      datasetSha256) [[ -z ${seen_digest:-} ]] || fail identity-invalid; seen_digest=1; dataset_sha256=$value;;
      *) fail identity-invalid;;
    esac
  done < "$IDENTITY"
  [[ "$count" -eq 8 && "${seen_schema:-}${seen_software:-}${seen_contract:-}${seen_sql:-}${seen_release:-}${seen_preparation:-}${seen_rule:-}${seen_digest:-}" == 11111111 ]] || fail identity-invalid
  [[ "$schema_version" == 1 && "$api_contract" =~ ^caption-hints\.v[1-9][0-9]{0,8}$ ]] || fail identity-invalid
  bounded_id "$software_version" && bounded_id "$sql_version" && bounded_id "$release_id" && bounded_id "$preparation_id" && bounded_id "$rule_id" && hex64 "$dataset_sha256" || fail identity-invalid
  SOFTWARE_VERSION=$software_version API_CONTRACT=$api_contract SQL_VERSION=$sql_version \
    RELEASE_ID=$release_id PREPARATION_ID=$preparation_id RULE_ID=$rule_id DATASET_SHA256=$dataset_sha256
}
volume_identity_line() {
  printf 'schemaVersion=4|sqlVersion=%s|releaseId=%s|preparationId=%s|ruleId=%s|datasetSha256=%s' \
    "$SQL_VERSION" "$RELEASE_ID" "$PREPARATION_ID" "$RULE_ID" "$DATASET_SHA256"
}
write_marker() {
  local marker="$ROOT/.lexiflow-ready" tmp="$ROOT/.lexiflow-ready.pending" expected
  expected=$(volume_identity_line)
  if [[ -e "$marker" || -L "$marker" ]]; then
    [[ -f "$marker" && ! -L "$marker" ]] || fail marker-invalid
    [[ "$(cat "$marker")" == "$expected" ]] || fail volume-identity-mismatch
    rm -f -- "$tmp" "$ROOT/.lexiflow-initializing" "$ROOT/.lexiflow-initializing.pending" || fail marker-cleanup-failed
    return
  fi
  if [[ -e "$tmp" || -L "$tmp" ]]; then [[ -f "$tmp" && ! -L "$tmp" && "$(cat "$tmp")" == "$expected" ]] || fail marker-pending-invalid
  else (umask 077; set -o noclobber; printf '%s\n' "$expected" > "$tmp") || fail marker-write-failed; fi
  mv -- "$tmp" "$marker" || fail marker-write-failed
  rm -f -- "$ROOT/.lexiflow-initializing" "$ROOT/.lexiflow-initializing.pending" || fail marker-cleanup-failed
}
write_proof() {
  local version=$1
  [[ "$version" =~ ^[1-9][0-9]{0,18}$ ]] || fail dataset-version-invalid
  printf '{"schemaVersion":1,"cliResult":"PASS","datasetVersion":%s,"releaseId":"%s","preparationId":"%s","ruleId":"%s","sqlVersion":"%s","sha256":"%s"}\n' \
    "$version" "$RELEASE_ID" "$PREPARATION_ID" "$RULE_ID" "$SQL_VERSION" "$DATASET_SHA256" > "$RUN/verified.pending" || fail proof-write-failed
  chmod 0600 "$RUN/verified.pending" || fail proof-write-failed
  mv -f -- "$RUN/verified.pending" "$RUN/verified.json" || fail proof-write-failed
}
prepare_run_state() {
  [[ -d "$RUN" && ! -L "$RUN" && -O "$RUN" && "$(stat -c '%a' "$RUN")" == 700 ]] || fail run-private-directory-invalid
  local entry name; local -a entries=()
  shopt -s nullglob dotglob; entries=("$RUN"/*); shopt -u nullglob dotglob
  for entry in ${entries[@]+"${entries[@]}"}; do
    name=${entry##*/}; case "$name" in initializing|verified.json|verified.pending) ;; *) fail run-private-state-unknown;; esac
    [[ -f "$entry" && ! -L "$entry" && -O "$entry" ]] || fail run-private-state-invalid
  done
  rm -f -- "$RUN/initializing" "$RUN/verified.json" "$RUN/verified.pending" || fail run-private-state-cleanup-failed
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


runtime_health() {
  http_status_body /internal/container-health || return 1
  local health_re='^\{"softwareVersion":"[A-Za-z0-9._+-]+","apiContract":"caption-hints\.v[1-9][0-9]{0,8}","mode":"formal","ready":true,"reason":"(OK|PREWARM_DEGRADED)","datasetVersion":[1-9][0-9]*,"publishedDataset":\{"releaseId":"[A-Za-z0-9._+-]+","preparationId":"[A-Za-z0-9._+-]+","ruleId":"[A-Za-z0-9._+-]+","sqlVersion":"[A-Za-z0-9._+-]+","sha256":"[0-9a-f]{64}"\}\}$'
  [[ "$HTTP_BODY" =~ $health_re && "$HTTP_BODY" == *\"apiContract\":\"$API_CONTRACT\"* ]]
}


stop_api() {
  local rc=0 deadline=$((SECONDS + STOP_SECONDS))
  trap - EXIT TERM INT HUP
  if [[ -n "$INIT_PID" ]]; then kill -TERM "$INIT_PID" 2>/dev/null || :; wait "$INIT_PID" 2>/dev/null || :; INIT_PID=''; fi
  if [[ -n "$API_PID" ]]; then kill -TERM "$API_PID" 2>/dev/null || :; while kill -0 "$API_PID" 2>/dev/null && ((SECONDS < deadline)); do sleep 1; done; if kill -0 "$API_PID" 2>/dev/null; then kill -KILL "$API_PID" 2>/dev/null || :; rc=1; fi; wait "$API_PID" 2>/dev/null || :; fi
  API_PID=''; return "$rc"
}
verify_storage_volume() {
  local kind=$1 file
  case "$kind" in
    lexicon) file=$DB;;
    preferences) file=$PREFERENCES_DB;;
    *) fail storage-role-invalid;;
  esac
  if [[ -e "$file" || -L "$file" ]]; then
    [[ -f "$file" && ! -L "$file" && -O "$file" ]] || fail "$kind-storage-ownership-invalid"
    timeout --kill-after=1s 15s java -Xms32m -Xmx128m -jar "$JAR" --verify-storage "$kind" "$file" >/dev/null 2>&1 ||
      fail "$kind-storage-incompatible"
  fi
}
runtime_start() {
  [[ -d "$PREFERENCES_DIR" && ! -L "$PREFERENCES_DIR" && -O "$PREFERENCES_DIR" && -w "$PREFERENCES_DIR" ]] || fail preferences-volume-invalid
  awk -v target="$PREFERENCES_DIR" '$5 == target { found=1 } END { exit !found }' "$PREFERENCES_MOUNT_INFO" 2>/dev/null || fail preferences-volume-not-mounted
  local preference_item; local -a preference_entries=()
  shopt -s nullglob dotglob; preference_entries=("$PREFERENCES_DIR"/*); shopt -u nullglob dotglob
  for preference_item in ${preference_entries[@]+"${preference_entries[@]}"}; do
    case "${preference_item##*/}" in preferences.db|preferences.db-journal) [[ -f "$preference_item" && ! -L "$preference_item" && -O "$preference_item" ]] || fail preferences-file-invalid;; *) fail preferences-volume-unknown-content;; esac
  done
  local dataset_hash item; local -a entries=()
  [[ "$(id -u)" == 10001 ]] || fail runtime-user-invalid
  [[ -f "$JAR" && ! -L "$JAR" && -f "$DATASET" && ! -L "$DATASET" ]] || fail runtime-input-invalid
  [[ -d "$ROOT" && ! -L "$ROOT" && -O "$ROOT" && -d "$RUN" && ! -L "$RUN" && -O "$RUN" ]] || fail runtime-directory-invalid
  [[ "$(stat -c '%a' "$ROOT")" == 700 && "$(stat -c '%a' "$RUN")" == 700 ]] || fail runtime-directory-permissions
  read_identity
  [[ "$(timeout --kill-after=1s 15s java -Xms32m -Xmx128m -jar "$JAR" --api-contract)" == "$API_CONTRACT" ]] || fail api-contract-artifact-mismatch
  timeout --kill-after=1s 15s java -Xms32m -Xmx128m -jar "$JAR" --storage-contract >/dev/null || fail storage-contract-artifact-mismatch
  dataset_hash=$(sha256sum < "$DATASET" | cut -d ' ' -f1) || fail dataset-invalid
  [[ "$dataset_hash" == "$DATASET_SHA256" ]] || fail dataset-digest-mismatch
  if [[ -e "$DB" || -L "$DB" ]]; then [[ -f "$DB" && ! -L "$DB" && -O "$DB" ]] || fail sqlite-file-invalid; fi
  if [[ -e "$ROOT/.lexiflow-owner" || -L "$ROOT/.lexiflow-owner" ]]; then [[ -f "$ROOT/.lexiflow-owner" && ! -L "$ROOT/.lexiflow-owner" && "$(cat "$ROOT/.lexiflow-owner")" == lexiflow-runtime ]] || fail volume-owner-invalid
  else
    # The named volume is expected to be empty. Never adopt existing unknown files.
    shopt -s nullglob dotglob; entries=("$ROOT"/*); shopt -u nullglob dotglob
    if ((${#entries[@]} != 0)); then
      ((${#entries[@]} == 1)) && [[ "${entries[0]##*/}" == preferences && -d "$PREFERENCES_DIR" && ! -L "$PREFERENCES_DIR" ]] || fail volume-unknown-content
    fi
    printf 'lexiflow-runtime\n' > "$ROOT/.lexiflow-owner"; chmod 0600 "$ROOT/.lexiflow-owner"
  fi
  shopt -s nullglob dotglob; entries=("$ROOT"/*); shopt -u nullglob dotglob
  for item in ${entries[@]+"${entries[@]}"}; do
    case "${item##*/}" in
      preferences)
        [[ -d "$PREFERENCES_DIR" && ! -L "$PREFERENCES_DIR" && -O "$PREFERENCES_DIR" ]] || fail preferences-volume-invalid
        shopt -s nullglob dotglob; preference_entries=("$PREFERENCES_DIR"/*); shopt -u nullglob dotglob
        for preference_item in ${preference_entries[@]+"${preference_entries[@]}"}; do
          case "${preference_item##*/}" in preferences.db|preferences.db-journal) [[ -f "$preference_item" && ! -L "$preference_item" && -O "$preference_item" ]] || fail preferences-file-invalid;; *) fail preferences-volume-unknown-content;; esac
        done;;
      .lexiflow-owner|.lexiflow-ready|.lexiflow-ready.pending|.lexiflow-initializing|.lexiflow-initializing.pending|lexicon.db|lexicon.db-journal) [[ ! -L "$item" ]] || fail volume-path-invalid;;
      *) fail volume-unknown-content;;
    esac
  done
  local expected marker
  expected=$(volume_identity_line)
  for marker in "$ROOT/.lexiflow-ready" "$ROOT/.lexiflow-ready.pending" "$ROOT/.lexiflow-initializing" "$ROOT/.lexiflow-initializing.pending"; do
    if [[ -e "$marker" || -L "$marker" ]]; then [[ -f "$marker" && ! -L "$marker" && "$(cat "$marker")" == "$expected" ]] || fail volume-identity-mismatch; fi
  done
  if [[ -e "$ROOT/.lexiflow-ready" || -e "$ROOT/.lexiflow-ready.pending" ]]; then
    [[ -f "$DB" && ! -L "$DB" ]] || fail sqlite-marker-without-database
  fi
  if [[ -e "$DB" && ! -e "$ROOT/.lexiflow-ready" && ! -e "$ROOT/.lexiflow-ready.pending" \
      && ! -e "$ROOT/.lexiflow-initializing" && ! -e "$ROOT/.lexiflow-initializing.pending" ]]; then fail sqlite-initialization-proof-missing; fi
  verify_storage_volume lexicon
  verify_storage_volume preferences
  if [[ ! -e "$PREFERENCES_DB" ]]; then
    umask 077
    timeout --kill-after=1s 30s java -Xms32m -Xmx128m -Djava.io.tmpdir=/tmp -jar "$JAR" --init-preferences "jdbc:sqlite:$PREFERENCES_DB" >/dev/null 2>&1 || fail preferences-initialize-failed
  fi
  [[ -f "$PREFERENCES_DB" && ! -L "$PREFERENCES_DB" && -O "$PREFERENCES_DB" ]] || fail preferences-database-unavailable
  chmod 0600 "$PREFERENCES_DB" || fail preferences-database-permissions
  export LEXIFLOW_PREFERENCES_JDBC_URL="jdbc:sqlite:$PREFERENCES_DB"
  prepare_run_state
  trap 'rc=$?; trap - EXIT; stop_api || rc=1; exit "$rc"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  printf '%s\n' "$(volume_identity_line)" > "$ROOT/.lexiflow-initializing.pending" || fail marker-write-failed
  chmod 0600 "$ROOT/.lexiflow-initializing.pending" || fail marker-write-failed
  mv -- "$ROOT/.lexiflow-initializing.pending" "$ROOT/.lexiflow-initializing" || fail marker-write-failed
  printf '%s\n' "$(volume_identity_line)" > "$RUN/initializing" || fail run-state-write-failed
  chmod 0600 "$RUN/initializing" || fail run-state-write-failed
  # Always finish offline initialization/publication before starting the read-only API.
  export LEXIFLOW_RELEASE_JDBC_URL="jdbc:sqlite:$DB"
  timeout --kill-after=5s 20m java -Xms256m -Xmx768m -Djava.io.tmpdir=/tmp -jar "$JAR" \
    --release-dataset initialize --package "$DATASET" --expected-sha256 "$DATASET_SHA256" --expected-file "$DB" & INIT_PID=$!
  if wait "$INIT_PID"; then INIT_PID=''; else INIT_PID=''; fail dataset-initialize-failed; fi
  [[ -f "$DB" && ! -L "$DB" && -O "$DB" ]] || fail sqlite-file-missing
  chmod 0600 "$DB" || fail sqlite-file-permissions
  unset LEXIFLOW_RELEASE_JDBC_URL
  [[ -f "$RUN/initializing" && ! -L "$RUN/initializing" ]] || fail run-state-missing
  export SPRING_DATASOURCE_URL="jdbc:sqlite:$DB?mode=ro"
  unset JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS
  java -Xms64m -Xmx768m -Dlexiflow.runtime.mode=formal -Dlexiflow.runtime.distribution=true -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar "$JAR" & API_PID=$!
  trap 'stop_api || exit 1; exit 0' TERM INT HUP
  local attempt=0 version=''; while ((attempt < 90)); do
    kill -0 "$API_PID" 2>/dev/null || fail api-exited
    if http_status_body /api/v1/runtime-status && [[ "$HTTP_BODY" =~ $DATASET_VERSION_RE ]]; then version=${BASH_REMATCH[1]}; break; fi
    sleep 1; ((attempt+=1))
  done
  [[ -n "$version" ]] || fail api-readiness-timeout
  write_proof "$version"
  rm -f -- "$RUN/initializing" || fail run-state-cleanup-failed
  runtime_health || fail runtime-health-failed
  write_marker
  while kill -0 "$API_PID" 2>/dev/null; do sleep 1; done
  wait "$API_PID" || fail api-exited
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    health) [[ "$#" -eq 1 ]] || exit 64; read_identity; runtime_health;;
    check-volume)
      [[ "$#" -eq 2 && "$(id -u)" == 10001 ]] || exit 64
      read_identity
      case "$2" in
        lexicon)
          [[ -f "$DB" && -f "$ROOT/.lexiflow-owner" && ! -L "$ROOT/.lexiflow-owner" &&
              "$(cat "$ROOT/.lexiflow-owner")" == lexiflow-runtime &&
              -f "$ROOT/.lexiflow-ready" && ! -L "$ROOT/.lexiflow-ready" &&
              "$(cat "$ROOT/.lexiflow-ready")" == "$(volume_identity_line)" ]] || fail lexicon-storage-proof-missing;;
        preferences)
          [[ -f "$PREFERENCES_DB" ]] || fail preferences-storage-proof-missing;;
        *) exit 64;;
      esac
      verify_storage_volume "$2"
      printf 'PASS\n';;

    init-preferences)
      [[ "$#" -eq 1 && -d "$PREFERENCES_DIR" && ! -L "$PREFERENCES_DIR" && -w "$PREFERENCES_DIR" ]] || exit 64
      awk -v target="$PREFERENCES_DIR" '$5 == target { found=1 } END { exit !found }' "$PREFERENCES_MOUNT_INFO" 2>/dev/null || exit 64
      umask 077
      exec java -Xms32m -Xmx128m -jar "$JAR" --init-preferences "jdbc:sqlite:$PREFERENCES_DB";;
    run|'') [[ "$#" -le 1 ]] || exit 64; runtime_start;;
    *) exit 64;;
  esac
fi
