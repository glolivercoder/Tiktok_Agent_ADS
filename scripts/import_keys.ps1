# Import API keys from F:\Projetos2025BKP\APIS to .env (masked output)
# Usage: .\scripts\import_keys.ps1

$ErrorActionPreference = 'Stop'
$SourceRoot = 'F:\Projetos2025BKP\APIS'
$DestEnv = Join-Path $PSScriptRoot '..\' '.env'

if (-not (Test-Path $SourceRoot)) {
    Write-Error "Diretório de chaves não encontrado: $SourceRoot"
    exit 1
}

function Get-KeyFromFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $content = Get-Content $Path -Raw -ErrorAction SilentlyContinue
    if (-not $content) { return $null }
    # Extrai primeira linha não-vazia que parece chave (alfanumérico + hífen/underscore, min 20 chars)
    $lines = $content -split "`n"
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^[A-Za-z0-9_\-]{20,}$') {
            return $trimmed
        }
    }
    return $null
}

function Mask-Key {
    param([string]$Key)
    if (-not $Key) { return '(ausente)' }
    $len = $Key.Length
    if ($len -le 8) { return '****' }
    $final = $Key.Substring($len - 4)
    return "presente ($len chars, final ****$final)"
}

Write-Host "`n=== Importando chaves de API ===`n" -ForegroundColor Cyan

$keys = @{}

# OpenRouter (prioridade: opencode.txt, senão primeira válida)
$orFiles = @(
    "$SourceRoot\Openrouter\opencode.txt",
    "$SourceRoot\Openrouter\Agent_Jarvis_API.txt"
)
foreach ($f in $orFiles) {
    $k = Get-KeyFromFile $f
    if ($k) {
        $keys['OPENROUTER_API_KEY'] = $k
        Write-Host "OpenRouter: $(Mask-Key $k) [$(Split-Path $f -Leaf)]" -ForegroundColor Green
        break
    }
}

# OmniRoute
$omFiles = @(
    "$SourceRoot\Ominiroute\opencode_omniAPI.txt",
    "$SourceRoot\Ominiroute\ominiroutAPI.txt"
)
foreach ($f in $omFiles) {
    $k = Get-KeyFromFile $f
    if ($k) {
        $keys['OMNIROUTE_API_KEY'] = $k
        Write-Host "OmniRoute: $(Mask-Key $k) [$(Split-Path $f -Leaf)]" -ForegroundColor Green
        break
    }
}

# Gemini
$gemFiles = @(
    "$SourceRoot\Geminicloud_todoCalendar\GeminiAPIGcoder.txt",
    "$SourceRoot\Geminicloud_todoCalendar\AIzaSyA_snVzHG2IkEIpXs_XGUg_QY5sBQb.txt"
)
foreach ($f in $gemFiles) {
    $k = Get-KeyFromFile $f
    if ($k) {
        $keys['GEMINI_API_KEY'] = $k
        Write-Host "Gemini: $(Mask-Key $k) [$(Split-Path $f -Leaf)]" -ForegroundColor Green
        break
    }
}

# Claude
$k = Get-KeyFromFile "$SourceRoot\Claude\Basefy_claude_opeaiAPI.txt"
if ($k) {
    $keys['ANTHROPIC_API_KEY'] = $k
    Write-Host "Anthropic: $(Mask-Key $k)" -ForegroundColor Green
}

# OpenAI
$k = Get-KeyFromFile "$SourceRoot\Openai\OpenaiMasterDevAPI.txt"
if ($k) {
    $keys['OPENAI_API_KEY'] = $k
    Write-Host "OpenAI: $(Mask-Key $k)" -ForegroundColor Green
}

# DeepSeek
$k = Get-KeyFromFile "$SourceRoot\Deepseek\deepseekapi301026.txt"
if ($k) {
    $keys['DEEPSEEK_API_KEY'] = $k
    Write-Host "DeepSeek: $(Mask-Key $k)" -ForegroundColor Green
}

# SerpAPI
$k = Get-KeyFromFile "$SourceRoot\SerpAPI\serp_glolivercoder_Agent_masterV2.txt"
if ($k) {
    $keys['SERPAPI_API_KEY'] = $k
    Write-Host "SerpAPI: $(Mask-Key $k)" -ForegroundColor Green
}

# EchoTok
$k = Get-KeyFromFile "$SourceRoot\Echotik Tiktok ADS\Echotik.txt"
if ($k) {
    $keys['ECHOTOK_API_KEY'] = $k
    Write-Host "EchoTok: $(Mask-Key $k)" -ForegroundColor Green
}

# Pesquisa (opcionais)
$k = Get-KeyFromFile "$SourceRoot\Tavily\Tavilly.txt"
if ($k) {
    $keys['TAVILY_API_KEY'] = $k
    Write-Host "Tavily: $(Mask-Key $k)" -ForegroundColor Green
}

$k = Get-KeyFromFile "$SourceRoot\Brave\AgentMasterV2_gloliverx.txt"
if ($k) {
    $keys['BRAVE_API_KEY'] = $k
    Write-Host "Brave: $(Mask-Key $k)" -ForegroundColor Green
}

$k = Get-KeyFromFile "$SourceRoot\Linkup\linkup  api de pesquisa profunda.txt"
if ($k) {
    $keys['LINKUP_API_KEY'] = $k
    Write-Host "Linkup: $(Mask-Key $k)" -ForegroundColor Green
}

$k = Get-KeyFromFile "$SourceRoot\Context7\context7API.txt"
if ($k) {
    $keys['CONTEXT7_API_KEY'] = $k
    Write-Host "Context7: $(Mask-Key $k)" -ForegroundColor Green
}

# Token local do agente (gera novo se não existir)
if (-not $keys.ContainsKey('ECHOTIK_AGENT_TOKEN')) {
    $keys['ECHOTIK_AGENT_TOKEN'] = -join ((1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) }))
    Write-Host "ECHOTIK_AGENT_TOKEN: gerado localmente (32 hex)" -ForegroundColor Yellow
}

# Escreve .env
$envLines = @("# Chaves de API importadas em $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')", "")
foreach ($kv in $keys.GetEnumerator() | Sort-Object Name) {
    $envLines += "$($kv.Key)=$($kv.Value)"
}

Set-Content -Path $DestEnv -Value ($envLines -join "`n") -NoNewline -Force
Write-Host "`n✅ .env criado: $DestEnv ($($keys.Count) chaves)" -ForegroundColor Green

# Confirma que está no .gitignore
$gitIgnoreCheck = & git -C (Split-Path $DestEnv -Parent) check-ignore .env 2>&1
if ($gitIgnoreCheck -like '*.env*') {
    Write-Host "✅ .env está no .gitignore" -ForegroundColor Green
} else {
    Write-Warning "⚠️  .env NÃO está no .gitignore — adicione manualmente!"
}

Write-Host "`n=== Próximos passos ===`n" -ForegroundColor Cyan
Write-Host "1. Adicione 'python-dotenv' ao pyproject.toml"
Write-Host "2. Carregue .env no config.py com dotenv.load_dotenv()"
Write-Host "3. Implemente cliente LLM no /chat (OmniRoute/OpenRouter)"
Write-Host "4. Implemente embeddings semânticos (Gemini/OpenAI)"
Write-Host "5. Rotacione as chaves expostas no nome do arquivo (renomeie primeiro)`n"
