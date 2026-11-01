$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot)
try {
    $apply = $args -contains '--apply'
    $expectedRoot = $null
    $rebindFrom = $null
    for ($i=0; $i -lt $args.Count; $i++) {
        if ($args[$i] -eq '--expected-root' -or $args[$i] -eq '--rebind-from') {
            $option = $args[$i]
            if ($i+1 -ge $args.Count -or $args[$i+1].StartsWith('--')) { throw "$option requires an absolute path." }
            if ($option -eq '--expected-root') { if ($null -ne $expectedRoot) { throw 'Duplicate --expected-root.' }; $expectedRoot=$args[++$i] }
            else { if ($null -ne $rebindFrom) { throw 'Duplicate --rebind-from.' }; $rebindFrom=$args[++$i] }
        }
    }
    if ($apply -and $null -eq $expectedRoot) { throw '--apply requires --expected-root ABSOLUTE_PROJECT_ROOT; inspect dry-run root first.' }
    if ($null -ne $expectedRoot) {
        if ($expectedRoot -notmatch '^(?:[a-zA-Z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)' -or [IO.Path]::GetFullPath($expectedRoot).TrimEnd('\') -ne $taskRoot.TrimEnd('\')) { throw '--expected-root does not match this launcher root; nothing was configured.' }
        $ancestor=Get-Item -LiteralPath $taskRoot
        while ($null -ne $ancestor) {
            if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Use the canonical project root, not a redirected directory.' }
            $ancestor=$ancestor.Parent
        }
    }
    if ($null -ne $rebindFrom -and (-not $apply -or $args -notcontains '--configure-desktop' -or $rebindFrom -notmatch '^(?:[a-zA-Z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)')) { throw '--rebind-from requires an absolute old root, --apply --expected-root and --configure-desktop.' }
    $release = Get-Content -LiteralPath (Join-Path $taskRoot 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($release.repository -cne 'Milozpace/Account-Preview' -or $release.version -notmatch '^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?$' -or $release.tag -cne ('v'+$release.version)) { throw 'Invalid release.json source identity or version.' }
    $source = @{kind='source-archive'; repository=$release.repository}
    if (Test-Path -LiteralPath (Join-Path $taskRoot '.git')) {
        $origin = & git -C $taskRoot config --get remote.origin.url 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot verify Git origin; no configuration performed.' }
        if ($origin -notmatch '^(?:https://(?:[^/@]+@)?github\.com/|ssh://(?:[^/@]+@)?github\.com/|git@github\.com:)Milozpace/Account-Preview(?:\.git)?$') { throw 'Git origin does not match the canonical release repository; no configuration performed.' }
        $source.kind='git'; $source.origin='https://github.com/Milozpace/Account-Preview.git'
    }
    $steps=@{}
    $selected=@{python=$true;components=($args -contains '--repo' -or $args -contains '--bundle');desktop=($args -contains '--configure-desktop');app=($args -contains '--install-app');native=($args -contains '--initialize-identity' -or $args -contains '--enable-publish')}
    foreach ($name in $selected.Keys) { $steps[$name]=@{requested=$selected[$name];status=$(if($selected[$name]){'planned'}else{'skipped'})} }
    if (-not $apply) {
        @{ok=$true;dryRun=$true;root=$taskRoot;source=$source;steps=$steps;next='After authorization add --apply --expected-root ABSOLUTE_PROJECT_ROOT. Then run account.py doctor; new mini uses documented zero technical budget placeholders and zero carry, with budget undecided. Preserve existing ledger and parameters.'} | ConvertTo-Json -Depth 5
        exit 0
    }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This launcher supports Windows x64.' }
    $toolDir = Join-Path $taskRoot '.runtime/bootstrap'
    $uv = Join-Path $toolDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
        $archive = Join-Path $toolDir 'uv-0.9.26.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/download/0.9.26/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'eb02fd95d8e0eed462b4a67ecdd320d865b38c560bffcda9a0b87ec944bdf036') { throw 'uv download checksum failed; stopped.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $toolDir -Force
    }
    $oldInstall=$env:UV_PYTHON_INSTALL_DIR
    $oldCache=$env:UV_CACHE_DIR
    $oldPreference=$ErrorActionPreference
    $nativePreference=Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    if ($null -ne $nativePreference) { $oldNativePreferenceValue=$nativePreference.Value }
    try {
        $env:UV_PYTHON_INSTALL_DIR=Join-Path $taskRoot '.runtime/python'
        $env:UV_CACHE_DIR=Join-Path $taskRoot '.runtime/uv-cache'
        # PS5.1 wraps redirected native stderr as ErrorRecords; preserve progress.
        $ErrorActionPreference='Continue'
        if ($null -ne $nativePreference) { $PSNativeCommandUseErrorActionPreference=$false }
        & $uv run --no-project --no-config --python 3.12.12 python (Join-Path $taskRoot 'bootstrap.py') --uv $uv @args
        $nativeExit=$LASTEXITCODE
    } finally {
        $ErrorActionPreference=$oldPreference
        if ($null -ne $nativePreference) { $PSNativeCommandUseErrorActionPreference=$oldNativePreferenceValue }
        $env:UV_PYTHON_INSTALL_DIR=$oldInstall
        $env:UV_CACHE_DIR=$oldCache
    }
    exit $nativeExit
} catch {
    $failure=@{ok=$false;error=$_.Exception.Message;next='Keep existing data and completed steps; inspect the error and retry without initializing the ledger again.'} | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($failure)
    exit 1
}
