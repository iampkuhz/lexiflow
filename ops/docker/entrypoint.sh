#!/bin/sh
set -eu

fail() { printf '%s\n' 'lexiflow container command rejected' >&2; exit 64; }
unset JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS

[ "$#" -ge 1 ] || fail
mode=$1
shift

hex64() { case "$1" in *[!0-9a-f]*|'') return 1;; esac; [ "${#1}" -eq 64 ]; }
read_secret() {
  file=$1
  bytes=$(wc -c < "$file" | tr -d ' ')
  case "$bytes" in
    64) IFS= read -r secret < "$file" || [ -n "${secret:-}" ] || return 1 ;;
    65)
      last=$(tail -c 1 "$file" | od -An -t x1 | tr -d ' \n')
      [ "$last" = 0a ] || return 1
      IFS= read -r secret < "$file" || [ -n "${secret:-}" ] || return 1
      ;;
    *) return 1 ;;
  esac
  hex64 "$secret"
}

case "$mode" in
  api)
    [ "$#" -eq 0 ] || fail
    if [ -n "${LEXIFLOW_DATABASE_HOST:-}" ]; then
      [ "$LEXIFLOW_DATABASE_HOST" = host.containers.internal ] || fail
      case "${LEXIFLOW_DATABASE_PORT:-}" in *[!0-9]*|'') fail;; esac
      [ "${LEXIFLOW_DATABASE_PORT}" -ge 1 ] && [ "${LEXIFLOW_DATABASE_PORT}" -le 65535 ] || fail
      case "${LEXIFLOW_DATABASE_USER:-}" in ''|*'
'*) fail;; esac
      [ "$(printf '%s' "$LEXIFLOW_DATABASE_USER" | wc -c | tr -d ' ')" -le 63 ] || fail
      case "${LEXIFLOW_DATABASE_AUTH_MODE:-}" in
        development-trust)
          [ "$LEXIFLOW_DATABASE_USER" = postgres ] || fail
          [ ! -e /run/secrets/app-password ] || fail
          [ -z "${SPRING_DATASOURCE_PASSWORD:-}" ] || fail
          password=''
          ;;
        password)
          [ -r /run/secrets/app-password ] || fail
          password=$(cat /run/secrets/app-password)
          [ -n "$password" ] || fail
          ;;
        *) fail ;;
      esac
      jdbc="jdbc:postgresql://host.containers.internal:${LEXIFLOW_DATABASE_PORT}/lexiflow?currentSchema=lexiflow_release"
      db_user=$LEXIFLOW_DATABASE_USER
    else
      [ "${LEXIFLOW_DATABASE_AUTH_MODE:-managed}" = managed ] || fail
      [ -r /run/secrets/app-password ] || fail
      read_secret /run/secrets/app-password || fail
      password=$secret
      unset secret
      jdbc='jdbc:postgresql://postgres:5432/lexiflow?currentSchema=lexiflow_release'
      db_user=lexiflow
    fi
    export SPRING_DATASOURCE_PASSWORD=$password
    unset password
    analysis=${LEXIFLOW_SEGMENT_ANALYSIS_ENABLED:-false}
    case "$analysis" in true|false) ;; *) fail ;; esac
    # API 默认启用可读日志；该默认行为不会开启 JSONL 分析。
    export SPRING_DATASOURCE_URL=$jdbc SPRING_DATASOURCE_USERNAME=$db_user
    unset jdbc db_user
    exec java -Xms256m -Xmx768m -Dlexiflow.runtime.mode=formal -Dlexiflow.caption-debug.enabled=true -Dlexiflow.segment-analysis.enabled="$analysis" -Dlexiflow.segment-analysis.path=/tmp/lexiflow-private/caption-segments.jsonl -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar /app/lexiflow-api.jar
    ;;
  initialize)
    [ "$#" -eq 0 ] || fail
    [ -n "${LEXIFLOW_DATASET_SHA256:-}" ] && hex64 "$LEXIFLOW_DATASET_SHA256" || fail
    [ -r /dataset/dataset.zip ] || fail
    [ -r /run/secrets/app-password ] || fail
    read_secret /run/secrets/app-password || fail
    password=$secret
    unset secret
    export LEXIFLOW_RELEASE_JDBC_URL="jdbc:postgresql://postgres:5432/lexiflow?currentSchema=lexiflow_release&user=lexiflow&password=$password"
    unset password
    exec java -Xms256m -Xmx768m -Djava.io.tmpdir=/work -jar /app/lexiflow-api.jar --release-dataset initialize --package /dataset/dataset.zip --expected-sha256 "$LEXIFLOW_DATASET_SHA256" --expected-database lexiflow --expected-schema lexiflow_release
    ;;
  health)
    [ "$#" -eq 0 ] || fail
    exec java -Xms16m -Xmx64m -Dloader.main=io.lexiflow.api.release.RuntimeHealthCommand -cp /app/lexiflow-api.jar org.springframework.boot.loader.launch.PropertiesLauncher
    ;;
  *) fail ;;
esac
