# =============================================================================
# start_server.ps1
# Description : Launches the OntoCast API server with environment loaded from
#               ../keys.env (API keys) and ontology.env (config).
#               Handles dynamic API key mapping based on LLM_PROVIDER.
# Last Updated: 2026-06-09
# Progress    : Configured to support both OpenAI and Google Gemini keys.
# Version History:
#   - v1.0.0 (2026-06-08): Initial OpenAI key mapping.
#   - v1.1.0 (2026-06-09): Added multi-key parser for keys.env and Gemini support.
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

# --- 1. Parse keys.env dynamically -------------------------------------------
$KeysEnvPath = Join-Path $RepoRoot "keys.env"
if (-not (Test-Path $KeysEnvPath)) {
    Write-Error "Missing: $KeysEnvPath"
    exit 1
}

$Keys = @{}
Get-Content $KeysEnvPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { return }
    if ($line -notmatch '=') { return }
    $parts = $line.Split('=', 2)
    $name = $parts[0].Trim()
    $value = ($parts[1] -replace '\s*#.*$', '').Trim()
    $Keys[$name] = $value
}

# --- 2. Load ontology.env (skip variable-interpolation lines) ----------------
$OntologyEnvPath = Join-Path $ScriptDir "ontology.env"
if (-not (Test-Path $OntologyEnvPath)) {
    Write-Error "Missing: $OntologyEnvPath"
    exit 1
}

Get-Content $OntologyEnvPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { return }
    if ($line -notmatch '=') { return }

    $parts = $line.Split('=', 2)
    $name = $parts[0].Trim()
    $value = ($parts[1] -replace '\s*#.*$', '').Trim()

    # Skip ${...} interpolation lines — set manually below
    if ($value -match '^\$\{') { return }

    [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

# --- 3. Set key vars explicitly ----------------------------------------------
$env:OPENAI_API_KEY = $Keys['OPENAI_API_KEY']
$env:GEMINI_KEY = $Keys['GEMINI_KEY']

if ($env:LLM_PROVIDER -eq 'google') {
    $env:LLM_API_KEY = $Keys['GEMINI_KEY']
} else {
    $env:LLM_API_KEY = $Keys['OPENAI_API_KEY']
}

# --- 4. Launch from repo root ------------------------------------------------
Write-Host "Starting OntoCast server (Provider: $env:LLM_PROVIDER, Model: $env:LLM_MODEL_NAME) on port $env:PORT ..." -ForegroundColor Cyan
Set-Location $RepoRoot
uv run ontocast --tenant art6 --project clean
