#!/usr/bin/env bash
# =============================================================================
# setup.sh —— 一键把 tushare_trader 装好并跑通
#
# 用法：
#   ./setup.sh                      # 用环境变量 TUSHARE_TOKEN 或已存在的 token 文件
#   ./setup.sh 你的token            # 直接传 token（只写入 ~/.tushare/token，不入库）
#   ./setup.sh --run                # 装完后立刻跑一次完整流程（含回测）
#   ./setup.sh --run --limit 50     # 其余参数原样传给 CLI
#
# 做的事：
#   1. 建虚拟环境 .venv（已存在则复用）
#   2. 安装依赖 pandas/numpy/pyyaml/tushare/pytest
#   3. 配置 token（优先级：命令行参数 > 环境变量 > 已有文件）
#   4. 体检 doctor
#   5. 可选：跑完整流程
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

PY=${PYTHON:-python3}
VENV=.venv
TOKEN_ARG=""
RUN=0
PASSTHRU=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run) RUN=1; shift ;;
    --limit|--start|--end|--date) PASSTHRU+=("$1" "${2:-}"); shift 2 ;;
    --dry-run|--no-backtest) PASSTHRU+=("$1"); shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) TOKEN_ARG="$1"; shift ;;
  esac
done

echo "============================================================"
echo "  tushare_trader 一键安装"
echo "============================================================"

# ---------------------------------------------------------------- 1. 虚拟环境
echo
echo "[1/5] Python 虚拟环境"
if [[ ! -d "$VENV" ]]; then
  echo "  创建 $VENV ..."
  "$PY" -m venv "$VENV"
else
  echo "  复用已有 $VENV"
fi
PIP="$VENV/bin/pip"
PYBIN="$VENV/bin/python"
[[ -x "$PYBIN" ]] || { echo "  [错误] 虚拟环境创建失败"; exit 1; }
"$PYBIN" -c "import sys; print('  Python', sys.version.split()[0])"

# ---------------------------------------------------------------- 2. 依赖
echo
echo "[2/5] 安装依赖"
# 优先用国内镜像加速；失败则回退官方源
MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"
echo "  升级 pip ..."
"$PIP" -q install --upgrade pip $MIRROR 2>/dev/null || "$PIP" -q install --upgrade pip

install_deps() {
  "$PIP" -q install "$@" $MIRROR 2>/dev/null || "$PIP" -q install "$@"
}
echo "  安装 pandas / numpy / pyyaml / tushare / pytest ..."
install_deps pandas numpy pyyaml tushare pytest
"$PYBIN" - <<'PY'
import pandas, numpy, yaml, tushare, pytest
print(f"  pandas {pandas.__version__}  numpy {numpy.__version__}  tushare {tushare.__version__}")
PY

# ---------------------------------------------------------------- 3. token
echo
echo "[3/5] 配置 Tushare token"
TOKEN_FILE="$HOME/.tushare/token"
if [[ -n "$TOKEN_ARG" ]]; then
  mkdir -p "$(dirname "$TOKEN_FILE")"
  printf '%s' "$TOKEN_ARG" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "  已写入 $TOKEN_FILE（权限 600，不在仓库内）"
elif [[ -n "${TUSHARE_TOKEN:-}" ]]; then
  echo "  使用环境变量 TUSHARE_TOKEN"
elif [[ -f "$TOKEN_FILE" ]]; then
  echo "  使用已有 $TOKEN_FILE"
elif [[ -f .tushare_token ]]; then
  echo "  使用仓库内 .tushare_token（已被 .gitignore 排除）"
else
  echo "  [错误] 没有找到 token。请任选一种："
  echo "    ./setup.sh 你的token"
  echo "    export TUSHARE_TOKEN=你的token && ./setup.sh"
  echo "    mkdir -p ~/.tushare && echo 你的token > ~/.tushare/token && chmod 600 ~/.tushare/token"
  exit 1
fi

# ---------------------------------------------------------------- 4. 体检
echo
echo "[4/5] 环境体检"
"$PYBIN" -m tushare_trader doctor || {
  echo "  [错误] 体检未通过，请检查上面的 FAIL 项"
  exit 1
}

# ---------------------------------------------------------------- 5. 运行
echo
echo "[5/5] 完成"
if [[ $RUN -eq 1 ]]; then
  echo
  echo "============================================================"
  echo "  跑完整流程"
  echo "============================================================"
  "$PYBIN" -m tushare_trader all "${PASSTHRU[@]+"${PASSTHRU[@]}"}"
else
  cat <<'EOF'

接下来可以：

  .venv/bin/python -m tushare_trader all          # 全流程（含回测）
  .venv/bin/python -m tushare_trader prepare      # 只做盘前
  .venv/bin/python -m tushare_trader backtest     # 只回测
  .venv/bin/python -m pytest tests -q             # 跑测试

或直接：./setup.sh --run
EOF
fi
