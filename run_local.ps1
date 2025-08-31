<# 
Run local profile card update on Windows (PowerShell).
Usage:
  .\run_local.ps1              # fast run (no heavy LOC/commit scan)
  .\run_local.ps1 -Heavy       # heavy run (LOC + commit scan)
  .\run_local.ps1 -Heavy -ForceCache  # force rebuild cache
Optional env beforehand:
  $env:ACCESS_TOKEN="ghp_XXXX"
#>

param(
  [switch]$Heavy,
  [switch]$ForceCache
)

Write-Host "== Setting up virtual environment ==" -ForegroundColor Cyan
if (-not (Test-Path .venv)) {
  py -3 -m venv .venv
}

# Activate venv in script scope
$activate = ".\.venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
  Write-Error "Activation script not found: $activate"
  exit 1
}
. $activate

Write-Host "== Installing dependencies ==" -ForegroundColor Cyan
python -m pip install --upgrade pip > $null
pip install -r cache\requirements.txt

if (-not $env:USER_NAME) { $env:USER_NAME = "HimuCodes" }
if (-not $env:BIRTHDATE) { $env:BIRTHDATE = "2005-01-17" }

$env:DO_HEAVY   = $(if ($Heavy) { "1" } else { "0" })
$env:FORCE_CACHE = $(if ($ForceCache) { "1" } else { "0" })

Write-Host "USER_NAME   = $($env:USER_NAME)"
Write-Host "BIRTHDATE   = $($env:BIRTHDATE)"
Write-Host "DO_HEAVY    = $($env:DO_HEAVY)"
Write-Host "FORCE_CACHE = $($env:FORCE_CACHE)"

Write-Host "== Running update_profile.py ==" -ForegroundColor Cyan
python update_profile.py

# Open generated SVGs if present
$svgs = @('dark.svg','light.svg')
$existing = $svgs | Where-Object { Test-Path $_ }
if ($existing.Count -gt 0) {
  Write-Host "Opening SVGs..."
  foreach ($f in $existing) { Start-Process $f }
} else {
  Write-Warning "SVG files not found."
}