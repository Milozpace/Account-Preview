$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
if ($args -notcontains '--apply') {
    @{ok=$true; dryRun=$true; root=$taskRoot; steps=@('下载并校验项目专用 uv','准备 Python 3.12.12 与 .venv','校验锁定依赖','按选项配置预览 App'); next='加 --apply 执行；其余参数见 bootstrap.py --help。'} | ConvertTo-Json
    exit 0
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw '此启动器支持 Windows x64。' }
$toolDir = Join-Path $taskRoot '.runtime/bootstrap'
$uv = Join-Path $toolDir 'uv.exe'
if (-not (Test-Path -LiteralPath $uv)) {
    New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
    $archive = Join-Path $toolDir 'uv-0.9.26.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.9.26/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'eb02fd95d8e0eed462b4a67ecdd320d865b38c560bffcda9a0b87ec944bdf036') { throw 'uv 下载校验失败，已停止。' }
    Expand-Archive -LiteralPath $archive -DestinationPath $toolDir -Force
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot '.runtime/python'
$env:UV_CACHE_DIR = Join-Path $taskRoot '.runtime/uv-cache'
& $uv run --no-project --no-config --python 3.12.12 python (Join-Path $taskRoot 'bootstrap.py') --uv $uv @args
exit $LASTEXITCODE
