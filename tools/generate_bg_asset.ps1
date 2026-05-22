param(
    [string]$InputImage = "C:\Users\User\Downloads\IMG_3103.jpg",
    [string]$OutputFile = "supermouse_35b\components\lvgl_ui\bg_image.c"
)

Add-Type -AssemblyName System.Drawing

$screenW = 320
$screenH = 480
$src = [System.Drawing.Image]::FromFile($InputImage)

$srcRatio = $src.Width / $src.Height
$dstRatio = $screenW / $screenH
if ($srcRatio -gt $dstRatio) {
    $cropH = $src.Height
    $cropW = [int]($src.Height * $dstRatio)
    $cropX = [int](($src.Width - $cropW) / 2)
    $cropY = 0
} else {
    $cropW = $src.Width
    $cropH = [int]($src.Width / $dstRatio)
    $cropX = 0
    $cropY = [int](($src.Height - $cropH) / 2)
}

$bmp = New-Object System.Drawing.Bitmap $screenW, $screenH
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$gfx.DrawImage(
    $src,
    (New-Object System.Drawing.Rectangle 0, 0, $screenW, $screenH),
    (New-Object System.Drawing.Rectangle $cropX, $cropY, $cropW, $cropH),
    [System.Drawing.GraphicsUnit]::Pixel
)

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
$src.Dispose()

Write-Output "Generated $OutputFile from $InputImage"
