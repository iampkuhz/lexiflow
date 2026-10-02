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
  api|api-debug)
    [ "$#" -eq 0 ] || fail
    [ -r /run/secrets/app-password ] || fail
    read_secret /run/secrets/app-password || fail
    password=$secret
    unset secret
    export SPRING_DATASOURCE_PASSWORD=$password
    unset password
    debug=false
    [ "$mode" != api-debug ] || debug=true
    exec java -Xms256m -Xmx768m -Dspring.datasource.url=jdbc:postgresql://postgres:5432/lexiflow?currentSchema=lexiflow_release -Dspring.datasource.username=lexiflow -Dlexiflow.runtime.mode=formal -Dlexiflow.segment-analysis.enabled="$debug" -Dlexiflow.segment-analysis.console="$debug" -Dlexiflow.segment-analysis.path=/tmp/lexiflow-private/caption-segments.jsonl -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar /app/lexiflow-api.jar
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
