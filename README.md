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

# chrF++・COMET 翻訳精度試験 実施手順

## 24. 評価対象

精度評価では、原則として以下を使用する。

```text
標準翻訳試験：
results/<model-name>/standard/run_001_raw_results.csv

OCR正常文：
results/<model-name>/ocr_clean/run_001_raw_results.csv

OCRノイズ文：
results/<model-name>/ocr_noise/run_001_raw_results.csv
```

標準翻訳の精度代表値には`Run 1`を使用する。

`Run 2`と`Run 3`は、主に速度と翻訳結果の安定性確認に使用する。複数Runの精度を比較する場合は、Runごとに別の評価結果として保存する。

---

# 25. 評価前の必須確認

精度評価に使用する`raw_results.csv`について、以下を満たしていることを確認する。

```text
標準試験：400件
OCR正常文：40件
OCRノイズ文：40件
request_successが全件true
translation_jaが全件空欄ではない
同じidが重複していない
input_variantが試験内容と一致している
```

API失敗や空出力がある場合、該当項目を除外して評価してはならない。

除外すると、失敗した翻訳がスコアに反映されず、モデルの評価が不当に高くなるためである。

以下のいずれかで対処する。

1. `run_benchmark.py --resume --retry-failed`で再実行する
2. 該当Runを無効として全体を再実行する
3. 不完全な結果として記録し、モデル比較には使用しない

---

# 26. 推奨ディレクトリ構成

以下を追加する。

```text
translation-benchmark/
├─ evaluation/
│  ├─ shared/
│  │  ├─ standard/
│  │  │  ├─ source.txt
│  │  │  ├─ reference.txt
│  │  │  └─ manifest.csv
│  │  ├─ ocr_clean/
│  │  │  ├─ source.txt
│  │  │  ├─ reference.txt
│  │  │  └─ manifest.csv
│  │  └─ ocr_noise/
│  │     ├─ source.txt
│  │     ├─ reference.txt
│  │     └─ manifest.csv
│  ├─ hypotheses/
│  │  ├─ model-a/
│  │  │  ├─ standard.txt
│  │  │  ├─ ocr_clean.txt
│  │  │  └─ ocr_noise.txt
│  │  └─ model-b/
│  │     ├─ standard.txt
│  │     ├─ ocr_clean.txt
│  │     └─ ocr_noise.txt
│  ├─ chrf/
│  └─ comet/
├─ results/
└─ reports/
```

---

# 27. 評価用ファイルの仕様

chrF++とCOMETでは、1件の翻訳を1行として保存したプレーンテキストファイルを使用する。

## 27.1 source.txt

モデルへ実際に入力した英文。

`raw_results.csv`の以下を使用する。

```text
input_text
```

`input_text`がない場合は、試験モードに応じて以下を使用する。

| 試験モード       | 使用列               |
| ----------- | ----------------- |
| `standard`  | `source_en`       |
| `ocr_clean` | `clean_source_en` |
| `ocr_noise` | `source_en`       |

COMETで使用する。chrF++では使用しない。

---

## 27.2 reference.txt

人間が作成した基準訳。

```text
reference_ja
```

chrF++とCOMETの両方で使用する。

---

## 27.3 hypothesis.txt

評価対象モデルが生成した訳文。

```text
translation_ja
```

本手順では、モデル名と試験モードが分かる名前にする。

例：

```text
evaluation/hypotheses/model-a/standard.txt
evaluation/hypotheses/model-b/standard.txt
```

---

## 27.4 manifest.csv

各行とテストIDの対応表。

推奨列：

```csv
line_no,model_name,run_id,id,category,subcategory,difficulty,input_variant
```

例：

```csv
line_no,model_name,run_id,id,category,subcategory,difficulty,input_variant
1,model-a,1,DLG001,会話,挨拶・日常,easy,standard
2,model-a,1,DLG002,会話,挨拶・日常,easy,standard
```

COMETの文単位スコアを元のテスト項目へ戻す際に使用する。

---

# 28. ファイル作成時の共通ルール

以下をすべてのファイルで統一する。

```text
文字コード：UTF-8
1件につき物理的に1行
並び順：id昇順
前後の空白：除去
セル内改行：半角スペースに置換
空行：作らない
```

`source.txt`、`reference.txt`、各モデルの訳文ファイルは、必ず同じ件数、同じ順番にする。

例えば標準試験の場合、次の3ファイルはすべて400行でなければならない。

```text
source.txt
reference.txt
model-a/standard.txt
```

---

# 29. PowerShellの確認

以下のファイル作成コマンドは、PowerShell 7を推奨する。

確認：

```powershell
$PSVersionTable.PSVersion
```

PowerShell 7は、通常以下で起動する。

```powershell
pwsh
```

以降のコマンドは、プロジェクトのルートディレクトリで実行する。

---

# 30. 評価用ディレクトリの作成

```powershell
New-Item -ItemType Directory -Force `
  -Path .\evaluation\shared\standard | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\shared\ocr_clean | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\shared\ocr_noise | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\hypotheses\model-a | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\hypotheses\model-b | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\chrf | Out-Null

New-Item -ItemType Directory -Force `
  -Path .\evaluation\comet | Out-Null
```

---

# 31. Model A標準試験から評価用ファイルを作成

## 31.1 CSVの読み込み

```powershell
$raw = Import-Csv `
  .\results\model-a\standard\run_001_raw_results.csv

$rows = $raw |
  Where-Object {
    $_.run_id -eq "1" -and
    $_.input_variant -eq "standard"
  } |
  Sort-Object id
```

---

## 31.2 件数確認

```powershell
$rows.Count
```

期待値：

```text
400
```

---

## 31.3 失敗・空出力確認

```powershell
(
  $rows |
  Where-Object {
    $_.request_success -notmatch "^(true|1)$" -or
    [string]::IsNullOrWhiteSpace($_.translation_ja)
  }
).Count
```

期待値：

```text
0
```

0以外の場合は、評価ファイルを作成せず、翻訳試験を修正または再実行する。

---

## 31.4 source.txtの作成

`input_text`が存在する場合：

```powershell
$rows |
  ForEach-Object {
    ($_.input_text -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\shared\standard\source.txt `
    -Encoding utf8
```

`input_text`が存在しない場合：

```powershell
$rows |
  ForEach-Object {
    ($_.source_en -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\shared\standard\source.txt `
    -Encoding utf8
```

---

## 31.5 reference.txtの作成

```powershell
$rows |
  ForEach-Object {
    ($_.reference_ja -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\shared\standard\reference.txt `
    -Encoding utf8
```

---

## 31.6 Model Aの訳文ファイル作成

```powershell
$rows |
  ForEach-Object {
    ($_.translation_ja -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\hypotheses\model-a\standard.txt `
    -Encoding utf8
```

---

## 31.7 manifest.csvの作成

```powershell
$lineNumber = 0

$rows |
  ForEach-Object {
    $lineNumber++

    [PSCustomObject]@{
      line_no      = $lineNumber
      model_name   = $_.model_name
      run_id       = $_.run_id
      id           = $_.id
      category     = $_.category
      subcategory  = $_.subcategory
      difficulty   = $_.difficulty
      input_variant = $_.input_variant
    }
  } |
  Export-Csv `
    .\evaluation\shared\standard\manifest.csv `
    -NoTypeInformation `
    -Encoding utf8
```

---

# 32. Model B以降の訳文ファイル作成

Model Bについても同じ並び順で訳文ファイルを作る。

```powershell
$raw = Import-Csv `
  .\results\model-b\standard\run_001_raw_results.csv

$rows = $raw |
  Where-Object {
    $_.run_id -eq "1" -and
    $_.input_variant -eq "standard"
  } |
  Sort-Object id
```

件数と失敗数を確認した後、訳文ファイルを作る。

```powershell
$rows |
  ForEach-Object {
    ($_.translation_ja -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\hypotheses\model-b\standard.txt `
    -Encoding utf8
```

Model B用に`source.txt`と`reference.txt`を作り直す必要はない。

ただし、Model AとModel BでID、原文、基準訳が完全に同じであることを確認する。

---

# 33. 評価用ファイルの行数確認

```powershell
(Get-Content .\evaluation\shared\standard\source.txt).Count
(Get-Content .\evaluation\shared\standard\reference.txt).Count
(Get-Content .\evaluation\hypotheses\model-a\standard.txt).Count
(Get-Content .\evaluation\hypotheses\model-b\standard.txt).Count
```

期待値：

```text
400
400
400
400
```

空行の確認：

```powershell
(
  Get-Content .\evaluation\hypotheses\model-a\standard.txt |
  Where-Object {
    [string]::IsNullOrWhiteSpace($_)
  }
).Count
```

期待値：

```text
0
```

---

# 34. OCR評価用ファイルの作成

同じ方法で、次のファイルを作成する。

```text
evaluation/shared/ocr_clean/source.txt
evaluation/shared/ocr_clean/reference.txt
evaluation/shared/ocr_clean/manifest.csv
evaluation/hypotheses/model-a/ocr_clean.txt

evaluation/shared/ocr_noise/source.txt
evaluation/shared/ocr_noise/reference.txt
evaluation/shared/ocr_noise/manifest.csv
evaluation/hypotheses/model-a/ocr_noise.txt
```

OCR正常文の入力CSV：

```text
results/model-a/ocr_clean/run_001_raw_results.csv
```

OCRノイズ文の入力CSV：

```text
results/model-a/ocr_noise/run_001_raw_results.csv
```

それぞれの期待行数：

```text
40
```

OCRノイズに含まれるセル内改行は、評価用テキストへ出力する際に半角スペースへ置換する。

モデルはすでに改行を含むOCR入力で翻訳を実行しているため、翻訳モデルのOCR耐性評価自体は維持される。

---

# 35. chrF++環境の準備

既存の仮想環境を有効化する。

```powershell
.\.venv\Scripts\Activate.ps1
```

インストール：

```powershell
python -m pip install --upgrade pip
python -m pip install sacrebleu
```

バージョン確認：

```powershell
sacrebleu --version
```

使用したバージョンを試験記録へ保存する。

---

# 36. chrF++の標準設定

本試験では以下を固定する。

```text
文字n-gram次数：6
単語n-gram次数：2
Beta：2
大文字・小文字：区別する
空白文字：n-gramに含めない
```

コマンドオプション：

```text
--chrf-char-order 6
--chrf-word-order 2
--chrf-beta 2
```

`--chrf-word-order 2`を指定したものをchrF++として扱う。

すべてのモデルで同じオプションを使用する。

---

# 37. Model AのchrF++評価

```powershell
sacrebleu `
  .\evaluation\shared\standard\reference.txt `
  -i .\evaluation\hypotheses\model-a\standard.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  -f json |
Set-Content `
  .\evaluation\chrf\model-a_standard.json `
  -Encoding utf8
```

結果確認：

```powershell
Get-Content `
  .\evaluation\chrf\model-a_standard.json
```

確認する項目：

```text
score
signature
nrefs
case
nc
nw
space
version
```

特に以下を確認する。

```text
nw:2
nc:6
```

---

# 38. Model BのchrF++評価

```powershell
sacrebleu `
  .\evaluation\shared\standard\reference.txt `
  -i .\evaluation\hypotheses\model-b\standard.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  -f json |
Set-Content `
  .\evaluation\chrf\model-b_standard.json `
  -Encoding utf8
```

同じ手順をすべてのモデルについて実施する。

---

# 39. chrF++信頼区間

単一モデルの95%信頼区間も保存する場合：

```powershell
$env:SACREBLEU_SEED = "12345"

sacrebleu `
  .\evaluation\shared\standard\reference.txt `
  -i .\evaluation\hypotheses\model-a\standard.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  --confidence `
  --confidence-n 1000 `
  -f text |
Set-Content `
  .\evaluation\chrf\model-a_standard_confidence.txt `
  -Encoding utf8
```

すべてのモデルで同じシードとリサンプリング回数を使用する。

---

# 40. chrF++のモデル間有意差検定

最初に指定した訳文ファイルが基準モデルになる。

Model Aを基準としてModel Bを比較する例：

```powershell
$env:SACREBLEU_SEED = "12345"

sacrebleu `
  .\evaluation\shared\standard\reference.txt `
  -i `
    .\evaluation\hypotheses\model-a\standard.txt `
    .\evaluation\hypotheses\model-b\standard.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  --paired-bs `
  --paired-bs-n 1000 `
  -f text |
Set-Content `
  .\evaluation\chrf\model-a_vs_model-b_standard.txt `
  -Encoding utf8
```

結果には以下が含まれる。

```text
各モデルのchrF++スコア
95%信頼区間
基準モデルとの差
p値
```

一般的には、`p < 0.05`で差が統計的に有意と判断する。

ただし、有意差があっても実用上の差が大きいとは限らないため、スコア差そのものも確認する。

---

# 41. OCRのchrF++評価

## OCR正常文

```powershell
sacrebleu `
  .\evaluation\shared\ocr_clean\reference.txt `
  -i .\evaluation\hypotheses\model-a\ocr_clean.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  -f json |
Set-Content `
  .\evaluation\chrf\model-a_ocr_clean.json `
  -Encoding utf8
```

## OCRノイズ文

```powershell
sacrebleu `
  .\evaluation\shared\ocr_noise\reference.txt `
  -i .\evaluation\hypotheses\model-a\ocr_noise.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  -f json |
Set-Content `
  .\evaluation\chrf\model-a_ocr_noise.json `
  -Encoding utf8
```

計算する値：

```text
OCR chrF++低下量
= ocr_cleanのchrF++ - ocr_noiseのchrF++
```

低下量が小さいモデルほど、OCRノイズに強いと判断する。

---

# 42. カテゴリ別chrF++評価

カテゴリ別評価では、該当カテゴリだけを抽出した次のファイルを作成する。

```text
reference.txt
hypothesis.txt
```

対象カテゴリ：

```text
UI・操作説明
会話
スキル・アイテム
クエスト
固有名詞
OCRノイズ
```

例えば「UI・操作説明」だけを抽出する場合：

```powershell
$category = "UI・操作説明"

$rows = Import-Csv `
  .\results\model-a\standard\run_001_raw_results.csv |
  Where-Object {
    $_.input_variant -eq "standard" -and
    $_.category -eq $category
  } |
  Sort-Object id
```

出力先を作る。

```powershell
New-Item -ItemType Directory -Force `
  -Path .\evaluation\chrf\categories\UI | Out-Null
```

基準訳：

```powershell
$rows |
  ForEach-Object {
    ($_.reference_ja -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\chrf\categories\UI\reference.txt `
    -Encoding utf8
```

Model Aの訳文：

```powershell
$rows |
  ForEach-Object {
    ($_.translation_ja -replace "(`r`n|`n|`r)", " ").Trim()
  } |
  Set-Content `
    .\evaluation\chrf\categories\UI\model-a.txt `
    -Encoding utf8
```

評価：

```powershell
sacrebleu `
  .\evaluation\chrf\categories\UI\reference.txt `
  -i .\evaluation\chrf\categories\UI\model-a.txt `
  -m chrf `
  --chrf-char-order 6 `
  --chrf-word-order 2 `
  --chrf-beta 2 `
  -f json |
Set-Content `
  .\evaluation\chrf\categories\UI\model-a_result.json `
  -Encoding utf8
```

同じ操作を各カテゴリと各モデルで繰り返す。

---

# 43. chrF++集計ファイルの作成

各JSON結果の`score`と`signature`を読み取り、表計算ソフトで以下のCSVを作成する。

```text
evaluation/chrf/chrf_summary.csv
```

推奨列：

```csv
model_name,run_id,input_variant,scope,category,item_count,chrf_corpus,signature,result_file
```

例：

```csv
model_name,run_id,input_variant,scope,category,item_count,chrf_corpus,signature,result_file
model-a,1,standard,all,,400,58.42,"nrefs:1|case:mixed|eff:yes|nc:6|nw:2|space:no|version:...","model-a_standard.json"
model-b,1,standard,all,,400,60.18,"nrefs:1|case:mixed|eff:yes|nc:6|nw:2|space:no|version:...","model-b_standard.json"
model-a,1,ocr_clean,all,OCRノイズ,40,61.30,"...","model-a_ocr_clean.json"
model-a,1,ocr_noise,all,OCRノイズ,40,54.20,"...","model-a_ocr_noise.json"
```

正式なchrF++比較では`chrf_corpus`を使用する。

文単位chrFの平均値を、コーパスchrF++の代わりに使用しない。

---

# 44. COMET専用環境の準備

COMETはPyTorchなど大きな依存関係を使用するため、専用の仮想環境を推奨する。

```powershell
python -m venv .venv-comet
.\.venv-comet\Scripts\Activate.ps1
```

インストール：

```powershell
python -m pip install --upgrade pip
python -m pip install unbabel-comet
```

確認：

```powershell
comet-score --help
comet-compare --help
```

バージョン情報：

```powershell
python -m pip show unbabel-comet
```

使用したCOMETバージョンを試験記録へ保存する。

---

# 45. COMET評価モデル

本試験では、参照訳ありモデルを固定して使用する。

```text
Unbabel/wmt22-comet-da
```

COMETモデルを途中で変更した結果は、同じ表で直接比較しない。

初回実行時はモデルデータのダウンロードが行われるため、インターネット接続が必要になる。

---

# 46. COMET実行前のGPU準備

COMETをGPUで実行する場合、llama-serverを終了する。

```text
Ctrl+C
```

理由：

```text
翻訳モデルがGPUメモリを使用したままだと、
COMETモデルをロードできない可能性があるため
```

GPU使用状況を確認する。

```powershell
nvidia-smi
```

---

# 47. Model AのCOMET評価

GPUを1基使用する場合：

```powershell
comet-score `
  -s .\evaluation\shared\standard\source.txt `
  -t .\evaluation\hypotheses\model-a\standard.txt `
  -r .\evaluation\shared\standard\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 1 `
  --to_json .\evaluation\comet\model-a_standard.json
```

CPUで実行する場合：

```powershell
comet-score `
  -s .\evaluation\shared\standard\source.txt `
  -t .\evaluation\hypotheses\model-a\standard.txt `
  -r .\evaluation\shared\standard\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 0 `
  --to_json .\evaluation\comet\model-a_standard.json
```

CPU評価とGPU評価で速度は異なるが、同じモデルと同じCOMETバージョンであれば品質スコア比較に使用できる。

ただし、評価環境は試験記録へ残す。

---

# 48. COMETのシステムスコアだけを保存する場合

```powershell
comet-score `
  -s .\evaluation\shared\standard\source.txt `
  -t .\evaluation\hypotheses\model-a\standard.txt `
  -r .\evaluation\shared\standard\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 1 `
  --quiet `
  --only_system |
Set-Content `
  .\evaluation\comet\model-a_standard_system_score.txt `
  -Encoding utf8
```

詳細分析ではJSON結果を保存し、システムスコアだけの実行は確認用として扱う。

---

# 49. Model B以降のCOMET評価

```powershell
comet-score `
  -s .\evaluation\shared\standard\source.txt `
  -t .\evaluation\hypotheses\model-b\standard.txt `
  -r .\evaluation\shared\standard\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 1 `
  --to_json .\evaluation\comet\model-b_standard.json
```

同じCOMETモデル、同じGPU設定、同じ入力ファイルを使用する。

---

# 50. COMETのモデル間比較

複数モデルの統計的な差を確認する。

```powershell
comet-compare `
  -s .\evaluation\shared\standard\source.txt `
  -t `
    .\evaluation\hypotheses\model-a\standard.txt `
    .\evaluation\hypotheses\model-b\standard.txt `
  -r .\evaluation\shared\standard\reference.txt |
Set-Content `
  .\evaluation\comet\model-a_vs_model-b_standard.txt `
  -Encoding utf8
```

3モデル以上の場合：

```powershell
comet-compare `
  -s .\evaluation\shared\standard\source.txt `
  -t `
    .\evaluation\hypotheses\model-a\standard.txt `
    .\evaluation\hypotheses\model-b\standard.txt `
    .\evaluation\hypotheses\model-c\standard.txt `
  -r .\evaluation\shared\standard\reference.txt |
Set-Content `
  .\evaluation\comet\all_models_standard.txt `
  -Encoding utf8
```

`comet-compare --help`で`--model`オプションが利用可能な場合は、採点時と同じモデルを明示する。

```text
Unbabel/wmt22-comet-da
```

---

# 51. OCRのCOMET評価

## OCR正常文

```powershell
comet-score `
  -s .\evaluation\shared\ocr_clean\source.txt `
  -t .\evaluation\hypotheses\model-a\ocr_clean.txt `
  -r .\evaluation\shared\ocr_clean\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 1 `
  --to_json .\evaluation\comet\model-a_ocr_clean.json
```

## OCRノイズ文

```powershell
comet-score `
  -s .\evaluation\shared\ocr_noise\source.txt `
  -t .\evaluation\hypotheses\model-a\ocr_noise.txt `
  -r .\evaluation\shared\ocr_noise\reference.txt `
  --model Unbabel/wmt22-comet-da `
  --gpus 1 `
  --to_json .\evaluation\comet\model-a_ocr_noise.json
```

計算する値：

```text
OCR COMET低下量
= ocr_cleanのCOMET - ocr_noiseのCOMET
```

低下量が小さいモデルほど、OCR誤認識の影響を受けにくいと判断する。

---

# 52. COMET文単位スコアCSVの作成

`aggregate_results.py`がCSVを要求する場合は、COMETのJSON結果と`manifest.csv`を組み合わせて以下を作成する。

```text
evaluation/comet/comet_scores.csv
```

推奨列：

```csv
model_name,run_id,id,input_variant,comet_score
```

例：

```csv
model_name,run_id,id,input_variant,comet_score
model-a,1,DLG001,standard,0.9123
model-a,1,DLG002,standard,0.8841
model-a,1,DLG003,standard,0.9352
```

作成方法：

1. `manifest.csv`をExcelで開く
2. `comet_score`列を追加する
3. COMET JSON内の文単位スコアを行順に貼り付ける
4. `line_no`とスコアの件数が一致していることを確認する
5. 不要な列を削除する
6. UTF-8 CSVとして保存する

標準試験では400件、OCR試験では40件になっていることを確認する。

COMET JSONの具体的なフィールド構成はバージョンによって変わる可能性があるため、JSON内の文単位スコア配列を確認してから取り込む。

---

# 53. COMETシステムスコア集計ファイル

以下のCSVも作成する。

```text
evaluation/comet/comet_summary.csv
```

推奨列：

```csv
model_name,run_id,input_variant,scope,category,item_count,comet_system_score,comet_model,result_file
```

例：

```csv
model_name,run_id,input_variant,scope,category,item_count,comet_system_score,comet_model,result_file
model-a,1,standard,all,,400,0.8421,Unbabel/wmt22-comet-da,model-a_standard.json
model-b,1,standard,all,,400,0.8563,Unbabel/wmt22-comet-da,model-b_standard.json
model-a,1,ocr_clean,all,OCRノイズ,40,0.8310,Unbabel/wmt22-comet-da,model-a_ocr_clean.json
model-a,1,ocr_noise,all,OCRノイズ,40,0.7614,Unbabel/wmt22-comet-da,model-a_ocr_noise.json
```

---

# 54. aggregate_results.pyへの配置

`aggregate_results.py`が各モデルの結果ディレクトリを検索する実装の場合、作成したスコアCSVを所定の場所へコピーする。

例：

```text
results/
└─ model-a/
   ├─ standard/
   │  ├─ run_001_raw_results.csv
   │  ├─ run_001_rule_evaluation.csv
   │  ├─ run_001_comet_scores.csv
   │  └─ run_001_chrf_summary.csv
   ├─ ocr_clean/
   │  ├─ run_001_raw_results.csv
   │  ├─ run_001_comet_scores.csv
   │  └─ run_001_chrf_summary.csv
   └─ ocr_noise/
      ├─ run_001_raw_results.csv
      ├─ run_001_comet_scores.csv
      └─ run_001_chrf_summary.csv
```

実際のファイル名と列構成は、実装済みの`aggregate_results.py --help`またはREADMEに合わせる。

確認：

```powershell
python scripts\aggregate_results.py --help
```

配置後、再集計する。

```powershell
python scripts\aggregate_results.py `
  --results-dir .\results `
  --output-dir .\reports `
  --config .\config\aggregation.yaml `
  --accuracy-run 1 `
  --overwrite
```

---

# 55. 結果の確認

## chrF++

主に以下を確認する。

```text
全400件のコーパスchrF++
カテゴリ別コーパスchrF++
95%信頼区間
モデル間のp値
OCRによるスコア低下量
メトリック署名
```

高いほど、基準訳との文字・単語表現が近い。

---

## COMET

主に以下を確認する。

```text
システムスコア
文単位スコア
カテゴリ別平均
低スコア項目
モデル間の有意差
OCRによるスコア低下量
```

COMETの絶対値だけで固定的な合格・不合格を決めず、同じ評価モデル、同じデータセット上でモデルを相対比較する。

---

# 56. 最終比較表

```csv
model_name,chrf_corpus,comet_system_score,ocr_chrf_drop,ocr_comet_drop,protected_token_pass_rate,critical_error_rate
model-a,58.42,0.8421,5.30,0.0696,0.995,
model-b,60.18,0.8563,12.80,0.1482,0.968,
```

判断例：

```text
Model B：
通常英文ではchrF++とCOMETが高い
ただしOCRノイズによる低下が大きい

Model A：
通常英文のスコアはModel Bより少し低い
OCR耐性と保護トークン維持率が高い
```

ゲーム画面のOCR翻訳用途では、通常英文のスコアだけでなく、OCR低下量、数字保持率、Criticalエラー率も合わせて判断する。

---

# 57. 実施完了チェックリスト

```text
□ 精度評価にはRun 1を使用した
□ 標準試験の全400件が成功している
□ OCR試験の各40件が成功している
□ source、reference、hypothesisの行数が一致している
□ 全ファイルをid順に並べた
□ セル内改行を半角スペースへ置換した
□ UTF-8で保存した
□ SacreBLEUのバージョンを記録した
□ chrF++に--chrf-word-order 2を指定した
□ chrF++のsignatureを保存した
□ chrF++の信頼区間を確認した
□ COMETのバージョンを記録した
□ COMETモデルを固定した
□ COMET実行前にllama-serverを停止した
□ COMETのJSON結果を保存した
□ manifestと文単位COMETスコアを対応させた
□ OCR clean/noiseの低下量を算出した
□ aggregate_results.pyを再実行した
□ chrF++とCOMETだけで最終判断していない
```


# 58. 人手評価

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

# 59. 最終的な選定基準

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

# 60. 再試験が必要な条件

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

# 61. 実施完了チェックリスト

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
