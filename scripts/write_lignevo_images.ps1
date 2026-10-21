# Rebuild lignevo CSV from product HTML already saved in %TEMP%\lignevo-images.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$csvPath = Join-Path $root "output\lignevo_products.csv"
$work = Join-Path $env:TEMP "lignevo-images"
$rows = @(Import-Csv -LiteralPath $csvPath)
Write-Host ("rows={0}" -f $rows.Count)

$srcRx = [regex]'"src"\s*:\s*"([^"]+)"'
$blockRx = [regex]'(?s)<script[^>]*id="wsp-gallery-data"[^>]*>(.*?)</script>'
$multi = 0
$missing = 0
$totalImgs = 0
for ($i = 0; $i -lt $rows.Count; $i++) {
    $file = Join-Path $work ("p{0}.html" -f $i)
    $imgs = New-Object System.Collections.Generic.List[string]
    if (Test-Path $file) {
        $html = [System.IO.File]::ReadAllText($file)
        $block = $blockRx.Match($html)
        if ($block.Success) {
            foreach ($m in $srcRx.Matches($block.Groups[1].Value)) {
                $src = ($m.Groups[1].Value -replace '\\/', '/').Trim()
                if ($src -and -not $imgs.Contains($src)) { $imgs.Add($src) }
            }
        }
    }
    if ($imgs.Count -eq 0) {
        $missing++
        if ($rows[$i].images) { $imgs.Add([string]$rows[$i].images) }
    }
    if ($imgs.Count -gt 1) { $multi++ }
    $totalImgs += $imgs.Count
    $rows[$i].images = ($imgs -join ",")
}
Write-Host ("with_many={0} fallback={1} avg={2:n2}" -f $multi, $missing, ($totalImgs / [Math]::Max(1, $rows.Count)))

function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$utf8 = New-Object System.Text.UTF8Encoding $true
$targets = @($csvPath, (Join-Path $root "output\lignevo_products_images.csv"))
$written = $null
foreach ($target in $targets) {
    try {
        $sw = New-Object System.IO.StreamWriter($target, $false, $utf8)
        $sw.WriteLine("title,cate,price,images,link")
        foreach ($row in $rows) {
            $sw.WriteLine((@(
                (Quote-Csv $row.title),
                (Quote-Csv $row.cate),
                (Quote-Csv $row.price),
                (Quote-Csv $row.images),
                (Quote-Csv $row.link)
            ) -join ","))
        }
        $sw.Flush()
        $sw.Close()
        $written = $target
        break
    } catch {
        Write-Host ("locked {0}" -f $target)
    }
}
if (-not $written) { throw "Could not write CSV" }
Write-Host ("done {0}" -f $written)
