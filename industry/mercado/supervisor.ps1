# Supervisor da base de mercado: roda as etapas até o fim, sem ninguém olhando.
#
#   powershell -ExecutionPolicy Bypass -File industry\mercado\supervisor.ps1 [-DesdeFornecedores 1380]
#
# - Mantém o Windows acordado enquanto roda (SetThreadExecutionState).
# - Antes de cada tentativa garante Docker Desktop e o Postgres do compose de pé; o Docker
#   caía quando a máquina suspendia ou reiniciava e as coletas morriam junto.
# - Cada etapa é repetida até terminar; as coletas são resumíveis, então repetir não duplica.
# Etapas: cadastro SICAF -> coleta até ontem -> modalidades -> nichos -> universo de prospects.
param([int]$DesdeFornecedores = 0)

# Também roda no logon do Windows (atalho na pasta Inicializar), para continuar depois de um
# reinício: um só por vez (mutex), e etapa concluída fica marcada em .data/mercado_etapa_*.ok.
$ErrorActionPreference = "Continue"
$unico = New-Object System.Threading.Mutex($false, "Global\mercado_supervisor")
if (-not $unico.WaitOne(0)) { exit }
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # pasta industry/
$dados = Join-Path $raiz ".data"
$logSup = Join-Path $dados "mercado_supervisor.log"
New-Item -ItemType Directory -Force $dados | Out-Null

function Feita($etapa) { Test-Path (Join-Path $dados "mercado_etapa_$etapa.ok") }
function Marcar($etapa) { Set-Content -Path (Join-Path $dados "mercado_etapa_$etapa.ok") -Value (Get-Date -Format s) }

if ($DesdeFornecedores -le 0) {
    # retoma o SICAF da última página registrada (o log de cada tentativa vai para .historico)
    $DesdeFornecedores = 1
    $h = Join-Path $dados "mercado_fornecedores.log.historico"
    foreach ($f in @($h, (Join-Path $dados "mercado_fornecedores.log"))) {
        if (Test-Path $f) {
            $m = [regex]::Matches((Get-Content $f -Raw), "gina (\d+)/")
            if ($m.Count) { $DesdeFornecedores = [math]::Max($DesdeFornecedores, [int]$m[$m.Count - 1].Groups[1].Value) }
        }
    }
}

Add-Type -Namespace Win -Name Energia -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[Win.Energia]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

function Log($m) { $l = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"; Add-Content -Path $logSup -Value $l -Encoding utf8; Write-Output $l }

function Garantir-Docker {
    for ($t = 0; $t -lt 10; $t++) {
        docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            Log "docker fora; abrindo o Docker Desktop"
            Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
            for ($i = 0; $i -lt 60; $i++) { Start-Sleep 5; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
        }
        Push-Location $raiz
        cmd /c "docker compose up -d postgres >nul 2>&1"
        for ($i = 0; $i -lt 30; $i++) {
            cmd /c "docker compose exec -T postgres pg_isready -U industry >nul 2>&1"
            if ($LASTEXITCODE -eq 0) { Pop-Location; return }
            Start-Sleep 5
        }
        Pop-Location
        Log "postgres não respondeu; tentando de novo"
        Start-Sleep 30
    }
    Log "docker/postgres não subiram depois de 10 tentativas"
}

function Rodar($nome, [string[]]$argumentos) {
    # roda um módulo python e devolve o texto do log (stderr, onde vai o logging)
    Garantir-Docker
    $log = Join-Path $dados "mercado_$nome.log"
    Log "começa $nome $($argumentos -join ' ')"
    $p = Start-Process -FilePath "python" -ArgumentList (@("-m") + $argumentos) -WorkingDirectory $raiz `
        -RedirectStandardError $log -RedirectStandardOutput "$log.out" -WindowStyle Hidden -PassThru -Wait
    Add-Content -Path "$log.historico" -Value (Get-Content $log -Raw) -Encoding utf8
    $texto = (Get-Content $log -Raw) + "`n" + (Get-Content "$log.out" -Raw)
    Log "termina $nome (código $($p.ExitCode))"
    return $texto
}

# 1. cadastro SICAF: repete de onde parou até o log dizer "fim"
$desde = $DesdeFornecedores
for ($t = 0; $t -lt 50 -and -not (Feita "fornecedores"); $t++) {
    $txt = Rodar "fornecedores" @("industry.mercado.fornecedores", "--desde", "$desde")
    if ($txt -match "\[fornecedores\] fim") { Marcar "fornecedores"; break }
    $m = [regex]::Matches($txt, "gina (\d+)/")
    if ($m.Count) { $desde = [int]$m[$m.Count - 1].Groups[1].Value }
    Start-Sleep 60
}

# 2. coleta até ontem (PNCP desde 28/10 e o Compras.gov.br que ainda estava pendente)
for ($t = 0; $t -lt 20 -and -not (Feita "coleta"); $t++) {
    $txt = Rodar "coleta" @("industry.mercado.coleta", "--inicio", "2026-09-28")
    if ($txt -match '\{"contratacoes"') { Marcar "coleta"; break }
    Start-Sleep 60
}

# 3. modalidades: até não sobrar compra pendente
for ($t = 0; $t -lt 200 -and -not (Feita "modalidades"); $t++) {
    $txt = Rodar "modalidades" @("industry.mercado.modalidades")
    if ($txt -match "\] 0 compras pendentes") { Marcar "modalidades"; break }
    Start-Sleep 30
}

# 4. nichos e universo de prospects
foreach ($etapa in @("nichos", "prospects")) {
    for ($t = 0; $t -lt 5 -and -not (Feita $etapa); $t++) {
        $txt = Rodar $etapa @("industry.mercado.$etapa")
        if ($txt -match "\[$etapa\] \{") { Marcar $etapa; break }
        Start-Sleep 120
    }
}

Log "supervisor terminou todas as etapas"
Set-Content -Path (Join-Path $dados "mercado_supervisor.fim") -Value (Get-Date -Format s)
[Win.Energia]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
