# 今回のベンチマークで使用したコマンド

対象モデル: `gemma-4-E4B-it-Q4_K_M.gguf`

実施日: 2026-06-18

## 1. PowerShellのUTF-8設定と共通変数

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$Root = "G:\AI_Models\benchmark\translation-benchmark"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ModelName = "gemma-4-E4B-it-Q4_K_M"
$ModelFile = "gemma-4-E4B-it-Q4_K_M.gguf"
$ModelPath = Join-Path $Root "models\$ModelFile"
$ApiUrl = "http://127.0.0.1:8080/v1/chat/completions"

Set-Location $Root
```

## 2. Python 3.12の導入

```powershell
winget install `
  --id Python.Python.3.12 `
  --exact `
  --source winget `
  --accept-package-agreements `
  --accept-source-agreements `
  --silent
```

## 3. ベンチマーク用仮想環境の作成

```powershell
$SystemPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"

& $SystemPython -m venv .\.venv
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m pip --version
& .\.venv\Scripts\python.exe -m pip check
```

`scripts/requirements.txt`には外部パッケージ不要と記載されていたため、追加インストールは行っていない。

## 4. スクリプトの事前確認

### 構文チェック

```powershell
& $Python -m py_compile `
  .\scripts\run_benchmark.py `
  .\scripts\evaluate_rules.py `
  .\scripts\aggregate_results.py
```

### CLIオプション確認

```powershell
& $Python .\scripts\run_benchmark.py --help
& $Python .\scripts\evaluate_rules.py --help
& $Python .\scripts\aggregate_results.py --help
```

## 5. 入力データ確認

```powershell
$Rows = Import-Csv `
  -LiteralPath .\data\local_llm_translation_benchmark_ja.csv `
  -Encoding UTF8

$Rows.Count
$Rows | Group-Object category | Select-Object Name, Count
```

確認結果は合計400件。

## 6. 実行環境情報の確認

```powershell
& "C:\tools\llama.cpp\llama-server.exe" --version
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
Get-CimInstance Win32_Processor | Select-Object Name
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
Get-CimInstance Win32_OperatingSystem |
  Select-Object Caption, Version, OSArchitecture
```

## 7. llama-serverの起動

各標準RunおよびOCR試験の前に、同じ引数でサーバーを再起動した。

```powershell
$LogDir = Join-Path $Root "results\$ModelName\server"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$ServerArgs = @(
  "-m", $ModelPath,
  "--host", "127.0.0.1",
  "--port", "8080",
  "--ctx-size", "8192",
  "--gpu-layers", "all",
  "--flash-attn", "on",
  "--parallel", "1",
  "--perf"
)

$Server = Start-Process `
  -FilePath "C:\tools\llama.cpp\llama-server.exe" `
  -ArgumentList $ServerArgs `
  -WorkingDirectory "C:\tools\llama.cpp" `
  -RedirectStandardOutput (Join-Path $LogDir "stdout.log") `
  -RedirectStandardError (Join-Path $LogDir "stderr.log") `
  -WindowStyle Hidden `
  -PassThru

Set-Content `
  -LiteralPath (Join-Path $LogDir "llama-server.pid") `
  -Value $Server.Id `
  -Encoding utf8
```

## 8. サーバーの起動確認

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/props
```

`status=ok`を確認してから各試験を開始した。

## 9. スモークテスト

```powershell
$SmokeDir = ".\results\$ModelName\smoke"
New-Item -ItemType Directory -Force -Path $SmokeDir | Out-Null

& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$SmokeDir\raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 0 `
  --input-variant standard `
  --ids UI001,DLG001,SKL001,QST001,PN001,OCR001 `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 1 `
  --timeout 60 `
  --overwrite `
  --metadata-output "$SmokeDir\metadata.json"
```

## 10. スモークテストのルール評価

```powershell
& $Python .\scripts\evaluate_rules.py `
  --input "$SmokeDir\raw_results.csv" `
  --output "$SmokeDir\rule_evaluation.csv" `
  --overwrite
```

## 11. 標準翻訳試験 Run 1

Run 1の前に手順7の引数でサーバーを再起動した。

```powershell
$StandardDir = ".\results\$ModelName\standard"
New-Item -ItemType Directory -Force -Path $StandardDir | Out-Null

& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$StandardDir\run_001_raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 1 `
  --input-variant standard `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 5 `
  --timeout 60 `
  --retries 1 `
  --overwrite `
  --progress-interval 50 `
  --metadata-output "$StandardDir\run_001_metadata.json"
```

## 12. 標準翻訳試験 Run 2

Run 2の前に手順7の引数でサーバーを再起動した。

```powershell
& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$StandardDir\run_002_raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 2 `
  --input-variant standard `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 5 `
  --timeout 60 `
  --retries 1 `
  --overwrite `
  --progress-interval 100 `
  --metadata-output "$StandardDir\run_002_metadata.json"
```

## 13. 標準翻訳試験 Run 3

Run 3の前に手順7の引数でサーバーを再起動した。

```powershell
& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$StandardDir\run_003_raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 3 `
  --input-variant standard `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 5 `
  --timeout 60 `
  --retries 1 `
  --overwrite `
  --progress-interval 100 `
  --metadata-output "$StandardDir\run_003_metadata.json"
```

## 14. OCR正常英文試験

OCR clean試験前に手順7の引数でサーバーを再起動した。

スクリプトがカテゴリ絞り込み前に全行を検証するため、OCR以外の360行を除外する目的で、実装済みCLIオプションの`--skip-invalid-rows`を追加した。

```powershell
$OcrCleanDir = ".\results\$ModelName\ocr_clean"
New-Item -ItemType Directory -Force -Path $OcrCleanDir | Out-Null

& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$OcrCleanDir\run_001_raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 1 `
  --input-variant ocr_clean `
  --categories "OCRノイズ" `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 5 `
  --timeout 60 `
  --retries 1 `
  --skip-invalid-rows `
  --overwrite `
  --progress-interval 20 `
  --metadata-output "$OcrCleanDir\run_001_metadata.json"
```

## 15. OCRノイズ英文試験

OCR noise試験前に手順7の引数でサーバーを再起動した。

```powershell
$OcrNoiseDir = ".\results\$ModelName\ocr_noise"
New-Item -ItemType Directory -Force -Path $OcrNoiseDir | Out-Null

& $Python .\scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output "$OcrNoiseDir\run_001_raw_results.csv" `
  --model-name $ModelName `
  --model-file $ModelFile `
  --quantization Q4_K_M `
  --api-url $ApiUrl `
  --run-id 1 `
  --input-variant ocr_noise `
  --categories "OCRノイズ" `
  --system-prompt-file .\config\translation_system_prompt.txt `
  --temperature 0 `
  --top-p 1.0 `
  --seed 42 `
  --max-tokens 256 `
  --warmup-count 5 `
  --timeout 60 `
  --retries 1 `
  --skip-invalid-rows `
  --overwrite `
  --progress-interval 20 `
  --metadata-output "$OcrNoiseDir\run_001_metadata.json"
```

## 16. 全結果のルール評価

```powershell
$EvaluationJobs = @(
  @("standard", "run_001"),
  @("standard", "run_002"),
  @("standard", "run_003"),
  @("ocr_clean", "run_001"),
  @("ocr_noise", "run_001")
)

foreach ($Job in $EvaluationJobs) {
  $Directory = ".\results\$ModelName\$($Job[0])"

  & $Python .\scripts\evaluate_rules.py `
    --input "$Directory\$($Job[1])_raw_results.csv" `
    --output "$Directory\$($Job[1])_rule_evaluation.csv" `
    --overwrite
}
```

## 17. スモーク結果の集計対象外への移動

```powershell
New-Item -ItemType Directory -Force `
  -Path ".\smoke-results\$ModelName" | Out-Null

Move-Item `
  -LiteralPath ".\results\$ModelName\smoke" `
  -Destination ".\smoke-results\$ModelName\smoke"
```

最初に試した`gemma-3-4b-pt-q4_0.gguf`の不合格スモーク結果も、`results`から`smoke-results`へ移動した。

## 18. 未実施モデル用空ディレクトリの退避

集計器が空の`model-a`、`model-b`をデータ品質ERRORとして検出したため、削除せず退避した。

```powershell
New-Item -ItemType Directory -Force -Path .\unrun-results | Out-Null
Move-Item .\results\model-a .\unrun-results\model-a
Move-Item .\results\model-b .\unrun-results\model-b
```

## 19. 結果の集計

`config/aggregation.yaml`は存在しなかったため、README例と一致するスクリプト内蔵のqualification既定値を使用した。

```powershell
& $Python .\scripts\aggregate_results.py `
  --results-dir .\results `
  --output-dir .\reports `
  --models $ModelName `
  --accuracy-run 1 `
  --overwrite
```

## 20. 集計結果の確認

```powershell
Import-Csv .\reports\data_quality_report.csv -Encoding UTF8

Import-Csv .\reports\model_summary.csv -Encoding UTF8 |
  Format-List *

Import-Csv .\reports\ocr_summary.csv -Encoding UTF8 |
  Format-List *

Import-Csv .\reports\stability_summary.csv -Encoding UTF8 |
  Format-List *
```

再集計後の`data_quality_report.csv`は0件だった。

## 21. llama-serverの終了

```powershell
$PidFile = ".\results\$ModelName\server\llama-server.pid"
$ServerPid = [int](Get-Content -LiteralPath $PidFile -Encoding UTF8)
$Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue

if ($Process -and $Process.ProcessName -eq "llama-server") {
  Stop-Process -Id $ServerPid
  Wait-Process -Id $ServerPid -Timeout 30 -ErrorAction SilentlyContinue
}
```

## 22. 後日評価用Python 3.11環境の構築

COMET 2.2.7のCLIがPython 3.12では互換エラーになったため、Python 3.11で`.venv-comet`を構築した。

### Python 3.11の導入

```powershell
winget install `
  --id Python.Python.3.11 `
  --exact `
  --source winget `
  --accept-package-agreements `
  --accept-source-agreements `
  --silent
```

### 仮想環境とパッケージの作成

```powershell
$Python311 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
$EvaluationPython = ".\.venv-comet\Scripts\python.exe"

& $Python311 -m venv .\.venv-comet
& $EvaluationPython -m pip install --upgrade pip
& $EvaluationPython -m pip install `
  sacrebleu `
  unbabel-comet `
  "setuptools<81"
```

### 環境確認

```powershell
& $EvaluationPython --version
& $EvaluationPython -m pip check
& .\.venv-comet\Scripts\sacrebleu.exe --version
& .\.venv-comet\Scripts\comet-score.exe --help
& .\.venv-comet\Scripts\comet-compare.exe --help

& $EvaluationPython -c @"
import torch
print(torch.__version__)
print(torch.cuda.is_available())
"@
```

確認結果:

- Python 3.11.9
- SacreBLEU 2.6.0
- unbabel-comet 2.2.7
- PyTorch 2.12.1 CPU版
- `pip check`: 問題なし
- `comet-score --help`: 終了コード0
- `comet-compare --help`: 終了コード0

COMET評価モデルのダウンロードおよびSacreBLEU/COMETのスコア計算は、今回は実施していない。
