# Phoenix V3 — full validation pack (energy audit, robustness, transient faults).
#
# Run from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts/run_phoenix_validation_pack.ps1
#
# Or from anywhere:
#   powershell -ExecutionPolicy Bypass -File "C:\...\project Phoenix\scripts\run_phoenix_validation_pack.ps1"
#
# Writes timestamped logs and copies key artifacts to designs/validation/<timestamp>/

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$OutDir = Join-Path $RepoRoot "designs\validation\$Timestamp"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$LogPath = Join-Path $OutDir "validation-pack.log"
$SummaryPath = Join-Path $OutDir "SUMMARY.txt"

function Write-Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Test-PythonHasModule {
    param(
        [string]$Exe,
        [string[]]$Prefix,
        [string]$ModuleName
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $checkArgs = @($Prefix + @("-c", "import $ModuleName"))
    & $Exe @checkArgs 1>$null 2>$null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $prevEap
    return $ok
}

function Get-PythonInvoker {
    param([string]$RequiredModule = "")
    $candidates = @()
    if ($env:VIRTUAL_ENV) {
        $venvPy = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path $venvPy) {
            $candidates += @{ Exe = $venvPy; Prefix = @() }
        }
    }
    $candidates += @{ Exe = "py"; Prefix = @("-3") }

    foreach ($c in $candidates) {
        if (-not $RequiredModule -or (Test-PythonHasModule $c.Exe $c.Prefix $RequiredModule)) {
            return $c
        }
    }
    throw "No Python found with module '$RequiredModule'. Install: pip install $RequiredModule"
}

function Invoke-ValidationStep {
    param(
        [string]$Name,
        [string[]]$PythonArgs,
        [string]$RequiredModule = ""
    )
    $py = Get-PythonInvoker -RequiredModule $RequiredModule
    Write-Log "=== $Name ==="
    $stepLog = Join-Path $OutDir ("{0}.log" -f ($Name -replace '[^\w\-]', '_'))
    $allArgs = @($py.Prefix + $PythonArgs)
    $cmd = "$($py.Exe) " + ($allArgs -join ' ')
    Write-Log "CMD: $cmd"
    & $py.Exe @allArgs 2>&1 | Tee-Object -FilePath $stepLog
    if ($LASTEXITCODE -ne 0) {
        Write-Log "FAILED: $Name (exit $LASTEXITCODE)"
        throw "Step failed: $Name"
    }
    Write-Log "OK: $Name"
    Write-Log ""
}

Write-Log "Phoenix V3 validation pack"
Write-Log "Repo:    $RepoRoot"
Write-Log "Output:  $OutDir"
Write-Log ""

# --- 1. Energy invariant tests ---
Invoke-ValidationStep "pytest_energy" @(
    "-m", "pytest", "tests/test_phoenix_v3_energy.py", "-v"
) -RequiredModule "pytest"

# --- 2. Canonical efficiency report (single definition) ---
Invoke-ValidationStep "canonical_efficiency" @(
    "designs/phoenix_v3/validation/report.py",
    "--best-tuning", "--cycles", "24", "--json-out",
    (Join-Path $OutDir "canonical_efficiency.json")
)

# --- 3. Main credible report (best-tuning v2) ---
Invoke-ValidationStep "best_tuning_audit" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--cycles", "12", "--canonical-report"
) -RequiredModule "matplotlib"

# --- 4. Actuator limits (v2 ideal plant vs actuator stack) ---
Invoke-ValidationStep "actuator_limits_v2" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--actuator-suite", "--cycles", "30"
) -RequiredModule "matplotlib"

# --- 4b. Actuator-retuned operating point ---
Invoke-ValidationStep "actuator_retuned" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning-actuator", "--actuator-suite", "--cycles", "30"
) -RequiredModule "matplotlib"

# --- 5. Controls-off baseline ---
Invoke-ValidationStep "controls_off" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--controls-off", "--cycles", "24"
) -RequiredModule "matplotlib"

# --- 6. Monte Carlo 1000 trials ---
Invoke-ValidationStep "monte_carlo_1000" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--monte-carlo", "--mc-trials", "1000",
    "--cycles", "30", "--mc-workers", "4"
) -RequiredModule "matplotlib"

# --- 7. Stochastic combustion (legacy 20-trial quick check) ---
Invoke-ValidationStep "asymmetry_sweep" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--sweep-asymmetry", "--cycles", "10"
) -RequiredModule "matplotlib"

Invoke-ValidationStep "disturbance_sweep" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--sweep-disturbance", "--cycles", "10"
) -RequiredModule "matplotlib"

# --- 7. Transient fault suite ---
Invoke-ValidationStep "transient_faults" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--transient", "--cycles", "12", "--fault-cycle", "3"
) -RequiredModule "matplotlib"

# --- 8. Extraction strategy sweep ---
Invoke-ValidationStep "extraction_sweep" @(
    "designs/phoenix_v3_simulation.py",
    "--headless", "--best-tuning", "--sweep-extraction", "--cycles", "8"
) -RequiredModule "matplotlib"

# --- Copy artifacts ---
$Artifacts = @(
    "designs\phoenix_v3_cycle_dashboard.png",
    "designs\phoenix_v3_scavenge_phases.png",
    "designs\phoenix_v3_sweep_extraction.png",
    "designs\phoenix_v3_best_tuning_v2.json",
    "designs\phoenix_v3_best_tuning_actuator.json"
)
foreach ($rel in $Artifacts) {
    $src = Join-Path $RepoRoot $rel
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $OutDir (Split-Path $rel -Leaf)) -Force
        Write-Log "Copied: $rel"
    }
}

# --- Summary ---
@"
Phoenix V3 Validation Pack
==========================
Run time:  $Timestamp
Repo:      $RepoRoot
Log:       $LogPath

Steps:
  1. pytest tests/test_phoenix_v3_energy.py
  2. Canonical efficiency report (24 cycles, JSON)
  3. Best-tuning energy audit (12 cycles)
  4. Actuator limits suite (sensor delay, back-EMF, current slew)
  5. Controls-off baseline (ECU disabled)
  6. Monte Carlo 1000 trials with failure taxonomy
  7. Stochastic combustion quick check (20 trials)
  8. Asymmetry sensitivity sweep
  9. Disturbance sensitivity sweep (capture, BDC, dEnet, collisions)
  10. Transient fault suite
  11. Extraction strategy sweep

Key files in this folder:
  validation-pack.log     — combined run log
  canonical_efficiency.json — single canonical Enet definition
  best_tuning_audit.log   — full energy audit output
  actuator_limits.log
  controls_off.log
  monte_carlo_1000.log
  stochastic_combustion.log
  asymmetry_sweep.log
  disturbance_sweep.log
  transient_faults.log
  extraction_sweep.log
  phoenix_v3_cycle_dashboard.png (if generated)
  phoenix_v3_scavenge_phases.png (if generated)
  phoenix_v3_sweep_extraction.png (if generated)

Re-run optimizer on corrected physics (not included — takes longer):
  py -3 designs/phoenix_v3_optimizer.py --trials 500 --workers 8 --cycles 10 `
      --seed 200 --corpus designs/phoenix_v3_tuning_corpus_v3.csv

Quick single-run audit:
  py -3 designs/phoenix_v3/validation/report.py --best-tuning --cycles 24
"@ | Set-Content -Path $SummaryPath -Encoding UTF8

Write-Log "Validation pack complete."
Write-Log "Artifacts: $OutDir"
Write-Host ""
Write-Host "Done. Results in:" -ForegroundColor Green
Write-Host "  $OutDir"
