# Local-LLM-Manager

A Windows desktop GUI (Python + `tkinter`) that wraps **llama.cpp `llama-server`** for running local models (e.g. **Qwen3.8-27B**) with a full set of tuning parameters, live GPU/system monitoring, and OpenAI-compatible serving.

## Features

- **One-click Start / Stop** — a single toggle button launches or terminates `llama-server.exe` as a managed subprocess.
- **Parameter editor** — all key `llama-server` flags grouped into **Basic**, **Model**, and **Advanced** sections, with collapsible groups and a scrollable panel.
- **File / directory pickers** — `Browse…` buttons for the model, vision projection, chat template, and llama directory.
- **Config persistence** — parameters are saved to `local_llm_gui_config.json` on start and on close; `Save Config` and `Reset Defaults` are available at any time.
- **Live monitoring** — per-GPU utilization / memory / temperature (via `nvidia-smi`), plus system CPU and memory usage (via `psutil`), refreshed every 3 seconds.
- **Token counter** — accumulates tokens from the server's timing logs and shows the running total.
- **Status & health check** — polls the server `/health` endpoint and shows `Stopped` / `Starting` / `Running`; also detects externally-started servers.
- **Colored log console** — level-tagged (error / ready / warn / cmd / info) live log with **Clear** and **Save Log** actions.
- **Runtime lock** — parameter fields are locked while the server is running to prevent mid-run changes.

## Requirements

- **Python 3.10+** with `tkinter` (bundled with standard Python).
- **`psutil`** — for CPU / memory monitoring: `pip install psutil`.
- **`nvidia-smi`** on `PATH` (ships with the NVIDIA driver) — for GPU monitoring.
- A **llama.cpp** build containing `llama-server.exe` (e.g. `llama-bXXXX-bin-win-cuda-XXXX-x64/`).
- A **GGUF** model file (optionally an `mmproj` vision projection and a Jinja chat template).
- Windows (the GUI auto-detects the script/exe directory so the config and `NVFP4.bat` live beside the executable).

## Running

### From source

```powershell
pip install psutil
python local_llm_gui.py
```

### As a packaged executable

A prebuilt `LocalLLMManager.exe` can be launched directly (it looks for `local_llm_gui_config.json` and `NVFP4.bat` in its own folder).

### Rebuild the executable

```powershell
pip install pyinstaller
# stop any running instance first, then:
python -m PyInstaller --noconfirm --onefile --windowed `
    --name LocalLLMManager `
    --distpath .\dist --workpath .\build `
    local_llm_gui.py
```

The output lands in `dist\LocalLLMManager.exe`.

## Configuration

Values are stored in `local_llm_gui_config.json` next to the program. On launch the GUI seeds defaults, merges in any variables defined in `NVFP4.bat`, then overlays saved JSON. Only the **llama directory** and **model path** are strictly required; everything else is optional and only added to the command line when set.

### Basic Parameters

| Key | Label | Description | Default |
|-----|-------|-------------|---------|
| `HOST` | Host | Server listen address | `0.0.0.0` |
| `CHECK_HOST` | Check Host | IP for the health check (default `127.0.0.1`) | *(empty)* |
| `PORT` | Port | Service port | `8080` |
| `ALIAS` | Alias | OpenAI-compatible `--alias` | *(empty)* |

### Model Parameters

| Key | Label | Description | Default |
|-----|-------|-------------|---------|
| `MODEL_PATH` | Model | GGUF model path (**required**) | *(empty)* |
| `MMPROJ_PATH` | Vision Proj | `--mmproj` file (optional) | *(empty)* |
| `CHAT_TEMPLATE` | Chat Template | `--chat-template-file` (optional) | *(empty)* |
| `CTX_SIZE` | Context Size | `-c` context size | `8192` |
| `REASONING` | Reasoning | `--reasoning`, on/off (optional) | `off` |
| `REASONING_EFFORT` | Reasoning Effort | `--reasoning-effort`, e.g. `low` (optional) | `low` |
| `REASONING_PRESERVE` | Reasoning Preserve | `--reasoning-preserve` on/off (optional) | *(empty)* |
| `TEMPERATURE` | Temperature | `--temp` sampling temperature | `0.8` |
| `IMAGE_MIN_TOKENS` | Image Min Tokens | `--image-min-tokens` (optional) | `1024` |
| `MAX_TOKENS` | Max Tokens | `--max-tokens`, max output tokens (optional) | *(empty)* |

### Advanced Parameters

| Key | Label | Description | Default |
|-----|-------|-------------|---------|
| `LLAMA_DIR` | llama Dir | Directory of `llama-server.exe` (**required**) | *(empty)* |
| `THREADS` | Threads | `-t` thread count | `8` |
| `TBATCH` | Thread Batch | `-tb` thread batch size | `2048` |
| `BATCH` | Batch | `-b` batch size | `2048` |
| `UBATCH` | UBatch | `-ub` unit batch size | `2048` |
| `CTK` | CTK | `-ctk` KV cache type for K, e.g. `q8_0` | `q8_0` |
| `CTV` | CTV | `-ctv` KV cache type for V, e.g. `q8_0` | `q8_0` |
| `NGL` | NGL | `-ngl` layers offloaded to GPU, e.g. `999` | `999` |
| `SM` | SM | `-sm` split mode, e.g. `tensor` / `layer` | `tensor` |
| `PARALLEL` | Parallel | `--parallel`, number of parallel sequences | `1` |
| `FLASH_ATTN` | Flash Attn | `--flash-attn`, on/off/auto | `on` |
| `KV_UNIFIED` | KV Unified | `--kv-unified`, on/off (optional) | `on` |
| `TENSOR_SPLIT` | Tensor Split | `--tensor-split`, multi-GPU split ratio (optional) | *(empty)* |
| `SPEC_TYPE` | Spec Type | `--spec-type`, e.g. `draft-mtp` (optional) | *(empty)* |
| `SPEC_DRAFT_N_MAX` | Spec Draft N-Max | `--spec-draft-n-max` (optional) | `2` |
| `SPEC_DRAFT_P_MIN` | Spec Draft P-Min | `--spec-draft-p-min` (optional) | `0.6` |
| `SPEC_DRAFT_TYPE_K` | Spec Draft Type K | `--spec-draft-type-k`, e.g. `f16` (optional) | `f16` |
| `SPEC_DRAFT_TYPE_V` | Spec Draft Type V | `--spec-draft-type-v`, e.g. `f16` (optional) | `f16` |
| `START_TIMEOUT_S` | Start Timeout (s) | Timeout waiting for the server to be ready | `180` |
| `API_KEY` | API Key | `--api-key` access key | *(empty)* |

## Usage

1. Fill in **llama Dir** (folder containing `llama-server.exe`) and **Model** (GGUF path) — use the `Browse…` buttons.
2. Optionally set the vision projection, chat template, and any advanced tuning flags.
3. Press **▶ Start**. The GUI validates the inputs, saves the config, launches `llama-server.exe`, and streams its output to the log.
4. Once the `/health` endpoint responds, status flips to **● Running** and the URL is shown. Parameters are locked while running.
5. Connect an OpenAI-compatible client to `http://<HOST>:<PORT>`.
6. Press **■ Stop** (with a confirmation prompt) to terminate the server and unlock the fields.

## Project layout

```
Local-LLM-Manager/
├── local_llm_gui.py            # the GUI application (single file)
├── local_llm_gui_config.json   # saved parameters (gitignored, user-edited)
├── NVFP4.bat                   # optional variable source for defaults
├── run_local_llm_gui.bat       # convenience launcher
├── LocalLLMManager.spec        # PyInstaller spec (gitignored)
├── dist/                       # built LocalLLMManager.exe (gitignored)
└── build/                      # PyInstaller work directory (gitignored)
```

## Notes

- The GUI resolves its base directory from the running executable when packaged (PyInstaller) and from the script location otherwise, so the config and `NVFP4.bat` always stay beside the program.
- Numeric fields (`CTX_SIZE`, `THREADS`, `TBATCH`, `BATCH`, `UBATCH`, `NGL`, `PARALLEL`, `PORT`) are validated before launch.
- GPU/system monitoring degrades gracefully: labels show `N/A` if `nvidia-smi` or `psutil` are unavailable.
