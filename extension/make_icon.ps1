# PowerShell 画 128x128 PNG 图标：深紫圆角底 + 白色 "AI" + 绿色在线点
Add-Type -AssemblyName System.Drawing
$size = 128
$bmp = New-Object System.Drawing.Bitmap($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'
$g.TextRenderingHint = 'AntiAlias'

$g.Clear([System.Drawing.Color]::Transparent)
# 圆角矩形底
$bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    (New-Object System.Drawing.Point(0, 0)), (New-Object System.Drawing.Point($size, $size)),
    [System.Drawing.Color]::FromArgb(255, 76, 61, 160), [System.Drawing.Color]::FromArgb(255, 40, 32, 92))
$radius = 28
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddArc(0, 0, $radius, $radius, 180, 90)
$path.AddArc($size - $radius, 0, $radius, $radius, 270, 90)
$path.AddArc($size - $radius, $size - $radius, $radius, $radius, 0, 90)
$path.AddArc(0, $size - $radius, $radius, $radius, 90, 90)
$path.CloseFigure()
$g.FillPath($bgBrush, $path)

# "AI" 文字
$font = New-Object System.Drawing.Font('Segoe UI', 42, [System.Drawing.FontStyle]::Bold)
$white = [System.Drawing.Brushes]::White
$fmt = New-Object System.Drawing.StringFormat
$fmt.Alignment = 'Center'
$fmt.LineAlignment = 'Center'
$g.DrawString('AI', $font, $white, (New-Object System.Drawing.RectangleF(0, -6, $size, $size)), $fmt)

# 右下角绿色在线点（协作在线的隐喻）
$green = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 72, 220, 120))
$g.FillEllipse($green, 88, 88, 26, 26)

$g.Dispose()
$bmp.Save("$PSScriptRoot\icon.png", [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Host "icon.png generated ($((Get-Item "$PSScriptRoot\icon.png").Length) bytes)"
