#!/bin/sh
set -eu
fail() { printf '%s\n' 'lexiflow container command rejected' >&2; exit 64; }
unset JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS
[ "$#" -ge 1 ] || fail
mode=$1; shift
case "$mode" in
  api)
    [ "$#" -eq 0 ] || fail
    analysis=${LEXIFLOW_SEGMENT_ANALYSIS_ENABLED:-false}
    case "$analysis" in true|false) ;; *) fail ;; esac
    [ -f /var/lib/lexiflow/lexicon.db ] && [ ! -L /var/lib/lexiflow/lexicon.db ] || fail
    export SPRING_DATASOURCE_URL='jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro'
    exec java -Xms64m -Xmx768m -Dlexiflow.runtime.mode=formal -Dlexiflow.caption-debug.enabled=true -Dlexiflow.segment-analysis.enabled="$analysis" -Dlexiflow.segment-analysis.path=/tmp/lexiflow-private/caption-segments.jsonl -Dserver.address=0.0.0.0 -Dserver.port=8080 -jar /app/lexiflow-api.jar
    ;;
  initialize)
    [ "$#" -eq 0 ] || fail
    [ -r /dataset/dataset.zip ] && [ -w /var/lib/lexiflow ] || fail
    [ -n "${LEXIFLOW_DATASET_SHA256:-}" ] || fail
    exec java -Xms256m -Xmx768m -Djava.io.tmpdir=/tmp -jar /app/lexiflow-api.jar \
      --release-dataset initialize --package /dataset/dataset.zip --expected-sha256 "$LEXIFLOW_DATASET_SHA256" \
      --expected-file /var/lib/lexiflow/lexicon.db
    ;;
  health)
    [ "$#" -eq 0 ] || fail
    # Health semantics stay bound to the API's fixed bounded local HTTP contract.
    exec /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/timeout --kill-after=1s 2s /bin/bash --noprofile --norc -s 2>/dev/null <<'HEALTH'
set -eu
exec 3<>/dev/tcp/127.0.0.1/8080
printf 'GET /internal/container-health HTTP/1.1\r\nHost: 127.0.0.1:8080\r\nConnection: close\r\n\r\n' >&3
wire=''
if IFS= read -r -d '' -n 8193 wire <&3; then exit 1; fi
exec 3<&- 3>&-
[[ "$wire" == *$'\r\n\r\n'* ]] || exit 1
headers=${wire%%$'\r\n\r\n'*}; body=${wire#*$'\r\n\r\n'}
[[ ${#headers} -le 4096 && ${#body} -le 4096 ]] || exit 1
status=${headers%%$'\r\n'*}
[[ "$status" == 'HTTP/1.1 200 '* || "$status" == 'HTTP/1.0 200 '* ]] || exit 1
[[ "$headers" == *$'\r\n'* ]] || exit 1
headers=${headers#*$'\r\n'}; length=''; content_type=0
shopt -s nocasematch
while IFS= read -r line; do
  line=${line%$'\r'}
  if [[ "$line" =~ ^Content-Length:[[:blank:]]*(0|[1-9][0-9]{0,3})[[:blank:]]*$ ]]; then [[ -z "$length" ]] || exit 1; length=${BASH_REMATCH[1]}
  elif [[ "$line" =~ ^Content-Type:[[:blank:]]*application/json([[:blank:]]*\;[[:blank:]]*charset=UTF-8)?[[:blank:]]*$ ]]; then [[ "$content_type" -eq 0 ]] || exit 1; content_type=1
  elif [[ "$line" =~ ^(Content-Length|Content-Type|Transfer-Encoding): ]]; then exit 1
  elif [[ ! "$line" =~ ^[A-Za-z0-9-]+:[[:blank:]]*[^[:cntrl:]]*$ ]]; then exit 1; fi
done <<< "$headers"
shopt -u nocasematch
[[ "$content_type" -eq 1 && -n "$length" && "$length" -eq "${#body}" ]] || exit 1
summary='^\{"softwareVersion":"[0-9A-Za-z.+-]{1,200}","apiContract":"caption-hints\.v2","mode":"formal","ready":true,"reason":"(OK|PREWARM_DEGRADED)","datasetVersion":([1-9][0-9]{0,18}),"publishedDataset":(null|\{"releaseId":"[A-Za-z0-9._+-]{1,128}","preparationId":"[A-Za-z0-9._+-]{1,128}","ruleId":"[A-Za-z0-9._+-]{1,128}","sqlVersion":"[A-Za-z0-9._+-]{1,128}","sha256":"[a-f0-9]{64}"\})\}$'
[[ "$body" =~ $summary ]] || exit 1
dataset=${BASH_REMATCH[2]}; [[ ${#dataset} -lt 19 || "$dataset" < 9223372036854775808 ]] || exit 1
printf '%s\n' "$body"
HEALTH
    ;;
  *) fail ;;
esac
