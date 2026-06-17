# ローカルLLM翻訳性能試験 実施手順

## 1. 試験の処理フロー

```text
ベンチマークCSV
      │
      ▼
run_benchmark.py
・LLMへ翻訳を要求
・翻訳結果を保存
・速度を記録
      │
      ▼
raw_results.csv
      │
      ▼
evaluate_rules.py
・数字や割合を検査
・用語を検査
・形式違反を検査
      │
      ▼
rule_evaluation.csv
      │
      ▼
aggregate_results.py
・モデル単位で集計
・カテゴリ別に集計
・速度と精度を比較
      │
      ▼
reports/*.csv
summary.json
```

---

## 2. 推奨ディレクトリ構成

```text
translation-benchmark/
├─ data/
│  └─ local_llm_translation_benchmark_ja.csv
├─ models/
│  ├─ model-a-Q4_K_M.gguf
│  └─ model-b-Q4_K_M.gguf
├─ scripts/
│  ├─ run_benchmark.py
│  ├─ evaluate_rules.py
│  └─ aggregate_results.py
├─ config/
│  ├─ translation_system_prompt.txt
│  └─ aggregation.yaml
├─ results/
└─ reports/
```

以降のコマンドは、`translation-benchmark`ディレクトリで実行する。

---

# 3. Python環境の準備

## 3.1 仮想環境の作成

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

PowerShellの実行ポリシーで拒否された場合は、現在のPowerShellだけ一時的に許可する。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

## 3.2 依存ライブラリのインストール

`requirements.txt`がある場合は、必ずそれを使用する。

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt`がない場合は、各プログラムの`import`を確認して必要なパッケージをインストールする。

一般的な構成例：

```powershell
python -m pip install requests pandas numpy pyyaml
```

COMETとSacreBLEUも使用する場合：

```powershell
python -m pip install sacrebleu unbabel-comet
```

---

# 4. プログラムの事前確認

## 4.1 構文チェック

```powershell
python -m py_compile `
  scripts\run_benchmark.py `
  scripts\evaluate_rules.py `
  scripts\aggregate_results.py
```

何も表示されなければ構文エラーはない。

---

## 4.2 CLIオプションの確認

```powershell
python scripts\run_benchmark.py --help
python scripts\evaluate_rules.py --help
python scripts\aggregate_results.py --help
```

実際のプログラムの`--help`と本手順に差がある場合は、実際の`--help`を優先する。

特に以下が実装されていることを確認する。

### run_benchmark.py

```text
--input
--output
--model-name
--model-file
--quantization
--run-id
--input-variant
--api-url
--warmup-count
--resume
--overwrite
```

### evaluate_rules.py

```text
--input
--output
--strict-terms
--overwrite
```

### aggregate_results.py

```text
--results-dir
--output-dir
--accuracy-run
--overwrite
```

---

# 5. 入力CSVの配置確認

以下のファイルが存在することを確認する。

```powershell
Test-Path .\data\local_llm_translation_benchmark_ja.csv
```

`True`が表示されること。

CSVの期待件数は以下とする。

| カテゴリ     |  件数 |
| -------- | --: |
| UI・操作説明  |  80 |
| 会話       | 100 |
| スキル・アイテム | 100 |
| クエスト     |  50 |
| 固有名詞     |  30 |
| OCRノイズ   |  40 |
| 合計       | 400 |

---

# 6. システムプロンプトの準備

`config/translation_system_prompt.txt`を作成する。

```text
You are a professional game localization translator.
Translate the English game text into natural Japanese.

Output only the Japanese translation.
Do not add explanations, headings, quotation marks, or alternative translations.
Preserve numbers, percentages, key names, placeholders, tags, and proper nouns.
Do not invent information that is not present in the source text.
```

すべてのモデルで同じファイルを使用する。

---

# 7. 試験条件の記録

試験前に次の情報を記録する。

```text
試験日時
OS
Pythonバージョン
llama.cppのバージョンまたはコミット
GPU
GPUドライバ
CPU
RAM
モデルファイル名
量子化形式
コンテキスト長
GPUオフロード設定
Flash Attention設定
サーバースロット数
Temperature
Top-p
Seed
最大出力トークン数
ウォームアップ回数
```

Pythonバージョン：

```powershell
python --version
```

llama-serverのバージョン：

```powershell
.\llama-server.exe --version
```

GPU情報の例：

```powershell
nvidia-smi
```

モデル間で変更してよい項目は、原則としてモデルファイルと量子化方式だけとする。

---

# 8. llama-serverの起動

以下はModel Aの例。

```powershell
.\llama-server.exe `
  -m ".\models\model-a-Q4_K_M.gguf" `
  --host 127.0.0.1 `
  --port 8080 `
  --ctx-size 8192 `
  --gpu-layers all `
  --flash-attn on `
  --parallel 1 `
  --perf
```

VRAM不足の場合は、`--gpu-layers all`を`auto`または固定レイヤー数へ変更する。

例：

```powershell
--gpu-layers 40
```

変更した値はモデルごとに記録する。

標準の速度比較では、同時実行を避けるため以下を使用する。

```text
--parallel 1
```

llama-serverを起動したPowerShellは、そのまま開いておく。

---

# 9. サーバーの起動確認

別のPowerShellを開く。

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

正常時：

```text
status
------
ok
```

モデル読み込み中は一時的にエラーになることがある。`status=ok`になってから試験を開始する。

---

# 10. スモークテスト

400件すべてを実行する前に、少数のIDだけで動作確認する。

```powershell
New-Item -ItemType Directory `
  -Force `
  -Path .\results\model-a\smoke | Out-Null
```

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\smoke\raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

確認事項：

```text
処理対象件数が6件である
request_successがtrueである
translation_jaに日本語訳が入っている
total_latency_msが記録されている
同じIDが重複していない
出力文字化けがない
```

---

# 11. スモークテストのルール評価

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\smoke\raw_results.csv `
  --output .\results\model-a\smoke\rule_evaluation.csv `
  --overwrite
```

確認事項：

```text
入力と出力の行数が同じ
rule_check_statusが出力されている
protected_tokens_passが出力されている
failure_reasonsが出力されている
```

スモークテストに問題がなければ、本試験へ進む。

---

# 12. Model Aの標準翻訳試験

## 12.1 出力ディレクトリの作成

```powershell
New-Item -ItemType Directory `
  -Force `
  -Path .\results\model-a\standard | Out-Null
```

---

## 12.2 Run 1

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\standard\run_001_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

---

## 12.3 Run 2

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\standard\run_002_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

---

## 12.4 Run 3

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\standard\run_003_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

標準試験では、各Runにつき400件出力されること。

精度の代表値にはRun 1を使用し、速度と出力安定性にはRun 1～3を使用する。

---

# 13. 公平な速度比較のための再起動方針

厳密に速度を比較する場合、各Runの前に以下を行う。

1. llama-serverを終了する
2. 同じ引数で再起動する
3. `/health`が`ok`になるまで確認する
4. `run_benchmark.py`の固定ウォームアップを実行する
5. 400件の本試験を開始する

すべてのモデルで同じ方針を使用する。

llama-serverを起動したままRun 1～3を連続実行する場合は、その方針もすべてのモデルで統一する。

---

# 14. OCR耐性試験

OCRカテゴリ40件について、正常英文とノイズ入り英文を別々に実行する。

## 14.1 ディレクトリの作成

```powershell
New-Item -ItemType Directory `
  -Force `
  -Path .\results\model-a\ocr_clean | Out-Null

New-Item -ItemType Directory `
  -Force `
  -Path .\results\model-a\ocr_noise | Out-Null
```

---

## 14.2 正常英文

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\ocr_clean\run_001_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

---

## 14.3 OCRノイズ英文

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\ocr_noise\run_001_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --overwrite
```

それぞれ40件出力されること。

---

# 15. 中断後の再開

試験が途中で停止した場合は、同じ出力ファイルを指定して`--resume`を使用する。

```powershell
python scripts\run_benchmark.py `
  --input .\data\local_llm_translation_benchmark_ja.csv `
  --output .\results\model-a\standard\run_001_raw_results.csv `
  --model-name model-a `
  --model-file model-a-Q4_K_M.gguf `
  --quantization Q4_K_M `
  --api-url http://127.0.0.1:8080/v1/chat/completions `
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
  --resume
```

失敗した行も再実行する場合：

```powershell
--resume --retry-failed
```

`--resume`と`--overwrite`は同時に使用しない。

---

# 16. Model Aのルール評価

標準試験3回をそれぞれ評価する。

## Run 1

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\standard\run_001_raw_results.csv `
  --output .\results\model-a\standard\run_001_rule_evaluation.csv `
  --overwrite
```

## Run 2

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\standard\run_002_raw_results.csv `
  --output .\results\model-a\standard\run_002_rule_evaluation.csv `
  --overwrite
```

## Run 3

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\standard\run_003_raw_results.csv `
  --output .\results\model-a\standard\run_003_rule_evaluation.csv `
  --overwrite
```

---

## 16.1 OCR正常英文のルール評価

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\ocr_clean\run_001_raw_results.csv `
  --output .\results\model-a\ocr_clean\run_001_rule_evaluation.csv `
  --overwrite
```

## 16.2 OCRノイズ英文のルール評価

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\ocr_noise\run_001_raw_results.csv `
  --output .\results\model-a\ocr_noise\run_001_rule_evaluation.csv `
  --overwrite
```

通常試験では`--strict-terms`を使用しない。

用語集付き試験を行った場合のみ使用する。

```powershell
python scripts\evaluate_rules.py `
  --input .\results\model-a\terminology\run_001_raw_results.csv `
  --output .\results\model-a\terminology\run_001_rule_evaluation.csv `
  --strict-terms `
  --overwrite
```

---

# 17. Model Aの試験完了確認

期待する構成：

```text
results/
└─ model-a/
   ├─ smoke/
   │  ├─ raw_results.csv
   │  └─ rule_evaluation.csv
   ├─ standard/
   │  ├─ run_001_raw_results.csv
   │  ├─ run_001_rule_evaluation.csv
   │  ├─ run_002_raw_results.csv
   │  ├─ run_002_rule_evaluation.csv
   │  ├─ run_003_raw_results.csv
   │  └─ run_003_rule_evaluation.csv
   ├─ ocr_clean/
   │  ├─ run_001_raw_results.csv
   │  └─ run_001_rule_evaluation.csv
   └─ ocr_noise/
      ├─ run_001_raw_results.csv
      └─ run_001_rule_evaluation.csv
```

スモークテスト用ディレクトリは、最終集計の対象外にする。

---

# 18. Model B以降の実行

Model Aのllama-serverを終了する。

```text
Ctrl+C
```

Model Bを起動する。

```powershell
.\llama-server.exe `
  -m ".\models\model-b-Q4_K_M.gguf" `
  --host 127.0.0.1 `
  --port 8080 `
  --ctx-size 8192 `
  --gpu-layers all `
  --flash-attn on `
  --parallel 1 `
  --perf
```

次の値をModel B用に変更する。

```text
model-a
↓
model-b

model-a-Q4_K_M.gguf
↓
model-b-Q4_K_M.gguf
```

以下を同じ手順で実施する。

1. `/health`確認
2. スモークテスト
3. 標準試験Run 1～3
4. OCR正常英文試験
5. OCRノイズ英文試験
6. `evaluate_rules.py`の実行

すべてのモデルで、プロンプト、Temperature、Seed、最大トークン数、コンテキスト長、ウォームアップ回数を統一する。

---

# 19. 集計対象からスモークテストを除外する

`aggregate_results.py`が`results`以下を再帰検索する場合、`smoke`ディレクトリを集計してしまう可能性がある。

以下のいずれかを使用する。

1. 集計プログラム側で`smoke`を除外する
2. `smoke`を`results`の外へ移動する
3. スモークテスト結果を削除する

移動例：

```powershell
New-Item -ItemType Directory -Force -Path .\smoke-results | Out-Null
Move-Item .\results\model-a\smoke .\smoke-results\model-a
Move-Item .\results\model-b\smoke .\smoke-results\model-b
```

---

# 20. 集計設定

`config/aggregation.yaml`の例：

```yaml
qualification:
  minimum_success_rate: 0.995
  minimum_protected_token_pass_rate: 0.99
  minimum_format_pass_rate: 0.99
  maximum_critical_error_rate: 0.01
  maximum_empty_output_rate: 0.005

aggregation:
  accuracy_run: 1
  latency_percentile_method: linear
  include_input_variants:
    - standard
    - ocr_clean
    - ocr_noise
```

Criticalエラーを人手評価していない段階では、`maximum_critical_error_rate`による判定を無効にするか、集計結果を`INSUFFICIENT_DATA`として扱う。

---

# 21. 全モデルの集計

```powershell
New-Item -ItemType Directory -Force -Path .\reports | Out-Null
```

```powershell
python scripts\aggregate_results.py `
  --results-dir .\results `
  --output-dir .\reports `
  --config .\config\aggregation.yaml `
  --accuracy-run 1 `
  --overwrite
```

COMET、chrF++、人手評価ファイルがない場合でも、実装要件どおりであれば以下は集計できる。

```text
リクエスト成功率
空出力率
ルール合格率
保護トークン合格率
用語一致率
形式合格率
p50レイテンシ
p95レイテンシ
生成tokens/sec
OCRによるルール合格率低下
複数Runの出力一致率
```

COMETやchrF++の入力ファイルがない場合、関連列は空欄とし、0として扱わない。

---

# 22. 集計後の出力確認

期待するファイル：

```text
reports/
├─ model_summary.csv
├─ category_summary.csv
├─ subcategory_summary.csv
├─ difficulty_summary.csv
├─ ocr_summary.csv
├─ ocr_subcategory_summary.csv
├─ stability_summary.csv
├─ failed_cases.csv
├─ data_quality_report.csv
├─ final_comparison.csv
└─ summary.json
```

---

# 23. 最初に確認するレポート

## 23.1 data_quality_report.csv

最初に確認する。

確認事項：

```text
複合キーの重複がない
モデル間でテスト件数が一致している
OCRのclean/noiseペアがそろっている
必須列が欠落していない
レイテンシが負数になっていない
```

データ品質に問題がある場合は、モデル比較へ進まず、該当試験を再実行する。

---

## 23.2 model_summary.csv

モデル全体の結果を確認する。

主な項目：

```text
success_rate
rule_pass_rate
protected_token_pass_rate
required_term_pass_rate
format_pass_rate
latency_p50_ms
latency_p95_ms
generation_tps_mean
exact_output_match_rate
qualification_status
```

---

## 23.3 category_summary.csv

次のカテゴリごとの弱点を確認する。

```text
UI・操作説明
会話
スキル・アイテム
クエスト
固有名詞
OCRノイズ
```

特に重視する項目：

| カテゴリ     | 確認内容        |
| -------- | ----------- |
| UI・操作説明  | 操作キー、否定、短文  |
| 会話       | 自然さ、文脈、口調   |
| スキル・アイテム | 数字、割合、条件、対象 |
| クエスト     | 方角、順番、期限、禁止 |
| 固有名詞     | 表記の一貫性      |
| OCRノイズ   | 誤認識への耐性     |

---

## 23.4 failed_cases.csv

ルール不合格の翻訳を確認する。

優先順位：

1. 数字・割合の欠落
2. 操作キーの欠落
3. 空出力
4. APIエラー
5. 翻訳以外の説明
6. Markdown混入
7. 用語不一致
8. 英文残存

---

## 23.5 ocr_summary.csv

以下を比較する。

```text
clean_rule_pass_rate
noise_rule_pass_rate
rule_pass_rate_drop
clean_protected_token_pass_rate
noise_protected_token_pass_rate
protected_token_pass_rate_drop
```

低下量が小さいモデルほど、OCR誤認識に強いと判断する。

---

## 23.6 stability_summary.csv

3回の実行結果を比較する。

```text
exact_output_match_rate
unique_translation_count_mean
latency_run_variation
generation_tps_run_variation
```

Temperatureが0でも一致率が低い場合は、次を確認する。

```text
Thinking設定
Seedの反映
チャットテンプレート
GPUバックエンド
並列処理
プロンプトキャッシュ
モデル固有の非決定性
```

---

# 24. COMET・chrF++を追加する場合

3本のプログラムだけでもルールと速度の比較は可能だが、意味的な精度比較にはCOMETまたはchrF++を追加する。

処理位置：

```text
run_benchmark.py
       │
       ├─ evaluate_rules.py
       │
       ├─ COMET評価
       │
       └─ chrF++評価
               │
               ▼
      aggregate_results.py
```

COMETやchrF++の結果ファイル形式は、`aggregate_results.py`が要求する列構成へ合わせる。

例：

```csv
model_name,run_id,id,input_variant,comet_score
model-a,1,UI001,standard,0.91
```

```csv
model_name,run_id,id,input_variant,sentence_chrf
model-a,1,UI001,standard,72.4
```

正式なchrF++比較では、文単位平均とコーパススコアを混同しない。

---

# 25. 人手評価

自動評価完了後、最低限以下を人手確認する。

```text
failed_cases.csvの全件
OCRノイズ40件
保護トークン不合格
COMET下位20%
各カテゴリからランダムに10件
モデル間で訳が大きく異なる項目
```

人手評価では、モデル名を隠して採点する。

記録項目例：

```text
model_name
id
input_variant
accuracy_score
fluency_score
game_localization_score
usability_pass
critical_error
human_comment
```

人手評価CSVを`results`または集計設定で指定された場所へ配置し、再度`aggregate_results.py`を実行する。

---

# 26. 最終的な選定基準

最初に以下で足切りする。

```text
リクエスト成功率
保護トークン合格率
形式合格率
空出力率
Criticalエラー率
```

足切りを通過したモデルについて、以下を比較する。

```text
精度：
COMET
chrF++
人手利用可能率
OCR耐性

速度：
p50レイテンシ
p95レイテンシ
生成tokens/sec
```

単一の総合点だけで決めず、次のように判断する。

```text
高精度かつ高速なモデル
精度低下が小さく大幅に高速なモデル
OCR耐性が高いモデル
VRAM上限内で安定動作するモデル
```

---

# 27. 再試験が必要な条件

以下に該当した場合は、そのRunを無効として再試験する。

```text
llama-serverが途中で再起動した
GPUで別の重い処理を実行した
GPU温度やクロックが大きく変動した
モデル間でサーバー引数が意図せず異なる
400件すべてが出力されていない
複合キーが重複している
API失敗が特定Runだけ異常に多い
プロンプトまたは生成設定が異なる
```

一部失敗行だけを再実行した場合は、最初の結果と重複しないようにする。

---

# 28. 実施完了チェックリスト

```text
□ Python仮想環境を使用した
□ 3プログラムの構文チェックに成功した
□ 各プログラムの--helpを確認した
□ 入力CSVが400件である
□ 全モデルで同じシステムプロンプトを使用した
□ 全モデルで同じ生成設定を使用した
□ llama-serverの起動引数を記録した
□ 各モデルでスモークテストを実施した
□ 標準試験を各モデル3回実施した
□ OCR正常英文を40件実施した
□ OCRノイズ英文を40件実施した
□ すべてのraw_resultsへルール評価を実施した
□ スモークテストを最終集計から除外した
□ data_quality_reportを確認した
□ failed_casesを人手確認した
□ p50とp95を比較した
□ 保護トークン合格率を比較した
□ OCR耐性を比較した
□ 最終結果と実行条件を保存した
```
