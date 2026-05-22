param(
    [string]$OutputFile = "supermouse_35b\components\lvgl_ui\bg_image.c"
)

Add-Type -AssemblyName System.Drawing

$screenW = 320
$screenH = 480
$bmp = New-Object System.Drawing.Bitmap $screenW, $screenH
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

function Lerp($a, $b, $t) {
    return [int]($a + ($b - $a) * $t)
}

function ColorLerp($c1, $c2, $t) {
    return [System.Drawing.Color]::FromArgb(
        (Lerp $c1.R $c2.R $t),
        (Lerp $c1.G $c2.G $t),
        (Lerp $c1.B $c2.B $t)
    )
}

function FillGradientStops($bitmap, $stops) {
    for ($y = 0; $y -lt $bitmap.Height; $y++) {
        $p = $y / ($bitmap.Height - 1)
        for ($i = 0; $i -lt $stops.Count - 1; $i++) {
            if ($p -ge $stops[$i].P -and $p -le $stops[$i + 1].P) {
                $local = ($p - $stops[$i].P) / ($stops[$i + 1].P - $stops[$i].P)
                $color = ColorLerp $stops[$i].C $stops[$i + 1].C $local
                break
            }
        }
        for ($x = 0; $x -lt $bitmap.Width; $x++) {
            $bitmap.SetPixel($x, $y, $color)
        }
    }
}

function DrawLayer($graphics, $points, $color) {
    $poly = New-Object System.Collections.Generic.List[System.Drawing.PointF]
    foreach ($p in $points) {
        $poly.Add((New-Object System.Drawing.PointF $p[0], $p[1]))
    }
    $poly.Add((New-Object System.Drawing.PointF 320, 480))
    $poly.Add((New-Object System.Drawing.PointF 0, 480))
    $brush = New-Object System.Drawing.SolidBrush $color
    $graphics.FillPolygon($brush, $poly.ToArray())
    $brush.Dispose()
}

$stops = @(
    @{ P = 0.00; C = [System.Drawing.Color]::FromArgb(255, 156, 55) },
    @{ P = 0.18; C = [System.Drawing.Color]::FromArgb(255, 96, 76) },
    @{ P = 0.34; C = [System.Drawing.Color]::FromArgb(155, 105, 158) },
    @{ P = 0.52; C = [System.Drawing.Color]::FromArgb(86, 84, 139) },
    @{ P = 1.00; C = [System.Drawing.Color]::FromArgb(4, 10, 24) }
)
FillGradientStops $bmp $stops

DrawLayer $gfx @(
    @(0, 222), @(45, 206), @(86, 196), @(126, 210), @(168, 204), @(210, 207), @(250, 198), @(282, 208), @(320, 210)
) ([System.Drawing.Color]::FromArgb(116, 113, 165))

DrawLayer $gfx @(
    @(0, 232), @(30, 238), @(64, 236), @(94, 250), @(132, 255), @(170, 258), @(210, 247), @(250, 232), @(278, 222), @(320, 214)
) ([System.Drawing.Color]::FromArgb(63, 63, 119))

DrawLayer $gfx @(
    @(0, 258), @(28, 276), @(62, 284), @(94, 280), @(126, 296), @(164, 304), @(204, 312), @(238, 320), @(274, 290), @(320, 276)
) ([System.Drawing.Color]::FromArgb(36, 38, 88))

DrawLayer $gfx @(
    @(0, 305), @(36, 315), @(76, 336), @(118, 350), @(160, 366), @(196, 398), @(236, 372), @(280, 348), @(320, 338)
) ([System.Drawing.Color]::FromArgb(18, 20, 58))

DrawLayer $gfx @(
    @(0, 430), @(36, 414), @(74, 408), @(116, 392), @(156, 388), @(198, 386), @(238, 370), @(278, 364), @(320, 340)
) ([System.Drawing.Color]::FromArgb(3, 8, 22))

$dir = Split-Path -Parent $OutputFile
if (!(Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$writer = New-Object System.IO.StreamWriter($OutputFile, $false, [System.Text.Encoding]::ASCII)
$writer.WriteLine('#include "lvgl.h"')
$writer.WriteLine("")
$writer.WriteLine("#ifndef LV_ATTRIBUTE_MEM_ALIGN")
$writer.WriteLine("#define LV_ATTRIBUTE_MEM_ALIGN")
$writer.WriteLine("#endif")
$writer.WriteLine("#ifndef LV_ATTRIBUTE_IMG_SUPERMOUSE_BG")
$writer.WriteLine("#define LV_ATTRIBUTE_IMG_SUPERMOUSE_BG")
$writer.WriteLine("#endif")
$writer.WriteLine("")
$writer.WriteLine("const LV_ATTRIBUTE_MEM_ALIGN LV_ATTRIBUTE_LARGE_CONST LV_ATTRIBUTE_IMG_SUPERMOUSE_BG uint8_t supermouse_bg_map[] = {")

for ($y = 0; $y -lt $screenH; $y++) {
    $line = "  "
    for ($x = 0; $x -lt $screenW; $x++) {
        $c = $bmp.GetPixel($x, $y)
        $rgb565 = (($c.R -band 0xF8) -shl 8) -bor (($c.G -band 0xFC) -shl 3) -bor ($c.B -shr 3)
        $hi = ($rgb565 -shr 8) -band 0xFF
        $lo = $rgb565 -band 0xFF
        $line += ("0x{0:X2}, 0x{1:X2}, " -f $hi, $lo)
    }
    $writer.WriteLine($line)
}

$writer.WriteLine("};")
$writer.WriteLine("")
$writer.WriteLine("const lv_img_dsc_t supermouse_bg = {")
$writer.WriteLine("  .header.cf = LV_IMG_CF_TRUE_COLOR,")
$writer.WriteLine("  .header.always_zero = 0,")
$writer.WriteLine("  .header.reserved = 0,")
$writer.WriteLine("  .header.w = 320,")
$writer.WriteLine("  .header.h = 480,")
$writer.WriteLine("  .data_size = 320 * 480 * 2,")
$writer.WriteLine("  .data = supermouse_bg_map,")
$writer.WriteLine("};")

$writer.Close()
$gfx.Dispose()
$bmp.Dispose()

Write-Output "Generated mountain background asset at $OutputFile"
