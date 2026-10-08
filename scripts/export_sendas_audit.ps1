# Exportiert SendAs-/SendOnBehalf-Einträge der letzten N Tage aus dem Unified Audit Log.
# Aufruf in pwsh nach Connect-ExchangeOnline:
#   ./scripts/export_sendas_audit.ps1 -Days 30 -OutFile ~/sla-monitor-data/audit_sendas.csv
# Danach: sla-monitor import-audit ~/sla-monitor-data/audit_sendas.csv
param(
  [int]$Days = 30,
  [string]$OutFile = "$HOME/sla-monitor-data/audit_sendas.csv"
)

$end   = (Get-Date).ToUniversalTime()
$start = $end.AddDays(-$Days)
$session = [guid]::NewGuid().ToString()
$all = @()

do {
  $batch = Search-UnifiedAuditLog -StartDate $start -EndDate $end `
           -RecordType ExchangeItem -Operations SendAs,SendOnBehalf `
           -SessionId $session -SessionCommand ReturnLargeSet -ResultSize 5000
  if ($batch) { $all += $batch }
  Write-Host ("{0} Einträge bisher" -f $all.Count)
} while ($batch -and $batch.Count -eq 5000)

$all | Select-Object CreationDate, UserIds, Operations, AuditData |
  Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8
Write-Host "Gespeichert: $OutFile ($($all.Count) Einträge)"
