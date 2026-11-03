import sys
import os
import joblib
import numpy as np
from pathlib import Path

# 把根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.features import extract_features, to_vector
from ml.gen_mock import CLASSES

# 全局变量
_model = None

# 五类细标签 -> 三类频率选择特性
# RC 低通与 RL 低通在相同 fc 下不可区分（实测仅差约 1.3 dB），
# 因此对外只输出"低通/高通/带通"这一层可可靠判定的结论。
SHAPE_OF = {
    "rc_lowpass": "低通",
    "rl_lowpass": "低通",
    "rc_highpass": "高通",
    "rl_highpass": "高通",
    "rlc_bandpass": "带通",
}
SHAPE_ORDER = ["低通", "高通", "带通"]


def get_model():
    """优先加载与 sklearn 版本无关的 JSON；没有 JSON 时回退到 joblib。"""
    global _model
    if _model is None:
        model_dir = os.path.join(os.path.dirname(__file__), "model")
        json_path = os.path.join(model_dir, "topo_mlp.json")
        if os.path.exists(json_path):
            from ml.json_infer import JsonModel
            _model = JsonModel(json_path)
        else:
            joblib_path = os.path.join(model_dir, "topo_mlp.joblib")
            if not os.path.exists(joblib_path):
                raise FileNotFoundError(
                    "找不到模型文件: %s 或 %s，请先运行 train.py" % (json_path, joblib_path))
            _model = joblib.load(joblib_path)
    return _model


def _fmt_hz(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "--"
    if v <= 0:
        return "--"
    if v >= 1000:
        return "%.1f kHz" % (v / 1000.0)
    return "%.0f Hz" % v


def _shape_probs(model, proba):
    """把五类概率按 低通/高通/带通 分组求和（划分是完备的，概率可加）。"""
    agg = {k: 0.0 for k in SHAPE_ORDER}
    for cls, p in zip(model.classes_, proba):
        key = SHAPE_OF.get(cls)
        if key is None:
            continue
        agg[key] += float(p)
    return agg


def predict_topology(sweep_dict, data_source="demo"):
    """
    核心推理函数。增加了进门校验，防止在无效数据上强行给出高置信度。
    data_source: "rtl" | "audio" | "demo" | "pynq_rtl" | "pynq_audio_loopback"
    """
    # ==========================================
    # C: 进门校验（防止一根直线或者断线测出来的数据让模型乱猜）
    # ==========================================
    freqs = sweep_dict.get("freqs", [])
    mags = sweep_dict.get("mag_db", [])
    phases = sweep_dict.get("phase_deg", [])

    # 1. 检查点数是否太少
    if len(freqs) < 10 or len(mags) < 10:
        return _invalid_response("数据点太少（<10个），无法提取有效特征", data_source)

    # 2. 检查是否包含 NaN 或 Infinity
    if np.any(np.isnan(mags)) or np.any(np.isinf(mags)):
        return _invalid_response("数据包含无效值 (NaN/Inf)，请检查硬件连接", data_source)

    # 3. 检查动态范围（如果曲线太平，说明没接电路或者短路了）
    mags_arr = np.array(mags)
    dynamic_range = np.max(mags_arr) - np.min(mags_arr)
    if dynamic_range < 1.0:
        return _invalid_response(f"信号动态范围过小 ({dynamic_range:.2f} dB)，可能未接入电路或已削顶", data_source)

    # 4. 检查信号强度：整条曲线最强点若远低于 -30 dB，说明几乎没有信号
    peak_db = float(np.max(mags_arr))
    if peak_db < -30.0:
        return _invalid_response(
            f"信号过弱（最强点仅 {peak_db:.1f} dB），请检查接线或重新标定",
            data_source)

    # ==========================================
    # 正常推理
    # ==========================================
    model = get_model()

    # 提取特征
    feats = extract_features(freqs, mags, phases)
    X_input = to_vector(feats).reshape(1, -1)

    # 预测（五类 -> 三类形状）
    pred_proba = model.predict_proba(X_input)[0]
    agg = _shape_probs(model, pred_proba)

    shape = max(SHAPE_ORDER, key=lambda k: agg[k])
    confidence = float(agg[shape])

    # 观测拐点：低通/高通用 f3db；带通用峰值频率
    if shape == "带通":
        fc = feats.get("peak_freq_hz", 0.0)
        fc_name = "中心频率"
    else:
        fc = feats.get("f3db_hz", 0.0)
        fc_name = "观测拐点"

    label = "%s（%s %s）" % (shape, fc_name, _fmt_hz(fc))

    top3 = [{"label": k, "confidence": float(agg[k])}
            for k in sorted(SHAPE_ORDER, key=lambda k: -agg[k])]

    # 统一处理 model_note (兼容 app.py 传过来的多种 mode 字符串)
    if data_source in ["rtl", "pynq_rtl"]:
        note = "基于自研 PL 解调实测数据"
    elif data_source in ["audio", "pynq_audio_loopback"]:
        note = "基于官方音频闭环数据，自研解调链已接入但未用于本次推理"
    else:
        note = "演示 CSV + 预训练演示模型，不代表硬件实测"

    return {
        "label": label,
        "shape": shape,
        "confidence": confidence,
        "fc_hz": float(fc) if fc else 0.0,
        "fc_name": fc_name,
        "top3": top3,
        "model_note": note,
    }


def _invalid_response(reason, data_source):
    """封装返回无效数据的结构，避免前端报错"""
    if data_source in ["rtl", "pynq_rtl"]:
        note = "基于自研 PL 解调实测数据"
    elif data_source in ["audio", "pynq_audio_loopback"]:
        note = "基于官方音频闭环数据"
    else:
        note = "演示 CSV 数据"

    return {
        "label": "数据无效",
        "shape": "无效",
        "confidence": 0.0,
        "fc_hz": 0.0,
        "fc_name": "",
        "top3": [],
        "model_note": f"【拦截】{reason}。{note}。",
    }
