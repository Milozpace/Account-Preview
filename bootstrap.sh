#!/bin/sh
set -eu
task_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
fail() { printf '%s\n' "$1" >&2; exit 1; }
apply=false
expected_root=''
rebind_from=''
configure=false
components=false
install=false
native=false
option=''
for arg in "$@"; do
  if [ -n "$option" ]; then
    case "$arg" in --*) fail "$option requires an absolute path." ;; esac
    case "$option" in
      --expected-root) [ -z "$expected_root" ] || fail 'Duplicate --expected-root.'; expected_root=$arg ;;
      --rebind-from) [ -z "$rebind_from" ] || fail 'Duplicate --rebind-from.'; rebind_from=$arg ;;
    esac
    option=''; continue
  fi
  case "$arg" in
    --apply) apply=true ;;
    --expected-root|--rebind-from) option=$arg ;;
    --configure-desktop) configure=true ;;
    --repo|--bundle) components=true ;;
    --install-app) install=true ;;
    --initialize-identity|--enable-publish) native=true ;;
  esac
done
[ -z "$option" ] || fail "$option requires an absolute path."
if [ "$apply" = true ] && [ -z "$expected_root" ]; then fail '--apply requires --expected-root ABSOLUTE_PROJECT_ROOT; inspect dry-run root first.'; fi
if [ -n "$expected_root" ]; then
  case "$expected_root" in /*) ;; *) fail '--expected-root must be absolute.' ;; esac
  canonical_expected=$(CDPATH='' cd -- "$expected_root" 2>/dev/null && pwd -P) || fail '--expected-root directory does not exist.'
  [ "$canonical_expected" = "$task_root" ] || fail '--expected-root does not match this launcher root; nothing was configured.'
fi
if [ -n "$rebind_from" ]; then
  case "$rebind_from" in /*) ;; *) fail '--rebind-from must be absolute.' ;; esac
  [ "$apply" = true ] && [ "$configure" = true ] || fail '--rebind-from requires --apply --expected-root and --configure-desktop.'
fi
# Fixed metadata is checked before downloading executables or creating paths.
repository=$(sed -n 's/.*"repository"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$task_root/release.json")
version=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$task_root/release.json")
tag=$(sed -n 's/.*"tag"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$task_root/release.json")
[ "$repository" = 'Milozpace/Account-Preview' ] && [ "$tag" = "v$version" ] || fail 'Invalid release.json source identity or version.'
printf '%s\n' "$version" | LC_ALL=C grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$' || fail 'Invalid source version.'
kind=source-archive
if [ -e "$task_root/.git" ]; then
  origin=$(git -C "$task_root" config --get remote.origin.url 2>/dev/null) || fail 'Cannot verify Git origin; no configuration performed.'
  printf '%s\n' "$origin" | LC_ALL=C grep -Eiq '^(https://([^/@]+@)?github\.com/|ssh://([^/@]+@)?github\.com/|git@github\.com:)Milozpace/Account-Preview(\.git)?$' || fail 'Git origin does not match the canonical release repository; no configuration performed.'
  kind=git
fi
step() { if [ "$1" = true ]; then printf '{"requested":true,"status":"planned"}'; else printf '{"requested":false,"status":"skipped"}'; fi; }
if [ "$apply" = false ]; then
  json_root=$(printf '%s' "$task_root" | sed 's/\\/\\\\/g;s/"/\\"/g')
  printf '{"ok":true,"dryRun":true,"root":"%s","source":{"kind":"%s","repository":"%s"},"steps":{"python":%s,"components":%s,"desktop":%s,"app":%s,"native":%s},"next":"After authorization add --apply --expected-root ABSOLUTE_PROJECT_ROOT. Then run account.py doctor; new mini uses documented zero technical budget placeholders and zero carry, with budget undecided. Preserve existing ledger and parameters."}\n' "$json_root" "$kind" "$repository" "$(step true)" "$(step "$components")" "$(step "$configure")" "$(step "$install")" "$(step "$native")"
  exit 0
fi
[ "$(uname -s)" = Darwin ] || fail 'This launcher supports macOS.'
case "$(uname -m)" in
  arm64) triple=aarch64-apple-darwin; expected=fcf0a9ea6599c6ae28a4c854ac6da76f2c889354d7c36ce136ef071f7ab9721f ;;
  x86_64) triple=x86_64-apple-darwin; expected=171eb8c518313e157c5b4cec7b4f743bc6bab1bd23e09b646679a02d096a047f ;;
  *) fail 'Unsupported Mac architecture.' ;;
esac
tool_dir="$task_root/.runtime/bootstrap"
uv="$tool_dir/uv-$triple/uv"
if [ ! -x "$uv" ]; then
  mkdir -p "$tool_dir"
  archive="$tool_dir/uv-0.9.26.tar.gz"
  curl --fail --location --proto '=https' --tlsv1.2 --output "$archive" "https://github.com/astral-sh/uv/releases/download/0.9.26/uv-$triple.tar.gz"
  actual=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
  [ "$actual" = "$expected" ] || fail 'uv download checksum failed; stopped.'
  tar -xzf "$archive" -C "$tool_dir"
fi
export UV_PYTHON_INSTALL_DIR="$task_root/.runtime/python"
export UV_CACHE_DIR="$task_root/.runtime/uv-cache"
exec "$uv" run --no-project --no-config --python 3.12.12 python "$task_root/bootstrap.py" --uv "$uv" "$@"
