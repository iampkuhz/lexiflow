lf_usage() { printf '%s\n' 'LF_ARGUMENTS_INVALID' >&2; exit 64; }
lf_fixed_fail() { printf '%s\n' "$1" >&2; exit 1; }
lf_abs_root() {
  local path rest part current
  path=$1
  case "$path" in /*) ;; *) return 1;; esac
  [ "$path" != / ] && [ -n "$path" ] || return 1
  case "$path" in *//*|*/./*|*/../*|*/.|*/..) return 1;; esac
  case "$path" in *'
'*|*"$(printf '\r')"*|*"$(printf '\t')"*) return 1;; esac
  # Reject all remaining ASCII control bytes, not only the usual line delimiters.
  printf '%s' "$path" | LC_ALL=C grep '[[:cntrl:]]' >/dev/null 2>&1 && return 1
  rest=${path#/}; current=
  while [ -n "$rest" ]; do
    case "$rest" in */*) part=${rest%%/*}; rest=${rest#*/};; *) part=$rest; rest=;; esac
    [ -n "$part" ] || return 1
    current="$current/$part"
    [ ! -L "$current" ] || return 1
  done
  if [ -e "$path" ]; then [ -d "$path" ] && [ ! -L "$path" ] || return 1; fi
  LF_ROOT=$path
}
lf_select_runtime() {
  lf_verify_release || return 1
  lf_docker_endpoint || return 1
  if [ "${1:-}" = existing ]; then
    [ -e "$LF_ROOT/engine" ] || [ -L "$LF_ROOT/engine" ] || return 1
    lf_docker_identity_check || return 1
  fi
  lf_docker_preflight || return 1
  lf_select_platform "$LF_HOST_PLATFORM" || return 1
}
# Every transition commit rechecks the installed engine; in particular a
# successful stable journal cannot be published from an earlier observation.
lf_bound_state_write() {
  lf_docker_identity_check || return 1
  lf_state_write "$@"
}
lf_set_release() {
  LF_RELEASE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || return 1
  LF_CURRENT_KEY=$LF_RELEASE_KEY
  [ -z "${LF_INSTALL_ID:-}" ] || LF_PROJECT="lf_${LF_INSTALL_ID}_$(printf '%s' "$LF_CURRENT_KEY" | cut -c1-16)"
  LF_COMPOSE_PATH=${LF_COMPOSE_PATH:-compose.yaml}
}
lf_make_record() {
  local dir="$LF_ROOT/releases/$LF_RELEASE_KEY" entry hash tmp value secret result
  lf_lock_check || return 1
  lf_safe_private_tree || return 1
  lf_valid_key "$LF_RELEASE_KEY" && lf_valid_token "$LF_INSTALL_ID" || return 1
  [ -d "$LF_ROOT/releases" ] && [ ! -L "$LF_ROOT/releases" ] || return 1
  lf_safe_absolute_file "$0" || return 1
  entry=$0
  lf_safe_absolute_file "$entry" || return 1
  hash=$(lf_sha "$entry") || return 1
  case "$LF_PLATFORM" in linux/amd64|linux/arm64) ;; *) return 1;; esac
  [ "$LF_PROJECT" = "lf_${LF_INSTALL_ID}_$(printf '%s' "$LF_RELEASE_KEY" | cut -c1-16)" ] || return 1
  [ ! -L "$dir" ] || return 1
  if [ ! -e "$dir" ]; then (umask 077; mkdir "$dir") 2>/dev/null || return 1; fi
  [ -d "$dir" ] && [ ! -L "$dir" ] || return 1
  if [ -e "$dir/record" ] || [ -L "$dir/record" ]; then
    lf_record_verify "$LF_RELEASE_KEY" || return 1
    [ "$LF_DELEGATE_ENTRY" = "$entry" ] && [ "$LF_RECORD_PLATFORM" = "$LF_PLATFORM" ] || return 1
  fi
  tmp="$dir/.record-$LF_LOCK_TOKEN"
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  (umask 077; set -C; printf 'key=%s\nentry=%s\nsha256=%s\nplatform=%s\nproject=%s\n' "$LF_RELEASE_KEY" "$entry" "$hash" "$LF_PLATFORM" "$LF_PROJECT" > "$tmp") 2>/dev/null || return 1
  if [ -e "$dir/record" ] || [ -L "$dir/record" ]; then
    result=0; cmp -s "$dir/record" "$tmp" || result=$?
    rm -f "$tmp" || return 1
    [ "$result" -eq 0 ] || return 1
  else
    lf_lock_check || { rm -f "$tmp"; return 1; }
    mv "$tmp" "$dir/record" || { rm -f "$tmp"; return 1; }
  fi
  lf_secret_publish "$dir" || return 1
}
lf_secret_mode_600() {
  local file="$1" mode
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  mode=$(stat -c %a "$file" 2>/dev/null) || mode=$(stat -f %Lp "$file" 2>/dev/null) || return 1
  [ "$mode" = 600 ]
}
lf_secret_file_verify() {
  local file="$1" value bytes
  lf_secret_mode_600 "$file" || return 1
  bytes=$(wc -c 2>/dev/null < "$file") || return 1
  bytes=$(printf '%s' "$bytes" | tr -d ' ') || return 1
  [ "$bytes" = 64 ] || return 1
  value=$(cat "$file" 2>/dev/null) || return 1
  case "$value" in *[!0123456789abcdef]*|'') return 1;; esac
  [ "${#value}" -eq 64 ]
}
lf_secret_context_check() {
  local key="$1" dir="$2"
  lf_valid_key "$key" && [ "$dir" = "$LF_ROOT/releases/$key" ] || return 1
  lf_lock_check && lf_record_verify "$key" 2>/dev/null || return 1
  [ -d "$dir" ] && [ ! -L "$dir" ]
}
lf_secret_pending_clean() {
  local key="$1" dir="$2" secret pending
  lf_secret_context_check "$key" "$dir" || return 1
  for secret in postgres-password app-password; do
    pending="$dir/.$secret.pending"
    if [ -e "$pending" ] || [ -L "$pending" ]; then
      lf_secret_mode_600 "$pending" || return 1
      lf_secret_context_check "$key" "$dir" && lf_secret_mode_600 "$pending" || return 1
      rm "$pending" 2>/dev/null || return 1
    fi
  done
}
lf_secret_write_pending() { printf '%s' "$1"; }
lf_secret_publish() {
  local dir="$1" key="$LF_RELEASE_KEY" secret pending target value
  lf_secret_context_check "$key" "$dir" || return 1
  # 已有秘密不变；在清理暂存文件之前先核验。
  for secret in postgres-password app-password; do
    target="$dir/$secret"
    if [ -e "$target" ] || [ -L "$target" ]; then lf_secret_file_verify "$target" || return 1; fi
  done
  lf_secret_pending_clean "$key" "$dir" || return 1
  for secret in postgres-password app-password; do
    target="$dir/$secret"; pending="$dir/.$secret.pending"
    if [ -e "$target" ] || [ -L "$target" ]; then continue; fi
    lf_secret_context_check "$key" "$dir" || return 1
    value=$(lf_random_hex 32 2>/dev/null) || return 1
    case "$value" in *[!0123456789abcdef]*|'') return 1;; esac
    [ "${#value}" -eq 64 ] || return 1
    [ ! -e "$pending" ] && [ ! -L "$pending" ] || return 1
    (umask 077; set -C; lf_secret_write_pending "$value" > "$pending") 2>/dev/null || return 1
    lf_secret_file_verify "$pending" || return 1
    printf '%s' "$value" | cmp -s - "$pending" || return 1
    lf_secret_context_check "$key" "$dir" && lf_secret_file_verify "$pending" || return 1
    [ ! -e "$target" ] && [ ! -L "$target" ] || return 1
    # 预检拒绝目录目标；原子硬链接不覆盖已有文件，不承诺抵御同用户恶意并发替换。
    ln "$pending" "$target" 2>/dev/null || return 1
    lf_secret_file_verify "$target" || return 1
    printf '%s' "$value" | cmp -s - "$target" || return 1
  done
  lf_secret_context_check "$key" "$dir" || return 1
  lf_secret_file_verify "$dir/postgres-password" && lf_secret_file_verify "$dir/app-password" || return 1
  lf_secret_pending_clean "$key" "$dir"
}
lf_load_release() {
  lf_record_verify "$1" || return 1
  LF_CURRENT_KEY=$1
  LF_RELEASE_ROOT=$(CDPATH= cd -- "$(dirname -- "$LF_DELEGATE_ENTRY")" 2>/dev/null && pwd -P) || return 1
  lf_verify_release || return 1
  LF_PROJECT="lf_${LF_INSTALL_ID}_$(printf '%s' "$1" | cut -c1-16)"
  lf_select_platform "$LF_PLATFORM" || return 1
}
lf_run_prepare() {
  local parent next_active item name daemon_id
  if [ -e "$LF_ROOT" ] || [ -L "$LF_ROOT" ]; then lf_select_runtime existing || return 1
  else lf_select_runtime || return 1; fi
  lf_set_release || return 1
  [ ! -e "$LF_ROOT" ] || lf_state_load || return 1
  if [ ! -e "$LF_ROOT" ]; then
    parent=$(dirname -- "$LF_ROOT"); [ -d "$parent" ] && [ ! -L "$parent" ] || return 1
    lf_owner_init || return 1
  else
    lf_lock_acquire || return 1
  fi
  lf_state_load || return 1
  if [ ! -e "$LF_ROOT/engine" ] && [ ! -L "$LF_ROOT/engine" ]; then
    # Install identity before the first transition journal and before any
    # Docker resource operation; a failed query must leave no resources.
    lf_docker_endpoint || return 1
    daemon_id=$(lf_docker_identity_query "$LF_DOCKER_ENDPOINT") || return 1
    lf_engine_publish "$LF_DOCKER_ENDPOINT" "$daemon_id" || return 1
    lf_docker_preflight || return 1
  else
    lf_docker_identity_check || return 1
  fi
  LF_PROJECT="lf_${LF_INSTALL_ID}_$(printf '%s' "$LF_RELEASE_KEY" | cut -c1-16)"
  case "$LF_PHASE" in
    idle|stopped)
      if [ "$LF_ACTIVE" = "$LF_RELEASE_KEY" ]; then
        lf_delegate "$LF_RELEASE_KEY" verify-record && lf_secrets_verify "$LF_RELEASE_KEY"
        return $?
      fi;;
    preparing|prepared)
      [ "$LF_CANDIDATE" = "$LF_RELEASE_KEY" ] || return 1
      if [ "$LF_PHASE" = prepared ]; then
        lf_delegate "$LF_RELEASE_KEY" verify-record && lf_secrets_verify "$LF_RELEASE_KEY"
        return $?
      fi;;
    *) return 1;;
  esac
  [ "$LF_PREVIOUS" = none ] || return 1
  [ "$LF_CANDIDATE" = none ] || [ "$LF_CANDIDATE" = "$LF_RELEASE_KEY" ] || return 1
  # 未被指针引用的记录和删除暂存目录不能被新的准备操作忽略。
  for item in "$LF_ROOT"/releases/* "$LF_ROOT"/releases/.[!.]* "$LF_ROOT"/releases/..?*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    name=${item##*/}
    [ "$name" = "$LF_ACTIVE" ] || [ "$name" = "$LF_CANDIDATE" ] || [ "$name" = "$LF_RELEASE_KEY" ] || return 1
  done
  next_active=$LF_ACTIVE
  lf_make_record || return 1
  lf_bound_state_write preparing "$next_active" "$LF_PREVIOUS" "$LF_RELEASE_KEY" none none || return 1
  lf_docker_prepare || return 1
  lf_bound_state_write prepared "$next_active" "$LF_PREVIOUS" "$LF_RELEASE_KEY" none none
}
lf_secrets_verify() {
  local key="$1" secret file
  lf_lock_check || return 1
  lf_record_verify "$key" || return 1
  file="$LF_ROOT/releases/$key"
  [ -d "$file" ] && [ ! -L "$file" ] || return 1
  for secret in postgres-password app-password; do
    lf_secret_file_verify "$file/$secret" || return 1
  done
}
lf_delegate() {
  local key="$1" action="$2" rec entry
  lf_lock_check || return 1
  rec=$(lf_record_path "$key"); lf_record_verify "$key" || return 1
  lf_docker_identity_check || return 1
  entry=$(sed -n '2s/^entry=//p' "$rec")
  LF_PLATFORM=$LF_RECORD_PLATFORM
  [ "$entry" = "$0" ] && [ "$LF_RELEASE_KEY" = "$key" ] || { sh "$entry" _project "$LF_ROOT" "$key" "$LF_LOCK_TOKEN" "$action"; return $?; }
  LF_CURRENT_KEY=$key LF_PROJECT="lf_${LF_INSTALL_ID}_$(printf '%s' "$key" | cut -c1-16)"
  LF_RELEASE_ROOT=$(CDPATH= cd -- "$(dirname -- "$entry")" 2>/dev/null && pwd -P) || return 1
  lf_verify_release || return 1
  lf_select_platform "$LF_PLATFORM" || return 1
  case "$action" in verify-record) lf_docker_identity_check;; prepare) lf_docker_prepare;; start) lf_docker_start;; stop) lf_docker_stop;; remove) lf_docker_remove;; *) return 1;; esac
}
lf_call() { "$LF_DELEGATE_ENTRY" _project "$LF_ROOT" "$LF_RELEASE_KEY" "$LF_LOCK_TOKEN" "$1"; }
lf_recovery_required() { printf '%s\n' LF_RECOVERY_REQUIRED >&2; return 1; }
lf_verify_pair() {
  lf_delegate "$1" verify-record || return 1
  [ "$2" = none ] || lf_delegate "$2" verify-record
}
lf_switch_recover() {
  local old="$LF_ACTIVE" key="$LF_CANDIDATE" previous="$LF_PREVIOUS"
  [ "$LF_PHASE" = switching ] && [ "$key" != none ] || return 1
  lf_verify_pair "$key" "$old" || return 1
  lf_delegate "$key" stop || { lf_recovery_required; return 1; }
  if [ "$old" != none ]; then
    lf_delegate "$old" start || { lf_recovery_required; return 1; }
  fi
  if [ "$previous" = "$key" ]; then
    lf_bound_state_write idle "$old" "$previous" none none none || { lf_recovery_required; return 1; }
  else
    lf_bound_state_write prepared "$old" none "$key" none none || { lf_recovery_required; return 1; }
  fi
}
lf_resume_active() {
  local key="$LF_ACTIVE" previous="$LF_PREVIOUS"
  [ "$key" = "$LF_RELEASE_KEY" ] && [ "$key" != none ] || return 1
  lf_delegate "$key" verify-record || return 1
  lf_bound_state_write starting "$key" "$previous" none none none || return 1
  if lf_delegate "$key" start; then
    lf_bound_state_write idle "$key" "$previous" none none none || { lf_recovery_required; return 1; }
    return 0
  fi
  if lf_delegate "$key" stop; then
    lf_bound_state_write stopped "$key" "$previous" none none none || { lf_recovery_required; return 1; }
  else
    lf_recovery_required
  fi
  return 1
}
lf_release_dir_check() {
  local dir="$1" mode="$2" item name
  [ -d "$LF_ROOT/releases" ] && [ ! -L "$LF_ROOT/releases" ] || return 1
  [ -d "$dir" ] && [ ! -L "$dir" ] || return 1
  for item in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    name=${item##*/}
    case "$name" in
      record) [ -f "$item" ] && [ ! -L "$item" ] || return 1;;
      postgres-password|app-password) [ -f "$item" ] && [ ! -L "$item" ] || return 1;;
      .postgres-password.pending|.app-password.pending) lf_secret_mode_600 "$item" || return 1;;
      *) return 1;;
    esac
  done
  if [ "$mode" = record ]; then [ -f "$dir/record" ] && [ ! -L "$dir/record" ] || return 1; fi
}
lf_delete_commit() {
  local key="$1" active="$LF_ACTIVE" previous="$LF_PREVIOUS" candidate="$LF_CANDIDATE" phase="$LF_RESUME"
  [ "$LF_PHASE" = deleting ] && [ "$LF_DELETING" = "$key" ] || return 1
  if [ "$key" = "$candidate" ]; then candidate=none; phase=idle
  elif [ "$key" = "$previous" ]; then previous=none
  elif [ "$key" = "$active" ] && [ "$phase" = stopped ]; then active=none
  else return 1; fi
  lf_bound_state_write "$phase" "$active" "$previous" "$candidate" none none
}
lf_tombstone_clean() {
  local key="$1" dir="$LF_ROOT/releases/.deleted-$1" item
  lf_lock_check && lf_release_dir_check "$dir" cleanup || return 1
  # A nonempty tombstone without its record has no provable owner.
  if [ ! -e "$dir/record" ]; then
    [ -z "$(ls -A "$dir" 2>/dev/null)" ] || return 1
    lf_lock_check && rmdir "$dir" 2>/dev/null; return $?
  fi
  lf_record_verify "$key" tombstone || return 1
  for item in .postgres-password.pending .app-password.pending postgres-password app-password record; do
    [ -e "$dir/$item" ] || [ -L "$dir/$item" ] || continue
    lf_lock_check && lf_record_verify "$key" tombstone && lf_release_dir_check "$dir" cleanup || return 1
    if [ "$item" = .postgres-password.pending ] || [ "$item" = .app-password.pending ]; then
      lf_secret_mode_600 "$dir/$item" || return 1
    else
      [ -f "$dir/$item" ] && [ ! -L "$dir/$item" ] || return 1
    fi
    rm "$dir/$item" 2>/dev/null || return 1
  done
  lf_lock_check && rmdir "$dir" 2>/dev/null
}
lf_delete() {
  local key="$1" dir="$LF_ROOT/releases/$1" tomb="$LF_ROOT/releases/.deleted-$1" phase
  lf_valid_key "$key" || return 1
  case "$LF_PHASE" in switching|starting|stopping) return 1;; esac
  if [ "$LF_PHASE" = deleting ]; then
    [ "$LF_DELETING" = "$key" ] || return 1
  else
    if [ "$key" != "$LF_ACTIVE" ] && [ "$key" != "$LF_PREVIOUS" ] && [ "$key" != "$LF_CANDIDATE" ]; then
      [ ! -e "$dir" ] && [ ! -L "$dir" ] && [ -d "$tomb" ] && [ ! -L "$tomb" ] || { printf '%s\n' LF_NOT_FOUND >&2; return 1; }
      lf_tombstone_clean "$key"; return $?
    fi
    case "$LF_PHASE" in idle|stopped|preparing|prepared) ;; *) return 1;; esac
    [ "$key" != "$LF_ACTIVE" ] || [ "$LF_PHASE" = stopped ] || return 1
    [ "$key" != "$LF_CANDIDATE" ] || { [ "$LF_PHASE" = preparing ] || [ "$LF_PHASE" = prepared ]; } || return 1
    [ ! -e "$tomb" ] && [ ! -L "$tomb" ] || return 1
    lf_release_dir_check "$dir" record && lf_delegate "$key" verify-record || return 1
    phase=$LF_PHASE
    [ "$phase" != preparing ] || phase=prepared
    lf_bound_state_write deleting "$LF_ACTIVE" "$LF_PREVIOUS" "$LF_CANDIDATE" "$key" "$phase" || return 1
  fi
  if [ -e "$dir" ] || [ -L "$dir" ]; then
    [ ! -e "$tomb" ] && [ ! -L "$tomb" ] || return 1
    lf_release_dir_check "$dir" record && lf_delegate "$key" verify-record || return 1
    lf_delegate "$key" remove || return 1
    [ ! -e "$tomb" ] && [ ! -L "$tomb" ] || return 1
    lf_lock_check && mv "$dir" "$tomb" 2>/dev/null || return 1
  else
    [ -d "$tomb" ] && [ ! -L "$tomb" ] || return 1
    lf_release_dir_check "$tomb" record && lf_record_verify "$key" tombstone || return 1
  fi
  lf_delete_commit "$key" || return 1
  lf_tombstone_clean "$key"
}
lf_user_main() {
  local cmd="${1:-}" root="${2:-}" key old new
  case "$cmd" in
    verify) [ "$#" -eq 1 ] || lf_usage; lf_verify_release || lf_fixed_fail LF_VERIFY_FAILED; return 0;;
    delete) [ "$#" -eq 4 ] && [ "$4" = --confirm-delete-data ] || lf_usage; root=$2;;
    unlock) [ "$#" -eq 4 ] && [ "$4" = --confirm-abandoned-lock ] || lf_usage; root=$2;;
    prepare|activate) [ "$#" -eq 2 ] || lf_usage; root=$2;;
    recover|stop|status) [ "$#" -eq 2 ] || lf_usage; root=$2;;
    *) lf_usage;;
  esac
  lf_abs_root "$root" || lf_fixed_fail LF_ROOT_INVALID
  lf_verify_release || lf_fixed_fail LF_VERIFY_FAILED
  # A present installation root must be proved complete before prepare asks
  # Docker for any endpoint or identity, or creates a lock/state transition.
  # Unknown and interrupted roots share this stable, non-destructive diagnosis.
  if [ "$cmd" = prepare ] && { [ -e "$LF_ROOT" ] || [ -L "$LF_ROOT" ]; }; then
    if ! lf_state_load 2>/dev/null || ! lf_engine_read 2>/dev/null; then
      lf_fixed_fail LF_INSTALLATION_UNVERIFIED
    fi
  fi
  if [ "$cmd" = unlock ]; then
    local item
    lf_state_load && lf_valid_token "$3" || return 1
    LF_LOCK_TOKEN=$3
    lf_lock_check || return 1
    for item in "$LF_ROOT"/.lock/* "$LF_ROOT"/.lock/.[!.]* "$LF_ROOT"/.lock/..?*; do
      [ -e "$item" ] || [ -L "$item" ] || continue
      [ "$item" = "$LF_ROOT/.lock/token" ] && [ -f "$item" ] && [ ! -L "$item" ] || return 1
    done
    lf_lock_check && rm "$LF_ROOT/.lock/token" 2>/dev/null && rmdir "$LF_ROOT/.lock" 2>/dev/null
    return $?
  fi
  [ -d "$LF_ROOT" ] && [ ! -L "$LF_ROOT" ] || { [ "$cmd" = prepare ] || return 1; }
  if [ "$cmd" = prepare ]; then lf_run_prepare; return $?; fi
  lf_state_load || lf_fixed_fail LF_STATE_INVALID
  if [ "$cmd" = status ]; then printf '%s %s %s %s\n' "$LF_PHASE" "$LF_ACTIVE" "$LF_PREVIOUS" "$LF_CANDIDATE"; return; fi
  lf_lock_acquire || lf_fixed_fail LF_LOCKED
  lf_state_load || lf_fixed_fail LF_STATE_INVALID
  case "$cmd" in
    activate)
      if [ "$LF_PHASE" = stopped ] || [ "$LF_PHASE" = starting ]; then lf_resume_active; return $?; fi
      key=$LF_CANDIDATE; [ "$key" = "$LF_RELEASE_KEY" ] && [ "$LF_PHASE" = prepared ] || return 1
      old=$LF_ACTIVE
      lf_verify_pair "$key" "$old" || return 1
      lf_bound_state_write switching "$old" none "$key" none none || return 1
      if [ "$old" != none ]; then lf_delegate "$old" stop || { lf_recovery_required; return 1; }; fi
      if lf_delegate "$key" start; then
        lf_bound_state_write idle "$key" "$old" none none none || { lf_recovery_required; return 1; }
      else
        lf_switch_recover || return 1
        return 1
      fi;;
    recover)
      if [ "$LF_PHASE" = switching ]; then
        lf_switch_recover
      elif [ "$LF_PHASE" = idle ] && [ "$LF_PREVIOUS" != none ]; then
        key=$LF_PREVIOUS; old=$LF_ACTIVE; lf_verify_pair "$key" "$old" || return 1
        lf_bound_state_write switching "$old" "$key" "$key" none none || return 1
        if ! lf_delegate "$old" stop; then lf_recovery_required; return 1; fi
        if ! lf_delegate "$key" start; then lf_switch_recover || return 1; return 1; fi
        lf_bound_state_write idle "$key" "$old" none none none || { lf_recovery_required; return 1; }
      elif [ "$LF_PHASE" = starting ] || [ "$LF_PHASE" = stopping ]; then
        lf_delegate "$LF_ACTIVE" verify-record || return 1
        lf_delegate "$LF_ACTIVE" stop || return 1
        lf_bound_state_write stopped "$LF_ACTIVE" "$LF_PREVIOUS" none none none
      elif [ "$LF_PHASE" = prepared ] && [ "$LF_CANDIDATE" != none ] && [ "$LF_PREVIOUS" = none ]; then
        lf_verify_pair "$LF_CANDIDATE" "$LF_ACTIVE"
      else return 1; fi;;
    stop)
      case "$LF_PHASE" in idle|stopped|starting|stopping) ;; *) return 1;; esac
      [ "$LF_ACTIVE" != none ] && lf_delegate "$LF_ACTIVE" verify-record || return 1
      lf_bound_state_write stopping "$LF_ACTIVE" "$LF_PREVIOUS" none none none || return 1
      lf_delegate "$LF_ACTIVE" stop || return 1
      lf_bound_state_write stopped "$LF_ACTIVE" "$LF_PREVIOUS" none none none;;
    delete)
      lf_delete "$3";;
  esac
}
if [ "${1:-}" = _project ]; then
  [ "$#" -eq 5 ] || exit 64
  # 委派入口必须从自身已校验发行初始化身份，不能继承父发行导出的 key。
  lf_verify_release || exit 1
  [ "$3" = "$LF_RELEASE_KEY" ] && lf_valid_key "$3" || exit 1
  LF_ROOT=$2; lf_state_load || exit 1
  LF_LOCK_TOKEN=$4; lf_lock_check || exit 1
  lf_docker_identity_check || exit 1
  lf_delegate "$3" "$5"; exit $?
fi
lf_user_main "$@" || lf_fixed_fail LF_OPERATION_FAILED
