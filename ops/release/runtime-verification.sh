# 静态 POSIX sh 校验器；构建端仅替换每个唯一槽位一次。
lf_error() { printf '%s\n' "$1" >&2; return 1; }

lf_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    lf_hash_output=$(sha256sum 2>/dev/null < "$1") || return 1
  elif command -v shasum >/dev/null 2>&1; then
    lf_hash_output=$(shasum -a 256 2>/dev/null < "$1") || return 1
  else lf_error LF_VERIFY_HASH_TOOL_MISSING; return 1; fi
  lf_hash_value=${lf_hash_output%% *}
  [ ${#lf_hash_value} -eq 64 ] || return 1
  case $lf_hash_value in *[!0123456789abcdef]*) return 1 ;; esac
  [ "$lf_hash_output" = "$lf_hash_value  -" ] || return 1
  printf '%s\n' "$lf_hash_value"
}

# 在 cd -P 隐藏符号链接祖先之前，先核查原始词法路径。
lf_path_has_symlink() {
  lf_remaining=$1
  lf_cursor=
  case $lf_remaining in
    /*) lf_cursor=/; lf_remaining=${lf_remaining#/} ;;
    *) lf_cursor=$(pwd -P) || return 0 ;;
  esac
  while [ -n "$lf_remaining" ]; do
    case $lf_remaining in
      */*) lf_part=${lf_remaining%%/*}; lf_remaining=${lf_remaining#*/} ;;
      *) lf_part=$lf_remaining; lf_remaining= ;;
    esac
    case $lf_part in
      ''|.) continue ;;
      ..) lf_cursor=${lf_cursor%/*}; [ -n "$lf_cursor" ] || lf_cursor=/ ;;
      *) lf_cursor=$lf_cursor/$lf_part; [ ! -L "$lf_cursor" ] || return 0 ;;
    esac
  done
  return 1
}

lf_check_file() {
  lf_rel=$1; lf_size=$2; lf_digest=$3
  lf_path_has_symlink "$LF_RELEASE_ROOT/$lf_rel" && { lf_error LF_VERIFY_SYMLINK; return 1; }
  lf_file=$LF_RELEASE_ROOT/$lf_rel
  [ -f "$lf_file" ] && [ ! -d "$lf_file" ] || { lf_error LF_VERIFY_FILE_MISSING; return 1; }
  lf_actual_size=$(wc -c 2>/dev/null < "$lf_file") || { lf_error LF_VERIFY_FILE_READ; return 1; }
  lf_actual_size=$(printf '%s' "$lf_actual_size" | tr -d '[:space:]') || { lf_error LF_VERIFY_FILE_READ; return 1; }
  [ "$lf_actual_size" = "$lf_size" ] || { lf_error LF_VERIFY_SIZE_MISMATCH; return 1; }
  lf_actual_hash=$(lf_sha256 "$lf_file") || { lf_error LF_VERIFY_HASH_FAILED; return 1; }
  [ "$lf_actual_hash" = "$lf_digest" ] || { lf_error LF_VERIFY_DIGEST_MISMATCH; return 1; }
}

lf_verify_release() {
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    lf_error LF_VERIFY_HASH_TOOL_MISSING; return 1
  fi
  case $0 in
    */*) lf_raw_dir=${0%/*} ;;
    *) lf_raw_dir=. ;;
  esac
  lf_path_has_symlink "$lf_raw_dir" && { lf_error LF_VERIFY_SYMLINK; return 1; }
  LF_RELEASE_ROOT=$(CDPATH= cd -P "$lf_raw_dir" 2>/dev/null && pwd -P) || { lf_error LF_VERIFY_ROOT_INVALID; return 1; }
  LF_SOFTWARE_VERSION=@@SOFTWARE_VERSION@@
  LF_API_CONTRACT=@@API_CONTRACT@@
  LF_SQL_VERSION=@@SQL_VERSION@@
  LF_DATASET_SHA256=@@DATASET_SHA256@@
  LF_DATASET_PATH=@@DATASET_PATH@@
  LF_COMPOSE_PATH=@@COMPOSE_PATH@@
  LF_RELEASE_KEY=@@RELEASE_KEY@@
  LF_MANIFEST_PREFIX=@@MANIFEST_PREFIX@@
  LF_MANIFEST_MIDDLE=@@MANIFEST_MIDDLE@@
  LF_MANIFEST_SUFFIX=@@MANIFEST_SUFFIX@@
  export LF_RELEASE_ROOT LF_RELEASE_KEY LF_SOFTWARE_VERSION LF_API_CONTRACT LF_SQL_VERSION LF_DATASET_SHA256 LF_DATASET_PATH LF_COMPOSE_PATH

  lf_script=$LF_RELEASE_ROOT/lexiflow.sh
  lf_path_has_symlink "$lf_script" && { lf_error LF_VERIFY_SYMLINK; return 1; }
  [ -f "$lf_script" ] || { lf_error LF_VERIFY_FILE_MISSING; return 1; }
  LF_RUNTIME_BYTES=$(wc -c 2>/dev/null < "$lf_script") || { lf_error LF_VERIFY_FILE_READ; return 1; }
  LF_RUNTIME_BYTES=$(printf '%s' "$LF_RUNTIME_BYTES" | tr -d '[:space:]') || { lf_error LF_VERIFY_FILE_READ; return 1; }
  LF_RUNTIME_SHA256=$(lf_sha256 "$lf_script") || { lf_error LF_VERIFY_HASH_FAILED; return 1; }

  lf_manifest=$LF_RELEASE_ROOT/manifest.json
  lf_path_has_symlink "$lf_manifest" && { lf_error LF_VERIFY_SYMLINK; return 1; }
  [ -f "$lf_manifest" ] || { lf_error LF_VERIFY_FILE_MISSING; return 1; }
  printf '%s%s%s%s%s' "$LF_MANIFEST_PREFIX" "$LF_RUNTIME_BYTES" "$LF_MANIFEST_MIDDLE" "$LF_RUNTIME_SHA256" "$LF_MANIFEST_SUFFIX" |
    cmp -s - "$lf_manifest" || { lf_error LF_VERIFY_MANIFEST_MISMATCH; return 1; }

  lf_sum=$LF_RELEASE_ROOT/manifest.json.sha256
  lf_path_has_symlink "$lf_sum" && { lf_error LF_VERIFY_SYMLINK; return 1; }
  [ -f "$lf_sum" ] || { lf_error LF_VERIFY_FILE_MISSING; return 1; }
  lf_manifest_hash=$(lf_sha256 "$lf_manifest") || { lf_error LF_VERIFY_HASH_FAILED; return 1; }
  printf '%s  manifest.json\n' "$lf_manifest_hash" |
    cmp -s - "$lf_sum" || { lf_error LF_VERIFY_CHECKSUM_MISMATCH; return 1; }
  @@ARTIFACT_CHECKS@@
  return 0
}

lf_select_platform() {
  case ${1:-} in
  @@PLATFORM_CASES@@
    *) lf_error LF_PLATFORM_UNSUPPORTED; return 1 ;;
  esac
  export LF_PLATFORM LF_API_IMAGE LF_POSTGRES_IMAGE LF_API_ARCHIVE LF_POSTGRES_ARCHIVE
}
