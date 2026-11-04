@echo off
chcp 65001 >nul
title TikTok Agent ADS - Inicializador

echo ============================================================
echo   TIKTOK AGENT ADS - Sidecar + Mass Messaging HITL
echo ============================================================
echo.

REM Verifica se esta no diretorio correto
if not exist "compose.yaml" (
    echo ERRO: Execute este script na raiz do projeto Tiktok_Agent_ADS
    pause
    exit /b 1
)

REM Verifica Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Docker nao encontrado. Instale Docker Desktop.
    pause
    exit /b 1
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Docker Compose v2+ nao encontrado.
    pause
    exit /b 1
)

echo [OK] Docker e Docker Compose encontrados

REM Verifica/cria .env
if not exist ".env" (
    echo.
    echo [AVISO] .env nao encontrado. Criando a partir do .env.example...
    copy .env.example .env >nul
    echo.
    echo ============================================================
    echo  IMPORTANTE: Edite o arquivo .env com suas credenciais!
    echo ============================================================
    echo.
    echo Credenciais obrigatorias:
    echo   - ECHOTIK_USERNAME / ECHOTIK_PASSWORD (EchoTik Data API, Basic Auth)
    echo   - GEMINI_API_KEY (para embeddings semanticos)
    echo   - OMNIROUTE_API_KEY / OPENROUTER_API_KEY (para LLM)
    echo   - ECHOTIK_AGENT_TOKEN (token do sidecar, gere um forte)
    echo   - HERMES_HTTP_TOKEN (token do Hermes monorepo)
    echo.
    echo Pressione ENTER apos editar o .env...
    pause
) else (
    echo [OK] .env encontrado
)

REM Cria diretorios necessarios
if not exist "data" mkdir data
if not exist "data\skills" mkdir data\skills
if not exist "data\market-reports" mkdir data\market-reports
if not exist "data\opencode" mkdir data\opencode
if not exist "data\hermes-cli" mkdir data\hermes-cli

echo.
echo [INFO] Subindo containers com Docker Compose...
docker compose up -d --build

echo.
echo [INFO] Aguardando health check (max 60s)...
set /a elapsed=0
set /a maxWait=60
set /a interval=3

:wait_loop
if %elapsed% geq 60 (
    echo.
    echo [AVISO] Timeout aguardando health check.
    echo Verifique logs: docker compose logs -f echotik-agent
    goto :show_summary
)

timeout /t %interval% /nobreak >nul
set /a elapsed+=%interval%

curl -s -f http://127.0.0.1:5077/health >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo [OK] Sidecar saudavel!
    goto :show_summary
)

echo . 
goto :wait_loop

:show_summary
echo.
echo ============================================================
echo   TIKTOK AGENT ADS INICIADO
echo ============================================================
echo.
echo Sidecar API:      http://127.0.0.1:5077
echo Docs (Swagger):   http://127.0.0.1:5077/docs
echo Health Check:     http://127.0.0.1:5077/health
echo.
echo Endpoints Principais:
echo   POST   /chat                    - Chat com agente (HITL + skills)
echo   POST   /echotok/import          - Importa produtos EchoTok
echo   GET    /echotok/status          - Status cota EchoTok
echo   GET    /hermes/health           - Status Hermes (research/market)
echo   GET    /hermes/research         - Pesquisa web
echo   POST   /hermes/market/analyze   - Analise de mercado com LLM
echo   POST   /messaging/batches       - Cria lote para HITL
echo   POST   /messaging/batches/^<id^>/approve - Aprova lote e agenda
echo   GET    /messaging/queue/stats   - Stats da fila de envio
echo.
echo Dados:
echo   SQLite:      .\data\echotik.db
echo   LanceDB:     .\data\lancedb
echo   Skills:      .\data\skills\strategic_skills.json
echo.
echo Logs:
echo   docker compose logs -f echotik-agent
echo.
echo Para parar: docker compose down
echo.

REM Testa health check final
curl -s http://127.0.0.1:5077/health >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Sidecar respondendo corretamente!
) else (
    echo [AVISO] Sidecar ainda nao responde. Verifique logs: docker compose logs -f echotik-agent
)

pause