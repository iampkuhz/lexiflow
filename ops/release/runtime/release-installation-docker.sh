# Docker/Compose 适配；每次资源变更前验证安装锁与标签归属。
# 内部缓存不能由调用者环境伪造；只接受本次解析并校验过的端点。
unset LF_DOCKER_ENDPOINT
unset LF_DOCKER_OBSERVATION
# 每次 CLI 都在独占目录中捕获；RLIMIT_FSIZE 在写入时限制两条流，
# 而不是在命令结束后才截断。只清理本次直接创建的 CLI/timer。
lf_docker_run() (
  set +x
  [ "$#" -ge 2 ] || exit 1
  case "$1" in 1|30|600) budget=$1;; *) exit 1;; esac
  shift
  input_mode=closed
  if [ "${1:-}" = --stdin-fd3 ]; then input_mode=fd3; shift; fi
  [ "$#" -gt 0 ] || exit 1
  umask 077
  dir=$(mktemp -d /tmp/lexiflow-docker.XXXXXXXX 2>/dev/null) || exit 1
  child= timer=
  cleanup() {
    trap - EXIT HUP INT TERM
    if [ -n "$timer" ]; then kill -TERM "$timer" 2>/dev/null || :; wait "$timer" 2>/dev/null || :; fi
    if [ -n "$child" ]; then
      kill -TERM "$child" 2>/dev/null || :
      kill -KILL "$child" 2>/dev/null || :
      wait "$child" 2>/dev/null || :
    fi
    rm -f -- "$dir/stdout" "$dir/stderr" "$dir/timedout" >/dev/null 2>&1
    rmdir -- "$dir" 2>/dev/null || :
  }
  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  (: > "$dir/stdout" && : > "$dir/stderr") 2>/dev/null || exit 1
  # Keep stdin on fd 3 because POSIX shells may replace asynchronous children'
  # stdin with /dev/null. Only explicitly designated private-file calls inherit it.
  exec 3<&0 2>/dev/null || :
  (
    # POSIX Shell 的 -f 单位在目标平台不同；512 在 macOS/Linux 都更保守。
    ulimit -f 512 2>/dev/null || exit 1
    exec 3<&3
    if [ "$input_mode" = fd3 ]; then
      exec "$@" <&3 3<&- > "$dir/stdout" 2> "$dir/stderr"
    else
      exec "$@" </dev/null 3<&- > "$dir/stdout" 2> "$dir/stderr"
    fi
  ) 3<&3 &
  child=$!
  (
    sleeper= grace= critical=0 cancelled=
    timer_cleanup() {
      trap - EXIT HUP INT TERM
      if [ -n "$sleeper" ]; then kill -TERM "$sleeper" 2>/dev/null || :; wait "$sleeper" 2>/dev/null || :; fi
      if [ -n "$grace" ]; then kill -TERM "$grace" 2>/dev/null || :; wait "$grace" 2>/dev/null || :; fi
    }
    trap timer_cleanup EXIT
    # 后台启动与 $! 登记之间只暂存取消；登记后立即清理，避免遗弃 helper。
    cancel_timer() {
      if [ "$critical" -eq 1 ]; then cancelled=1; else exit 0; fi
    }
    trap cancel_timer HUP INT TERM
    critical=1
    sleep "$budget" >/dev/null 2>&1 & sleeper=$!
    critical=0
    if [ -n "$cancelled" ]; then exit 0; fi
    if wait "$sleeper" 2>/dev/null; then timer_status=0; else timer_status=$?; fi
    sleeper=
    # timer 工具或磁盘失败也须终止本次 CLI，不能撤掉保护后继续等待。
    if ! (: > "$dir/timedout") 2>/dev/null || [ "$timer_status" -ne 0 ]; then
      kill -TERM "$child" 2>/dev/null || :
      kill -KILL "$child" 2>/dev/null || :
      exit 1
    fi
    kill -TERM "$child" 2>/dev/null || :
    critical=1
    sleep 1 >/dev/null 2>&1 & grace=$!
    critical=0
    if [ -n "$cancelled" ]; then exit 0; fi
    wait "$grace" 2>/dev/null || :
    grace=
    kill -KILL "$child" 2>/dev/null || :
    exit 1
  ) &
  timer=$!
  if wait "$child" 2>/dev/null; then status=0; else status=$?; fi
  kill -TERM "$timer" 2>/dev/null || :
  if wait "$timer" 2>/dev/null; then timer_status=0; else timer_status=$?; fi
  timer= child=
  # 极快 CLI 可在 timer 安装 trap 前完成；本次 TERM 的默认退出也属正常回收。
  case "$timer_status" in 0|143) ;; *) exit 1;; esac
  [ "$status" -eq 0 ] && [ ! -e "$dir/timedout" ] || exit 1
  out_bytes=$(wc -c 2>/dev/null < "$dir/stdout") || exit 1
  err_bytes=$(wc -c 2>/dev/null < "$dir/stderr") || exit 1
  # Linux 的 512 block 可能是 256 KiB，macOS 则可能是 512 KiB；
  # 统一以更保守的 256 KiB 作为成功阈值，达到阈值亦视作超限。
  [ "$out_bytes" -lt 262144 ] && [ "$err_bytes" -lt 262144 ] || exit 1
  cat -- "$dir/stdout" 2>/dev/null
)
lf_docker_endpoint() {
  [ -z "${LF_DOCKER_ENDPOINT:-}" ] || return 0
  local endpoint
  if [ -n "${DOCKER_CONTEXT:-}" ]; then
    case "$DOCKER_CONTEXT" in *[!a-zA-Z0-9_.-]*|-*) return 1;; esac
    endpoint=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
      docker context inspect --format '{{(index .Endpoints "docker").Host}}' "$DOCKER_CONTEXT" 2>/dev/null) || return 1
  elif [ -n "${DOCKER_HOST:-}" ]; then endpoint=$DOCKER_HOST
  else
    endpoint=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
      docker context inspect --format '{{(index .Endpoints "docker").Host}}' 2>/dev/null) || return 1
  fi
  case "$endpoint" in unix:///*) ;; *) return 1;; esac
  case "$endpoint" in unix:///|unix:////*|*[[:space:][:cntrl:]]*) return 1;; esac
  LF_DOCKER_ENDPOINT=$endpoint
}
lf_docker_server_kind() {
  local endpoint="$1" components podman_count engine_count docker_name_count unknown_engine_count invalid_count
  components=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" LC_ALL=C \
    docker --host "$endpoint" version --format '{{range .Server.Components}}{{.Name}}{{"\n"}}{{end}}' 2>/dev/null) || return 1
  [ -n "$components" ] || return 1
  if podman_count=$(printf '%s\n' "$components" | grep -cx 'Podman Engine'); then :; else [ "$?" -eq 1 ] || return 1; podman_count=0; fi
  if engine_count=$(printf '%s\n' "$components" | grep -cx 'Engine'); then :; else [ "$?" -eq 1 ] || return 1; engine_count=0; fi
  if docker_name_count=$(printf '%s\n' "$components" | grep -cx 'Docker Engine'); then :; else [ "$?" -eq 1 ] || return 1; docker_name_count=0; fi
  unknown_engine_count=$(printf '%s\n' "$components" | awk '$0 ~ /Engine$/ && $0 !~ /^(Engine|Podman Engine|Docker Engine)$/ { count++ } END { print count + 0 }') || return 1
  invalid_count=$(printf '%s\n' "$components" | awk '$0 == "" || $0 ~ /[[:cntrl:]]/ { count++ } END { print count + 0 }') || return 1
  [ "$unknown_engine_count" -eq 0 ] && [ "$invalid_count" -eq 0 ] || return 1
  case "$podman_count:$engine_count:$docker_name_count" in
    1:0:0|1:1:0) printf '%s\n' podman;;
    0:1:0|0:0:1) printf '%s\n' docker;;
    *) return 1;;
  esac
}
lf_podman_map_valid() {
  local value="$1" rootless="$2" rest entry container host size old_ifs
  case "$value" in
    -) [ "$rootless" != true ] && return 0; return 1;;
    *';') rest=$value;;
    *) return 1;;
  esac
  [ -n "$rest" ] || return 1
  while [ -n "$rest" ]; do
    case "$rest" in *';'*) entry=${rest%%;*}; rest=${rest#*;};; *) return 1;; esac
    [ -n "$entry" ] || return 1
    case "$entry" in *[!0-9,]*|*','*','*','*) return 1;; esac
    old_ifs=$IFS; IFS=,
    set -- $entry
    IFS=$old_ifs
    [ "$#" -eq 3 ] || return 1
    container=$1 host=$2 size=$3
    [ -n "$container" ] && [ -n "$host" ] && [ -n "$size" ] || return 1
    case "$container:$host:$size" in *[!0-9:]*|::*|:*:|'' ) return 1;; esac
    for value in "$container" "$host" "$size"; do
      case "$value" in 0|[1-9]*) ;; *) return 1;; esac
      [ "${#value}" -le 10 ] || return 1
    done
    case "$size" in 0) return 1;; esac
    # Values are bounded to ten decimal digits before awk, where integers
    # through 2**32 remain exactly representable; avoid shell arithmetic.
    awk -v container="$container" -v host="$host" -v size="$size" \
      'BEGIN { exit !((container + size) <= 4294967296 && (host + size) <= 4294967296) }' || return 1
  done
  return 0
}
lf_podman_identity_query() {
  local endpoint="$1" record os arch hostname rootless mappings_present uid_map gid_map driver graph_root run_root volume_path transient version hash_line digest old_ifs value
  local format='{{.Host.OS}}|{{.Host.Arch}}|{{.Host.Hostname}}|{{.Host.Security.Rootless}}|{{if .Host.IDMappings}}present{{else}}missing{{end}}|{{range .Host.IDMappings.UIDMap}}{{.ContainerID}},{{.HostID}},{{.Size}};{{else}}-{{end}}|{{range .Host.IDMappings.GIDMap}}{{.ContainerID}},{{.HostID}},{{.Size}};{{else}}-{{end}}|{{.Store.GraphDriverName}}|{{.Store.GraphRoot}}|{{.Store.RunRoot}}|{{.Store.VolumePath}}|{{.Store.TransientStore}}|{{.Version.Version}}'
  record=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" LC_ALL=C \
    podman --url "$endpoint" info --format "$format" 2>/dev/null) || return 1
  [ "$(printf '%s\n' "$record" | awk 'END { print NR }')" -eq 1 ] || return 1
  IFS='|' read -r os arch hostname rootless mappings_present uid_map gid_map driver graph_root run_root volume_path transient version <<EOF
$record
EOF
  [ -n "$version" ] || return 1
  [ "$os" = linux ] || return 1
  case "$arch" in amd64|x86_64|arm64|aarch64) ;; *) return 1;; esac
  case "$rootless:$transient" in true:true|true:false|false:true|false:false) ;; *) return 1;; esac
  [ "$mappings_present" = present ] || return 1
  for value in "$hostname" "$driver" "$graph_root" "$run_root" "$volume_path" "$version"; do
    [ -n "$value" ] || return 1
  done
  case "$hostname:$driver:$graph_root:$run_root:$volume_path:$version" in *[!A-Za-z0-9._/+:-]*|:*|*:) return 1;; esac
  case "$graph_root:$run_root:$volume_path" in /*:/*:/*) ;; *) return 1;; esac
  printf '%s\n' "$version" | awk 'BEGIN { ok=0 } /^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.-]+)?$/ { ok=1 } END { exit !ok }' || return 1
  lf_podman_map_valid "$uid_map" "$rootless" || return 1
  lf_podman_map_valid "$gid_map" "$rootless" || return 1
  if command -v sha256sum >/dev/null 2>&1; then
    hash_line=$(printf '%s\n' "$record" | sha256sum) || return 1
  elif command -v shasum >/dev/null 2>&1; then
    hash_line=$(printf '%s\n' "$record" | shasum -a 256) || return 1
  else return 1; fi
  IFS=' ' read -r digest value <<EOF
$hash_line
EOF
  [ "$value" = '-' ] || return 1
  case "$digest" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#digest}" -eq 64 ] || return 1
  [ "$hash_line" = "$digest  -" ] || return 1
  printf 'podman:%s\n' "$digest"
}
lf_docker_identity_query() {
  local endpoint="$1" value kind
  # Every identity observation classifies the selected endpoint afresh.
  kind=$(lf_docker_server_kind "$endpoint") || return 1
  case "$kind" in
    podman) value=$(lf_podman_identity_query "$endpoint") || return 1;;
    docker)
      value=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" LC_ALL=C \
        docker --host "$endpoint" info --format '{{.ID}}' 2>/dev/null) || return 1
      lf_valid_engine_id "$value" || return 1;;
    *) return 1;;
  esac
  lf_valid_engine_id "$value" || return 1
  printf '%s\n' "$value"
}
lf_docker_identity_check() {
  local endpoint actual
  lf_docker_endpoint || return 1
  lf_engine_read || return 1
  endpoint=$LF_ENGINE_ENDPOINT
  [ "$LF_DOCKER_ENDPOINT" = "$endpoint" ] || return 1
  actual=$(lf_docker_identity_query "$endpoint") || return 1
  [ "$actual" = "$LF_ENGINE_ID" ]
}
# A read-only phase has one identity check at each boundary. The marker is
# established only here (and cleared at source), never accepted from env.
lf_docker_observe() {
  local observe_rc
  if [ "${LF_DOCKER_OBSERVATION:-}" = active ]; then "$@"; return $?; fi
  lf_docker_identity_check || return 1
  LF_DOCKER_OBSERVATION=active
  if "$@"; then observe_rc=0; else observe_rc=$?; fi
  LF_DOCKER_OBSERVATION=
  lf_docker_identity_check || return 1
  [ "$observe_rc" -eq 0 ]
}
lf_docker_readonly() {
  case "${1:-}:${2:-}:${3:-}" in
    compose:version:|info:--format:*|image:inspect:--format|inspect:--format:*|\
    container:ls:--all|network:ls:--no-trunc|volume:ls:--filter|\
    container:inspect:--format|network:inspect:--format|volume:inspect:--format) return 0;;
  esac
  return 1
}
lf_docker_mutating() {
  case "${1:-}:${2:-}:${3:-}" in
    load:-i:*|container:stop:--time|container:rm:--force|network:rm:*|volume:rm:*|volume:create:--label) return 0;;
  esac
  return 1
}
lf_docker_budget() {
  local budget="$1" output status
  shift
  lf_docker_endpoint || return 1
  if lf_docker_readonly "$@"; then :
  elif lf_docker_mutating "$@"; then
    [ -z "${LF_DOCKER_OBSERVATION:-}" ] && [ -n "${LF_ROOT:-}" ] && [ -d "$LF_ROOT" ] || return 1
  else return 1
  fi
  # An existing installation must never become "first prepare" merely
  # because its binding was removed. Only the pre-install read-only preflight
  # (before ROOT exists) is allowed to reach the daemon without a binding.
  if [ -n "${LF_ROOT:-}" ] && [ -d "$LF_ROOT" ] && [ "${LF_DOCKER_OBSERVATION:-}" != active ]; then
    lf_docker_identity_check || return 1
  fi
  if output=$(lf_docker_run "$budget" env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
    docker --host "$LF_DOCKER_ENDPOINT" "$@"); then status=0; else status=$?; fi
  if [ -n "${LF_ROOT:-}" ] && [ -d "$LF_ROOT" ] && [ "${LF_DOCKER_OBSERVATION:-}" != active ]; then
    lf_docker_identity_check || return 1
  fi
  [ "$status" -eq 0 ] || return 1
  [ -z "$output" ] || printf '%s\n' "$output"
}
lf_docker() { lf_docker_budget 30 "$@"; }
lf_docker_preflight() {
  command -v docker >/dev/null 2>&1 || return 1
  lf_docker_endpoint || return 1
  if [ -n "${LF_ROOT:-}" ] && [ -d "$LF_ROOT" ]; then
    lf_docker_observe lf_docker_preflight_observe
  else
    lf_docker_preflight_observe
  fi
}
lf_docker_preflight_observe() {
  lf_docker compose version >/dev/null 2>&1 || return 1
  local os arch
  os=$(lf_docker info --format '{{.OSType}}' 2>/dev/null) || return 1
  arch=$(lf_docker info --format '{{.Architecture}}' 2>/dev/null) || return 1
  [ "$os" = linux ] || return 1
  case "$arch" in x86_64|amd64) LF_HOST_PLATFORM=linux/amd64;; aarch64|arm64) LF_HOST_PLATFORM=linux/arm64;; *) return 1;; esac
  return 0
}
# Validate secret inputs without ever loading their contents into shell state.
lf_private_secret_check() {
  local file="$1" mode bytes
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  mode=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file" 2>/dev/null) || return 1
  [ "$mode" = 600 ] || return 1
  bytes=$(wc -c < "$file" 2>/dev/null) || return 1
  bytes=$(printf '%s' "$bytes" | tr -d ' ') || return 1
  [ "$bytes" = 64 ] || return 1
  LC_ALL=C grep -Eqx '[0-9a-f]{64}' "$file"
}
lf_private_sources_check() {
  local dir="$LF_ROOT/releases/$LF_CURRENT_KEY" file
  [ -d "$LF_ROOT" ] && [ ! -L "$LF_ROOT" ] || return 1
  [ -d "$LF_ROOT/releases" ] && [ ! -L "$LF_ROOT/releases" ] || return 1
  [ -d "$dir" ] && [ ! -L "$dir" ] || return 1
  lf_private_secret_check "$dir/postgres-password" || return 1
  lf_private_secret_check "$dir/app-password" || return 1
  case "$LF_DATASET_BYTES" in ''|*[!0-9]*|0|0*) return 1;; esac
  [ "${#LF_DATASET_BYTES}" -le 10 ] && [ "$LF_DATASET_BYTES" -le 3221225472 ] || return 1
  file="$LF_RELEASE_ROOT/$LF_DATASET_PATH"
  lf_check_file "$LF_DATASET_PATH" "$LF_DATASET_BYTES" "$LF_DATASET_SHA256" || return 1
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
}
lf_transfer_journal_check() {
  local file="${1:-$LF_ROOT/releases/$LF_CURRENT_KEY/.private-file-transfer}" mode bytes digest volume uuid extra schema installation release uuid_value s1 s2 s3 s4 s5 s6
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  [ "$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file" 2>/dev/null)" = 600 ] || return 1
  [ "$(stat -c '%h' "$file" 2>/dev/null || stat -f '%l' "$file" 2>/dev/null)" = 1 ] || return 1
  [ "$(wc -c < "$file" | tr -d ' ')" -le 1024 ] || return 1
  {
    IFS= read -r schema || return 1
    IFS= read -r installation || return 1
    IFS= read -r release || return 1
    IFS= read -r volume || return 1
    IFS= read -r mode || return 1
    IFS= read -r uuid || return 1
    IFS= read -r bytes || return 1
    IFS= read -r digest || return 1
    if IFS= read -r extra; then return 1; fi
  } < "$file"
  [ "$schema" = schema=lexiflow-private-transfer-v1 ] || return 1
  [ "$installation" = "installation=$LF_INSTALL_ID" ] || return 1
  [ "$release" = "release=$LF_CURRENT_KEY" ] || return 1
  case "$mode:$volume" in
    mode=pg64hex:volume="${LF_PROJECT}_postgres-secret"|mode=app64hex:volume="${LF_PROJECT}_app-secret"|mode=zip:volume="${LF_PROJECT}_dataset") ;;
    *) return 1;;
  esac
  case "$uuid" in uuid=????????-????-????-????-????????????) ;; *) return 1;; esac
  uuid_value=${uuid#uuid=}
  IFS=- read -r s1 s2 s3 s4 s5 s6 <<EOF_TRANSFER_UUID
$uuid_value
EOF_TRANSFER_UUID
  [ -z "$s6" ] || return 1
  for extra in "$s1" "$s2" "$s3" "$s4" "$s5"; do
    case "$extra" in *[!0123456789abcdef]*|'') return 1;; esac
  done
  case "$bytes" in bytes=*) bytes=${bytes#bytes=};; *) return 1;; esac
  case "$bytes" in ''|*[!0-9]*|0|0*) return 1;; esac
  case "$digest" in sha256=*) digest=${digest#sha256=};; *) return 1;; esac
  case "$digest" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#digest}" -eq 64 ] || return 1
  LF_TRANSFER_JOURNAL_VOLUME=${volume#volume=}
  LF_TRANSFER_JOURNAL_MODE=${mode#mode=}
  LF_TRANSFER_JOURNAL_UUID=${uuid#uuid=}
  LF_TRANSFER_JOURNAL_BYTES=$bytes
  LF_TRANSFER_JOURNAL_SHA256=$digest
}
lf_transfer_journal_begin() {
  local mode="$1" volume="$2" bytes="$3" digest="$4" file="$LF_ROOT/releases/$LF_CURRENT_KEY/.private-file-transfer" existing_uuid raw tmp
  if [ -e "$file" ] || [ -L "$file" ]; then
    [ ! -L "$file" ] || return 1
    lf_transfer_journal_check || return 1
    [ "$LF_TRANSFER_JOURNAL_VOLUME" = "$volume" ] && [ "$LF_TRANSFER_JOURNAL_MODE" = "$mode" ] && \
      [ "$LF_TRANSFER_JOURNAL_BYTES" = "$bytes" ] && [ "$LF_TRANSFER_JOURNAL_SHA256" = "$digest" ] || return 1
    LF_TRANSFER_UUID=$LF_TRANSFER_JOURNAL_UUID
    return 0
  fi
  raw=$(lf_random_hex 16) || return 1
  case "$raw" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#raw}" -eq 32 ] || return 1
  LF_TRANSFER_UUID=$(printf '%s-%s-%s-%s-%s' "$(printf '%s' "$raw" | cut -c1-8)" "$(printf '%s' "$raw" | cut -c9-12)" "$(printf '%s' "$raw" | cut -c13-16)" "$(printf '%s' "$raw" | cut -c17-20)" "$(printf '%s' "$raw" | cut -c21-32)")
  tmp="$LF_ROOT/releases/$LF_CURRENT_KEY/.private-file-transfer-$LF_LOCK_TOKEN"
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  (umask 077; set -C; printf 'schema=lexiflow-private-transfer-v1\ninstallation=%s\nrelease=%s\nvolume=%s\nmode=%s\nuuid=%s\nbytes=%s\nsha256=%s\n' \
    "$LF_INSTALL_ID" "$LF_CURRENT_KEY" "$volume" "$mode" "$LF_TRANSFER_UUID" "$bytes" "$digest" > "$tmp") 2>/dev/null || return 1
  [ "$(stat -c '%a' "$tmp" 2>/dev/null || stat -f '%Lp' "$tmp" 2>/dev/null)" = 600 ] || { rm -f "$tmp"; return 1; }
  lf_lock_check || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
  lf_transfer_journal_check
}
lf_transfer_journal_remove() {
  local file="$LF_ROOT/releases/$LF_CURRENT_KEY/.private-file-transfer"
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  lf_transfer_journal_check || return 1
  lf_lock_check && lf_transfer_journal_check || return 1
  rm "$file" || return 1
  [ ! -e "$file" ] && [ ! -L "$file" ]
}
lf_compose() (
  set +x
  lf_lock_check && lf_docker_endpoint || exit 1
  case "$LF_ROOT:$LF_CURRENT_KEY" in /*:????????????????????????????????????????????????????????????????) ;; *) exit 1;; esac
  lf_docker_identity_check || exit 1
  if [ "$#" -eq 6 ] && [ "$1" = run ] && [ "$2" = --rm ] && [ "$3" = --no-deps ] && [ "$4" = --name ] && [ "$5" = "${LF_PROJECT}-initialize-1" ] && [ "$6" = initialize ]; then
    budget=600
  else budget=30; fi
  if lf_docker_run "$budget" env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
    "LEXIFLOW_PLATFORM=$LF_PLATFORM" "LEXIFLOW_API_IMAGE=$LF_API_IMAGE" "LEXIFLOW_POSTGRES_IMAGE=$LF_POSTGRES_IMAGE" \
    "LEXIFLOW_DATASET_SHA256=$LF_DATASET_SHA256" "LEXIFLOW_INSTALLATION_ID=$LF_INSTALL_ID" "LEXIFLOW_RELEASE_KEY=$LF_CURRENT_KEY" \
    docker --host "$LF_DOCKER_ENDPOINT" compose --project-name "$LF_PROJECT" --project-directory "$LF_RELEASE_ROOT" \
    --env-file /dev/null -f "$LF_RELEASE_ROOT/$LF_COMPOSE_PATH" "$@" >/dev/null 2>&1; then status=0; else status=$?; fi
  lf_docker_identity_check || exit 1
  [ "$status" -eq 0 ]
)
lf_private_volumes_ensure() (
  set +x
  local volume name listed output
  for volume in postgres-secret app-secret dataset; do
    name="${LF_PROJECT}_$volume"
    lf_operation_preflight || exit 1
    listed=no
    while IFS='|' read -r _ entry extra; do
      [ -n "$_$entry$extra" ] || continue
      [ -z "$extra" ] || exit 1
      if [ "$entry" = "$name" ]; then listed=yes; fi
    done <<EOF_PRIVATE_VOLUME_LIST
$LF_OP_VOLUMES
EOF_PRIVATE_VOLUME_LIST
    if [ "$listed" = no ]; then
      # Only the internal fixed role/name/labels may be created; caller values
      # never select the resource or labels. A subsequent preflight and inspect
      # binds the created volume before any stdin payload is sent.
      lf_operation_preflight || exit 1
      output=$(lf_docker volume create \
        --label "com.docker.compose.project=$LF_PROJECT" \
        --label "lexiflow.installation=$LF_INSTALL_ID" \
        --label "lexiflow.release=$LF_CURRENT_KEY" "$name" 2>/dev/null) || exit 1
      [ "$output" = "$name" ] || exit 1
      lf_operation_preflight || exit 1
      lf_removal_owned volume "$name" "$name" || exit 1
    else
      lf_removal_owned volume "$name" "$name" || exit 1
    fi
  done
  lf_operation_preflight || exit 1
  lf_operation_volume_labels || exit 1
)
lf_private_import() (
  set +x
  local mode="$1" volume="$2" source="$3" bytes="$4" digest="$5" args
  lf_operation_preflight || exit 1
  lf_private_volumes_ensure || exit 1
  lf_transfer_journal_begin "$mode" "$volume" "$bytes" "$digest" || exit 1
  lf_operation_preflight && lf_transfer_journal_check || exit 1
  [ "$LF_TRANSFER_JOURNAL_UUID" = "$LF_TRANSFER_UUID" ] || exit 1
  if [ "$mode" = zip ]; then
    { cat -- "$source"; } |
      lf_docker_run 600 --stdin-fd3 env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
        "LEXIFLOW_PLATFORM=$LF_PLATFORM" "LEXIFLOW_API_IMAGE=$LF_API_IMAGE" "LEXIFLOW_POSTGRES_IMAGE=$LF_POSTGRES_IMAGE" \
        "LEXIFLOW_DATASET_SHA256=$LF_DATASET_SHA256" "LEXIFLOW_INSTALLATION_ID=$LF_INSTALL_ID" "LEXIFLOW_RELEASE_KEY=$LF_CURRENT_KEY" \
        docker --host "$LF_DOCKER_ENDPOINT" compose --project-name "$LF_PROJECT" --project-directory "$LF_RELEASE_ROOT" \
        --env-file /dev/null -f "$LF_RELEASE_ROOT/$LF_COMPOSE_PATH" run --rm --no-deps -T \
        --name "${LF_PROJECT}-private-file-importer" private-file-importer "$mode" "$LF_TRANSFER_UUID" "$bytes" "$digest" >/dev/null 2>&1 || exit 1
  else
    { printf '%s\n' "$digest"; cat -- "$source"; } |
      lf_docker_run 600 --stdin-fd3 env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
        "LEXIFLOW_PLATFORM=$LF_PLATFORM" "LEXIFLOW_API_IMAGE=$LF_API_IMAGE" "LEXIFLOW_POSTGRES_IMAGE=$LF_POSTGRES_IMAGE" \
        "LEXIFLOW_DATASET_SHA256=$LF_DATASET_SHA256" "LEXIFLOW_INSTALLATION_ID=$LF_INSTALL_ID" "LEXIFLOW_RELEASE_KEY=$LF_CURRENT_KEY" \
        docker --host "$LF_DOCKER_ENDPOINT" compose --project-name "$LF_PROJECT" --project-directory "$LF_RELEASE_ROOT" \
        --env-file /dev/null -f "$LF_RELEASE_ROOT/$LF_COMPOSE_PATH" run --rm --no-deps -T \
        --name "${LF_PROJECT}-private-file-importer" private-file-importer "$mode" "$LF_TRANSFER_UUID" >/dev/null 2>&1 || exit 1
  fi
  lf_operation_preflight || exit 1
  lf_operation_absent "${LF_PROJECT}-private-file-importer" || exit 1
  lf_operation_volume_labels || exit 1
  lf_transfer_journal_remove || exit 1
)
lf_private_files_import() {
  local dir="$LF_ROOT/releases/$LF_CURRENT_KEY" dataset="$LF_RELEASE_ROOT/$LF_DATASET_PATH" pg_hash app_hash journal
  lf_private_sources_check || return 1
  pg_hash=$(lf_sha256 "$dir/postgres-password") || return 1
  app_hash=$(lf_sha256 "$dir/app-password") || return 1
  case "$pg_hash:$app_hash" in *[!0123456789abcdef:]*|:*|*:) return 1;; esac
  [ "${#pg_hash}" -eq 64 ] && [ "${#app_hash}" -eq 64 ] || return 1
  journal="$dir/.private-file-transfer"
  if [ -e "$journal" ] || [ -L "$journal" ]; then
    # Replay the authenticated interrupted mode before starting the fixed
    # sequence. A different source, owner or unknown journal is never erased.
    lf_transfer_journal_check "$journal" || return 1
    case "$LF_TRANSFER_JOURNAL_MODE:$LF_TRANSFER_JOURNAL_VOLUME:$LF_TRANSFER_JOURNAL_BYTES:$LF_TRANSFER_JOURNAL_SHA256" in
      "pg64hex:${LF_PROJECT}_postgres-secret:64:$pg_hash")
        lf_private_import pg64hex "${LF_PROJECT}_postgres-secret" "$dir/postgres-password" 64 "$pg_hash" || return 1 ;;
      "app64hex:${LF_PROJECT}_app-secret:64:$app_hash")
        lf_private_import app64hex "${LF_PROJECT}_app-secret" "$dir/app-password" 64 "$app_hash" || return 1 ;;
      "zip:${LF_PROJECT}_dataset:$LF_DATASET_BYTES:$LF_DATASET_SHA256")
        lf_private_import zip "${LF_PROJECT}_dataset" "$dataset" "$LF_DATASET_BYTES" "$LF_DATASET_SHA256" || return 1 ;;
      *) return 1 ;;
    esac
  fi
  lf_private_import pg64hex "${LF_PROJECT}_postgres-secret" "$dir/postgres-password" 64 "$pg_hash" || return 1
  lf_private_import app64hex "${LF_PROJECT}_app-secret" "$dir/app-password" 64 "$app_hash" || return 1
  lf_private_import zip "${LF_PROJECT}_dataset" "$dataset" "$LF_DATASET_BYTES" "$LF_DATASET_SHA256" || return 1
  lf_operation_preflight && lf_operation_volume_labels
}
lf_private_files_verify() {
  lf_operation_temp_cleanup || return 1
  lf_operation_preflight || return 1
  lf_operation_volume_labels all || return 1
  lf_private_files_import
}
lf_docker_import() {
  local api_identity database_identity architecture
  lf_docker_preflight || return 1
  [ "$LF_HOST_PLATFORM" = "$LF_PLATFORM" ] || return 1
  case "$LF_PLATFORM" in linux/amd64) architecture=amd64;; linux/arm64) architecture=arm64;; *) return 1;; esac
  lf_docker load -i "$LF_RELEASE_ROOT/$LF_API_ARCHIVE" >/dev/null 2>&1 || return 1
  lf_docker load -i "$LF_RELEASE_ROOT/$LF_POSTGRES_ARCHIVE" >/dev/null 2>&1 || return 1
  api_identity=$(lf_docker image inspect --format '{{.Id}}|{{.Os}}|{{.Architecture}}' "$LF_API_IMAGE" 2>/dev/null) || return 1
  database_identity=$(lf_docker image inspect --format '{{.Id}}|{{.Os}}|{{.Architecture}}' "$LF_POSTGRES_IMAGE" 2>/dev/null) || return 1
  [ "$api_identity" = "$LF_API_IMAGE|linux|$architecture" ] && [ "$database_identity" = "$LF_POSTGRES_IMAGE|linux|$architecture" ] || return 1
}
lf_wait_healthy() {
  local name="$1" i=0 status= id= seen= running=
  while [ "$i" -lt 60 ]; do
    lf_docker_observe lf_wait_healthy_observe "$name" || return 1
    [ "$status" = healthy ] && return 0
    [ "$status" = unhealthy ] && return 1
    sleep 1; i=$((i + 1))
  done
  return 1
}
lf_wait_healthy_observe() {
    local name="$1"
    # Every poll is one bounded read-only phase; its observations are unusable
    # until lf_docker_observe has checked identity again.
    lf_operation_preflight || return 1
    seen=$(lf_operation_container_id "$name") || return 1
    if [ -z "$id" ]; then id=$seen; else [ "$seen" = "$id" ] || return 1; fi
    lf_removal_owned container "$id" "$name" || return 1
    running=$(lf_docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null) || return 1
    [ "$running" = true ] || return 1
    status=$(lf_docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null) || return 1
    return 0
}
lf_operation_preflight() {
  lf_docker_observe lf_operation_preflight_observe
}
lf_operation_preflight_observe() {
  lf_lock_check || return 1
  case "$LF_INSTALL_ID" in *[!0123456789abcdef]*|'') return 1;; esac
  case "$LF_CURRENT_KEY" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#LF_INSTALL_ID}" -eq 32 ] && [ "${#LF_CURRENT_KEY}" -eq 64 ] || return 1
  [ "$LF_PROJECT" = "lf_${LF_INSTALL_ID}_$(printf '%s' "$LF_CURRENT_KEY" | cut -c1-16)" ] || return 1
  lf_docker_endpoint || return 1
  lf_removal_discover container || return 1; LF_OP_CONTAINERS=$LF_REMOVE_ROWS
  lf_removal_discover network || return 1; LF_OP_NETWORKS=$LF_REMOVE_ROWS
  lf_removal_discover volume || return 1; LF_OP_VOLUMES=$LF_REMOVE_ROWS
  lf_lock_check
}
# 只解析已经通过预检的有界集合，不把外部文本工具失败解释成“不存在”。
lf_operation_container_id() {
  local target="$1" id name extra result=
  while IFS='|' read -r id name extra; do
    [ "$name" = "$target" ] || continue
    [ -z "$extra" ] && [ -z "$result" ] || return 1
    result=$id
  done <<EOF_CONTAINER_ID
$LF_OP_CONTAINERS
EOF_CONTAINER_ID
  [ -n "$result" ] || return 1
  printf '%s\n' "$result"
}
lf_operation_absent() {
  local target="$1" id name extra
  while IFS='|' read -r id name extra; do
    [ "$name" != "$target" ] || return 1
  done <<EOF_CONTAINER_ABSENT
$LF_OP_CONTAINERS
EOF_CONTAINER_ABSENT
  return 0
}
lf_operation_temp_cleanup() {
  local rows id name extra running
  lf_operation_preflight || return 1
  rows=$LF_OP_CONTAINERS
  while IFS='|' read -r id name extra; do
    case "$name" in "${LF_PROJECT}-candidate"|"${LF_PROJECT}-initialize-1"|"${LF_PROJECT}-private-file-importer")
      [ -z "$extra" ] || return 1
      lf_operation_preflight || return 1
      lf_removal_owned container "$id" "$name" && lf_lock_check || return 1
      # 停止失败或状态未知时立即终止，不能继续强制删除。
      lf_docker container stop --time 10 "$id" >/dev/null 2>&1 || return 1
      lf_operation_preflight || return 1
      lf_removal_owned container "$id" "$name" || return 1
      running=$(lf_docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null) || return 1
      [ "$running" = false ] && lf_lock_check || return 1
      lf_docker container rm --force "$id" >/dev/null 2>&1 || return 1
      lf_operation_preflight || return 1
      lf_operation_absent "$name" || return 1;;
    esac
  done <<EOF_TEMP
$rows
EOF_TEMP
}
lf_operation_compose() {
  lf_operation_preflight || return 1
  lf_compose "$@" || return 1
  lf_operation_preflight
}
lf_docker_prepare() {
  lf_private_sources_check || return 1
  lf_operation_temp_cleanup || return 1
  lf_operation_preflight || return 1
  lf_docker_import || return 1
  lf_private_files_import || return 1
  lf_operation_compose up -d postgres >/dev/null 2>&1 || return 1
  lf_wait_healthy "${LF_PROJECT}-postgres-1" || return 1
  lf_operation_preflight || return 1
  lf_operation_absent "${LF_PROJECT}-initialize-1" || return 1
  lf_operation_compose run --rm --no-deps --name "${LF_PROJECT}-initialize-1" initialize >/dev/null 2>&1 || return 1
  lf_operation_preflight && lf_operation_volume_labels all || return 1
  lf_operation_preflight || return 1
  lf_operation_absent "${LF_PROJECT}-candidate" || return 1
  lf_operation_compose run --no-deps --detach --name "${LF_PROJECT}-candidate" api >/dev/null 2>&1 || return 1
  lf_wait_healthy "${LF_PROJECT}-candidate" || return 1
  lf_operation_remove_temp "${LF_PROJECT}-candidate"
}
lf_operation_remove_temp() {
  local name="$1" id
  case "$name" in "${LF_PROJECT}-candidate"|"${LF_PROJECT}-initialize-1") ;; *) return 1;; esac
  lf_operation_preflight || return 1
  id=$(lf_operation_container_id "$name") || return 1
  lf_removal_owned container "$id" "$name" && lf_lock_check || return 1
  lf_docker container rm --force "$id" >/dev/null 2>&1 || return 1
  lf_operation_preflight || return 1
  lf_operation_absent "$name"
}
lf_docker_start() {
  lf_private_sources_check || return 1
  lf_operation_temp_cleanup || return 1
  lf_operation_preflight || return 1
  lf_docker_import || return 1
  lf_operation_volume_labels all || return 1
  lf_private_files_import || return 1
  # 资料在 prepare 中完成；重启不得沿 depends_on 再次运行 initialize。
  lf_operation_compose up -d --no-deps postgres >/dev/null 2>&1 || return 1
  lf_wait_healthy "${LF_PROJECT}-postgres-1" || return 1
  lf_operation_compose up -d --no-deps api >/dev/null 2>&1 || return 1
  lf_wait_healthy "${LF_PROJECT}-api-1"
}
lf_docker_stop() {
  local rows id name extra running
  lf_operation_preflight || return 1
  rows=$LF_OP_CONTAINERS
  while IFS='|' read -r id name extra; do
    [ -n "$id$name$extra" ] || continue
    [ -z "$extra" ] || return 1
    lf_operation_preflight || return 1
    lf_removal_owned container "$id" "$name" && lf_lock_check || return 1
    lf_docker container stop --time 10 "$id" >/dev/null 2>&1 || return 1
    running=$(lf_docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null) || return 1
    [ "$running" = false ] || return 1
  done <<EOF_STOP
$rows
EOF_STOP
  lf_operation_preflight || return 1
  while IFS='|' read -r id name extra; do
    [ -n "$id$name$extra" ] || continue
    [ -z "$extra" ] || return 1
    printf '%s\n' "$rows" | grep -Fqx -- "$id|$name" >/dev/null 2>&1 || return 1
    running=$(lf_docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null) || return 1
    [ "$running" = false ] || return 1
  done <<EOF_STOP_CHECK
$LF_OP_CONTAINERS
EOF_STOP_CHECK
  lf_lock_check
}
# 发现集合同时包含项目标签和同名资源，避免无标签名称碰撞被忽略。
lf_removal_discover() {
  lf_docker_observe lf_removal_discover_observe "$@"
}
lf_removal_discover_observe() {
  local kind="$1" rows format limit
  case "$kind" in
    container) format='{{.ID}}|{{.Names}}'; limit=5;;
    network) format='{{.ID}}|{{.Name}}'; limit=1;;
    volume) format='{{.Name}}|{{.Name}}'; limit=5;;
    *) return 1;;
  esac
  rows=$(
    for filter in "name=$LF_PROJECT" "label=com.docker.compose.project=$LF_PROJECT"; do
      if [ "$kind" = container ]; then
        lf_docker container ls --all --no-trunc --filter "$filter" --format "$format" 2>/dev/null || exit 1
      elif [ "$kind" = network ]; then
        lf_docker network ls --no-trunc --filter "$filter" --format "$format" 2>/dev/null || exit 1
      else
        lf_docker volume ls --filter "$filter" --format "$format" 2>/dev/null || exit 1
      fi
    done
  ) || return 1
  [ "${#rows}" -le 8192 ] || return 1
  LF_REMOVE_ROWS=$(printf '%s\n' "$rows" | LC_ALL=C sort -u) || return 1
  local id name extra count=0
  while IFS='|' read -r id name extra; do
    [ -n "$id$name$extra" ] || continue
    [ -z "$extra" ] || return 1
    count=$((count + 1)); [ "$count" -le "$limit" ] || return 1
    lf_removal_owned "$kind" "$id" "$name" || return 1
  done <<EOF_ROWS
$LF_REMOVE_ROWS
EOF_ROWS
}
lf_removal_owned() {
  local kind="$1" id="$2" name="$3" expected actual format
  case "$kind:$name" in
    "container:${LF_PROJECT}-api-1"|"container:${LF_PROJECT}-postgres-1"|"container:${LF_PROJECT}-initialize-1"|"container:${LF_PROJECT}-candidate"|"container:${LF_PROJECT}-private-file-importer") ;;
    "network:${LF_PROJECT}_private") ;;
    "volume:${LF_PROJECT}_pgdata"|"volume:${LF_PROJECT}_initialization-work"|"volume:${LF_PROJECT}_postgres-secret"|"volume:${LF_PROJECT}_app-secret"|"volume:${LF_PROJECT}_dataset") ;;
    *) return 1;;
  esac
  if [ "$kind" = volume ]; then [ "$id" = "$name" ] || return 1
  else
    case "$id" in *[!0123456789abcdef]*|'') return 1;; esac
    [ "${#id}" -eq 64 ] || return 1
  fi
  case "$kind" in
    container) format='{{.Id}}|{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "lexiflow.installation"}}|{{index .Config.Labels "lexiflow.release"}}'; expected="$id|/$name|$LF_PROJECT|$LF_INSTALL_ID|$LF_CURRENT_KEY";;
    network) format='{{.Id}}|{{.Name}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "lexiflow.installation"}}|{{index .Labels "lexiflow.release"}}'; expected="$id|$name|$LF_PROJECT|$LF_INSTALL_ID|$LF_CURRENT_KEY";;
    volume) format='{{.Name}}|{{.Name}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "lexiflow.installation"}}|{{index .Labels "lexiflow.release"}}'; expected="$id|$name|$LF_PROJECT|$LF_INSTALL_ID|$LF_CURRENT_KEY";;
  esac
  actual=$(lf_docker "$kind" inspect --format "$format" "$id" 2>/dev/null) || return 1
  [ "$actual" = "$expected" ]
}
lf_operation_volume_labels() {
  local volume name id entry extra found volumes
  if [ "${1:-}" = all ]; then volumes='pgdata initialization-work postgres-secret app-secret dataset'
  else volumes='postgres-secret app-secret dataset'; fi
  for volume in $volumes; do
    name="${LF_PROJECT}_$volume"; found=
    while IFS='|' read -r id entry extra; do
      [ -n "$id$entry$extra" ] || continue
      [ -z "$extra" ] || return 1
      [ "$entry" = "$name" ] || continue
      [ -z "$found" ] || return 1
      lf_removal_owned volume "$id" "$entry" || return 1
      found=yes
    done <<EOF_PRIVATE_VOLUMES
$LF_OP_VOLUMES
EOF_PRIVATE_VOLUMES
    [ "$found" = yes ] || return 1
  done
}
lf_removal_apply() {
  local kind="$1" rows="$2" id name extra
  while IFS='|' read -r id name extra; do
    [ -n "$id$name$extra" ] || continue
    lf_lock_check && lf_removal_owned "$kind" "$id" "$name" || return 1
    case "$kind" in
      container) lf_docker container rm --force "$id" >/dev/null 2>&1 || return 1;;
      network) lf_docker network rm "$id" >/dev/null 2>&1 || return 1;;
      volume) lf_docker volume rm "$id" >/dev/null 2>&1 || return 1;;
      *) return 1;;
    esac
  done <<EOF_ROWS
$rows
EOF_ROWS
}
lf_docker_remove() {
  lf_lock_check || return 1
  lf_docker_endpoint || return 1
  local containers networks volumes
  case "$LF_INSTALL_ID" in *[!0123456789abcdef]*|'') return 1;; esac
  case "$LF_CURRENT_KEY" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#LF_INSTALL_ID}" -eq 32 ] && [ "${#LF_CURRENT_KEY}" -eq 64 ] || return 1
  [ "$LF_PROJECT" = "lf_${LF_INSTALL_ID}_$(printf '%s' "$LF_CURRENT_KEY" | cut -c1-16)" ] || return 1
  # 在任何破坏操作之前检查全部资源；发现失败不等同于资源不存在。
  lf_docker_observe lf_docker_remove_initial || return 1
  lf_removal_apply container "$containers" || return 1
  lf_removal_apply network "$networks" || return 1
  lf_removal_apply volume "$volumes" || return 1
  # 删除过程中新增或遗留的对象不能被当作完整清理。
  lf_docker_observe lf_docker_remove_final || return 1
  lf_lock_check
}
lf_docker_remove_initial() {
  lf_removal_discover container || return 1; containers=$LF_REMOVE_ROWS
  lf_removal_discover network || return 1; networks=$LF_REMOVE_ROWS
  lf_removal_discover volume || return 1; volumes=$LF_REMOVE_ROWS
}
lf_docker_remove_final() {
  local kind
  for kind in container network volume; do
    lf_removal_discover "$kind" || return 1
    [ -z "$LF_REMOVE_ROWS" ] || return 1
  done
}
