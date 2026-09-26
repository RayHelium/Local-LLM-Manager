#!/usr/bin/env python3
"""Local LLM Manager — Web UI 版（Flask + 单页 HTML）。

运行:  python local_llm_web.py [--port 8090]
打开:  http://127.0.0.1:8090

功能与 tkinter 版 (local_llm_gui.py) 一致：
- 参数分组表单（Basic / Model / Advanced），路径字段带本地浏览
- 启动/停止 llama-server，日志实时回显，error 自动停止
- 健康检查、GPU/MEM 实时状态、累计 token 数
- 配置保存/恢复默认（复用 local_llm_gui_config.json）
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path

import psutil
from flask import Flask, Response, jsonify, request

# 路径基准：打包成 exe（PyInstaller）时 __file__ 指向临时解压目录，
# 需改用 sys.executable 所在目录，保证配置/bat 与 exe 同目录
if getattr(sys, "frozen", False):  # 打包后
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:  # 直接运行脚本
    SCRIPT_DIR = Path(__file__).resolve().parent
BAT_PATH = SCRIPT_DIR / "NVFP4.bat"
CONFIG_PATH = SCRIPT_DIR / "local_llm_gui_config.json"

# ============================================================
# 参数分组：(key, 标签, 说明)
# ============================================================
GROUP_BASE = [
    ("HOST", "Host", "Server listen address"),
    ("PORT", "Port", "Service port"),
    ("ALIAS", "Alias", "OpenAI-compatible alias"),
]
GROUP_MODEL = [
    ("MODEL_PATH", "Model", "GGUF model path (required)"),
    ("MMPROJ_PATH", "Vision Proj", "mmproj file path (optional)"),
    ("CHAT_TEMPLATE", "Chat Template", "Jinja template path (optional)"),
    ("CTX_SIZE", "Context Size", "Context size"),
    ("REASONING", "Reasoning", "--reasoning, on/off (optional)"),
    ("REASONING_EFFORT", "Reasoning Effort", "--reasoning-effort, e.g. low (optional)"),
    ("REASONING_PRESERVE", "Reasoning Preserve", "--reasoning-preserve, on/off (optional)"),
    ("REASONING_BUDGET", "Reasoning Budget", "--reasoning-budget, max reasoning tokens (optional)"),
    ("TEMPERATURE", "Temperature", "Sampling temperature"),
    ("TOP_K", "Top K", "--top-k, top-k sampling (default 40, 0=disabled)"),
    ("TOP_P", "Top P", "--top-p, nucleus sampling (default 0.95, 1.0=disabled)"),
    ("MIN_P", "Min P", "--min-p, min-p sampling (default 0.05, 0.0=disabled)"),
    ("SEED", "Seed", "--seed, RNG seed (-1=random)"),
    ("REPEAT_PENALTY", "Repeat Penalty", "--repeat-penalty (default 1.1, 1.0=disabled)"),
    ("N_PREDICT", "N Predict", "--n-predict, max tokens to generate (default 16384)"),
    ("IMAGE_MIN_TOKENS", "Image Min Tokens", "--image-min-tokens (optional)"),
    ("IMAGE_MAX_TOKENS", "Image Max Tokens", "--image-max-tokens (optional)"),
]
GROUP_ADVANCED = [
    ("LLAMA_DIR", "llama Dir", "Directory of llama-server.exe (required)"),
    ("THREADS", "Threads", "-t thread count"),
    ("TBATCH", "Thread Batch", "-tb thread batch size"),
    ("BATCH", "Batch", "-b batch size"),
    ("UBATCH", "UBatch", "-ub unit batch size"),
    ("CTK", "CTK", "-ctk KV cache type for K, e.g. q8_0 (optional)"),
    ("CTV", "CTV", "-ctv KV cache type for V, e.g. q8_0 (optional)"),
    ("NGL", "NGL", "-ngl layers offloaded to GPU, e.g. 999"),
    ("SM", "SM", "-sm split mode, e.g. tensor / layer"),
    ("PARALLEL", "Parallel", "--parallel, number of parallel sequences"),
    ("FLASH_ATTN", "Flash Attn", "--flash-attn, on/off/auto"),
    ("KV_UNIFIED", "KV Unified", "--kv-unified, on/off (optional)"),
    ("TENSOR_SPLIT", "Tensor Split", "--tensor-split, Multi-GPU split ratio (optional)"),
    ("SPEC_TYPE", "Spec Type", "--spec-type, e.g. draft-mtp (optional)"),
    ("SPEC_DRAFT_N_MAX", "Spec Draft N-Max", "--spec-draft-n-max (optional)"),
    ("SPEC_DRAFT_P_MIN", "Spec Draft P-Min", "--spec-draft-p-min (optional)"),
    ("SPEC_DRAFT_P_SPLIT", "Spec Draft P-Split", "--spec-draft-p-split, e.g. 0.10 (optional)"),
    ("SPEC_DRAFT_TYPE_K", "Spec Draft Type K", "--spec-draft-type-k, e.g. f16"),
    ("SPEC_DRAFT_TYPE_V", "Spec Draft Type V", "--spec-draft-type-v, e.g. f16"),
    ("CACHE_PROMPT", "Cache Prompt", "--cache-prompt, on/off (optional)"),
    ("CACHE_REUSE", "Cache Reuse", "--cache-reuse, reuse cached tokens, e.g. 1"),
    ("CACHE_RAM", "Cache RAM", "--cache-ram, prompt cache size in MB, e.g. 8192"),
    ("CACHE_IDLE_SLOTS", "Cache Idle Slots", "--cache-idle-slots, on/off (optional)"),
    ("SLEEP_IDLE_SECONDS", "Sleep Idle Seconds", "--sleep-idle-seconds, idle sleep timeout (optional)"),
    ("START_TIMEOUT_S", "Start Timeout (s)", "Timeout waiting for ready"),
    ("TIMEOUT_S", "Server Timeout (s)", "--timeout, server read/write timeout (optional)"),
    ("API_KEY", "API Key", "API access key"),
]

DEFAULT_VALUES = {
    # 核心：用户只需填写 llama-server 所在目录 + 模型路径
    "LLAMA_DIR": "",
    "MODEL_PATH": "",
    # 可选：视觉投影 / 聊天模板（留空则不启用）
    "MMPROJ_PATH": "",
    "CHAT_TEMPLATE": "",
    "HOST": "0.0.0.0",
    "PORT": "8080",
    "CTX_SIZE": "8192",
    "ALIAS": "",
    "THREADS": "8",
    "TBATCH": "2048",
    "BATCH": "2048",
    "UBATCH": "2048",
    "CTK": "",
    "CTV": "",
    "NGL": "999",
    "SM": "tensor",
    "PARALLEL": "1",
    "FLASH_ATTN": "on",
    "KV_UNIFIED": "on",
    "TENSOR_SPLIT": "",
    "SPEC_TYPE": "",
    "SPEC_DRAFT_N_MAX": "2",
    "SPEC_DRAFT_P_MIN": "0.6",
    "SPEC_DRAFT_P_SPLIT": "",
    "SPEC_DRAFT_TYPE_K": "",
    "SPEC_DRAFT_TYPE_V": "",
    "CACHE_PROMPT": "on",
    "CACHE_REUSE": "1",
    "CACHE_RAM": "8192",
    "CACHE_IDLE_SLOTS": "on",
    "SLEEP_IDLE_SECONDS": "900",
    "START_TIMEOUT_S": "180",
    "TIMEOUT_S": "",
    "API_KEY": "",
    "REASONING": "off",
    "REASONING_EFFORT": "low",
    "REASONING_PRESERVE": "",
    "REASONING_BUDGET": "20480",
    "TEMPERATURE": "0.8",
    "IMAGE_MIN_TOKENS": "1024",
    "TOP_K": "",
    "TOP_P": "",
    "MIN_P": "",
    "SEED": "",
    "REPEAT_PENALTY": "",
    "N_PREDICT": "16384",
    "IMAGE_MAX_TOKENS": "",
}

# 需要浏览器的字段：file=选文件, dir=选目录
PICKERS = {
    "MODEL_PATH": "file",
    "MMPROJ_PATH": "file",
    "CHAT_TEMPLATE": "file",
    "LLAMA_DIR": "dir",
}


# ============================================================
# 配置读写（与 tkinter 版一致）
# ============================================================
def _clean_value(value):
    v = str(value).strip()
    # 处理 set "KEY=VALUE" 整体加引号：value 可能带末尾孤立引号
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        v = v[1:-1]
    elif v.endswith('"') and len(v) >= 1:
        v = v[:-1]
    return v.strip()


def _collect_bat_vars():
    raw = {}
    if BAT_PATH.exists():
        try:
            with BAT_PATH.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line.lower().startswith("set"):
                        continue
                    rest = line[3:].lstrip()
                    m = re.match(r'^"?([A-Za-z0-9_]+)=(.*)"?$', rest)
                    if not m:
                        continue
                    key = m.group(1)
                    value = _clean_value(m.group(2))
                    raw[key] = value
        except Exception:
            pass
    # bat 中 SCRIPT_DIR=%~dp0，等价于本脚本所在目录
    raw["SCRIPT_DIR"] = str(SCRIPT_DIR) + os.sep
    return raw


def _expand(value, raw):
    for _ in range(6):
        def repl(mm):
            return raw.get(mm.group(1), mm.group(0))
        new = re.sub(r'%([A-Za-z0-9_]+)%', repl, value)
        if new == value:
            break
        value = new
    return value


def load_defaults():
    data = DEFAULT_VALUES.copy()
    raw = _collect_bat_vars()
    for key in data:
        if key in raw:
            data[key] = _expand(_clean_value(raw[key]), raw)
    return data


def load_saved_config():
    """默认值 + 已保存配置（若存在），返回完整参数 dict。"""
    data = load_defaults()
    if not CONFIG_PATH.exists():
        return data
    raw = _collect_bat_vars()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            saved = json.load(f)
        for key, value in saved.items():
            if key in data:
                # 修复历史脏数据：剥离引号 + 展开 %VAR%
                data[key] = _expand(_clean_value(value), raw)
    except Exception:
        pass
    return data


def save_config(values):
    current = {k: str(values.get(k, "")).strip() for k in DEFAULT_VALUES}
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


# ============================================================
# 日志级别分类（与 tkinter 版一致）
# ============================================================
def classify(message):
    m = message.lower()
    if any(k in m for k in ("error", "not found", "does not exist", "failed")):
        return "error"
    if any(k in m for k in ("ready", "running", "stopped", "done", "exited")):
        return "ready"
    if any(k in m for k in ("warn", "timeout")):
        return "warn"
    if m.startswith(("command:", "preparing", "starting")):
        return "cmd"
    return "info"


# ============================================================
# 服务器管理
# ============================================================
class Manager:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self._log_lock = threading.Lock()
        self.logs = deque(maxlen=3000)   # {id, ts, level, text}
        self.log_id = 0
        self.total_tokens = 0
        self.status = "stopped"          # stopped / starting / running
        self.url = ""

    # ---------- 日志 ----------
    def append_log(self, message):
        with self._log_lock:
            self.log_id += 1
            self.logs.append({
                "id": self.log_id,
                "ts": time.strftime("%H:%M:%S"),
                "level": classify(message),
                "text": message,
            })

    def clear_logs(self):
        with self._log_lock:
            self.logs.clear()

    # ---------- 校验 / 命令 ----------
    def validate(self, values):
        v = {k: str(values.get(k, "")).strip() for k in DEFAULT_VALUES}
        # 必填：llama-server 所在目录 + 模型文件
        for key, label in [("LLAMA_DIR", "llama dir"), ("MODEL_PATH", "Model")]:
            if not v[key]:
                raise ValueError(f"{label} cannot be empty")
            if not os.path.exists(v[key]):
                raise FileNotFoundError(f"{label} not found: {v[key]}")
        # 可选：填写了才校验存在
        for key, label in [("MMPROJ_PATH", "Vision Proj"), ("CHAT_TEMPLATE", "Chat Template")]:
            if v[key] and not os.path.exists(v[key]):
                raise FileNotFoundError(f"{label} not found: {v[key]}")
        exe = os.path.join(v["LLAMA_DIR"], "llama-server.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(f"llama-server.exe not found: {exe}")
        if not v["PORT"].isdigit():
            raise ValueError(f"Port must be a number: {v['PORT']}")
        for key, label in [("CTX_SIZE", "Context Size"), ("THREADS", "Threads"),
                           ("TBATCH", "Thread Batch"), ("BATCH", "Batch"),
                           ("UBATCH", "UBatch"), ("NGL", "NGL"),
                           ("PARALLEL", "Parallel")]:
            if not v[key].isdigit():
                raise ValueError(f"{label} must be a number: {v[key]}")
        return v

    def build_command(self, v):
        cmd = [
            os.path.join(v["LLAMA_DIR"], "llama-server.exe"),
            "-m", v["MODEL_PATH"],
            "-t", v["THREADS"],
            "-tb", v["TBATCH"],
            "-b", v["BATCH"],
            "-ub", v["UBATCH"],
            "-c", v["CTX_SIZE"],
            "--temp", v["TEMPERATURE"],
            "--host", v["HOST"],
            "--port", v["PORT"],
            "-ngl", v["NGL"] or "999",
            "--parallel", v["PARALLEL"] or "1",
            "--flash-attn", v["FLASH_ATTN"] or "on",
            "-sm", v["SM"] or "tensor",
        ]
        # 以下参数仅在填写时加入，保持通用性
        if v.get("KV_UNIFIED", "").lower() == "on":
            cmd += ["--kv-unified"]
        if v["CTK"]:
            cmd += ["-ctk", v["CTK"]]
        if v["CTV"]:
            cmd += ["-ctv", v["CTV"]]
        if v["MMPROJ_PATH"]:
            cmd += ["--mmproj", v["MMPROJ_PATH"]]
        if v["CHAT_TEMPLATE"]:
            cmd += ["--chat-template-file", v["CHAT_TEMPLATE"]]
        if v["ALIAS"]:
            cmd += ["--alias", v["ALIAS"]]
        if v["TENSOR_SPLIT"]:
            cmd += ["--tensor-split", v["TENSOR_SPLIT"]]
        if v["SPEC_TYPE"]:
            cmd += ["--spec-type", v["SPEC_TYPE"]]
        if v["SPEC_DRAFT_N_MAX"]:
            cmd += ["--spec-draft-n-max", v["SPEC_DRAFT_N_MAX"]]
        if v["SPEC_DRAFT_P_MIN"]:
            cmd += ["--spec-draft-p-min", v["SPEC_DRAFT_P_MIN"]]
        if v["SPEC_DRAFT_P_SPLIT"]:
            cmd += ["--spec-draft-p-split", v["SPEC_DRAFT_P_SPLIT"]]
        if v["SPEC_DRAFT_TYPE_K"]:
            cmd += ["--spec-draft-type-k", v["SPEC_DRAFT_TYPE_K"]]
        if v["SPEC_DRAFT_TYPE_V"]:
            cmd += ["--spec-draft-type-v", v["SPEC_DRAFT_TYPE_V"]]
        if v["CACHE_PROMPT"].lower() == "on":
            cmd += ["--cache-prompt"]
        if v["CACHE_REUSE"]:
            cmd += ["--cache-reuse", v["CACHE_REUSE"]]
        if v["CACHE_RAM"]:
            cmd += ["--cache-ram", v["CACHE_RAM"]]
        if v["CACHE_IDLE_SLOTS"].lower() == "on":
            cmd += ["--cache-idle-slots"]
        if v["SLEEP_IDLE_SECONDS"]:
            cmd += ["--sleep-idle-seconds", v["SLEEP_IDLE_SECONDS"]]
        if v["REASONING"]:
            cmd += ["--reasoning", v["REASONING"]]
        if v["REASONING_EFFORT"]:
            cmd += ["--reasoning-effort", v["REASONING_EFFORT"]]
        if v["REASONING_PRESERVE"].lower() == "on":
            cmd += ["--reasoning-preserve"]
        elif v["REASONING_PRESERVE"].lower() == "off":
            cmd += ["--no-reasoning-preserve"]
        if v["REASONING_BUDGET"]:
            cmd += ["--reasoning-budget", v["REASONING_BUDGET"]]
        if v["IMAGE_MIN_TOKENS"]:
            cmd += ["--image-min-tokens", v["IMAGE_MIN_TOKENS"]]
        if v["IMAGE_MAX_TOKENS"]:
            cmd += ["--image-max-tokens", v["IMAGE_MAX_TOKENS"]]
        if v["TOP_K"]:
            cmd += ["--top-k", v["TOP_K"]]
        if v["TOP_P"]:
            cmd += ["--top-p", v["TOP_P"]]
        if v["MIN_P"]:
            cmd += ["--min-p", v["MIN_P"]]
        if v["SEED"]:
            cmd += ["--seed", v["SEED"]]
        if v["REPEAT_PENALTY"]:
            cmd += ["--repeat-penalty", v["REPEAT_PENALTY"]]
        if v["N_PREDICT"]:
            cmd += ["--n-predict", v["N_PREDICT"]]
        if v["TIMEOUT_S"]:
            cmd += ["--timeout", v["TIMEOUT_S"]]
        if v["API_KEY"]:
            cmd += ["--api-key", v["API_KEY"]]
        return cmd

    # ---------- 启动 / 停止 ----------
    def start(self, values):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return False, "Server is already running. No need to start again."
            try:
                v = self.validate(values)
            except (ValueError, FileNotFoundError) as exc:
                self.append_log(f"Start failed: {exc}")
                return False, str(exc)
            save_config(v)
            cmd = self.build_command(v)
            self.append_log("Preparing to start llama-server ...")
            self.append_log("Command: " + " ".join(cmd))
            self.url = f"http://{v['HOST']}:{v['PORT']}"
            self.process = subprocess.Popen(
                cmd,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.status = "starting"
            threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()
            threading.Thread(target=self._poll_health, args=(v["HOST"], v["PORT"]), daemon=True).start()
            return True, None

    def stop(self):
        with self.lock:
            if self.process is None:
                return False, "No server process is currently managed."
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.append_log("Server stopped.")
            except subprocess.TimeoutExpired:
                self.append_log("Stop timed out. Force-killing the process.")
                self.process.kill()
            finally:
                self.process = None
                self.status = "stopped"
                self.total_tokens = 0
            return True, None

    # ---------- 输出读取 / 健康检查 ----------
    def _read_output(self, proc):
        error_found = False
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            line = line.rstrip()
            self.append_log(line)
            # 累加 token 数（print_timing 日志，例如: ... eval time = 97085.65 ms / 7880 tokens ...）
            mt = re.search(r'(\d+)\s*tokens', line)
            if mt:
                self.total_tokens += int(mt.group(1))
            # 出现 error 级别日志：立即停止服务器
            if re.search(r"\berror\b", line, re.IGNORECASE):
                error_found = True
                self.append_log("Error detected in server log. Stopping server.")
                break
        if self.process is proc:
            if error_found and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                    self.append_log("Server process stopped due to error.")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self.append_log("Server process killed due to error.")
            code = proc.poll()
            if code is not None:
                self.append_log(f"Process exited with code: {code}")
                self.status = "stopped"
                self.total_tokens = 0
                self.process = None

    def _poll_health(self, host, port):
        h = host.strip()
        if not h or h == "0.0.0.0":
            h = "127.0.0.1"
        url = f"http://{h}:{port}/health"
        while True:
            proc = self.process
            if proc is None or proc.poll() is not None:
                return
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    if resp.status == 200:
                        self.status = "running"
                        self.append_log(f"Server ready: {url}")
                        return
            except Exception:
                pass
            time.sleep(1.5)


MANAGER = Manager()
app = Flask(__name__)


# ============================================================
# 系统状态
# ============================================================
def gpu_info():
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            out = []
            for idx in range(2):
                if idx < len(lines):
                    parts = [p.strip() for p in lines[idx].split(",")]
                    if len(parts) >= 6:
                        util = int(parts[1])
                        used, total = parts[2], parts[3]
                        temp = int(parts[4])
                        # 功率：power.draw（W），个别显卡/虚拟化环境可能为 [N/A]
                        pw = parts[5]
                        if "N/A" in pw:
                            power = "N/A"
                        else:
                            try:
                                power = f"{float(pw):.0f}W"
                            except ValueError:
                                power = f"{pw}W"
                        out.append(f"GPU{idx} {util}%  {used}/{total}MB  {temp}°C  {power}")
                    else:
                        out.append(f"GPU{idx} N/A")
                else:
                    out.append(f"GPU{idx} not detected")
            return out
    except Exception:
        pass
    return ["GPU0 N/A", "GPU1 N/A"]


# ============================================================
# API
# ============================================================
@app.get("/api/init")
def api_init():
    """前端初始化：分组定义 + 当前参数值。"""
    groups = [
        {"title": "Basic Parameters", "fields": GROUP_BASE},
        {"title": "Model Parameters", "fields": GROUP_MODEL},
        {"title": "Advanced Parameters", "fields": GROUP_ADVANCED},
    ]
    # 附带 picker 信息
    for g in groups:
        g["fields"] = [
            {"key": k, "label": label, "hint": hint,
             "picker": PICKERS.get(k)}
            for k, label, hint in g["fields"]
        ]
    return jsonify(groups=groups, params=load_saved_config())


@app.get("/api/state")
def api_state():
    m = MANAGER
    proc = m.process
    running = proc is not None and proc.poll() is None
    try:
        mem = psutil.virtual_memory().percent
        mem_txt = f"{mem:.0f}%"
    except Exception:
        mem_txt = "N/A"
    return jsonify(
        status=m.status if running else "stopped",
        url=m.url if running else "",
        tokens=m.total_tokens,
        locked=running,
        gpu=gpu_info(),
        mem=mem_txt,
    )


@app.get("/api/logs")
def api_logs():
    after = int(request.args.get("after", 0))
    if request.args.get("download") == "1":
        text = "\n".join(f'{e["ts"]}  {e["text"]}' for e in MANAGER.logs)
        filename = f"llm_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        return Response(text, mimetype="text/plain",
                        headers={"Content-Disposition": f'attachment; filename={filename}'})
    with MANAGER._log_lock:
        entries = [e for e in MANAGER.logs if e["id"] > after]
        total = MANAGER.log_id
    return jsonify(entries=entries, total=total)


@app.post("/api/clear-logs")
def api_clear_logs():
    MANAGER.clear_logs()
    MANAGER.append_log("Log cleared.")
    return jsonify(ok=True)


@app.post("/api/start")
def api_start():
    values = request.get_json(force=True, silent=True) or {}
    ok, err = MANAGER.start(values)
    return jsonify(ok=ok, error=err)


@app.post("/api/stop")
def api_stop():
    ok, err = MANAGER.stop()
    return jsonify(ok=ok, error=err)


@app.post("/api/save")
def api_save():
    values = request.get_json(force=True, silent=True) or {}
    try:
        save_config(values)
        MANAGER.append_log(f"Config saved: {CONFIG_PATH.name}")
        return jsonify(ok=True)
    except Exception as exc:
        MANAGER.append_log(f"Failed to save config: {exc}")
        return jsonify(ok=False, error=str(exc))


@app.post("/api/reset")
def api_reset():
    params = load_defaults()
    MANAGER.append_log("Restored default parameters.")
    return jsonify(params=params)


@app.post("/api/quit")
def api_quit():
    """退出 Web 服务本身（配合后台 pythonw 启动使用）。"""
    MANAGER.append_log("Web service is shutting down.")
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.get("/api/browse")
def api_browse():
    """本地目录浏览（供前端文件/目录选择器使用）。"""
    p = Path(request.args.get("path", str(SCRIPT_DIR)))
    try:
        if not p.is_dir():
            p = p.parent if p.parent != p else Path(str(SCRIPT_DIR))
        dirs, files = [], []
        for item in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if item.name.startswith("."):
                continue
            (dirs if item.is_dir() else files).append(item.name)
        parent = str(p.parent) if p.resolve().parent != p.resolve() else ""
    except Exception as exc:
        return jsonify(path=str(p), parent="", dirs=[], files=[], error=str(exc))
    return jsonify(path=str(p), parent=parent, sep=os.sep, dirs=dirs, files=files)


# ============================================================
# 前端页面
# ============================================================
HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local LLM Manager</title>
<style>
:root {
  --bg:#1e1e22; --panel:#2a2a30; --panel-light:#3a3a42;
  --fg:#f2f2f5; --dim:#9a9aa5; --accent:#4a9eff;
  --green:#34c759; --red:#ff453a; --yellow:#ff9f0a; --entry:#16161a;
}
* { box-sizing:border-box; }
html,body { height:100%; }
body {
  margin:0; background:var(--bg); color:var(--fg);
  font-family:"Segoe UI",system-ui,-apple-system,sans-serif; font-size:14px;
  display:flex; flex-direction:column;
}
header { display:flex; align-items:center; gap:18px; padding:14px 18px 6px; }
h1 { font-size:20px; color:var(--accent); margin:0; white-space:nowrap; }
#url { color:var(--dim); font-weight:600; font-size:13px; }
.spacer { flex:1; }
.status { font-weight:700; font-size:14px; white-space:nowrap; }
.status.idle { color:var(--dim); } .status.start { color:var(--yellow); } .status.run { color:var(--green); }
#stats { display:flex; flex-wrap:wrap; gap:8px 28px; padding:4px 18px 10px;
         font-family:Consolas,Menlo,monospace; font-weight:700; font-size:16px; color:var(--dim); }
#stats .gpu-line { white-space:nowrap; }
main { flex:1; display:flex; flex-direction:column; gap:12px; padding:0 18px 14px; min-height:0; }
#params { flex:1; overflow-y:auto; }
.group { background:var(--panel); border-radius:8px; margin-bottom:10px; padding:8px 12px; }
.group-title { margin:0 0 6px; font-size:13px; color:var(--accent); cursor:pointer; user-select:none; }
.group.collapsed .group-body { display:none; }
.row { display:flex; align-items:center; gap:12px; padding:5px 0; }
.left { width:360px; min-width:360px; }
.title { font-weight:700; font-size:13px; }
.hint { color:var(--dim); font-size:12px; margin-top:1px; }
.entry { flex:1; min-width:0; background:var(--entry); border:1px solid var(--panel-light);
         color:var(--fg); border-radius:6px; padding:7px 9px;
         font-family:Consolas,Menlo,monospace; font-size:13px; }
.entry:focus { outline:1px solid var(--accent); }
.entry:disabled { opacity:.55; }
.btn { background:var(--panel-light); color:var(--fg); border:none; border-radius:6px;
       padding:8px 14px; font-weight:700; cursor:pointer; font-size:13px; }
.btn:hover { background:var(--accent); color:#fff; }
.btn.small { padding:6px 10px; font-size:12px; }
.btn.start { background:var(--green); color:#fff; } .btn.start:hover { background:#5fd87d; }
.btn.stop { background:var(--red); color:#fff; } .btn.stop:hover { background:#ff6b60; }
#btnrow { display:flex; gap:8px; padding:0 18px 10px; }
#logpanel { height:38%; min-height:200px; display:flex; flex-direction:column;
            background:var(--panel); border-radius:8px; padding:10px; min-height:0; }
#loghead { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
#loghead b { font-size:13px; }
#logbox { flex:1; overflow-y:auto; background:var(--entry); border-radius:6px; padding:8px;
          font-family:Consolas,Menlo,monospace; font-size:12px; line-height:1.5; }
.logline { white-space:pre-wrap; word-break:break-all; }
.logline.error { color:var(--red); font-weight:700; }
.logline.ready { color:var(--green); font-weight:700; }
.logline.warn { color:var(--yellow); }
.logline.cmd { color:var(--accent); }
.modal-bg { position:fixed; inset:0; background:rgba(0,0,0,.6);
            display:flex; align-items:center; justify-content:center; z-index:10; }
.modal { background:var(--panel); border-radius:10px; width:540px; max-width:92vw;
         max-height:70vh; display:flex; flex-direction:column; padding:14px; }
#mPath { color:var(--dim); font-family:Consolas,monospace; font-size:12px; word-break:break-all; }
#mList { overflow-y:auto; flex:1; margin-top:8px; }
.item { padding:6px 10px; border-radius:5px; cursor:pointer;
        font-family:Consolas,monospace; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.item:hover { background:var(--panel-light); }
.item.dir { color:var(--accent); }
.item.select { color:var(--green); }
.hidden { display:none !important; }
</style>
</head>
<body>
<header>
  <h1>Local LLM Manager</h1>
  <span id="url"></span>
  <div class="spacer"></div>
  <span id="status" class="status idle">● Stopped</span>
  <button id="btnQuit" class="btn small" title="Stop the web service (background process)">✕ Quit</button>
</header>
<div id="stats">
  <span class="gpu-line" id="gpu0">GPU0 --%  --/--MB  --°C  --W</span>
  <span class="gpu-line" id="gpu1">GPU1 --%  --/--MB  --°C  --W</span>
  <span id="tokens">Tokens: 0</span>
  <span id="sys">MEM: --%</span>
</div>
<div id="btnrow">
  <button id="btnToggle" class="btn start">▶  Start</button>
  <button id="btnSave" class="btn">Save Config</button>
  <button id="btnReset" class="btn">Reset Defaults</button>
</div>
<main>
  <section id="params"></section>
  <section id="logpanel">
    <div id="loghead">
      <b>Log Output</b>
      <span>
        <button id="btnClear" class="btn small">Clear</button>
        <button id="btnSaveLog" class="btn small">Save Log</button>
      </span>
    </div>
    <div id="logbox"></div>
  </section>
</main>
<div id="modal" class="modal-bg hidden">
  <div class="modal">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
      <b>Browse</b><span id="mPath"></span>
    </div>
    <div id="mList"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:10px;">
      <button class="btn" id="mClose">Cancel</button>
    </div>
  </div>
</div>
<script>
let groups = [], params = {}, lastLogId = 0, running = false;
let browseKey = null, browseType = null;

const $ = id => document.getElementById(id);
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                               body: JSON.stringify(body || {}) });
  return r.json();
}

function collectParams() {
  for (const k in params) {
    const e = $("f_" + k);
    if (e) params[k] = e.value;
  }
  return params;
}

function renderGroups() {
  const root = $("params");
  root.innerHTML = "";
  for (const g of groups) {
    const sec = el("section", "group");
    const h = el("h2", "group-title");
    h.textContent = "▼ " + g.title;
    h.onclick = () => {
      sec.classList.toggle("collapsed");
      h.textContent = (sec.classList.contains("collapsed") ? "▶ " : "▼ ") + g.title;
    };
    sec.appendChild(h);
    const body = el("div", "group-body");
    for (const f of g.fields) {
      const row = el("div", "row");
      const left = el("div", "left");
      const t = el("div", "title"); t.textContent = f.label + "  (" + f.key + ")";
      const hint = el("div", "hint"); hint.textContent = f.hint;
      left.append(t, hint);
      const input = document.createElement("input");
      input.id = "f_" + f.key;
      input.value = params[f.key] || "";
      input.className = "entry";
      row.append(left, input);
      if (f.picker) {
        const b = el("button", "btn small");
        b.textContent = "Browse…";
        b.onclick = () => openBrowse(f.key, f.picker);
        row.append(b);
      }
      body.appendChild(row);
    }
    sec.appendChild(body);
    root.appendChild(sec);
  }
}

async function pollState() {
  try {
    const s = await jget("/api/state");
    running = s.locked;
    const st = $("status");
    if (s.status === "running") { st.textContent = "● Running"; st.className = "status run"; }
    else if (s.status === "starting") { st.textContent = "● Starting"; st.className = "status start"; }
    else { st.textContent = "● Stopped"; st.className = "status idle"; }
    $("url").textContent = s.url ? "URL: " + s.url : "";
    $("tokens").textContent = "Tokens: " + Number(s.tokens).toLocaleString();
    $("sys").textContent = "MEM: " + s.mem;
    const gpu = s.gpu || [];
    $("gpu0").textContent = gpu[0] || "GPU0 N/A";
    $("gpu1").textContent = gpu[1] || "GPU1 N/A";
    const btn = $("btnToggle");
    btn.textContent = running ? "■  Stop" : "▶  Start";
    btn.className = "btn " + (running ? "stop" : "start");
    document.querySelectorAll(".entry").forEach(e => e.disabled = running);
  } catch (e) { /* server restarting */ }
}

async function pollLogs() {
  try {
    const r = await jget("/api/logs?after=" + lastLogId);
    if (r.entries.length) {
      const box = $("logbox");
      const stick = box.scrollTop + box.clientHeight > box.scrollHeight - 40;
      for (const e of r.entries) {
        const div = el("div", "logline " + e.level);
        div.textContent = e.ts + "  " + e.text;
        box.appendChild(div);
        lastLogId = e.id;
      }
      while (box.children.length > 2000) box.removeChild(box.firstChild);
      if (stick) box.scrollTop = box.scrollHeight;
    } else {
      lastLogId = r.total; // 日志被清空时同步
    }
  } catch (e) { /* ignore */ }
}

// ---------- 浏览（文件/目录选择） ----------
async function openBrowse(key, type) {
  browseKey = key; browseType = type;
  const start = params[key] ? "?path=" + encodeURIComponent(params[key]) : "";
  await loadBrowse(start);
  $("modal").classList.remove("hidden");
}
function closeModal() { $("modal").classList.add("hidden"); }

async function loadBrowse(query) {
  const r = await jget("/api/browse" + query);
  $("mPath").textContent = r.path;
  const list = $("mList");
  list.innerHTML = "";
  if (r.parent) {
    const up = el("div", "item dir");
    up.textContent = "..";
    up.onclick = () => loadBrowse("?path=" + encodeURIComponent(r.parent));
    list.appendChild(up);
  }
  for (const d of r.dirs) {
    const it = el("div", "item dir");
    it.textContent = d + "/";
    it.title = d;
    it.onclick = () => loadBrowse("?path=" + encodeURIComponent(r.path + r.sep + d));
    list.appendChild(it);
  }
  if (browseType === "dir") {
    const sel = el("div", "item select");
    sel.textContent = "✓ Select this folder";
    sel.onclick = () => {
      params[browseKey] = r.path;
      $("f_" + browseKey).value = r.path;
      closeModal();
    };
    list.appendChild(sel);
  }
  for (const f of r.files) {
    const it = el("div", "item");
    it.textContent = f;
    it.title = f;
    if (browseType === "file") {
      it.onclick = () => {
        params[browseKey] = r.path + r.sep + f;
        $("f_" + browseKey).value = params[browseKey];
        closeModal();
      };
    }
    list.appendChild(it);
  }
}

// ---------- 按钮 ----------
$("btnToggle").onclick = async () => {
  if (running) {
    await jpost("/api/stop");
  } else {
    await jpost("/api/start", collectParams());
  }
  pollState();
};
$("btnSave").onclick = async () => { await jpost("/api/save", collectParams()); };
$("btnReset").onclick = async () => {
  const r = await jpost("/api/reset");
  Object.assign(params, r.params);
  for (const k in params) {
    const e = $("f_" + k);
    if (e) e.value = params[k];
  }
};
$("btnClear").onclick = async () => {
  await jpost("/api/clear-logs");
  $("logbox").innerHTML = "";
};
$("btnSaveLog").onclick = () => { window.location = "/api/logs?download=1"; };
$("mClose").onclick = closeModal;$("btnQuit").onclick = async () => {
  if (confirm("Quit the web service? The background process will stop.")) {
    await jpost("/api/quit");
    document.body.innerHTML = "<div style='padding:40px;font-size:16px;'>Web service stopped. You can close this page.</div>";
  }
};
// ---------- 初始化 ----------
(async function init() {
  const data = await jget("/api/init");
  groups = data.groups;
  params = data.params;
  renderGroups();
  pollState();
  setInterval(pollState, 2000);
  pollLogs();
  setInterval(pollLogs, 1500);
})();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Local LLM Manager (Web UI)")
    ap.add_argument("--port", type=int, default=8090, help="Web UI port (default 8090)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Listen address (use 0.0.0.0 to allow LAN access)")
    ap.add_argument("--no-browser", action="store_true", help="Do not auto-open browser")
    args = ap.parse_args()
    MANAGER.append_log("Web GUI started. Set your llama-server directory and model path, then press Start.")
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"Local LLM Manager (Web): {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, threaded=True)
