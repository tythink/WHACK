<#
.SYNOPSIS
tools/board.ps1: Asynchronous agent communication over a pinned GitHub Issue (PowerShell version)

.EXAMPLE
.\tools\board.ps1 post "[Stream B: Emotion] Emotion stream is live on :5001"
.\tools\board.ps1 read 5
.\tools\board.ps1 wait "@emotion-lead"
#>

[CmdletBinding()]
param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet("post", "read", "wait", "help")]
    [string]$Command,

    [Parameter(Position=1)]
    [string]$Arg1
)

$IssueNum = if ($env:AGENT_BOARD_ISSUE) { $env:AGENT_BOARD_ISSUE } else { "1" }

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed or not in PATH. Please install GitHub CLI."
    exit 1
}

switch ($Command) {
    "post" {
        if (-not $Arg1) {
            Write-Host "Usage: .\tools\board.ps1 post '<message>'"
            exit 1
        }
        gh issue comment $IssueNum --body $Arg1
        Write-Host "✓ Posted message to Issue #$IssueNum"
    }

    "read" {
        $limit = if ($Arg1) { [int]$Arg1 } else { 5 }
        $json = gh issue view $IssueNum --json comments | ConvertFrom-Json
        $comments = $json.comments | Select-Object -Last $limit
        foreach ($c in $comments) {
            Write-Host "[$($c.author.login)] $($c.createdAt):"
            Write-Host $c.body
            Write-Host "----------------------------------------"
        }
    }

    "wait" {
        if (-not $Arg1) {
            Write-Host "Usage: .\tools\board.ps1 wait '@handle'"
            exit 1
        }
        Write-Host "Waiting for message mentioning '$Arg1' on Issue #$IssueNum (polling every 8s)..."
        $lastId = ""
        while ($true) {
            $json = gh issue view $IssueNum --json comments | ConvertFrom-Json
            if ($json.comments.Count -gt 0) {
                $latest = $json.comments[-1]
                if ($latest.body -match [regex]::Escape($Arg1) -and $latest.id -ne $lastId) {
                    Write-Host "`n🚨 Mention detected for $Arg1!"
                    Write-Host $latest.body
                    break
                }
            }
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 8
        }
    }

    Default {
        Write-Host "Hackathon Agent Board CLI (PowerShell)"
        Write-Host "Usage:"
        Write-Host "  .\tools\board.ps1 post '<message>'   # Post an update or handoff"
        Write-Host "  .\tools\board.ps1 read [count]       # Read the latest N messages"
        Write-Host "  .\tools\board.ps1 wait '@your-handle' # Poll until tagged"
    }
}
