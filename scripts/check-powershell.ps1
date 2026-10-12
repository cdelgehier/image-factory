# Parse scripts without running guest setup commands on the check runner.
$ErrorActionPreference = 'Stop'
$failed = $false
Get-ChildItem targets -Recurse -Filter *.ps1 | ForEach-Object {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    foreach ($error in $errors) {
        Write-Host "$($_.FullName):$($error.Extent.StartLineNumber): $($error.Message)"
        $failed = $true
    }
}
if ($failed) { exit 1 }
