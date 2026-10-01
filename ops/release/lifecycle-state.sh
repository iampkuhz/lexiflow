# 安装私有状态、锁与发行记录；仅由内嵌入口的固定生命周期正文调用。
lf_state_error() { printf '%s\n' "$1" >&2; return 1; }
lf_valid_key() { case "$1" in *[!0123456789abcdef]*|'') return 1;; esac; [ "${#1}" -eq 64 ]; }
lf_valid_token() { case "$1" in *[!0123456789abcdef]*|'') return 1;; esac; [ "${#1}" -eq 32 ]; }
lf_valid_engine_id() {
  local value="$1"
  [ -n "$value" ] && [ "${#value}" -le 128 ] || return 1
  case "$value" in *[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-]*) return 1;; esac
}
lf_random_hex() {
  local raw value
  raw=$(od -An -N"$1" -tx1 /dev/urandom 2>/dev/null) || return 1
  value=$(printf '%s' "$raw" | tr -d ' \n') || return 1
  [ "${#value}" -eq "$(( $1 * 2 ))" ] || return 1
  case "$value" in *[!0123456789abcdef]*|'') return 1;; esac
  printf '%s\n' "$value"
}
lf_state_text() {
  printf 'schema=lexiflow-installation-v1\ninstallation_id=%s\nphase=%s\nactive=%s\nprevious=%s\ncandidate=%s\ndeleting=%s\nresume=%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7"
}
lf_state_valid() {
  local phase="$1" active="$2" previous="$3" candidate="$4" deleting="$5" resume="$6" v
  case "$phase" in idle|preparing|prepared|switching|starting|stopping|stopped|deleting) ;; *) return 1;; esac
  for v in "$active" "$previous" "$candidate"; do [ "$v" = none ] || lf_valid_key "$v" || return 1; done
  [ "$active" = none ] || { [ "$active" != "$previous" ] && [ "$active" != "$candidate" ]; } || return 1
  if [ "$phase" = switching ]; then
    [ "$previous" = none ] || [ "$previous" = "$candidate" ] || return 1
  elif [ "$phase" = deleting ] && [ "$resume" = prepared ]; then
    [ "$previous" = none ] || return 1
  else
    [ "$previous" = none ] || [ "$candidate" = none ] || return 1
  fi
  if [ "$phase" = deleting ]; then
    lf_valid_key "$deleting" || return 1
    case "$resume" in idle|stopped|prepared) ;; *) return 1;; esac
    [ "$deleting" = "$active" ] || [ "$deleting" = "$previous" ] || [ "$deleting" = "$candidate" ] || return 1
    [ "$deleting" != "$active" ] || [ "$resume" = stopped ] || return 1
    lf_state_valid "$resume" "$active" "$previous" "$candidate" none none || return 1
  else
    [ "$deleting" = none ] && [ "$resume" = none ] || return 1
    case "$phase" in
      idle|starting|stopping|stopped) [ "$candidate" = none ] || return 1;;
      preparing|prepared) [ "$candidate" != none ] && [ "$previous" = none ] || return 1;;
      switching) [ "$candidate" != none ] || return 1;;
    esac
    case "$phase" in starting|stopping) [ "$active" != none ] || return 1;; esac
  fi
  return 0
}
lf_state_parse() {
  local file="$1" install="$2" s1 s2 s3 s4 s5 s6 s7 s8 phase active previous candidate deleting resume
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  [ "$(wc -c < "$file" | tr -d ' ')" -le 1024 ] || return 1
  { IFS= read -r s1 && IFS= read -r s2 && IFS= read -r s3 && IFS= read -r s4 && IFS= read -r s5 && IFS= read -r s6 && IFS= read -r s7 && IFS= read -r s8; } < "$file" || return 1
  [ "$s1" = schema=lexiflow-installation-v1 ] && [ "$s2" = "installation_id=$install" ] || return 1
  case "$s3" in phase=*) phase=${s3#phase=};; *) return 1;; esac
  case "$s4" in active=*) active=${s4#active=};; *) return 1;; esac
  case "$s5" in previous=*) previous=${s5#previous=};; *) return 1;; esac
  case "$s6" in candidate=*) candidate=${s6#candidate=};; *) return 1;; esac
  case "$s7" in deleting=*) deleting=${s7#deleting=};; *) return 1;; esac
  case "$s8" in resume=*) resume=${s8#resume=};; *) return 1;; esac
  lf_state_valid "$phase" "$active" "$previous" "$candidate" "$deleting" "$resume" || return 1
  lf_state_text "$install" "$phase" "$active" "$previous" "$candidate" "$deleting" "$resume" | cmp -s - "$file" || return 1
  if [ "${3:-}" != check ]; then
    LF_PHASE=$phase LF_ACTIVE=$active LF_PREVIOUS=$previous LF_CANDIDATE=$candidate LF_DELETING=$deleting LF_RESUME=$resume
  fi
}
lf_safe_absolute_file() {
  local path rest part current
  path=$1
  case "$path" in /*) ;; *) return 1;; esac
  [ "$path" != / ] || return 1
  case "$path" in *//*|*/./*|*/../*|*/.|*/..) return 1;; esac
  case "$path" in *'
'*) return 1;; esac
  printf '%s' "$path" | LC_ALL=C grep '[[:cntrl:]]' >/dev/null 2>&1 && return 1
  rest=${path#/}; current=
  while :; do
    case "$rest" in */*) part=${rest%%/*}; rest=${rest#*/}; more=yes;; *) part=$rest; rest=; more=no;; esac
    [ -n "$part" ] || return 1
    current="$current/$part"
    [ ! -L "$current" ] || return 1
    [ "$more" = yes ] || break
  done
  [ -f "$path" ] && [ ! -L "$path" ]
}
lf_safe_private_tree() {
  local path rest part current
  path=$LF_ROOT
  case "$path" in /*) ;; *) return 1;; esac
  [ "$path" != / ] || return 1
  case "$path" in *//*|*/./*|*/../*|*/.|*/..) return 1;; esac
  case "$path" in *'
'*) return 1;; esac
  printf '%s' "$path" | LC_ALL=C grep '[[:cntrl:]]' >/dev/null 2>&1 && return 1
  rest=${path#/}; current=
  while [ -n "$rest" ]; do
    case "$rest" in */*) part=${rest%%/*}; rest=${rest#*/};; *) part=$rest; rest=;; esac
    [ -n "$part" ] || return 1
    current="$current/$part"
    [ ! -L "$current" ] || return 1
  done
  if [ -e "$path" ]; then [ -d "$path" ] && [ ! -L "$path" ] || return 1; fi
  return 0
}
lf_state_load() {
  local install
  lf_safe_private_tree || return 1
  [ -f "$LF_ROOT/owner" ] && [ ! -L "$LF_ROOT/owner" ] || return 1
  [ "$(wc -c < "$LF_ROOT/owner" 2>/dev/null)" -eq 33 ] || return 1
  IFS= read -r install < "$LF_ROOT/owner" || return 1
  lf_valid_token "$install" || return 1
  printf '%s\n' "$install" | cmp -s - "$LF_ROOT/owner" || return 1
  lf_state_parse "$LF_ROOT/state" "$install" || return 1
  LF_INSTALL_ID=$install
}
lf_state_write() {
  local phase="$1" active="$2" previous="$3" candidate="$4" deleting="$5" resume="$6" tmp
  lf_lock_check && lf_valid_token "$LF_INSTALL_ID" || return 1
  lf_state_valid "$phase" "$active" "$previous" "$candidate" "$deleting" "$resume" || return 1
  [ ! -L "$LF_ROOT/state" ] && { [ ! -e "$LF_ROOT/state" ] || [ -f "$LF_ROOT/state" ]; } || return 1
  tmp="$LF_ROOT/.state-$LF_LOCK_TOKEN"
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  (umask 077; set -C; : > "$tmp") 2>/dev/null || return 1
  if ! lf_state_text "$LF_INSTALL_ID" "$phase" "$active" "$previous" "$candidate" "$deleting" "$resume" > "$tmp" 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    return 1
  fi
  if ! lf_state_parse "$tmp" "$LF_INSTALL_ID" check || ! lf_lock_check || [ -L "$LF_ROOT/state" ] || { [ -e "$LF_ROOT/state" ] && [ ! -f "$LF_ROOT/state" ]; }; then
    [ -f "$tmp" ] && [ ! -L "$tmp" ] && rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$LF_ROOT/state" 2>/dev/null || { [ -f "$tmp" ] && [ ! -L "$tmp" ] && rm -f "$tmp"; return 1; }
  LF_PHASE=$phase LF_ACTIVE=$active LF_PREVIOUS=$previous LF_CANDIDATE=$candidate LF_DELETING=$deleting LF_RESUME=$resume
}
lf_lock_check() { lf_safe_private_tree && lf_valid_token "${LF_LOCK_TOKEN:-}" && [ -d "$LF_ROOT/.lock" ] && [ ! -L "$LF_ROOT/.lock" ] && [ -f "$LF_ROOT/.lock/token" ] && [ ! -L "$LF_ROOT/.lock/token" ] && printf '%s\n' "$LF_LOCK_TOKEN" | cmp -s - "$LF_ROOT/.lock/token"; }
lf_engine_text() { printf 'schema=lexiflow-engine-v1\nendpoint=%s\ndaemon_id=%s\n' "$1" "$2"; }
lf_engine_read() {
  local file="$LF_ROOT/engine" s1 s2 s3 endpoint daemon_id metadata size
  lf_safe_private_tree || return 1
  [ -e "$file" ] && [ ! -L "$file" ] && [ -f "$file" ] || return 1
  # Read all fixed metadata together, without caching across identity checks.
  metadata=$(stat -c '%h:%a:%s' "$file" 2>/dev/null || stat -f '%l:%Lp:%z' "$file" 2>/dev/null) || return 1
  case "$metadata" in 1:600:*) size=${metadata#1:600:};; *) return 1;; esac
  case "$size" in ''|*[!0123456789]*) return 1;; esac
  [ "$size" -le 4096 ] 2>/dev/null || return 1
  { IFS= read -r s1 && IFS= read -r s2 && IFS= read -r s3; } < "$file" || return 1
  [ "$s1" = schema=lexiflow-engine-v1 ] || return 1
  case "$s2" in endpoint=*) endpoint=${s2#endpoint=};; *) return 1;; esac
  case "$s3" in daemon_id=*) daemon_id=${s3#daemon_id=};; *) return 1;; esac
  lf_valid_engine_id "$daemon_id" || return 1
  lf_engine_text "$endpoint" "$daemon_id" | cmp -s - "$file" || return 1
  case "$endpoint" in unix:///*) ;; *) return 1;; esac
  case "$endpoint" in unix:///|unix:////*|*[[:space:][:cntrl:]]*) return 1;; esac
  LF_ENGINE_ENDPOINT=$endpoint LF_ENGINE_ID=$daemon_id
}
lf_engine_publish() {
  local endpoint="$1" daemon_id="$2" tmp
  lf_lock_check && lf_valid_engine_id "$daemon_id" || return 1
  [ ! -e "$LF_ROOT/engine" ] && [ ! -L "$LF_ROOT/engine" ] || return 1
  tmp="$LF_ROOT/.engine-$LF_LOCK_TOKEN"
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  (umask 077; set -C; lf_engine_text "$endpoint" "$daemon_id" > "$tmp") 2>/dev/null || return 1
  chmod 600 "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  if ! { IFS= read -r s1 && IFS= read -r s2 && IFS= read -r s3; } < "$tmp" ||
    [ "$s1" != schema=lexiflow-engine-v1 ] || [ "$s2" != "endpoint=$endpoint" ] || [ "$s3" != "daemon_id=$daemon_id" ] ||
    ! lf_valid_engine_id "$daemon_id" || ! lf_lock_check || [ -e "$LF_ROOT/engine" ] || [ -L "$LF_ROOT/engine" ]; then
    rm -f "$tmp" 2>/dev/null || :; return 1
  fi
  # Hard-link publication is atomic and fails if the binding already exists;
  # unlike mv it cannot replace a concurrently published different identity.
  ln "$tmp" "$LF_ROOT/engine" 2>/dev/null || { rm -f "$tmp"; return 1; }
  rm "$tmp" 2>/dev/null || return 1
  lf_engine_read
}
lf_lock_acquire() {
  lf_safe_private_tree && [ -d "$LF_ROOT" ] && [ ! -L "$LF_ROOT" ] || return 1
  LF_LOCK_TOKEN=$(lf_random_hex 16); lf_valid_token "$LF_LOCK_TOKEN" || return 1
  (umask 077; mkdir "$LF_ROOT/.lock") 2>/dev/null || return 1
  (umask 077; set -C; printf '%s\n' "$LF_LOCK_TOKEN" > "$LF_ROOT/.lock/token") 2>/dev/null || { rmdir "$LF_ROOT/.lock" 2>/dev/null || :; return 1; }
  lf_lock_traps
}
lf_lock_traps() {
  trap 'lf_lock_release' 0
  # 信号终止入口但保留锁；外部中断不证明同步子操作已停止。
  trap 'lf_lock_interrupt 129' HUP
  trap 'lf_lock_interrupt 130' INT
  trap 'lf_lock_interrupt 143' TERM
}
lf_lock_interrupt() {
  local status="$1"
  # 禁用 EXIT 释放及重复信号处理，避免中断路径意外恢复正常解锁。
  trap ':' 0
  trap '' HUP INT TERM
  exit "$status"
}
lf_lock_release() { if lf_lock_check; then rm "$LF_ROOT/.lock/token" && rmdir "$LF_ROOT/.lock" 2>/dev/null || :; fi; }
lf_owner_init() {
  lf_safe_private_tree || return 1
  [ ! -e "$LF_ROOT" ] || return 1
  (umask 077; mkdir "$LF_ROOT") || return 1
  LF_INSTALL_ID=$(lf_random_hex 16) || return 1; lf_valid_token "$LF_INSTALL_ID" || return 1
  (umask 077; printf '%s\n' "$LF_INSTALL_ID" > "$LF_ROOT/owner") || return 1
  mkdir "$LF_ROOT/releases" || return 1
  LF_PHASE=idle LF_ACTIVE=none LF_PREVIOUS=none LF_CANDIDATE=none LF_DELETING=none LF_RESUME=none
  lf_lock_acquire || return 1
  lf_state_write idle none none none none none
}
lf_record_path() { printf '%s/releases/%s/record' "$LF_ROOT" "$1"; }
lf_record_verify() {
  local key="$1" dir rec entrance hash platform project r1 r2 r3 r4 r5
  lf_safe_private_tree && lf_valid_token "$LF_INSTALL_ID" && lf_valid_key "$key" || return 1
  [ -d "$LF_ROOT/releases" ] && [ ! -L "$LF_ROOT/releases" ] || return 1
  case "${2:-ordinary}" in
    ordinary) dir="$LF_ROOT/releases/$key";;
    tombstone) dir="$LF_ROOT/releases/.deleted-$key";;
    *) return 1;;
  esac
  rec="$dir/record"
  [ -d "$dir" ] && [ ! -L "$dir" ] && [ -f "$rec" ] && [ ! -L "$rec" ] || return 1
  [ "$(wc -c < "$rec" | tr -d ' ')" -le 8192 ] || return 1
  { IFS= read -r r1 && IFS= read -r r2 && IFS= read -r r3 && IFS= read -r r4 && IFS= read -r r5; } < "$rec" || return 1
  [ "$r1" = "key=$key" ] || return 1
  case "$r2" in entry=/*) entrance=${r2#entry=};; *) return 1;; esac
  case "$r3" in sha256=*) hash=${r3#sha256=};; *) return 1;; esac
  lf_valid_key "$hash" || return 1
  case "$r4" in platform=linux/amd64|platform=linux/arm64) platform=${r4#platform=};; *) return 1;; esac
  project="lf_${LF_INSTALL_ID}_$(printf '%s' "$key" | cut -c1-16)"
  [ "$r5" = "project=$project" ] || return 1
  printf 'key=%s\nentry=%s\nsha256=%s\nplatform=%s\nproject=%s\n' "$key" "$entrance" "$hash" "$platform" "$project" | cmp -s - "$rec" || return 1
  lf_safe_absolute_file "$entrance" && [ "$(lf_sha "$entrance")" = "$hash" ] || return 1
  LF_RECORD_PLATFORM=$platform LF_DELEGATE_ENTRY=$entrance
}
lf_sha() {
  local output digest
  # 先检查摘要工具本身的退出码，不能让管道末端覆盖读取失败。
  if command -v sha256sum >/dev/null 2>&1; then
    output=$(sha256sum "$1" 2>/dev/null) || return 1
  else
    output=$(shasum -a 256 "$1" 2>/dev/null) || return 1
  fi
  digest=${output%% *}
  lf_valid_key "$digest" || return 1
  printf '%s\n' "$digest"
}
