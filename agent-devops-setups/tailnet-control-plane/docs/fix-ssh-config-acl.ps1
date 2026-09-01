# Run elevated once to unlock default ~/.ssh/config so `ssh kooshas-laptop` works without -F
$cfg = Join-Path $env:USERPROFILE ".ssh\config"
$pheno = Join-Path $env:USERPROFILE ".ssh\config.pheno"
takeown /f $cfg
icacls $cfg /grant "${env:USERNAME}:(F)"
icacls $cfg /inheritance:r
icacls $cfg /grant:r "${env:USERNAME}:(F)" "SYSTEM:(F)"
# Merge: keep any existing Hosts, prepend pheno block
$phenoText = Get-Content $pheno -Raw
$old = if (Test-Path $cfg) { Get-Content $cfg -Raw } else { "" }
if ($old -notmatch "Host kooshas-laptop") {
  Set-Content -Path $cfg -Value ($phenoText + "`n" + $old) -Encoding ascii
} else {
  Write-Host "kooshas-laptop already present in config"
}
icacls $cfg
ssh -o BatchMode=yes -o ConnectTimeout=8 kooshas-laptop "echo DEFAULT_ALIAS_OK; hostname"
