# 混淆图工具 Windows 单文件构建脚本；仅使用项目虚拟环境。
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Name = 'ConfusingImages'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$IconPath = Join-Path $ProjectRoot 'build\app.ico'
if (-not (Test-Path -LiteralPath $Python))
{
    throw '请先创建 .venv 并安装 requirements-dev.txt 中的依赖。'
}

Push-Location $ProjectRoot
try
{
    # 测试失败时不生成交付产物，避免将未验证版本当作正式版本。
    & $Python -X utf8 -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0)
    {
        throw '回归测试失败，已停止打包。'
    }

    & $Python -X utf8 -m confusing_images.branding $IconPath
    if ($LASTEXITCODE -ne 0)
    {
        throw '生成图标失败。'
    }

    $Arguments = @(
        '-X', 'utf8', '-m', 'PyInstaller',
        '--onefile', '--windowed', '--noconfirm', '--clean', '--noupx',
        '--name', $Name,
        # 修复 spec 放入 build 后相对图标路径重复拼接为 build/build 的问题。
        '--icon', $IconPath,
        '--specpath', 'build',
        '--workpath', 'build\pyinstaller',
        '--distpath', 'dist',
        '--paths', $ProjectRoot,
        'main.py'
    )
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0)
    {
        throw 'PyInstaller 打包失败。'
    }

    # 输出实际产物位置供开发者确认，不运行或上传生成的 EXE。
    Get-Item -LiteralPath (Join-Path 'dist' ($Name + '.exe')) | Select-Object FullName, Length
}
finally
{
    Pop-Location
}
