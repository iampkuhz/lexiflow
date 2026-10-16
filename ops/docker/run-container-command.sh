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
    exec java -Xms64m -Xmx768m -Dlexiflow.runtime.mode=formal -Dlexiflow.caption-debug.enabled=true -Dlexiflow.segment-analysis.enabled="$analysis" -Dlexiflow.segment-analysis.path=/tmp/lexiflow-private/caption-segments.jsonl -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar /app/lexiflow-api.jar
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
    # 有界本机传输；就绪语义在唯一 API 进程内判定，不为探针另起 JVM。
    exec /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/timeout --kill-after=1s 2s /bin/bash --noprofile --norc -s 2>/dev/null <<'HEALTH'
set -eu
exec 3<>/dev/tcp/127.0.0.1/8080
printf 'GET /internal/container-health HTTP/1.1\r\nHost: 127.0.0.1:8080\r\nConnection: close\r\n\r\n' >&3
# NUL、达到上限和未在 deadline 内 EOF 均拒绝；LC_ALL=C 按字节计数。
wire=''
if IFS= read -r -d '' -n 8193 wire <&3; then exit 1; fi
exec 3<&- 3>&-
[[ "$wire" == *$'\r\n\r\n'* ]] || exit 1
headers=${wire%%$'\r\n\r\n'*}
body=${wire#*$'\r\n\r\n'}
[[ ${#headers} -le 4096 && ${#body} -le 4096 ]] || exit 1
status=${headers%%$'\r\n'*}
[[ "$status" == 'HTTP/1.1 200 '* || "$status" == 'HTTP/1.0 200 '* ]] || exit 1
[[ "$headers" == *$'\r\n'* ]] || exit 1
headers=${headers#*$'\r\n'}
length=''
content_type=0
shopt -s nocasematch
while IFS= read -r line; do
  line=${line%$'\r'}
  if [[ "$line" =~ ^Content-Length:[[:blank:]]*(0|[1-9][0-9]{0,3})[[:blank:]]*$ ]]; then
    [[ -z "$length" ]] || exit 1
    length=${BASH_REMATCH[1]}
  elif [[ "$line" =~ ^Content-Type:[[:blank:]]*application/json([[:blank:]]*\;[[:blank:]]*charset=UTF-8)?[[:blank:]]*$ ]]; then
    [[ "$content_type" -eq 0 ]] || exit 1
    content_type=1
  elif [[ "$line" =~ ^(Content-Length|Content-Type|Transfer-Encoding): ]]; then
    exit 1
  elif [[ ! "$line" =~ ^[A-Za-z0-9-]+:[[:blank:]]*[^[:cntrl:]]*$ ]]; then
    exit 1
  fi
done <<< "$headers"
shopt -u nocasematch
[[ "$content_type" -eq 1 && -n "$length" && "$length" -eq "${#body}" ]] || exit 1
# 只接受服务端已校验的固定序列化摘要；不实现第二份通用 JSON/版本解析器。
summary='^\{"softwareVersion":"[0-9A-Za-z.+-]{1,200}","apiContract":"caption-hints\.v2","mode":"formal","ready":true,"reason":"(OK|PREWARM_DEGRADED)","datasetVersion":([1-9][0-9]{0,18})\}$'
[[ "$body" =~ $summary ]] || exit 1
dataset=${BASH_REMATCH[2]}
[[ ${#dataset} -lt 19 || "$dataset" < 9223372036854775808 ]] || exit 1
printf '%s\n' "$body"
HEALTH
    ;;
  *) fail ;;
esac
