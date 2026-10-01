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
  # Keep an explicit copy of stdin before launching the asynchronous child. The
  # Docker secret bootstrap uses FD 3; shells may replace background stdin with
  # /dev/null, so relying on fd 0 inheritance is not sufficient.
  exec 3<&0 2>/dev/null || :
  (
    # POSIX Shell 的 -f 单位在目标平台不同；512 在 macOS/Linux 都更保守。
    ulimit -f 512 2>/dev/null || exit 1
    exec 3<&3
    exec "$@" > "$dir/stdout" 2> "$dir/stderr"
  ) &
  child=$!
  (
    sleeper= grace=
    timer_cleanup() {
      trap - EXIT HUP INT TERM
      if [ -n "$sleeper" ]; then kill -TERM "$sleeper" 2>/dev/null || :; wait "$sleeper" 2>/dev/null || :; fi
      if [ -n "$grace" ]; then kill -TERM "$grace" 2>/dev/null || :; wait "$grace" 2>/dev/null || :; fi
    }
    trap timer_cleanup EXIT
    trap 'exit 0' HUP INT TERM
    sleep "$budget" >/dev/null 2>&1 & sleeper=$!
    wait "$sleeper" 2>/dev/null
    timer_status=$?
    sleeper=
    # timer 工具或磁盘失败也须终止本次 CLI，不能撤掉保护后继续等待。
    if ! (: > "$dir/timedout") 2>/dev/null || [ "$timer_status" -ne 0 ]; then
      kill -TERM "$child" 2>/dev/null || :
      kill -KILL "$child" 2>/dev/null || :
      exit 1
    fi
    kill -TERM "$child" 2>/dev/null || :
    sleep 1 >/dev/null 2>&1 & grace=$!
    wait "$grace" 2>/dev/null || :
    grace=
    kill -KILL "$child" 2>/dev/null || :
    exit 1
  ) &
  timer=$!
  wait "$child" 2>/dev/null
  status=$?
  kill -TERM "$timer" 2>/dev/null || :
  wait "$timer" 2>/dev/null
  timer_status=$?
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
lf_docker_identity_query() {
  local endpoint="$1" value
  # This is the sole identity query: a direct bounded runner call avoids
  # recursively entering lf_docker_budget/lf_docker_identity_check.
  value=$(lf_docker_run 30 env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
    docker --host "$endpoint" info --format '{{.ID}}' 2>/dev/null) || return 1
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
    load:-i:*|container:stop:--time|container:rm:--force|network:rm:*|volume:rm:*) return 0;;
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
# 只读取本安装的固定秘密文件，不把值放入进程 argv 或父 shell。
lf_compose_secret() {
  [ "$#" -eq 1 ] && [ -f "$1" ] && [ ! -L "$1" ] || return 1
  LF_SECRET_BYTES=$(wc -c < "$1" 2>/dev/null) || return 1
  [ "$LF_SECRET_BYTES" -eq 64 ] || return 1
  LF_SECRET_MODE=$(stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null) || return 1
  [ "$LF_SECRET_MODE" = 600 ] || return 1
  LF_COMPOSE_SECRET=
  IFS= read -r LF_COMPOSE_SECRET < "$1" || [ -n "$LF_COMPOSE_SECRET" ] || return 1
  case "$LF_COMPOSE_SECRET" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#LF_COMPOSE_SECRET}" -eq 64 ]
}
# This is deliberately static: credentials arrive only on fd 3 and never in
# shell source or argv. The receiver validates both records before execing.
LF_COMPOSE_SECRET_BOOTSTRAP='IFS= read -r p <&3 || exit 1
IFS= read -r a <&3 || exit 1
case "$p" in ???????*) ;; *) exit 1;; esac
case "$p" in *[!0123456789abcdef]*|"") exit 1;; esac
case "$a" in ???????*) ;; *) exit 1;; esac
case "$a" in *[!0123456789abcdef]*|"") exit 1;; esac
[ "${#p}" -eq 64 ] && [ "${#a}" -eq 64 ] || exit 1
extra=
if IFS= read -r extra <&3 || [ -n "$extra" ]; then exit 1; fi
export LEXIFLOW_COMPOSE_POSTGRES_PASSWORD="$p" LEXIFLOW_COMPOSE_APP_PASSWORD="$a"
unset p a extra
# POSIX shells may synthesize bookkeeping variables after env -i. Strip only
# these known shell-generated names with env (no credential-bearing argv).
exec 3<&-
exec env -u PWD -u SHLVL -u __CF_USER_TEXT_ENCODING "$@" </dev/null'
lf_compose() (
  set +x
  lf_lock_check || exit 1
  unset LEXIFLOW_COMPOSE_POSTGRES_PASSWORD LEXIFLOW_COMPOSE_APP_PASSWORD LF_COMPOSE_SECRET postgres_secret app_secret
  lf_docker_endpoint || exit 1
  case "$LF_ROOT" in /*) ;; *) exit 1;; esac
  case "$LF_CURRENT_KEY" in *[!0123456789abcdef]*|'') exit 1;; esac
  [ "${#LF_CURRENT_KEY}" -eq 64 ] || exit 1
  # 拒绝凭据目录任一祖先 symlink，保留宿主 0600 文件权限。
  LF_SECRET_DIR="$LF_ROOT/releases/$LF_CURRENT_KEY"
  LF_SECRET_PARENT=$LF_SECRET_DIR
  while [ "$LF_SECRET_PARENT" != / ]; do
    [ -d "$LF_SECRET_PARENT" ] && [ ! -L "$LF_SECRET_PARENT" ] || exit 1
    LF_SECRET_PARENT=$(dirname -- "$LF_SECRET_PARENT") || exit 1
  done
  lf_compose_secret "$LF_SECRET_DIR/postgres-password" || exit 1
  postgres_secret=$LF_COMPOSE_SECRET
  unset LF_COMPOSE_SECRET
  lf_compose_secret "$LF_SECRET_DIR/app-password" || exit 1
  app_secret=$LF_COMPOSE_SECRET
  unset LF_COMPOSE_SECRET LF_SECRET_BYTES LF_SECRET_MODE
  LF_COMPOSE_PRODUCT_ENV="LEXIFLOW_PLATFORM=$LF_PLATFORM"
  LF_COMPOSE_API_ENV="LEXIFLOW_API_IMAGE=$LF_API_IMAGE"
  LF_COMPOSE_PG_ENV="LEXIFLOW_POSTGRES_IMAGE=$LF_POSTGRES_IMAGE"
  LF_COMPOSE_SHA_ENV="LEXIFLOW_DATASET_SHA256=$LF_DATASET_SHA256"
  LF_COMPOSE_FILE_ENV="LEXIFLOW_DATASET_FILE=$LF_RELEASE_ROOT/$LF_DATASET_PATH"
  LF_COMPOSE_INSTALL_ENV="LEXIFLOW_INSTALLATION_ID=$LF_INSTALL_ID"
  LF_COMPOSE_KEY_ENV="LEXIFLOW_RELEASE_KEY=$LF_CURRENT_KEY"
  LF_COMPOSE_ARGS=""
  # All fixed Compose business operations use the same private-FD bootstrap.
  # The caller's stdin is intentionally not used as a credential transport.
  lf_compose_invoke() {
    budget=$1; shift
    lf_docker_identity_check || return 1
    if { printf '%s\n%s\n' "$postgres_secret" "$app_secret"; } |
      lf_docker_run "$budget" env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=${HOME:-}" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" LC_ALL=C \
        "$LF_COMPOSE_PRODUCT_ENV" "$LF_COMPOSE_API_ENV" "$LF_COMPOSE_PG_ENV" "$LF_COMPOSE_SHA_ENV" "$LF_COMPOSE_FILE_ENV" \
        "$LF_COMPOSE_INSTALL_ENV" "$LF_COMPOSE_KEY_ENV" /bin/sh -c "$LF_COMPOSE_SECRET_BOOTSTRAP" lexiflow-docker \
        docker --host "$LF_DOCKER_ENDPOINT" compose --project-name "$LF_PROJECT" --project-directory "$LF_RELEASE_ROOT" \
        --env-file /dev/null -f "$LF_RELEASE_ROOT/$LF_COMPOSE_PATH" "$@"; then status=0; else status=$?; fi
    lf_docker_identity_check || return 1
    [ "$status" -eq 0 ] || return 1
  }
  if [ "$#" -eq 6 ] && [ "$1" = run ] && [ "$2" = --rm ] && [ "$3" = --no-deps ] && [ "$4" = --name ] && [ "$5" = "${LF_PROJECT}-initialize-1" ] && [ "$6" = initialize ]; then
    lf_compose_invoke 600 "$@"
  else
    lf_compose_invoke 30 "$@"
  fi
)
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
  LF_API_ID=$LF_API_IMAGE LF_DB_ID=$LF_POSTGRES_IMAGE
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
    case "$name" in "${LF_PROJECT}-candidate"|"${LF_PROJECT}-initialize-1")
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
  lf_operation_temp_cleanup || return 1
  lf_operation_preflight || return 1
  lf_docker_import || return 1
  lf_operation_compose up -d postgres >/dev/null 2>&1 || return 1
  lf_wait_healthy "${LF_PROJECT}-postgres-1" || return 1
  lf_operation_preflight || return 1
  lf_operation_absent "${LF_PROJECT}-initialize-1" || return 1
  lf_operation_compose run --rm --no-deps --name "${LF_PROJECT}-initialize-1" initialize >/dev/null 2>&1 || return 1
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
  lf_operation_temp_cleanup || return 1
  lf_operation_preflight || return 1
  lf_docker_import || return 1
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
    container) format='{{.ID}}|{{.Names}}'; limit=4;;
    network) format='{{.ID}}|{{.Name}}'; limit=1;;
    volume) format='{{.Name}}|{{.Name}}'; limit=2;;
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
    "container:${LF_PROJECT}-api-1"|"container:${LF_PROJECT}-postgres-1"|"container:${LF_PROJECT}-initialize-1"|"container:${LF_PROJECT}-candidate") ;;
    "network:${LF_PROJECT}_private") ;;
    "volume:${LF_PROJECT}_pgdata"|"volume:${LF_PROJECT}_initialization-work") ;;
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
