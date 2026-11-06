# Installs the Formula Pattern add-in into Fusion 360 (Windows).
#
#   .\install.ps1          links this folder's FormulaPattern directory into Fusion's AddIns
#                          folder (a directory junction, so edits here are live)
#   .\install.ps1 -Copy    copies it instead of linking
#
# Afterwards, in Fusion: UTILITIES > ADD-INS > Add-Ins tab > FormulaPattern > Run
# (tick "Run on Startup" to keep it). The button appears in SOLID > CREATE.

param([switch]$Copy)

$src = Join-Path $PSScriptRoot 'FormulaPattern'
$addins = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
$dst = Join-Path $addins 'FormulaPattern'

if (-not (Test-Path $src)) { throw "Cannot find $src" }
New-Item -ItemType Directory -Force $addins | Out-Null

$existing = Get-Item $dst -Force -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # a junction: remove only the link, never its target
        cmd /c rmdir "$dst"
    } else {
        Remove-Item $dst -Recurse -Force
    }
}

if ($Copy) {
    Copy-Item $src $dst -Recurse
    Write-Host "Copied the add-in to $dst"
} else {
    New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
    Write-Host "Linked $dst -> $src"
}
Write-Host 'In Fusion 360: UTILITIES > ADD-INS > Add-Ins tab > FormulaPattern > Run (tick Run on Startup).'
Write-Host 'The Formula Pattern button is in SOLID > CREATE.'
