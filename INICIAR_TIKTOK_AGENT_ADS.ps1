<#>
.SYNOPSIS
    Inicializa o Tiktok_Agent_ADS (Sidecar Python + Mass Messaging HITL)

.DESCRIPTION
    Este script inicializa o ecossistema completo:
    1. Verifica pré-requisitos (Docker, Docker Compose, .env)
    2. Cria .env a partir do .env.example se não existir
    3. Sobe containers Docker (sidecar + volumes)
    3. Verifica health checks
    4. Mostra URLs de acesso

.NOTES
    Autor: EchoTok Hub Team
    Versão: 1.0
    Requer: Docker Desktop + Docker Compose v2+
#>

param(
    [switch]$ForceRebuild,
    [switch]$Detach,
    [switch]$NoCache,
    [string]$EnvFile = ".env"
)

# Configurações
$ProjectRoot = "F:\Projetos2026\Tiktok_Agent_ADS"
$EnvExample = "$ProjectRoot\.env.example"
$EnvFile = "$ProjectRoot\$EnvFile"
$ComposeFile = "$ProjectRoot\compose.yaml"
$DataDir = "$ProjectRoot\data"
$SkillsDir = "$ProjectRoot\data\skills"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🚀 INICIAR TIKTOK AGENT ADS - Sidecar + Mass Messaging" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Verifica se está no diretório correto
if (-not (Test-Path $ProjectRoot)) {
    Write-Error "❌ Diretório do projeto não encontrado: $ProjectRoot"
    exit 1
}

Set-Location $ProjectRoot

# 1. Verifica Docker
Write-Host "🔍 Verificando pré-requisitos..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version
    Write-Host "  ✅ Docker: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Error "❌ Docker não encontrado. Instale Docker Desktop."
    exit 1
}

try {
    $composeVersion = docker compose version
    Write-Host "  ✅ Docker Compose: $composeVersion" -ForegroundColor Green
} catch {
    Write-Error "❌ Docker Compose v2+ não encontrado."
    exit 1
}

# 2. Verifica/cria .env
Write-Host "`n📝 Verificando arquivo .env..." -ForegroundColor Yellow
if (-not (Test-Path $EnvFile)) {
    Write-Host "  ⚠️  .env não encontrado. Criando a partir do .env.example..." -ForegroundColor Yellow
    Copy-Item $EnvExample $EnvFile -Force
    Write-Host "  ✅ .env criado. EDITE O ARQUIVO COM SUAS CREDENCIAIS!" -ForegroundColor Red
    Write-Host "`n  Credenciais obrigatórias:" -ForegroundColor Yellow
    Write-Host "  - ECHOTIK_USERNAME / ECHOTIK_PASSWORD (EchoTik Data API, Basic Auth)" -ForegroundColor Yellow
    Write-Host "  - GEMINI_API_KEY (para embeddings semânticos)" -ForegroundColor Yellow
    Write-Host "  - OMNIROUTE_API_KEY / OPENROUTER_API_KEY (para LLM)" -ForegroundColor Yellow
    Write-Host "  - ECHOTIK_AGENT_TOKEN (token do sidecar, gere um forte)" -ForegroundColor Yellow
    Write-Host "  - HERMES_HTTP_TOKEN (token do Hermes monorepo)" -ForegroundColor Yellow
    Write-Host "`n  Pressione ENTER após editar o .env..." -ForegroundColor Cyan
    Read-Host | Out-Null
} else {
    Write-Host "  ✅ .env já existe" -ForegroundColor Green
}

# 3. Verifica credenciais críticas no .env
$envContent = Get-Content $EnvFile -Raw
$missingCreds = @()

if ($envContent -match 'ECHOTIK_USERNAME=$' -or $envContent -match 'ECHOTIK_USERNAME=\s*$') { $missingCreds += "ECHOTIK_USERNAME" }
if ($envContent -match 'ECHOTIK_PASSWORD=$' -or $envContent -match 'ECHOTIK_PASSWORD=\s*$') { $missingCreds += "ECHOTIK_PASSWORD" }
if ($envContent -match 'GEMINI_API_KEY=$' -or $envContent -match 'GEMINI_API_KEY=\s*$') { $missingCreds += "GEMINI_API_KEY (para embeddings)" }
if ($envContent -match 'ECHOTIK_AGENT_TOKEN=$' -or $envContent -match 'ECHOTIK_AGENT_TOKEN=\s*$') { $missingCreds += "ECHOTIK_AGENT_TOKEN" }

if ($missingCreds.Count -gt 0) {
    Write-Warning "`n⚠️  Credenciais faltando no .env:"
    $missingCreds | ForEach-Object { Write-Warning "  - $_" }
    Write-Host "`nDeseja continuar mesmo assim? (s/N): " -NoNewline
    $resp = Read-Host
    if ($resp -notmatch '^[sS]') { exit 1 }
}

# 4. Cria diretórios necessários
Write-Host "`n📁 Criando diretórios de dados..." -ForegroundColor Yellow
$dirs = @($DataDir, "$DataDir\skills", "$DataDir\market-reports", "$DataDir\opencode", "$DataDir\hermes-cli")
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  ✅ Criado: $dir" -ForegroundColor Green
    }
}

# 3. Verifica skills
if (-not (Test-Path "$SkillsDir\strategic_skills.json")) {
    Write-Warning "⚠️  strategic_skills.json não encontrado em $SkillsDir"
}

# 4. Build e sobe containers
Write-Host "`n🐳 Iniciando containers Docker..." -ForegroundColor Yellow

$composeArgs = @("compose", "-f", $ComposeFile)
if ($ForceRebuild) { $composeArgs += "--build" }
if ($NoCache) { $composeArgs += "--no-cache" }
if ($Detach) { $composeArgs += "-d" }

$composeArgs += "up"

Write-Host "  Executando: docker $($composeArgs -join ' ')" -ForegroundColor Gray

try {
    docker @composeArgs
} catch {
    Write-Error "❌ Falha ao subir containers: $_"
    exit 1
}

# 5. Aguarda health check
if ($Detach) {
    Write-Host "`n⏳ Aguardando health check (máx 60s)..." -ForegroundColor Yellow
    $maxWait = 60
    $elapsed = 0
    $interval = 3
    
    while ($elapsed -lt $maxWait) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:5077/health" -TimeoutSec 5 -ErrorAction Stop
            $health = $response.Content | ConvertFrom-Json
            if ($health.status -eq "ok") {
                Write-Host "`n✅ Sidecar saudável!" -ForegroundColor Green
                Write-Host "  LLM: $($health.llm)" -ForegroundColor Gray
                Write-Host "  Skills: $($health.skills)" -ForegroundColor Gray
                Write-Host "  Embedding: $($health.embedding)" -ForegroundColor Gray
                Write-Host "  RAG Skills: $($health.ragSkills)" -ForegroundColor Gray
                break
            }
        } catch {
            # Ignora erros de conexão
        }
        Start-Sleep -Seconds $interval
        $elapsed += $interval
        Write-Host "." -NoNewline -ForegroundColor Gray
    }
    
    if ($elapsed -ge $maxWait) {
        Write-Warning "`n⚠️  Timeout aguardando health check. Verifique logs: docker compose logs -f"
    }
}

# 6. Mostra resumo
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  ✅ TIKTOK AGENT ADS INICIADO" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📡 Sidecar API:      http://127.0.0.1:5077" -ForegroundColor Cyan
Write-Host "📚 Docs (Swagger):   http://127.0.0.1:5077/docs" -ForegroundColor Cyan
Write-Host "❤️  Health Check:    http://127.0.0.1:5077/health" -ForegroundColor Cyan
Write-Host ""
Write-Host "📋 Endpoints Principais:" -ForegroundColor Yellow
Write-Host "  POST   /chat                    - Chat com agente (HITL + skills)" -ForegroundColor Gray
Write-Host "  POST   /echotok/import          - Importa produtos EchoTok" -ForegroundColor Gray
Write-Host "  GET    /echotok/status          - Status cota EchoTok" -ForegroundColor Gray
Write-Host "  GET    /hermes/health           - Status Hermes (research/market)" -ForegroundColor Gray
Write-Host "  GET    /hermes/research         - Pesquisa web (tavily/serpapi/brave)" -ForegroundColor Gray
Write-Host "  POST   /hermes/market/analyze   - Análise de mercado com LLM" -ForegroundColor Gray
Write-Host "  POST   /messaging/batches       - Cria lote para HITL" -ForegroundColor Gray
Write-Host "  POST   /messaging/batches/{id}/approve - Aprova lote e agenda" -ForegroundColor Gray
Write-Host "  GET    /messaging/queue/stats   - Stats da fila de envio" -ForegroundColor Gray
Write-Host ""
Write-Host "📁 Dados:" -ForegroundColor Yellow
Write-Host "  SQLite:      $DataDir\echotik.db" -ForegroundColor Gray
Write-Host "  LanceDB:     $DataDir\lancedb" -ForegroundColor Gray
Write-Host "  Skills:      $SkillsDir\strategic_skills.json" -ForegroundColor Gray
Write-Host ""
Write-Host "📋 Logs:" -ForegroundColor Yellow
Write-Host "  docker compose logs -f echotik-agent" -ForegroundColor Gray
Write-Host ""
Write-Host "🛑 Para parar: docker compose down" -ForegroundColor Red
Write-Host ""

# Verifica se sidecar responde
try {
    $test = Invoke-WebRequest -Uri "http://127.0.0.1:5077/health" -TimeoutSec 3 -ErrorAction Stop
    if ($test.StatusCode -eq 200) {
        Write-Host "✅ Sidecar respondendo corretamente!" -ForegroundColor Green
    }
} catch {
    Write-Warning "⚠️  Sidecar ainda não responde. Verifique: docker compose logs echotik-agent"
}