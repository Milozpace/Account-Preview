#!/bin/sh
set -eu
task_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
apply=false
for arg in "$@"; do [ "$arg" != '--apply' ] || apply=true; done
if [ "$apply" = false ]; then
  printf '%s\n' '只检查：执行时将准备本项目专用 uv、Python 3.12.12、.venv 和锁定依赖。' '加 --apply 执行；其余参数见 bootstrap.py --help。'
  exit 0
fi
[ "$(uname -s)" = Darwin ] || { printf '%s\n' '此启动器支持 macOS。' >&2; exit 1; }
case "$(uname -m)" in
  arm64) triple=aarch64-apple-darwin; expected=fcf0a9ea6599c6ae28a4c854ac6da76f2c889354d7c36ce136ef071f7ab9721f ;;
  x86_64) triple=x86_64-apple-darwin; expected=171eb8c518313e157c5b4cec7b4f743bc6bab1bd23e09b646679a02d096a047f ;;
  *) printf '%s\n' '不支持的 Mac 架构。' >&2; exit 1 ;;
esac
tool_dir="$task_root/.runtime/bootstrap"
uv="$tool_dir/uv-$triple/uv"
if [ ! -x "$uv" ]; then
  mkdir -p "$tool_dir"
  archive="$tool_dir/uv-0.9.26.tar.gz"
  curl --fail --location --proto '=https' --tlsv1.2 --output "$archive" "https://github.com/astral-sh/uv/releases/download/0.9.26/uv-$triple.tar.gz"
  actual=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
  [ "$actual" = "$expected" ] || { printf '%s\n' 'uv 下载校验失败，已停止。' >&2; exit 1; }
  tar -xzf "$archive" -C "$tool_dir"
fi
export UV_PYTHON_INSTALL_DIR="$task_root/.runtime/python"
export UV_CACHE_DIR="$task_root/.runtime/uv-cache"
exec "$uv" run --no-project --no-config --python 3.12.12 python "$task_root/bootstrap.py" --uv "$uv" "$@"
